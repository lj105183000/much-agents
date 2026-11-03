"""Private evaluation dataset tooling for deterministic L0/L1 regression."""

from .calibration import agreement_rate, calibration_report, cohens_kappa
from .dataset import (
    build_all,
    build_case,
    list_cases,
    mark_reviewed,
    seed_must_cover_points,
    validate_case,
)
from .judge import EvalJudgeError, judge_section
from .rule_baseline import (
    RuleBaselineError,
    build_baseline,
    build_scorecard,
    compare_scorecard,
    load_reports,
    score_report,
)

__all__ = [
    "agreement_rate",
    "build_all",
    "build_baseline",
    "build_case",
    "build_scorecard",
    "calibration_report",
    "cohens_kappa",
    "compare_scorecard",
    "EvalJudgeError",
    "judge_section",
    "list_cases",
    "load_reports",
    "mark_reviewed",
    "RuleBaselineError",
    "score_report",
    "seed_must_cover_points",
    "validate_case",
]
