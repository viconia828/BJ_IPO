from __future__ import annotations

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import valuation_engine


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _close(actual: object, expected: float, message: str, failures: list[str], tolerance: float = 1e-8) -> None:
    try:
        value = float(actual)
    except (TypeError, ValueError):
        failures.append(f"{message}: actual={actual!r}")
        return
    if abs(value - expected) > tolerance:
        failures.append(f"{message}: actual={value}, expected={expected}")


def _base_params() -> dict[str, object]:
    return {
        "comparable_pe_stat": "median",
        "bse_discount_factor": 1.0,
        "method1_pe_float_factors_enabled": True,
        "method1_industry_fallback_enabled": True,
        "pe_low_threshold": 0.30,
        "pe_high_threshold": 0.70,
        "pe_discount_boost": 0.20,
        "pe_premium_drag": -0.20,
        "float_size_threshold": 2000,
        "small_cap_premium": 0.20,
        "method1_industry_fallback_confidence": 0.50,
        "weight_comparable": 0.50,
        "weight_industry_momentum": 0.50,
        "price_range_width": 0.10,
        "recent_days": 60,
        "sentiment_first_day_baseline_pct": 0.0,
        "sentiment_first_day_scale": 1.0,
        "sentiment_post_listing_scale": 0.0,
        "sentiment_premium_floor_pct": -1000.0,
        "sentiment_premium_cap_pct": 1000.0,
    }


def method1_factor_case(failures: list[str]) -> None:
    result = valuation_engine.method1_comparable(
        issue_price=10.0,
        issue_pe=10.0,
        comparable_data=[{"pe_ttm": 20.0}],
        params=_base_params(),
        industry_pe=20.0,
        float_shares=1000.0,
    )
    _assert(result.get("anchor_source") == "prospectus_comparables", "method1 should prefer direct comparables", failures)
    _close(result.get("pe_factor"), 1.0, "midpoint PE ratio should interpolate to neutral", failures)
    _close(result.get("float_factor"), 1.1, "small float should receive continuous premium", failures)
    _close(result.get("target_price"), 22.0, "method1 should apply PE and float factors", failures)
    disabled_params = _base_params()
    disabled_params["method1_pe_float_factors_enabled"] = "False"
    disabled = valuation_engine.method1_comparable(
        issue_price=10.0,
        issue_pe=10.0,
        comparable_data=[{"pe_ttm": 20.0}],
        params=disabled_params,
        industry_pe=20.0,
        float_shares=1000.0,
    )
    _close(disabled.get("pe_factor"), 1.0, "string False should disable PE factor", failures)
    _close(disabled.get("float_factor"), 1.0, "string False should disable float factor", failures)


def method1_fallback_confidence_case(failures: list[str]) -> None:
    params = _base_params()
    fallback = valuation_engine.method1_comparable(
        issue_price=10.0,
        issue_pe=10.0,
        comparable_data=[],
        params=params,
        industry_pe=20.0,
        float_shares=2000.0,
    )
    _assert(fallback.get("available"), "industry PE should keep method1 available", failures)
    _assert(fallback.get("anchor_source") == "industry_pe_fallback", "fallback source should be explicit", failures)
    _close(fallback.get("confidence_multiplier"), 0.5, "fallback confidence mismatch", failures)
    method2 = {"available": True, "target_price": 10.0}
    composite = valuation_engine.composite_valuation(fallback, method2, params)
    _close(composite.get("weight_method1"), 1 / 3, "fallback confidence should reduce method1 composite weight", failures)
    _close(composite.get("weight_method2"), 2 / 3, "method2 should receive the remaining normalized weight", failures)
    fallback_only = valuation_engine.composite_valuation(fallback, None, params)
    _assert(not fallback_only.get("available"), "industry PE fallback must not support a final valuation by itself", failures)


def method2_independent_weight_case(failures: list[str]) -> None:
    params = _base_params()
    params.update(
        {
            "method2_weight_mode": "static",
            "sample_weight_mode": "time_decay",
            "sample_decay_half_life_days": 1,
            "robust_median_min_samples": 4,
        }
    )
    samples = [
        {"SECURITY_CODE": "A", "LISTING_DATE": "2026-01-01", "LD_AVERAGE_CHANGE": 100.0, "industry_secondary": "AI硬件"},
        {"SECURITY_CODE": "B", "LISTING_DATE": "2026-01-10", "LD_AVERAGE_CHANGE": 300.0, "industry_secondary": "AI硬件"},
    ]
    result = valuation_engine.method2_industry_momentum(
        issue_price=10.0,
        issue_pe=10.0,
        industry_pe=20.0,
        float_shares=1000.0,
        industry={"primary": "电子", "secondary": "AI硬件"},
        recent_ipos=samples,
        params=params,
        target_code="T",
        target_listing_date="2026-01-11",
    )
    _close(result.get("base_chg"), 200.0, "method2 static industry median should ignore legacy short decay", failures)
    _assert("时间衰减" not in str(result.get("base_stat_label")), "method2 label should expose static statistic", failures)


def method3_short_decay_case(failures: list[str]) -> None:
    samples = [
        {"SECURITY_CODE": "A", "LISTING_DATE": "2026-01-01", "LD_AVERAGE_CHANGE": 0.0},
        {"SECURITY_CODE": "B", "LISTING_DATE": "2026-01-10", "LD_AVERAGE_CHANGE": 100.0},
    ]
    short_params = _base_params()
    short_params["sentiment_decay_half_life_days"] = 2
    long_params = _base_params()
    long_params["sentiment_decay_half_life_days"] = 20
    short = valuation_engine.method3_recent_sentiment(10.0, samples, short_params, "T", "2026-01-11")
    long = valuation_engine.method3_recent_sentiment(10.0, samples, long_params, "T", "2026-01-11")
    _assert(
        float(short.get("first_day_factor_pct") or 0) > float(long.get("first_day_factor_pct") or 0),
        "short method3 half-life should react more strongly to the latest sample",
        failures,
    )


def main() -> int:
    failures: list[str] = []
    method1_factor_case(failures)
    method1_fallback_confidence_case(failures)
    method2_independent_weight_case(failures)
    method3_short_decay_case(failures)
    if failures:
        print("Valuation method role validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Valuation method role validation passed: 4 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
