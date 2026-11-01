from __future__ import annotations

from src.evaluation.calibration import (
    agreement_rate,
    calibration_report,
    cohens_kappa,
)


def test_agreement_and_kappa() -> None:
    pairs = [
        ("pass", "pass"),
        ("revise", "revise"),
        ("pass", "revise"),
        ("reject", "reject"),
        ("pass", "pass"),
    ]

    assert agreement_rate(pairs) == 0.8
    assert cohens_kappa(pairs) == 0.6875
    report = calibration_report(pairs)
    assert report["n"] == 5
    assert report["agreement"] == 0.8


def test_calibration_handles_empty_and_single_label() -> None:
    assert agreement_rate([]) is None
    assert cohens_kappa([]) is None
    assert cohens_kappa([("pass", "pass")]) is None
