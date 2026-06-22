from __future__ import annotations

import logging
import multiprocessing
import os
from typing import Any


LOGGER = logging.getLogger(__name__)
_FINBERT_PIPELINE: Any | None = None
_FINBERT_FAILED = False


def _finbert_timeout_seconds() -> float:
    return max(1.0, float(os.environ.get("PAISA_FINBERT_TIMEOUT_SECONDS", "8")))


def load_finbert():
    """Load and cache the ProsusAI FinBERT pipeline lazily.

    Args:
        None.

    Returns:
        A HuggingFace text-classification pipeline, or ``None`` when unavailable.

    Example:
        ``load_finbert()("Markets rally")`` scores a headline when FinBERT is installed.
    """
    global _FINBERT_FAILED, _FINBERT_PIPELINE
    if _FINBERT_PIPELINE is not None:
        return _FINBERT_PIPELINE
    if _FINBERT_FAILED:
        return None
    try:
        from transformers import pipeline

        _FINBERT_PIPELINE = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            device=-1,
        )
    except Exception as exc:
        _FINBERT_FAILED = True
        LOGGER.warning("FinBERT unavailable; falling back to neutral/lexical sentiment: %s", exc)
        return None
    return _FINBERT_PIPELINE


def score_headline(text: str) -> dict[str, float | str]:
    """Score one headline with FinBERT.

    Args:
        text: News headline text.

    Returns:
        Dict with textual label, confidence score, and signed numeric value.

    Example:
        ``score_headline("Markets rally")["numeric"]`` returns a bullish score.
    """
    if not text.strip():
        return {"label": "neutral", "score": 0.0, "numeric": 0.0}

    if _FINBERT_FAILED:
        return _lexical_score(text)
    try:
        result = _score_with_timeout(text)
    except TimeoutError:
        _disable_finbert("FinBERT scoring timed out; falling back to lexical sentiment")
        return _lexical_score(text)
    except Exception as exc:
        LOGGER.warning("FinBERT scoring failed; falling back to lexical sentiment: %s", exc)
        return _lexical_score(text)
    label = str(result.get("label", "neutral")).lower()
    score = float(result.get("score", 0.0))
    if "positive" in label:
        numeric = score
        clean_label = "positive"
    elif "negative" in label:
        numeric = -score
        clean_label = "negative"
    else:
        numeric = 0.0
        clean_label = "neutral"
    return {"label": clean_label, "score": score, "numeric": numeric}


def _score_with_timeout(text: str) -> dict[str, Any]:
    start_method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    ctx = multiprocessing.get_context(start_method)
    results = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_finbert_process_score, args=(text, results), daemon=True)
    process.start()
    process.join(_finbert_timeout_seconds())
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        raise TimeoutError("FinBERT scoring timed out")
    if results.empty():
        raise RuntimeError("FinBERT scoring process exited without a result")
    result = results.get()
    if "__error__" in result:
        raise RuntimeError(str(result["__error__"]))
    return result


def _disable_finbert(message: str) -> None:
    global _FINBERT_FAILED, _FINBERT_PIPELINE
    LOGGER.warning(message)
    _FINBERT_FAILED = True
    _FINBERT_PIPELINE = None


def _finbert_process_score(text: str, results: Any) -> None:
    try:
        from transformers import pipeline

        pipe = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            device=-1,
        )
        results.put(pipe(text, batch_size=1, truncation=True)[0])
    except BaseException as exc:
        results.put({"__error__": repr(exc)})


def score_headlines(texts: list[str]) -> dict[str, float | int | str]:
    """Aggregate FinBERT scores across headlines.

    Args:
        texts: Headline strings to score.

    Returns:
        Dict with composite score, counts, and dominant sentiment label.

    Example:
        ``score_headlines(["Markets rally"])["dominant"]`` returns ``"bullish"``.
    """
    if not texts:
        return get_dummy_sentiment()
    scores = [score_headline(text) for text in texts if text.strip()]
    if not scores:
        return get_dummy_sentiment()
    composite = sum(float(item["numeric"]) for item in scores) / len(scores)
    bullish = sum(1 for item in scores if item["label"] == "positive")
    bearish = sum(1 for item in scores if item["label"] == "negative")
    neutral = sum(1 for item in scores if item["label"] == "neutral")
    if composite > 0.10:
        dominant = "bullish"
    elif composite < -0.10:
        dominant = "bearish"
    else:
        dominant = "neutral"
    return {
        "composite": float(max(-1.0, min(1.0, composite))),
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "dominant": dominant,
    }


def get_dummy_sentiment() -> dict[str, float | int | str]:
    """Return neutral sentiment for missing headlines.

    Args:
        None.

    Returns:
        Neutral aggregate sentiment dict.

    Example:
        ``get_dummy_sentiment()["composite"]`` returns ``0.0``.
    """
    return {
        "composite": 0.0,
        "bullish_count": 0,
        "bearish_count": 0,
        "neutral_count": 0,
        "dominant": "neutral",
    }


def _lexical_score(text: str) -> dict[str, float | str]:
    lowered = text.lower()
    bullish_terms = {"rally", "strong", "growth", "profit", "beats", "surge", "gain", "gdp"}
    bearish_terms = {"crash", "recession", "fear", "weak", "loss", "miss", "fall", "slump"}
    bullish = sum(1 for term in bullish_terms if term in lowered)
    bearish = sum(1 for term in bearish_terms if term in lowered)
    if bullish > bearish:
        return {"label": "positive", "score": min(1.0, 0.55 + bullish * 0.1), "numeric": min(1.0, 0.55 + bullish * 0.1)}
    if bearish > bullish:
        score = min(1.0, 0.55 + bearish * 0.1)
        return {"label": "negative", "score": score, "numeric": -score}
    return {"label": "neutral", "score": 0.0, "numeric": 0.0}
