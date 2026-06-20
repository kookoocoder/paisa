"""
calibration.py - confidence calibration for AI predictions.

Tracks predicted confidence vs actual outcome and computes calibration error.
Optionally adjusts the decision threshold dynamically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import statistics
from typing import Optional


@dataclass
class CalibrationBin:
    """Tracks outcomes within a confidence bucket, for example 0.60-0.65."""

    low: float
    high: float
    predictions: list[float] = field(default_factory=list)
    outcomes: list[int] = field(default_factory=list)

    @property
    def mean_confidence(self) -> float:
        return statistics.mean(self.predictions) if self.predictions else 0.0

    @property
    def actual_hit_rate(self) -> float:
        return sum(self.outcomes) / len(self.outcomes) if self.outcomes else 0.0

    @property
    def calibration_error(self) -> float:
        """Positive means over-confident, negative means under-confident."""
        return self.mean_confidence - self.actual_hit_rate

    @property
    def n(self) -> int:
        return len(self.predictions)


class ConfidenceCalibrator:
    """
    Bin predictions by confidence and track actual hit rate per bin.

    Args:
        save_path: Optional JSON file used for persistence.

    Returns:
        A calibrator instance that can record predictions and adjust thresholds.

    Example:
        ``ConfidenceCalibrator().record(confidence=0.72, outcome=1)`` stores a
        settled HIT for calibration statistics.
    """

    BIN_EDGES = [0.0, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 1.01]

    def __init__(self, save_path: Optional[Path] = None):
        self.bins: list[CalibrationBin] = [
            CalibrationBin(low=self.BIN_EDGES[i], high=self.BIN_EDGES[i + 1])
            for i in range(len(self.BIN_EDGES) - 1)
        ]
        self.save_path = save_path

    def record(self, confidence: float, outcome: int) -> None:
        """Record a settled prediction. Outcome uses 1=hit, 0=miss."""
        bounded_confidence = max(0.0, min(1.0, float(confidence)))
        bounded_outcome = 1 if int(outcome) else 0
        for calibration_bin in self.bins:
            if calibration_bin.low <= bounded_confidence < calibration_bin.high:
                calibration_bin.predictions.append(bounded_confidence)
                calibration_bin.outcomes.append(bounded_outcome)
                break
        if self.save_path:
            self._persist()

    def calibration_stats(self) -> list[dict]:
        """Return per-bin calibration summary."""
        return [
            {
                "bin": f"{calibration_bin.low:.2f}-{calibration_bin.high:.2f}",
                "n": calibration_bin.n,
                "mean_confidence": round(calibration_bin.mean_confidence, 4),
                "actual_hit_rate": round(calibration_bin.actual_hit_rate, 4),
                "calibration_error": round(calibration_bin.calibration_error, 4),
            }
            for calibration_bin in self.bins
            if calibration_bin.n > 0
        ]

    def expected_calibration_error(self) -> float:
        """
        Weighted average absolute calibration error.

        Args:
            None.

        Returns:
            ECE value where 0.0 is perfectly calibrated.

        Example:
            ``ConfidenceCalibrator().expected_calibration_error()`` returns 0.0
            before any predictions are recorded.
        """
        total = sum(calibration_bin.n for calibration_bin in self.bins)
        if total == 0:
            return 0.0
        return sum(
            (calibration_bin.n / total) * abs(calibration_bin.calibration_error)
            for calibration_bin in self.bins
            if calibration_bin.n > 0
        )

    def adjusted_threshold(self, base_threshold: float = 0.65) -> float:
        """Raise the minimum confidence threshold when calibration error is high."""
        ece = self.expected_calibration_error()
        if ece > 0.08:
            return round(min(base_threshold + 0.05, 0.85), 4)
        if ece > 0.05:
            return round(min(base_threshold + 0.03, 0.85), 4)
        return round(base_threshold, 4)

    def _persist(self) -> None:
        data = {
            "bins": [
                {
                    "low": calibration_bin.low,
                    "high": calibration_bin.high,
                    "predictions": calibration_bin.predictions,
                    "outcomes": calibration_bin.outcomes,
                }
                for calibration_bin in self.bins
            ]
        }
        if self.save_path is None:
            return
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.save_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ConfidenceCalibrator":
        """Load persisted calibration bins from disk if the file exists."""
        calibrator = cls(save_path=path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for saved, calibration_bin in zip(data["bins"], calibrator.bins):
                calibration_bin.predictions = list(saved["predictions"])
                calibration_bin.outcomes = list(saved["outcomes"])
        return calibrator
