from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_SCAN_REPORT = ROOT_DIR / "调参" / "valuation_hit_rate_scan_202603plus_20260710_001437.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guidance = _load_module("evaluate_intraday_valuation_guidance", ROOT_DIR / "tools" / "evaluate_intraday_valuation_guidance.py")
proxy = guidance.proxy
distill = guidance.distill
param_tuning = guidance.param_tuning


FALLBACK_VARIANTS = (
    "recent_mood",
    "recent_mood_regime_blend50",
    "recent_mood_regime_blend75",
    "recent_mood_regime_cap",
)


def _safe_float(value: Any) -> float | None:
    return guidance._safe_float(value)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "" if number is None else f"{number:.{digits}f}"


def _fmt_pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    return "" if number is None else f"{number:.{digits}f}%"


def _strategy(base: dict[str, Any], fallback_policy: str) -> dict[str, Any]:
    result = dict(base)
    result["fallback_policy"] = fallback_policy
    result["name"] = str(base["name"]).replace("recent_mood", fallback_policy)
    return result


def _load_intraday(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    by_code = distill._dataset_by_code(dataset)
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(args.intraday_dir).glob("*.csv")):
        turnover = _safe_float((by_code.get(path.stem) or {}).get("TURNOVERRATE"))
        intraday = guidance._read_intraday(path, turnover)
        if intraday:
            result[path.stem] = intraday
    return result


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    affected = [row for row in result["rows"] if row.get("regime_break_triggered") and row.get("fallback_used")]
    affected_errors = [
        abs(float(row["predicted_change_pct"]) - float(row["actual_change_pct"]))
        for row in affected
        if _safe_float(row.get("predicted_change_pct")) is not None and _safe_float(row.get("actual_change_pct")) is not None
    ]
    affected_hits = [row for row in affected if row.get("interval_hit") is not None]
    return {
        "strategy": result["strategy"],
        "target_count": result["target_count"],
        "hit_count": result["hit_count"],
        "hit_rate": result["full_hit_rate"],
        "mae_change_pct": result["mae_change_pct"],
        "spearman": result["spearman_predicted_vs_actual_change"],
        "fallback_count": result["fallback_count"],
        "fallback_hit_count": result["fallback_hit_count"],
        "affected_count": len(affected),
        "affected_codes": [row["code"] for row in affected],
        "affected_hit_count": sum(bool(row.get("interval_hit")) for row in affected_hits),
        "affected_mae_change_pct": _mean(affected_errors),
        "rows": result["rows"],
    }


def _latest_comparison(results: list[dict[str, Any]], code: str, opening: float | None) -> dict[str, Any]:
    rows = []
    for result in results:
        row = next((item for item in result["rows"] if item["code"] == code), None)
        if not row:
            continue
        position = guidance._open_position(opening, row.get("range_low"), row.get("range_high")) if opening is not None else {}
        rows.append(
            {
                "strategy": result["strategy"]["name"],
                "fallback_policy": result["strategy"]["fallback_policy"],
                "source": row.get("source"),
                "predicted_change_pct": row.get("predicted_change_pct"),
                "target_price": row.get("target_price"),
                "range_low": row.get("range_low"),
                "range_high": row.get("range_high"),
                "actual_change_pct": row.get("actual_change_pct"),
                "actual_price": row.get("actual_price"),
                "interval_hit": row.get("interval_hit"),
                "opening": opening,
                "opening_position": position,
                "previous_code": row.get("previous_regime_code"),
                "previous_expected_change_pct": row.get("previous_regime_expected_change_pct"),
                "previous_actual_change_pct": row.get("previous_regime_actual_change_pct"),
                "regime_break_triggered": row.get("regime_break_triggered"),
            }
        )
    return {"code": code, "rows": rows}


def _build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Regime-break 情绪兜底降权评估",
        "",
        f"> 生成时间：{payload['generated_at']}",
        f"> 样本：{payload['summary']['sample_count']} 只",
        "",
        "## 全量结果",
        "",
        "| 估值线 | 兜底策略 | 命中 | MAE(涨幅) | Spearman | fallback命中 | 受影响代码 | 受影响MAE |",
        "|---|---|---:|---:|---:|---:|---|---:|",
    ]
    for result in payload["results"]:
        strategy = result["strategy"]
        lines.append(
            "| {line} | `{policy}` | {hit}/{total} | {mae} | {spearman} | {fh}/{fc} | {codes} | {affected_mae} |".format(
                line="滚动线" if strategy.get("center_policy") == "all_rolling50" else "保守线",
                policy=strategy.get("fallback_policy"),
                hit=result["hit_count"],
                total=result["target_count"],
                mae=_fmt_num(result["mae_change_pct"]),
                spearman=_fmt_num(result["spearman"], 3),
                fh=result["fallback_hit_count"],
                fc=result["fallback_count"],
                codes="、".join(result["affected_codes"]) or "无",
                affected_mae=_fmt_num(result["affected_mae_change_pct"]),
            )
        )

    latest = payload["latest_case"]
    lines.extend(
        [
            "",
            f"## 最新案例：{latest['code']}",
            "",
            "| 估值线 | 兜底策略 | 预测涨幅 | 目标价 | 区间 | 开盘位置 | 实际均价 | 命中 |",
            "|---|---|---:|---:|---:|---|---:|---|",
        ]
    )
    for row in latest["rows"]:
        lines.append(
            "| {line} | `{policy}` | {change} | {target} | {low}-{high} | {position} | {actual} | {hit} |".format(
                line="滚动线" if "all_rolling50" in row["strategy"] else "保守线",
                policy=row["fallback_policy"],
                change=_fmt_pct(row["predicted_change_pct"]),
                target=_fmt_num(row["target_price"]),
                low=_fmt_num(row["range_low"]),
                high=_fmt_num(row["range_high"]),
                position=(row.get("opening_position") or {}).get("state") or "",
                actual=_fmt_num(row["actual_price"]),
                hit="是" if row.get("interval_hit") else "否",
            )
        )

    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- Regime-break 只修改模型不可用时的本地情绪兜底；模型可用样本保持原中枢。",
            "- 一次破预期后的全面重置会错杀后续反弹，是否升级默认必须同时看全量命中和受影响样本 MAE。",
            "- 920081 单例改善只能作为机制检查，不能反向决定全量阈值和降权强度。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    intraday = _load_intraday(args)
    teacher_rows, _strict_rows, _rolling_rows = guidance._prediction_context(args, intraday)
    params = guidance.config_loader.load_params(args.params)
    strategies = [
        *(_strategy(guidance.STRICT_STRATEGY, policy) for policy in FALLBACK_VARIANTS),
        *(_strategy(guidance.ROLLING_STRATEGY, policy) for policy in FALLBACK_VARIANTS),
    ]
    raw_results = [proxy._evaluate_strategy(strategy, teacher_rows, params) for strategy in strategies]
    results = [_compact_result(result) for result in raw_results]
    latest_code = max(intraday.values(), key=lambda row: (row["listing_date"], row["code"]))["code"]
    latest_case = _latest_comparison(raw_results, latest_code, _safe_float(intraday[latest_code].get("open")))
    payload = {
        "schema": "regime_break_fallback_evaluation_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "scan_report": str(Path(args.scan_report)),
            "intraday_dir": str(Path(args.intraday_dir)),
        },
        "thresholds": {"previous_expected_min_pct": 80, "actual_to_expected_max": 0.60, "gap_min_pct": 50},
        "summary": {"sample_count": len(teacher_rows), "latest_code": latest_code},
        "results": [{key: value for key, value in result.items() if key != "rows"} for result in results],
        "latest_case": latest_case,
        "teacher_rows": teacher_rows,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"regime_break_fallback_{timestamp}.json"
    md_path = output_dir / f"regime_break_fallback_{timestamp}.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate regime-break discounts for local sentiment fallback.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--scan-report", default=str(DEFAULT_SCAN_REPORT))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    print(json.dumps({"outputs": payload["outputs"], "summary": payload["summary"], "latest_case": payload["latest_case"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
