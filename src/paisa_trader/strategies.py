from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from .indicators import sma, zscore


class Strategy(Protocol):
    name: str

    def signals(self, candles: pd.DataFrame) -> pd.DataFrame:
        """Return candles with target_position in [0, 1]."""


@dataclass(frozen=True)
class BuyHoldStrategy:
    name: str = "buy-hold"

    def signals(self, candles: pd.DataFrame) -> pd.DataFrame:
        out = candles.copy()
        out["target_position"] = 1.0
        return out


@dataclass(frozen=True)
class MovingAverageCrossStrategy:
    fast: int = 10
    slow: int = 30
    name: str = "ma-cross"

    def signals(self, candles: pd.DataFrame) -> pd.DataFrame:
        if self.fast >= self.slow:
            raise ValueError("fast window must be smaller than slow window")
        out = candles.copy()
        out["fast_ma"] = sma(out["close"], self.fast)
        out["slow_ma"] = sma(out["close"], self.slow)
        out["target_position"] = (out["fast_ma"] > out["slow_ma"]).astype(float)
        out.loc[out["slow_ma"].isna(), "target_position"] = 0.0
        return out


@dataclass(frozen=True)
class MeanReversionStrategy:
    window: int = 20
    entry_z: float = -1.0
    exit_z: float = 0.0
    name: str = "mean-reversion"

    def signals(self, candles: pd.DataFrame) -> pd.DataFrame:
        out = candles.copy()
        out["zscore"] = zscore(out["close"], self.window)
        target = []
        position = 0.0
        for z in out["zscore"]:
            if pd.isna(z):
                position = 0.0
            elif position == 0.0 and z <= self.entry_z:
                position = 1.0
            elif position == 1.0 and z >= self.exit_z:
                position = 0.0
            target.append(position)
        out["target_position"] = target
        return out


def build_strategy(name: str) -> Strategy:
    if name == "buy-hold":
        return BuyHoldStrategy()
    if name == "ma-cross":
        return MovingAverageCrossStrategy()
    if name == "mean-reversion":
        return MeanReversionStrategy()
    raise ValueError(f"Unknown strategy: {name}")
