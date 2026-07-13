from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import evaluate_method1_anchor_reliability as reliability


def _metrics(*, hit_count: int = 2, mae: float = 100.0, p90: float = 200.0) -> dict[str, float | int]:
    return {
        "hit_count": hit_count,
        "mae_change_pct": mae,
        "p90_change_abs_error_pct": p90,
        "available_rate": 1.0,
    }


def main() -> int:
    baseline = _metrics()
    candidate = _metrics(mae=99.0, p90=199.0)
    folds = [
        {"fold": 1, "baseline": baseline, "candidate": candidate},
        {"fold": 2, "baseline": baseline, "candidate": candidate},
        {"fold": 3, "baseline": baseline, "candidate": candidate},
    ]
    accepted = reliability._acceptance(baseline, candidate, folds, baseline, candidate)
    assert accepted["passed"] is True

    unstable_folds = list(folds)
    unstable_folds[0] = {"fold": 1, "baseline": baseline, "candidate": _metrics(mae=111.0, p90=199.0)}
    rejected = reliability._acceptance(baseline, candidate, unstable_folds, baseline, candidate)
    assert rejected["passed"] is False
    assert rejected["catastrophic_folds"] == [1]

    distribution = reliability._anchor_distribution(
        [
            {
                "anchor_source": "prospectus_comparables",
                "sample_count": 2,
                "max_min_ratio": 4.0,
                "robust_dispersion_ratio": 1.5,
            },
            {"anchor_source": "industry_pe_fallback", "sample_count": 0},
        ]
    )
    assert distribution["direct_with_two_or_fewer"] == 1
    assert distribution["direct_max_min_ratio_ge_3"] == 1
    assert distribution["industry_fallback_rows"] == 1

    print("Method-1 anchor reliability validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
