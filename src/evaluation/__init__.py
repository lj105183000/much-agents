"""Private evaluation dataset tooling for deterministic L0/L1 regression."""

from .calibration import agreement_rate, calibration_report, cohens_kappa
from .dataset import (
    build_all,
    build_case,
    list_cases,
    seed_must_cover_points,
    validate_case,
)
from .judge import EvalJudgeError, judge_section

__all__ = [
    "agreement_rate",
    "build_all",
    "build_case",
    "calibration_report",
    "cohens_kappa",
    "EvalJudgeError",
    "judge_section",
    "list_cases",
    "seed_must_cover_points",
    "validate_case",
]
