from pathlib import Path

from paisa_trader.calibration import ConfidenceCalibrator


def test_empty_calibrator_ece_is_zero():
    calibrator = ConfidenceCalibrator()

    assert calibrator.expected_calibration_error() == 0.0
    assert calibrator.adjusted_threshold(0.65) == 0.65


def test_over_confident_calibrator_raises_threshold():
    calibrator = ConfidenceCalibrator()
    for _ in range(10):
        calibrator.record(0.80, 0)

    assert calibrator.expected_calibration_error() > 0.08
    assert calibrator.adjusted_threshold(0.65) == 0.70


def test_perfect_calibration_ece_is_zero():
    calibrator = ConfidenceCalibrator()
    calibrator.record(1.0, 1)
    calibrator.record(0.0, 0)

    assert calibrator.expected_calibration_error() == 0.0


def test_calibrator_persist_load_round_trip(tmp_path: Path):
    path = tmp_path / "calibration.json"
    calibrator = ConfidenceCalibrator(save_path=path)
    calibrator.record(0.72, 1)
    calibrator.record(0.72, 0)

    loaded = ConfidenceCalibrator.load(path)

    assert loaded.calibration_stats() == calibrator.calibration_stats()


def test_calibrator_bin_boundary_edges():
    calibrator = ConfidenceCalibrator()
    calibrator.record(0.55, 1)
    calibrator.record(0.85, 0)

    stats = calibrator.calibration_stats()

    assert stats[0]["bin"] == "0.55-0.60"
    assert stats[1]["bin"] == "0.85-1.01"
