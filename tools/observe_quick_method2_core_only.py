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


DEFAULT_CODES = ["920188", "920012", "920069", "920055"]
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


def _candidate_tag(price_range_width: float, float_size_threshold: int, small_cap_premium: float) -> str:
    def _fmt(value: float) -> str:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text.replace(".", "p")

    return "qm2_core_only_prw_{width}_fst_{threshold}_scp_{premium}".format(
        width=_fmt(price_range_width),
        threshold=float_size_threshold,
        premium=_fmt(small_cap_premium),
    )


def _load_actual_listing_result(code: str, params: dict[str, Any]) -> dict[str, Any]:
    bundle = ipo_data_helper.prepare_ipo_data(code, int(params.get("recent_months", 3)), params)
    ipo_info = bundle.get("ipo_info") or {}
    issue_price = _safe_float(ipo_info.get("ISSUE_PRICE"))
    actual_close_price = _safe_float(ipo_info.get("CLOSE_PRICE"))
    actual_change_pct = _safe_float(ipo_info.get("LD_CLOSE_CHANGE"))
    for item in bundle.get("recent_ipos") or []:
        if str(item.get("SECURITY_CODE") or "").strip() != code:
            continue
        issue_price = _safe_float(item.get("ISSUE_PRICE")) or issue_price
        actual_close_price = _safe_float(item.get("CLOSE_PRICE")) or actual_close_price
        actual_change_pct = _safe_float(item.get("LD_CLOSE_CHANGE")) or actual_change_pct
        break
    return {
        "issue_price": issue_price,
        "actual_close_price": actual_close_price,
        "actual_change_pct": actual_change_pct,
    }


def _is_interval_hit(actual_close_price: float | None, range_low: float | None, range_high: float | None) -> bool | None:
    if actual_close_price is None or range_low is None or range_high is None:
        return None
    return range_low <= actual_close_price <= range_high


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
    baseline_range_low = _safe_float(baseline_final.get("range_low"))
    baseline_range_high = _safe_float(baseline_final.get("range_high"))
    candidate_range_low = _safe_float(candidate_final.get("range_low"))
    candidate_range_high = _safe_float(candidate_final.get("range_high"))
    baseline_interval_hit = _is_interval_hit(actual_close_price, baseline_range_low, baseline_range_high)
    candidate_interval_hit = _is_interval_hit(actual_close_price, candidate_range_low, candidate_range_high)
    baseline_float_factor = _safe_float(baseline_method2.get("float_factor"))
    candidate_float_factor = _safe_float(candidate_method2.get("float_factor"))

    return {
        "code": code,
        "name": str(ipo_info.get("SECURITY_NAME_ABBR") or "").strip(),
        "issue_price": _safe_float(actual_listing_result.get("issue_price")),
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
        "baseline_range_low": baseline_range_low,
        "baseline_range_high": baseline_range_high,
        "candidate_range_low": candidate_range_low,
        "candidate_range_high": candidate_range_high,
        "baseline_interval_hit": baseline_interval_hit,
        "candidate_interval_hit": candidate_interval_hit,
        "baseline_change_pct": _safe_float(baseline_payload.get("final_change_pct")),
        "candidate_change_pct": _safe_float(candidate_payload.get("final_change_pct")),
        "baseline_method2_target_price": _safe_float(baseline_method2.get("target_price")),
        "candidate_method2_target_price": _safe_float(candidate_method2.get("target_price")),
        "baseline_method2_sample_scope": baseline_method2.get("sample_scope"),
        "baseline_method2_sample_count": baseline_method2.get("sample_count"),
        "candidate_method2_sample_scope": candidate_method2.get("sample_scope"),
        "candidate_method2_sample_count": candidate_method2.get("sample_count"),
        "baseline_float_factor": baseline_float_factor,
        "candidate_float_factor": candidate_float_factor,
        "baseline_small_cap_triggered": bool(baseline_float_factor and baseline_float_factor > 1.0),
        "candidate_small_cap_triggered": bool(candidate_float_factor and candidate_float_factor > 1.0),
        "baseline_float_note": baseline_method2.get("float_note"),
        "candidate_float_note": candidate_method2.get("float_note"),
        "candidate_report_path": candidate_report_path,
        "error": error,
    }


def _average(values: list[float | None]) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def _hit_rate(values: list[bool | None]) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(1 for value in filtered if value) / len(filtered)


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
            "price_range_width": baseline_params.get("price_range_width"),
            "float_size_threshold": baseline_params.get("float_size_threshold"),
            "small_cap_premium": baseline_params.get("small_cap_premium"),
        },
        "candidate_params": {
            "price_range_width": candidate_params.get("price_range_width"),
            "float_size_threshold": candidate_params.get("float_size_threshold"),
            "small_cap_premium": candidate_params.get("small_cap_premium"),
        },
        "rows": rows,
        "baseline_avg_abs_price_error": _average(
            [_safe_float(row.get("baseline_abs_price_error")) for row in rows]
        ),
        "candidate_avg_abs_price_error": _average(
            [_safe_float(row.get("candidate_abs_price_error")) for row in rows]
        ),
        "baseline_interval_hit_rate": _hit_rate([row.get("baseline_interval_hit") for row in rows]),
        "candidate_interval_hit_rate": _hit_rate([row.get("candidate_interval_hit") for row in rows]),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    table_lines = [
        "| 代码 | 名称 | 实际收盘 | baseline 目标价 | 候选目标价 | baseline 误差 | 候选误差 | baseline 小盘 | 候选小盘 | baseline 区间命中 | 候选区间命中 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        table_lines.append(
            "| {code} | {name} | {actual_close} | {base_target} | {cand_target} | {base_error} | {cand_error} | {base_small} | {cand_small} | {base_hit} | {cand_hit} |".format(
                code=row.get("code"),
                name=row.get("name") or "-",
                actual_close=_format_metric(row.get("actual_close_price")),
                base_target=_format_metric(row.get("baseline_target_price")),
                cand_target=_format_metric(row.get("candidate_target_price")),
                base_error=_format_metric(row.get("baseline_abs_price_error")),
                cand_error=_format_metric(row.get("candidate_abs_price_error")),
                base_small="是" if row.get("baseline_small_cap_triggered") else "否",
                cand_small="是" if row.get("candidate_small_cap_triggered") else "否",
                base_hit="是" if row.get("baseline_interval_hit") else "否",
                cand_hit="是" if row.get("candidate_interval_hit") else "否",
            )
        )

    markdown = "\n".join(
        [
            f"# quick_method2_core_only 观察期样本复核（{candidate_name}）",
            "",
            f"- 生成时间：{payload['generated_at']}",
            (
                "- baseline："
                f"`price_range_width = {baseline_params.get('price_range_width')}`，"
                f"`float_size_threshold = {baseline_params.get('float_size_threshold')}`，"
                f"`small_cap_premium = {baseline_params.get('small_cap_premium')}`"
            ),
            (
                "- 候选："
                f"`price_range_width = {candidate_params.get('price_range_width')}`，"
                f"`float_size_threshold = {candidate_params.get('float_size_threshold')}`，"
                f"`small_cap_premium = {candidate_params.get('small_cap_premium')}`"
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
            f"- baseline 区间命中率：`{_format_metric(payload['baseline_interval_hit_rate'])}`",
            f"- 候选区间命中率：`{_format_metric(payload['candidate_interval_hit_rate'])}`",
            "",
        ]
    )
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="quick_method2_core_only 观察期样本复核工具")
    parser.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"), help="当前参数文件路径")
    parser.add_argument("--codes", help="样本代码列表，逗号分隔；默认使用 920188,920012,920069,920055")
    parser.add_argument("--baseline-price-range-width", type=float, default=0.10, help="回退 baseline 的区间宽度")
    parser.add_argument("--baseline-float-size-threshold", type=int, default=2000, help="回退 baseline 的流通盘阈值")
    parser.add_argument("--baseline-small-cap-premium", type=float, default=0.10, help="回退 baseline 的小盘溢价")
    parser.add_argument("--candidate-price-range-width", type=float, default=0.12, help="候选区间宽度")
    parser.add_argument("--candidate-float-size-threshold", type=int, default=1500, help="候选流通盘阈值")
    parser.add_argument("--candidate-small-cap-premium", type=float, default=0.15, help="候选小盘溢价")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="观察期输出目录")
    parser.add_argument("--skip-pdf", action="store_true", help="只输出观察摘要，不生成候选 PDF")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    current_params = config_loader.load_params(args.params_file)
    baseline_params = dict(current_params)
    baseline_params["price_range_width"] = args.baseline_price_range_width
    baseline_params["float_size_threshold"] = args.baseline_float_size_threshold
    baseline_params["small_cap_premium"] = args.baseline_small_cap_premium

    candidate_params = dict(current_params)
    candidate_params["price_range_width"] = args.candidate_price_range_width
    candidate_params["float_size_threshold"] = args.candidate_float_size_threshold
    candidate_params["small_cap_premium"] = args.candidate_small_cap_premium

    codes = _parse_codes(args.codes)
    output_dir = Path(args.output_dir)
    candidate_name = _candidate_tag(
        args.candidate_price_range_width,
        args.candidate_float_size_threshold,
        args.candidate_small_cap_premium,
    )
    rows: list[dict[str, Any]] = []

    for index, code in enumerate(codes, start=1):
        print(f"[{index}/{len(codes)}] observing {code}", flush=True)
        try:
            baseline_payload = bse_ipo_valuation.build_analysis_data(code, params=baseline_params)
            candidate_payload = bse_ipo_valuation.build_analysis_data(code, params=candidate_params)
            actual_listing_result = _load_actual_listing_result(code, candidate_params)
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

    json_path, md_path = _write_outputs(output_dir, candidate_name, baseline_params, candidate_params, rows)
    print(f"JSON 摘要：{json_path}", flush=True)
    print(f"Markdown 摘要：{md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
