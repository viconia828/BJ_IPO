from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "tools" / "evaluate_local_proxy_strategy.py"
    spec = importlib.util.spec_from_file_location("local_proxy_single_method_validation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy = _load_module()


def main() -> int:
    failures: list[str] = []
    row = {
        "current_method_count": 1,
        "rolling_proxy_expected_change_pct": 100.0,
        "rolling_proxy_expected_source": "fixture",
        "proxy_tier": "mid",
        "proxy_score": 2.0,
        "model_uncertainty_score": 1.0,
    }
    strategy = {"center_condition": "single_method", "center_alpha": 0.5, "width_policy": "balanced", "model": "current_params"}
    predicted, source = proxy._center_override_change(row, strategy, 0.0)
    if predicted != 50.0 or source != "fixture":
        failures.append("single-method anchor should blend 50% rolling local center")
    two_method = dict(row)
    two_method["current_method_count"] = 2
    predicted, _source = proxy._center_override_change(two_method, strategy, 0.0)
    if predicted is not None:
        failures.append("single-method policy should not alter a two-method valuation")
    width, reasons = proxy._dynamic_width(row, strategy, False)
    if width < 0.20 or "single_method_anchor" not in reasons:
        failures.append("single-method anchor should receive uncertainty width protection")
    if failures:
        print("\n".join(f"FAIL: {failure}" for failure in failures))
        return 1
    print("Local proxy single-method validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
