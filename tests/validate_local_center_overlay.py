from __future__ import annotations

import json
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "code"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

import config_loader
import param_tuning
import valuation_engine


def _history() -> list[dict[str, object]]:
    rows = []
    for index in range(12):
        rows.append(
            {
                "SECURITY_CODE": f"920{index:03d}",
                "LISTING_DATE": f"2026-01-{index + 1:02d}",
                "ISSUE_PRICE": 10.0 + index,
                "AFTER_ISSUE_PE": 12.0 + index,
                "INDUSTRY_PE_NEW": 30.0,
                "TOTAL_ISSUE_NUM": 1000.0,
                "ONLINE_ISSUE_NUM": 800.0 + index * 20,
                "TOP_APPLY_MARKETCAP": 500.0 + index * 10,
                "old_shares": 0.0,
                "float_shares": 1000.0,
                "industry_primary": "高端装备",
                "industry_secondary": "机械设备",
                "LD_AVERAGE_CHANGE": 80.0 + index * 15,
            }
        )
    return rows


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "price_range_width": 0.10,
        "local_center_overlay_enabled": True,
        "local_center_alpha": 0.50,
        "local_center_min_history": 8,
        "local_center_history_window": 20,
        "local_center_actual_cap_pct": 900.0,
        "local_center_slope_cap": 25.0,
    }
    params.update(overrides)
    return params


def _apply(history: list[dict[str, object]], params: dict[str, object]) -> dict[str, object]:
    return valuation_engine.apply_local_center_overlay(
        {"available": True, "target_price": 30.0, "range_low": 27.0, "range_high": 33.0},
        issue_price=10.0,
        issue_pe=15.0,
        industry_pe=30.0,
        float_shares=1000.0,
        old_shares=0.0,
        industry={"primary": "高端装备", "secondary": "机械设备"},
        recent_ipos=history,
        params=params,
        target_code="920999",
        target_listing_date="2026-02-01",
        online_issue_num=900.0,
        top_apply_marketcap=600.0,
    )


def main() -> None:
    failures: list[str] = []
    history = _history()
    result = _apply(history, _params())
    if result.get("local_center_overlay_applied") is not True:
        failures.append("overlay should apply after minimum history is available")
    if int(result.get("local_center_history_count") or 0) != 12:
        failures.append("overlay should report the completed history count")
    target = float(result.get("target_price") or 0.0)
    if not math.isclose(float(result.get("range_low") or 0.0), target * 0.9, rel_tol=1e-9):
        failures.append("range low should be rebuilt from the blended center")
    if not math.isclose(float(result.get("range_high") or 0.0), target * 1.1, rel_tol=1e-9):
        failures.append("range high should be rebuilt from the blended center")

    future = {
        **history[-1],
        "SECURITY_CODE": "920998",
        "LISTING_DATE": "2026-02-02",
        "LD_AVERAGE_CHANGE": 9000.0,
    }
    with_future = _apply([*history, future], _params())
    if not math.isclose(target, float(with_future.get("target_price") or 0.0), rel_tol=1e-12):
        failures.append("future listings must not affect the target overlay")

    disabled = _apply(history, _params(local_center_overlay_enabled=False))
    if disabled.get("target_price") != 30.0 or "local_center_overlay_applied" in disabled:
        failures.append("disabled overlay should return the original final valuation")

    insufficient = _apply(history[:7], _params())
    if insufficient.get("local_center_overlay_applied") is not False or insufficient.get("target_price") != 30.0:
        failures.append("insufficient history should preserve the original center")

    dataset = json.loads((ROOT / "data" / "offline_tuning" / "replay_dataset.json").read_text(encoding="utf-8"))
    formal_params = config_loader.load_params(ROOT / "策略参数.txt")
    replay_params = dict(formal_params)
    replay_params.update(_params())
    replay = param_tuning.evaluate_replay_targets(dataset, replay_params)
    if not math.isclose(float(replay.get("interval_hit_rate") or 0.0), 5 / 40, rel_tol=1e-9):
        failures.append("formal replay should hit 5/40 after industry completion and method2 confidence weighting")
    if not math.isclose(float(replay.get("mae_change_pct") or 0.0), 130.8751441275241, rel_tol=1e-9):
        failures.append("formal replay MAE should match the current industry and method2-confidence contract")

    if failures:
        raise AssertionError("\n".join(failures))
    print("Local-center overlay validation passed")


if __name__ == "__main__":
    main()
