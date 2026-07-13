from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import revalidate_dynamic_width_acceptance as dynamic_width


def _metrics(hit_count: int, loss: float, width: float, efficiency: float, p90: float | None = None) -> dict[str, float | int]:
    return {
        "evaluated_count": 4,
        "hit_count": hit_count,
        "hit_rate": hit_count / 4,
        "avg_half_width": width,
        "average_normalized_interval_loss": loss,
        "p90_normalized_interval_loss": loss if p90 is None else p90,
        "coverage_per_full_width": efficiency,
    }


def main() -> int:
    inside = dynamic_width.normalized_interval_loss(
        {"target_price": 100.0, "actual_price": 105.0, "dynamic_width": 0.10},
        miss_penalty=4.0,
    )
    assert abs(float(inside) - 0.20) < 1e-12, "inside interval loss should equal full width"
    outside = dynamic_width.normalized_interval_loss(
        {"target_price": 100.0, "actual_price": 130.0, "dynamic_width": 0.10},
        miss_penalty=4.0,
    )
    assert abs(float(outside) - 1.00) < 1e-12, "outside interval loss should add miss distance penalty"

    baseline = _metrics(1, 1.0, 0.10, 0.60)
    candidate = _metrics(2, 0.90, 0.14, 0.70)
    folds = [
        {"fold": index, "baseline": _metrics(1, 1.0, 0.10, 0.60), "candidate": _metrics(2, 0.90, 0.14, 0.70)}
        for index in range(1, 4)
    ]
    accepted = dynamic_width.build_acceptance(
        baseline,
        candidate,
        folds,
        baseline,
        candidate,
        max_avg_half_width=0.15,
    )
    assert accepted["passed"] is True

    too_wide = dict(candidate)
    too_wide["avg_half_width"] = 0.18
    rejected = dynamic_width.build_acceptance(
        baseline,
        too_wide,
        folds,
        baseline,
        too_wide,
        max_avg_half_width=0.15,
    )
    assert rejected["passed"] is False
    assert rejected["checks"]["full_average_half_width_within_cap"] is False
    assert rejected["checks"]["combined_average_half_width_within_cap"] is False

    inefficient = dict(candidate)
    inefficient["coverage_per_full_width"] = 0.50
    rejected = dynamic_width.build_acceptance(
        baseline,
        candidate,
        folds,
        baseline,
        inefficient,
        max_avg_half_width=0.15,
    )
    assert rejected["passed"] is False
    assert rejected["checks"]["combined_coverage_efficiency_not_lower"] is False

    rows = [
        {"code": "A", "listing_date": "2026-01-01", "proxy_score": 1.0},
        {"code": "B", "listing_date": "2026-01-02", "proxy_score": 2.0},
        {"code": "C", "listing_date": "2026-01-02", "proxy_score": 3.0},
    ]
    dynamic_width._attach_walk_forward_proxy_tiers(rows)
    assert rows[0]["proxy_tier"] == "mid", "cold-start singleton should remain neutral"
    assert rows[1]["proxy_tier"] == "mid" and rows[2]["proxy_tier"] == "high", "same-day ranks should share one pre-result reference"

    print("Dynamic-width acceptance validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
