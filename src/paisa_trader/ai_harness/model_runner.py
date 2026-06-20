from __future__ import annotations

import json
import os
import re
import asyncio
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelRunnerConfig:
    provider: str = "mock"
    model_name: str = "mock"
    api_key_env: str = ""
    temperature: float = 0.1
    max_tokens: int = 512
    local_url: str = "http://127.0.0.1:11434/api/generate"


class ModelRunner(ABC):
    @abstractmethod
    async def run(self, system: str, user: str) -> str:
        """Return raw model text."""


class MockRunner(ModelRunner):
    async def run(self, system: str, user: str) -> str:
        score = _extract_float(user, r"Next-move score:\s*([+-]?\d+(?:\.\d+)?)", default=0.0)
        confidence = _extract_percent(user, r"Confidence:\s*(\d+(?:\.\d+)?)%", default=abs(score))
        gate = "Intelligence gate: True" in user

        if score >= 0.25 and confidence >= 0.65 and gate:
            action = "BUY"
            risk = "Paper entry only; respect the synthetic depth and spread assumptions."
        elif score <= -0.25:
            action = "CLOSE"
            risk = "Momentum is weakening; close any existing long exposure."
        else:
            action = "HOLD"
            risk = "No trade because confidence, gate, or direction is not strong enough."

        return json.dumps(
            {
                "action": action,
                "confidence": round(min(1.0, max(confidence, abs(score))), 3),
                "reasoning": f"Mock decision from next-move score {score:+.3f}, confidence {confidence:.0%}, gate={gate}.",
                "next_move_prediction": _prediction(score),
                "future_predictions": [
                    _future_prediction(score, 1, "+5m"),
                    _future_prediction(score * 0.75, 3, "+15m"),
                ],
                "key_signals": ["NEXT_MOVE_SCORE", "CONFIDENCE", "INTELLIGENCE_GATE"],
                "risk_note": risk,
            }
        )


class ClaudeRunner(ModelRunner):
    def __init__(self, config: ModelRunnerConfig):
        self.config = config

    async def run(self, system: str, user: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("Install anthropic to use ClaudeRunner") from exc

        api_key = _api_key(self.config)
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=self.config.model_name,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(block, "text", "") for block in response.content)


class OpenAIRunner(ModelRunner):
    def __init__(self, config: ModelRunnerConfig):
        self.config = config

    async def run(self, system: str, user: str) -> str:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Install openai to use OpenAIRunner") from exc

        client = AsyncOpenAI(api_key=_api_key(self.config))
        response = await client.chat.completions.create(
            model=self.config.model_name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class LocalRunner(ModelRunner):
    def __init__(self, config: ModelRunnerConfig):
        self.config = config

    async def run(self, system: str, user: str) -> str:
        payload = json.dumps(
            {
                "model": self.config.model_name,
                "prompt": f"{system}\n\n{user}",
                "stream": False,
                "options": {"temperature": self.config.temperature, "num_predict": self.config.max_tokens},
            }
        ).encode()
        data = await asyncio.to_thread(_post_json, self.config.local_url, payload)
        return str(data.get("response", ""))


class LMStudioRunner(ModelRunner):
    def __init__(self, config: ModelRunnerConfig):
        self.config = config
        self.base_url = config.local_url.rstrip("/")

    async def run(self, system: str, user: str) -> str:
        model = self.config.model_name
        if model in {"", "auto", "lmstudio"}:
            model = await asyncio.to_thread(self.detect_model)
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "stream": False,
            }
        ).encode()
        data = await asyncio.to_thread(_post_json, f"{self.base_url}/v1/chat/completions", payload)
        return str(data["choices"][0]["message"].get("content", ""))

    def detect_model(self) -> str:
        data = _get_json(f"{self.base_url}/v1/models")
        models = data.get("data", [])
        if not models:
            raise RuntimeError(
                "LM Studio is running but no model is loaded yet. Finish the download/load in LM Studio and retry."
            )
        model_id = str(models[0].get("id", "")).strip()
        if not model_id:
            raise RuntimeError("LM Studio returned a model entry without an id")
        if "embed" in model_id.lower():
            raise RuntimeError(
                f"LM Studio detected {model_id}, but predictions require a chat/completion model. "
                "Finish loading the downloaded chat model in LM Studio and retry."
            )
        return model_id


def runner_from_config(config: ModelRunnerConfig | Any | None = None) -> ModelRunner:
    cfg = config or ModelRunnerConfig()
    provider = str(getattr(cfg, "provider", "mock")).lower()
    runner_config = (
        cfg
        if isinstance(cfg, ModelRunnerConfig)
        else ModelRunnerConfig(
            provider=provider,
            model_name=str(getattr(cfg, "model_name", provider)),
            api_key_env=str(getattr(cfg, "api_key_env", "")),
            temperature=float(getattr(cfg, "temperature", 0.1)),
            max_tokens=int(getattr(cfg, "max_tokens", 512)),
            local_url=str(getattr(cfg, "local_url", ModelRunnerConfig.local_url)),
        )
    )
    if provider == "claude":
        return ClaudeRunner(runner_config)
    if provider == "openai":
        return OpenAIRunner(runner_config)
    if provider == "local":
        return LocalRunner(runner_config)
    if provider == "lmstudio":
        return LMStudioRunner(runner_config)
    return MockRunner()


def _api_key(config: ModelRunnerConfig) -> str:
    if not config.api_key_env:
        raise RuntimeError("api_key_env is required for this model provider")
    value = os.environ.get(config.api_key_env)
    if not value:
        raise RuntimeError(f"Missing API key environment variable: {config.api_key_env}")
    return value


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode())


def _post_json(url: str, payload: bytes) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def _extract_float(text: str, pattern: str, default: float) -> float:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else default


def _extract_percent(text: str, pattern: str, default: float) -> float:
    match = re.search(pattern, text)
    return float(match.group(1)) / 100 if match else default


def _prediction(score: float) -> str:
    if score >= 0.25:
        return "Likely upward continuation if volume and spread remain supportive."
    if score <= -0.25:
        return "Likely downside or long-exit pressure on the next bar."
    return "Likely sideways to mildly directional; wait for stronger confirmation."


def _future_prediction(score: float, horizon_bars: int, horizon_label: str) -> dict[str, Any]:
    if score >= 0.08:
        direction = "UP"
    elif score <= -0.08:
        direction = "DOWN"
    else:
        direction = "FLAT"
    return {
        "horizon_bars": horizon_bars,
        "horizon_label": horizon_label,
        "direction": direction,
        "confidence": round(min(1.0, max(0.0, abs(score))), 3),
        "price_target": None,
        "reasoning": f"Mock {horizon_label} horizon follows next-move score {score:+.3f}.",
    }
