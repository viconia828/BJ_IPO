from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "tools" / "evaluate_intraday_valuation_guidance.py"
    spec = importlib.util.spec_from_file_location("intraday_regret_guidance_validation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guidance = _load_module()


def _snapshot(node: str, *, price: float, high: float, vwap: float, turnover: float, drawdown: float, high_time: str = "09:40") -> dict:
    return {
        "node": node,
        "price": price,
        "high": high,
        "vwap": vwap,
        "cumulative_turnover": turnover,
        "max_drawdown_pct": drawdown,
        "high_time": high_time,
        "low": min(price, vwap),
    }


def _intraday(snapshots: dict[str, dict], *, close: float = 22.0, morning_high: float = 20.0) -> dict:
    return {
        "open": 14.0,
        "close": close,
        "morning_high": morning_high,
        "snapshots": snapshots,
    }


def main() -> int:
    failures: list[str] = []
    strict = {"range_low": 10.0, "range_high": 20.0}
    rolling = {"range_low": 10.0, "range_high": 20.0}
    early_snapshots = {
        "09:35": _snapshot("09:35", price=14.5, high=14.8, vwap=14.0, turnover=30.0, drawdown=-3.0, high_time="09:35"),
        "09:45": _snapshot("09:45", price=15.5, high=16.0, vwap=14.5, turnover=40.0, drawdown=-3.0, high_time="09:45"),
        "10:00": _snapshot("10:00", price=17.0, high=18.0, vwap=15.0, turnover=50.0, drawdown=-4.0, high_time="10:00"),
        "10:30": _snapshot("10:30", price=18.0, high=19.0, vwap=16.0, turnover=65.0, drawdown=-4.0, high_time="10:30"),
        "13:30": _snapshot("13:30", price=20.0, high=21.0, vwap=18.0, turnover=75.0, drawdown=-5.0, high_time="13:30"),
        "14:00": _snapshot("14:00", price=21.0, high=22.0, vwap=18.5, turnover=80.0, drawdown=-5.0, high_time="14:00"),
        "14:30": _snapshot("14:30", price=22.0, high=23.0, vwap=19.0, turnover=85.0, drawdown=-5.0, high_time="14:30"),
    }
    early = guidance._regret_aware_exit(
        _intraday(early_snapshots),
        strict,
        rolling,
        {"price": 15.5, "nodes": "09:45(100%)"},
    )
    if early.get("trigger") != "early_exit_runner":
        failures.append("strong early center reach should retain a runner")
    if "30%" not in str(early.get("nodes")):
        failures.append("early-exit protection should retain 30% position")

    wait_strict = {"range_low": 30.0, "range_high": 40.0}
    wait_rolling = {"range_low": 32.0, "range_high": 42.0}
    wait_snapshots = dict(early_snapshots)
    wait_snapshots["09:35"] = _snapshot("09:35", price=18.0, high=20.0, vwap=19.0, turnover=20.0, drawdown=-5.0, high_time="09:34")
    wait = guidance._regret_aware_exit(
        _intraday(wait_snapshots, close=16.0),
        wait_strict,
        wait_rolling,
        {"price": 17.0, "nodes": "10:00(100%)"},
    )
    if wait.get("trigger") != "wait_regret_guard":
        failures.append("unreached high expectation plus weak tape should shorten waiting")
    if not str(wait.get("nodes") or "").startswith("09:35(70%)"):
        failures.append("wait-regret guard should sell 70% at the trigger node")

    if failures:
        print("\n".join(f"FAIL: {failure}" for failure in failures))
        return 1
    print("Intraday regret guidance validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
