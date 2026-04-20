from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_ipo_valuation
import config_loader
import ipo_data_helper
import report_generator


DEFAULT_CODES = ["920177", "920181", "920180"]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "输出" / "观察期"


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_codes(raw_value: str | None) -> list[str]:
    if not raw_value:
        return list(DEFAULT_CODES)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _format_metric(value: Any) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "-"
    return f"{numeric:.4f}"


def _candidate_tag(
    industry_trend_weight: float,
    market_sentiment_weight: float,
    half_life_days: int,
    strong_threshold: int,
    weak_threshold: int,
) -> str:
    def _fmt(value: float) -> str:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text.replace(".", "p")

    return "itw_{industry}_msw_{sentiment}_h{half_life}_s{strong}_w{weak}".format(
        industry=_fmt(industry_trend_weight),
        sentiment=_fmt(market_sentiment_weight),
        half_life=half_life_days,
        strong=strong_threshold,
        weak=weak_threshold,
    )


def _build_result_row(
    code: str,
    baseline_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
    actual_listing_result: dict[str, Any],
    candidate_report_path: str | None,
    error: str = "",
) -> dict[str, Any]:
    baseline_final = baseline_payload.get("final") or {}
    candidate_final = candidate_payload.get("final") or {}
    baseline_method2 = baseline_payload.get("method2") or {}
    candidate_method2 = candidate_payload.get("method2") or {}
    ipo_info = candidate_payload.get("ipo_info") or baseline_payload.get("ipo_info") or {}

    actual_close_price = _safe_float(actual_listing_result.get("actual_close_price"))
    baseline_target = _safe_float(baseline_final.get("target_price"))
    candidate_target = _safe_float(candidate_final.get("target_price"))

    return {
        "code": code,
        "name": str(ipo_info.get("SECURITY_NAME_ABBR") or "").strip(),
        "actual_close_price": actual_close_price,
        "actual_change_pct": _safe_float(actual_listing_result.get("actual_change_pct")),
        "baseline_target_price": baseline_target,
        "candidate_target_price": candidate_target,
        "target_price_delta": (
            candidate_target - baseline_target
            if baseline_target is not None and candidate_target is not None
            else None
        ),
        "baseline_abs_price_error": (
            abs(baseline_target - actual_close_price)
            if baseline_target is not None and actual_close_price is not None
            else None
        ),
        "candidate_abs_price_error": (
            abs(candidate_target - actual_close_price)
            if candidate_target is not None and actual_close_price is not None
            else None
        ),
        "baseline_change_pct": _safe_float(baseline_payload.get("final_change_pct")),
        "candidate_change_pct": _safe_float(candidate_payload.get("final_change_pct")),
        "baseline_method2_sample_scope": baseline_method2.get("sample_scope"),
        "baseline_method2_sample_count": baseline_method2.get("sample_count"),
        "candidate_method2_sample_scope": candidate_method2.get("sample_scope"),
        "candidate_method2_sample_count": candidate_method2.get("sample_count"),
        "baseline_trend_factor": _safe_float(baseline_method2.get("trend_factor")),
        "candidate_trend_factor": _safe_float(candidate_method2.get("trend_factor")),
        "candidate_report_path": candidate_report_path,
        "error": error,
    }


def _load_actual_listing_result(code: str, params: dict[str, Any]) -> dict[str, Any]:
    bundle = ipo_data_helper.prepare_ipo_data(code, int(params.get("recent_months", 3)), params)
    ipo_info = bundle.get("ipo_info") or {}
    actual_close_price = _safe_float(ipo_info.get("CLOSE_PRICE"))
    actual_change_pct = _safe_float(ipo_info.get("LD_CLOSE_CHANGE"))
    for item in bundle.get("recent_ipos") or []:
        if str(item.get("SECURITY_CODE") or "").strip() != code:
            continue
        actual_close_price = _safe_float(item.get("CLOSE_PRICE")) or actual_close_price
        actual_change_pct = _safe_float(item.get("LD_CLOSE_CHANGE")) or actual_change_pct
        break
    return {
        "actual_close_price": actual_close_price,
        "actual_change_pct": actual_change_pct,
    }


def _average(values: list[float | None]) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def _write_outputs(
    output_dir: Path,
    candidate_name: str,
    baseline_params: dict[str, Any],
    candidate_params: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"observe_{candidate_name}_{timestamp}.json"
    md_path = output_dir / f"observe_{candidate_name}_{timestamp}.md"

    payload = {
        "candidate_name": candidate_name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "baseline_params": {
            "industry_trend_weight": baseline_params.get("industry_trend_weight"),
            "market_sentiment_weight": baseline_params.get("market_sentiment_weight"),
            "sample_decay_half_life_days": baseline_params.get("sample_decay_half_life_days"),
            "trend_strong_threshold": baseline_params.get("trend_strong_threshold"),
            "trend_weak_threshold": baseline_params.get("trend_weak_threshold"),
        },
        "candidate_params": {
            "industry_trend_weight": candidate_params.get("industry_trend_weight"),
            "market_sentiment_weight": candidate_params.get("market_sentiment_weight"),
            "sample_decay_half_life_days": candidate_params.get("sample_decay_half_life_days"),
            "trend_strong_threshold": candidate_params.get("trend_strong_threshold"),
            "trend_weak_threshold": candidate_params.get("trend_weak_threshold"),
        },
        "rows": rows,
        "baseline_avg_abs_price_error": _average(
            [_safe_float(row.get("baseline_abs_price_error")) for row in rows]
        ),
        "candidate_avg_abs_price_error": _average(
            [_safe_float(row.get("candidate_abs_price_error")) for row in rows]
        ),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    table_lines = [
        "| 代码 | 名称 | 实际收盘 | baseline 目标价 | 候选目标价 | baseline 误差 | 候选误差 | baseline 趋势因子 | 候选趋势因子 | 候选报告 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        table_lines.append(
            "| {code} | {name} | {actual_close} | {base_target} | {cand_target} | {base_error} | {cand_error} | {base_trend} | {cand_trend} | {report} |".format(
                code=row.get("code"),
                name=row.get("name") or "-",
                actual_close=_format_metric(row.get("actual_close_price")),
                base_target=_format_metric(row.get("baseline_target_price")),
                cand_target=_format_metric(row.get("candidate_target_price")),
                base_error=_format_metric(row.get("baseline_abs_price_error")),
                cand_error=_format_metric(row.get("candidate_abs_price_error")),
                base_trend=_format_metric(row.get("baseline_trend_factor")),
                cand_trend=_format_metric(row.get("candidate_trend_factor")),
                report=row.get("candidate_report_path") or "-",
            )
        )

    markdown = "\n".join(
        [
            f"# trend_balance 观察期样本复核（{candidate_name}）",
            "",
            f"- 生成时间：{payload['generated_at']}",
            (
                "- baseline："
                f"`industry_trend_weight = {baseline_params.get('industry_trend_weight')}`，"
                f"`market_sentiment_weight = {baseline_params.get('market_sentiment_weight')}`，"
                f"`sample_decay_half_life_days = {baseline_params.get('sample_decay_half_life_days')}`，"
                f"`trend_strong_threshold = {baseline_params.get('trend_strong_threshold')}`，"
                f"`trend_weak_threshold = {baseline_params.get('trend_weak_threshold')}`"
            ),
            (
                "- 候选："
                f"`industry_trend_weight = {candidate_params.get('industry_trend_weight')}`，"
                f"`market_sentiment_weight = {candidate_params.get('market_sentiment_weight')}`，"
                f"`sample_decay_half_life_days = {candidate_params.get('sample_decay_half_life_days')}`，"
                f"`trend_strong_threshold = {candidate_params.get('trend_strong_threshold')}`，"
                f"`trend_weak_threshold = {candidate_params.get('trend_weak_threshold')}`"
            ),
            "",
            "## 样本对比",
            "",
            *table_lines,
            "",
            "## 汇总",
            "",
            f"- baseline 平均绝对误差：`{_format_metric(payload['baseline_avg_abs_price_error'])}`",
            f"- 候选平均绝对误差：`{_format_metric(payload['candidate_avg_abs_price_error'])}`",
            "",
        ]
    )
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="trend_balance 候选观察期样本复核工具")
    parser.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"), help="baseline 参数文件路径")
    parser.add_argument("--codes", help="样本代码列表，逗号分隔；默认使用 920177,920181,920180")
    parser.add_argument("--industry-trend-weight", type=float, default=0.70, help="候选行业趋势权重")
    parser.add_argument("--market-sentiment-weight", type=float, default=0.30, help="候选市场情绪权重")
    parser.add_argument("--sample-decay-half-life-days", type=int, default=10, help="候选样本半衰期")
    parser.add_argument("--trend-strong-threshold", type=int, default=70, help="候选强趋势阈值")
    parser.add_argument("--trend-weak-threshold", type=int, default=40, help="候选弱趋势阈值")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="观察期输出目录")
    parser.add_argument("--skip-pdf", action="store_true", help="只输出观察摘要，不生成候选 PDF")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    base_params = config_loader.load_params(args.params_file)
    candidate_params = dict(base_params)
    candidate_params["industry_trend_weight"] = args.industry_trend_weight
    candidate_params["market_sentiment_weight"] = args.market_sentiment_weight
    candidate_params["sample_decay_half_life_days"] = args.sample_decay_half_life_days
    candidate_params["trend_strong_threshold"] = args.trend_strong_threshold
    candidate_params["trend_weak_threshold"] = args.trend_weak_threshold

    total_weight = float(candidate_params["industry_trend_weight"]) + float(
        candidate_params["market_sentiment_weight"]
    )
    if abs(total_weight - 1.0) > 1e-6:
        raise ValueError("候选趋势权重之和必须为 1。")

    codes = _parse_codes(args.codes)
    output_dir = Path(args.output_dir)
    candidate_name = _candidate_tag(
        args.industry_trend_weight,
        args.market_sentiment_weight,
        args.sample_decay_half_life_days,
        args.trend_strong_threshold,
        args.trend_weak_threshold,
    )
    rows: list[dict[str, Any]] = []

    for index, code in enumerate(codes, start=1):
        print(f"[{index}/{len(codes)}] observing {code}", flush=True)
        try:
            baseline_payload = bse_ipo_valuation.build_analysis_data(code, params=base_params)
            candidate_payload = bse_ipo_valuation.build_analysis_data(code, params=candidate_params)
            actual_listing_result = _load_actual_listing_result(code, base_params)
            candidate_report_path = None
            if not args.skip_pdf:
                candidate_report_path = report_generator.generate_report(candidate_payload, str(output_dir))
            row = _build_result_row(
                code,
                baseline_payload,
                candidate_payload,
                actual_listing_result,
                candidate_report_path,
            )
        except Exception as exc:
            row = _build_result_row(code, {}, {}, {}, None, error=f"{type(exc).__name__}: {exc}")
        rows.append(row)

    json_path, md_path = _write_outputs(output_dir, candidate_name, base_params, candidate_params, rows)
    print(f"JSON 摘要：{json_path}", flush=True)
    print(f"Markdown 摘要：{md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
