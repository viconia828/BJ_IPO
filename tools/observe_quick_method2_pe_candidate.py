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

import config_loader
import param_tuning


DEFAULT_CODES = ["920012", "920036", "920183"]
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


def _format_flag(value: bool | None) -> str:
    if value is None:
        return "-"
    return "是" if value else "否"


def _candidate_tag(
    pe_low_threshold: float,
    pe_discount_boost: float,
    pe_high_threshold: float,
    pe_premium_drag: float,
) -> str:
    def _fmt(value: float) -> str:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text.replace("-", "m").replace(".", "p")

    return "qm2_pe_plt_{low}_pdb_{boost}_pht_{high}_ppd_{drag}".format(
        low=_fmt(pe_low_threshold),
        boost=_fmt(pe_discount_boost),
        high=_fmt(pe_high_threshold),
        drag=_fmt(pe_premium_drag),
    )


def _is_interval_hit(actual_close_price: float | None, range_low: float | None, range_high: float | None) -> bool | None:
    if actual_close_price is None or range_low is None or range_high is None:
        return None
    return range_low <= actual_close_price <= range_high


def _build_row_note(
    baseline_result: dict[str, Any] | None,
    candidate_result: dict[str, Any] | None,
) -> str:
    if baseline_result is None and candidate_result is None:
        return "baseline 与候选均无可用结果"
    if baseline_result is None:
        return "baseline 无可用结果"
    if candidate_result is None:
        return "候选无可用结果"
    return ""


def _build_result_row(
    code: str,
    item: dict[str, Any],
    baseline_result: dict[str, Any] | None,
    candidate_result: dict[str, Any] | None,
) -> dict[str, Any]:
    actual_close_price = _safe_float(item.get("CLOSE_PRICE"))
    issue_pe = _safe_float(item.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(item.get("INDUSTRY_PE_NEW"))
    pe_ratio = None
    if issue_pe is not None and industry_pe not in (None, 0):
        pe_ratio = issue_pe / industry_pe

    baseline_range_low = _safe_float((baseline_result or {}).get("range_low"))
    baseline_range_high = _safe_float((baseline_result or {}).get("range_high"))
    candidate_range_low = _safe_float((candidate_result or {}).get("range_low"))
    candidate_range_high = _safe_float((candidate_result or {}).get("range_high"))

    note = _build_row_note(baseline_result, candidate_result)

    return {
        "code": code,
        "name": str(item.get("SECURITY_NAME_ABBR") or "").strip(),
        "actual_close_price": actual_close_price,
        "actual_change_pct": _safe_float(item.get("LD_CLOSE_CHANGE")),
        "issue_pe": issue_pe,
        "industry_pe": industry_pe,
        "pe_ratio": pe_ratio,
        "baseline_target_price": _safe_float((baseline_result or {}).get("predicted_target_price")),
        "candidate_target_price": _safe_float((candidate_result or {}).get("predicted_target_price")),
        "baseline_abs_price_error": _safe_float((baseline_result or {}).get("price_abs_error")),
        "candidate_abs_price_error": _safe_float((candidate_result or {}).get("price_abs_error")),
        "baseline_interval_hit": _is_interval_hit(actual_close_price, baseline_range_low, baseline_range_high),
        "candidate_interval_hit": _is_interval_hit(actual_close_price, candidate_range_low, candidate_range_high),
        "baseline_change_pct": _safe_float((baseline_result or {}).get("predicted_change_pct")),
        "candidate_change_pct": _safe_float((candidate_result or {}).get("predicted_change_pct")),
        "baseline_pe_factor": _safe_float((baseline_result or {}).get("pe_factor")),
        "candidate_pe_factor": _safe_float((candidate_result or {}).get("pe_factor")),
        "baseline_sample_scope": (baseline_result or {}).get("sample_scope"),
        "candidate_sample_scope": (candidate_result or {}).get("sample_scope"),
        "baseline_sample_count": (baseline_result or {}).get("sample_count"),
        "candidate_sample_count": (candidate_result or {}).get("sample_count"),
        "note": note,
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
            "small_cap_premium": baseline_params.get("small_cap_premium"),
            "float_size_threshold": baseline_params.get("float_size_threshold"),
            "pe_low_threshold": baseline_params.get("pe_low_threshold"),
            "pe_discount_boost": baseline_params.get("pe_discount_boost"),
            "pe_high_threshold": baseline_params.get("pe_high_threshold"),
            "pe_premium_drag": baseline_params.get("pe_premium_drag"),
        },
        "candidate_params": {
            "price_range_width": candidate_params.get("price_range_width"),
            "small_cap_premium": candidate_params.get("small_cap_premium"),
            "float_size_threshold": candidate_params.get("float_size_threshold"),
            "pe_low_threshold": candidate_params.get("pe_low_threshold"),
            "pe_discount_boost": candidate_params.get("pe_discount_boost"),
            "pe_high_threshold": candidate_params.get("pe_high_threshold"),
            "pe_premium_drag": candidate_params.get("pe_premium_drag"),
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
        "| 代码 | 名称 | PE 比值 | baseline PE 因子 | 候选 PE 因子 | baseline 误差 | 候选误差 | baseline 区间命中 | 候选区间命中 | 备注 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        table_lines.append(
            "| {code} | {name} | {ratio} | {base_factor} | {cand_factor} | {base_error} | {cand_error} | {base_hit} | {cand_hit} | {note} |".format(
                code=row.get("code"),
                name=row.get("name") or "-",
                ratio=_format_metric(row.get("pe_ratio")),
                base_factor=_format_metric(row.get("baseline_pe_factor")),
                cand_factor=_format_metric(row.get("candidate_pe_factor")),
                base_error=_format_metric(row.get("baseline_abs_price_error")),
                cand_error=_format_metric(row.get("candidate_abs_price_error")),
                base_hit=_format_flag(row.get("baseline_interval_hit")),
                cand_hit=_format_flag(row.get("candidate_interval_hit")),
                note=row.get("note") or "-",
            )
        )

    markdown = "\n".join(
        [
            f"# quick_method2 PE 参数观察期复核（{candidate_name}）",
            "",
            f"- 生成时间：{payload['generated_at']}",
            (
                "- baseline："
                f"`pe_low_threshold = {baseline_params.get('pe_low_threshold')}`，"
                f"`pe_discount_boost = {baseline_params.get('pe_discount_boost')}`，"
                f"`pe_high_threshold = {baseline_params.get('pe_high_threshold')}`，"
                f"`pe_premium_drag = {baseline_params.get('pe_premium_drag')}`"
            ),
            (
                "- 候选："
                f"`pe_low_threshold = {candidate_params.get('pe_low_threshold')}`，"
                f"`pe_discount_boost = {candidate_params.get('pe_discount_boost')}`，"
                f"`pe_high_threshold = {candidate_params.get('pe_high_threshold')}`，"
                f"`pe_premium_drag = {candidate_params.get('pe_premium_drag')}`"
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
    parser = argparse.ArgumentParser(description="quick_method2 PE 参数观察期样本复核工具（基于 replay_dataset）")
    parser.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"), help="baseline 参数文件路径")
    parser.add_argument("--dataset-path", default=str(param_tuning.DEFAULT_DATASET_PATH), help="历史回放数据集路径")
    parser.add_argument("--codes", help="样本代码列表，逗号分隔；默认使用当前 PE 变化样本")
    parser.add_argument("--pe-low-threshold", type=float, default=0.25, help="候选低 PE 阈值")
    parser.add_argument("--pe-discount-boost", type=float, default=0.10, help="候选低 PE 加成")
    parser.add_argument("--pe-high-threshold", type=float, default=0.60, help="候选高 PE 阈值")
    parser.add_argument("--pe-premium-drag", type=float, default=-0.10, help="候选高 PE 调整")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="观察期输出目录")
    parser.add_argument("--skip-pdf", action="store_true", help="兼容旧接口；当前 replay 观察不会生成 PDF")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    baseline_params = config_loader.load_params(args.params_file)
    candidate_params = dict(baseline_params)
    candidate_params["pe_low_threshold"] = args.pe_low_threshold
    candidate_params["pe_discount_boost"] = args.pe_discount_boost
    candidate_params["pe_high_threshold"] = args.pe_high_threshold
    candidate_params["pe_premium_drag"] = args.pe_premium_drag

    codes = _parse_codes(args.codes)
    dataset = param_tuning.load_replay_dataset(args.dataset_path)
    item_map = {
        str(item.get("SECURITY_CODE") or "").strip(): item
        for item in dataset.get("items") or []
    }

    baseline_metrics = param_tuning.evaluate_replay_targets(dataset, baseline_params, target_codes=codes)
    candidate_metrics = param_tuning.evaluate_replay_targets(dataset, candidate_params, target_codes=codes)
    baseline_map = {row["code"]: row for row in baseline_metrics.get("available_results") or []}
    candidate_map = {row["code"]: row for row in candidate_metrics.get("available_results") or []}

    rows: list[dict[str, Any]] = []
    for index, code in enumerate(codes, start=1):
        print(f"[{index}/{len(codes)}] observing {code}", flush=True)
        item = item_map.get(code, {"SECURITY_CODE": code, "SECURITY_NAME_ABBR": ""})
        rows.append(_build_result_row(code, item, baseline_map.get(code), candidate_map.get(code)))

    output_dir = Path(args.output_dir)
    candidate_name = _candidate_tag(
        args.pe_low_threshold,
        args.pe_discount_boost,
        args.pe_high_threshold,
        args.pe_premium_drag,
    )
    json_path, md_path = _write_outputs(output_dir, candidate_name, baseline_params, candidate_params, rows)
    print(f"JSON 摘要：{json_path}", flush=True)
    print(f"Markdown 摘要：{md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
