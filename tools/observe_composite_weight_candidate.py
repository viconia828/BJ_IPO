from __future__ import annotations

import argparse
import copy
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
import valuation_engine


DEFAULT_CODES = ["920177", "920181", "920180"]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "输出" / "观察期"


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _calc_change_pct(issue_price: float | None, target_price: float | None) -> float | None:
    if not issue_price or not target_price:
        return None
    return (target_price / issue_price - 1) * 100


def _parse_codes(raw_value: str | None) -> list[str]:
    if not raw_value:
        return list(DEFAULT_CODES)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _candidate_tag(weight_comparable: float, weight_industry: float) -> str:
    def _fmt(value: float) -> str:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text.replace(".", "p")

    return f"wc_{_fmt(weight_comparable)}_wi_{_fmt(weight_industry)}"


def _prepare_candidate_payload(
    base_payload: dict[str, Any],
    candidate_params: dict[str, Any],
) -> dict[str, Any]:
    payload = copy.deepcopy(base_payload)
    final = valuation_engine.composite_valuation(
        payload.get("method1"),
        payload.get("method2"),
        candidate_params,
    )
    issue_price = _safe_float((payload.get("ipo_info") or {}).get("ISSUE_PRICE"))
    payload["params"] = dict(candidate_params)
    payload["final"] = final
    payload["final_change_pct"] = _calc_change_pct(issue_price, final.get("target_price"))
    payload["range_change_low"] = _calc_change_pct(issue_price, final.get("range_low"))
    payload["range_change_high"] = _calc_change_pct(issue_price, final.get("range_high"))
    return payload


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
    baseline_method1 = baseline_payload.get("method1") or {}
    baseline_method2 = baseline_payload.get("method2") or {}
    candidate_method1 = candidate_payload.get("method1") or {}
    candidate_method2 = candidate_payload.get("method2") or {}

    baseline_target = _safe_float(baseline_final.get("target_price"))
    candidate_target = _safe_float(candidate_final.get("target_price"))
    issue_price = _safe_float(actual_listing_result.get("issue_price")) or _safe_float(
        (candidate_payload.get("ipo_info") or {}).get("ISSUE_PRICE")
    )
    actual_close_price = _safe_float(actual_listing_result.get("actual_close_price"))
    actual_change_pct = _safe_float(actual_listing_result.get("actual_change_pct"))
    baseline_change = _safe_float(baseline_payload.get("final_change_pct"))
    candidate_change = _safe_float(candidate_payload.get("final_change_pct"))

    return {
        "code": code,
        "name": str((candidate_payload.get("ipo_info") or {}).get("SECURITY_NAME_ABBR") or "").strip(),
        "issue_price": issue_price,
        "actual_close_price": actual_close_price,
        "actual_change_pct": actual_change_pct,
        "baseline_target_price": baseline_target,
        "candidate_target_price": candidate_target,
        "target_price_delta": (candidate_target - baseline_target) if baseline_target is not None and candidate_target is not None else None,
        "baseline_abs_price_error": abs(baseline_target - actual_close_price) if baseline_target is not None and actual_close_price is not None else None,
        "candidate_abs_price_error": abs(candidate_target - actual_close_price) if candidate_target is not None and actual_close_price is not None else None,
        "baseline_change_pct": baseline_change,
        "candidate_change_pct": candidate_change,
        "change_pct_delta": (candidate_change - baseline_change) if baseline_change is not None and candidate_change is not None else None,
        "baseline_method1_available": bool(baseline_method1.get("available")),
        "baseline_method2_available": bool(baseline_method2.get("available")),
        "candidate_method1_available": bool(candidate_method1.get("available")),
        "candidate_method2_available": bool(candidate_method2.get("available")),
        "method1_target_price": _safe_float(candidate_method1.get("target_price")),
        "method2_target_price": _safe_float(candidate_method2.get("target_price")),
        "method2_sample_scope": candidate_method2.get("sample_scope"),
        "method2_sample_count": candidate_method2.get("sample_count"),
        "candidate_weight_comparable": _safe_float(candidate_final.get("weight_comparable")),
        "candidate_weight_industry_momentum": _safe_float(candidate_final.get("weight_industry_momentum")),
        "candidate_report_path": candidate_report_path,
        "error": error,
    }


def _write_outputs(
    output_dir: Path,
    candidate_name: str,
    base_params: dict[str, Any],
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
        "baseline_weights": {
            "weight_comparable": base_params.get("weight_comparable"),
            "weight_industry_momentum": base_params.get("weight_industry_momentum"),
        },
        "candidate_weights": {
            "weight_comparable": candidate_params.get("weight_comparable"),
            "weight_industry_momentum": candidate_params.get("weight_industry_momentum"),
        },
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    table_lines = [
        "| 代码 | 名称 | 实际收盘 | baseline 目标价 | 候选 目标价 | baseline 误差 | 候选 误差 | 方法二口径 | 候选报告 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        report_text = row.get("candidate_report_path") or "-"
        table_lines.append(
            "| {code} | {name} | {actual_close} | {base_target} | {cand_target} | {base_error} | {cand_error} | {scope}({count}) | {report} |".format(
                code=row.get("code"),
                name=row.get("name") or "-",
                actual_close=_format_metric(row.get("actual_close_price")),
                base_target=_format_metric(row.get("baseline_target_price")),
                cand_target=_format_metric(row.get("candidate_target_price")),
                base_error=_format_metric(row.get("baseline_abs_price_error")),
                cand_error=_format_metric(row.get("candidate_abs_price_error")),
                scope=row.get("method2_sample_scope") or "-",
                count=row.get("method2_sample_count") or 0,
                report=report_text,
            )
        )

    markdown = "\n".join(
        [
            f"# 综合权重观察期样本复核（{candidate_name}）",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- baseline：`weight_comparable = {base_params.get('weight_comparable')}`，`weight_industry_momentum = {base_params.get('weight_industry_momentum')}`",
            f"- 候选：`weight_comparable = {candidate_params.get('weight_comparable')}`，`weight_industry_momentum = {candidate_params.get('weight_industry_momentum')}`",
            "",
            "## 样本对比",
            "",
            *table_lines,
            "",
        ]
    )
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def _format_metric(value: Any) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "-"
    return f"{numeric:.4f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="综合权重候选观察期样本复核工具")
    parser.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"), help="baseline 参数文件路径")
    parser.add_argument("--codes", help="样本代码列表，逗号分隔；默认使用 920177,920181,920180")
    parser.add_argument("--weight-comparable", type=float, default=0.20, help="候选方法一权重")
    parser.add_argument("--weight-industry-momentum", type=float, default=0.80, help="候选方法二权重")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="观察期输出目录")
    parser.add_argument("--skip-pdf", action="store_true", help="只输出观察摘要，不生成候选 PDF")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    base_params = config_loader.load_params(args.params_file)
    candidate_params = dict(base_params)
    candidate_params["weight_comparable"] = args.weight_comparable
    candidate_params["weight_industry_momentum"] = args.weight_industry_momentum
    total_weight = float(candidate_params["weight_comparable"]) + float(candidate_params["weight_industry_momentum"])
    if abs(total_weight - 1.0) > 1e-6:
        raise ValueError("候选综合权重之和必须为 1。")

    codes = _parse_codes(args.codes)
    output_dir = Path(args.output_dir)
    candidate_name = _candidate_tag(args.weight_comparable, args.weight_industry_momentum)
    rows: list[dict[str, Any]] = []

    for index, code in enumerate(codes, start=1):
        print(f"[{index}/{len(codes)}] observing {code}", flush=True)
        try:
            baseline_payload = bse_ipo_valuation.build_analysis_data(code, params=base_params)
            candidate_payload = _prepare_candidate_payload(baseline_payload, candidate_params)
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
