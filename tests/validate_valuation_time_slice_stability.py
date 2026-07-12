from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import revalidate_valuation_time_slices as recheck


def _item(index: int, listing_date: str) -> dict[str, object]:
    return {
        "SECURITY_CODE": f"{index:06d}",
        "LISTING_DATE": f"{listing_date} 00:00:00",
    }


def _metrics(code: str, predicted: float, actual: float, low: float, high: float) -> dict[str, object]:
    return recheck.aggregate_fold_metrics(
        [
            {
                "available_results": [
                    {
                        "code": code,
                        "predicted_change_pct": predicted,
                        "actual_interval_change_pct": actual,
                        "change_abs_error": abs(predicted - actual),
                        "predicted_target_price": predicted + 100.0,
                        "actual_interval_price": actual + 100.0,
                        "range_low": low + 100.0,
                        "range_high": high + 100.0,
                    }
                ],
                "unavailable_results": [],
            }
        ]
    )


def main() -> int:
    dataset = {
        "items": [
            _item(1, "2026-01-01"),
            _item(2, "2026-01-02"),
            _item(3, "2026-01-03"),
            _item(4, "2026-01-04"),
            _item(5, "2026-01-04"),
            _item(6, "2026-01-05"),
            _item(7, "2026-01-06"),
            _item(8, "2026-01-07"),
            _item(9, "2026-01-08"),
            _item(10, "2026-01-09"),
            _item(11, "2026-01-10"),
            _item(12, "2026-01-11"),
        ]
    }
    folds = recheck.build_anchored_folds(dataset, initial_train_size=4, fold_size=2, fold_count=3)
    assert len(folds) == 3
    assert len(folds[0]["train_codes"]) == 5, "首个边界不得拆开同日上市样本"
    for fold in folds:
        train_dates = {recheck._item_date(item) for item in fold["train_items"]}
        validation_dates = {recheck._item_date(item) for item in fold["validation_items"]}
        assert not (train_dates & validation_dates), "训练集与验证集不得共享同一上市日"

    fold_rows = []
    for index in range(1, 4):
        baseline = _metrics(str(index), 30.0, 10.0, 0.0, 5.0)
        candidate = _metrics(str(index), 15.0, 10.0, 5.0, 15.0)
        fold_rows.append(
            {
                "fold": index,
                "baseline": baseline,
                "core": {"validation_metrics": candidate, "overrides": {"x": 1}},
            }
        )
    acceptance = recheck._path_acceptance(fold_rows, "core")
    assert acceptance["passed"] is True
    assert acceptance["candidate"]["hit_count"] == 3
    assert acceptance["candidate"]["mae_change_pct"] == 5.0
    assert acceptance["paired_robustness"]["trimmed_mae_not_higher"] is True

    fold_rows[2]["core"]["validation_metrics"] = _metrics("3", 50.0, 10.0, 0.0, 5.0)
    failed = recheck._path_acceptance(fold_rows, "core")
    assert failed["passed"] is False
    assert failed["checks"]["no_catastrophic_fold"] is False

    stability = recheck._parameter_stability(
        {"x": 10},
        {"x": 9},
        [
            {"core": {"overrides": {"x": 11}}},
            {"core": {"overrides": {"x": 12}}},
            {"core": {"overrides": {}}},
        ],
        "core",
    )
    assert stability["warning"] is True
    assert stability["direction_conflicts"] == ["x"], "应识别全样本候选与折内候选方向相反"

    print("Valuation time-slice stability validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
