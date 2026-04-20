from __future__ import annotations

import argparse
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
import param_tuning
import pdf_parser
import valuation_engine


DEFAULT_CACHE_PATH = ROOT_DIR / "data" / "offline_tuning" / "comparable_code_cache.json"


def _load_cache(cache_path: Path) -> dict[str, list[str]]:
    if not cache_path.exists():
        return {}
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, dict):
        return {}
    return {
        str(code).strip(): [str(item).strip() for item in values or [] if str(item).strip()]
        for code, values in items.items()
    }


def _save_cache(cache_path: Path, mapping: dict[str, list[str]]) -> None:
    payload = {
        "schema": "comparable_code_cache_v1",
        "generated_at": param_tuning._now_text(),
        "items": mapping,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_comparable_codes(
    code: str,
    cache_mapping: dict[str, list[str]],
) -> list[str]:
    cached = cache_mapping.get(code)
    if cached is not None:
        return list(cached)

    pdf_dir = ROOT_DIR / "公告文件"
    prospectus_pdf = bse_ipo_valuation._pick_prospectus_pdf(pdf_dir, code, "comparables")
    if prospectus_pdf is None:
        cache_mapping[code] = []
        return []

    extracted = pdf_parser.extract_comparable_companies(prospectus_pdf)
    cache_mapping[code] = list(extracted)
    return list(extracted)


def _build_caveats(evaluation_scope: str) -> list[str]:
    caveats = [
        "每只样本只使用上市当时可见的发行字段、历史新股样本和本地首日分时数据。",
        "历史流通盘使用本地 PDF 提取的 old_shares 结果，缺失时回退按 0 万股处理。",
    ]
    if evaluation_scope == param_tuning.COMPOSITE_EVALUATION_SCOPE:
        caveats.insert(
            0,
            "方法一历史回放快照基于 Tushare `daily_basic`，按标的上市日前一日向前回看交易窗口，未取到时该样本自动降级为仅方法二。",
        )
    else:
        caveats.insert(
            0,
            "当前未形成可用的方法一历史快照，离线调参仍按方法二口径评估。",
        )
    return caveats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在现有 replay_dataset 基础上补齐 composite 回放所需的方法一历史快照")
    parser.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"), help="参数文件路径")
    parser.add_argument("--dataset-path", default=str(param_tuning.DEFAULT_DATASET_PATH), help="回放数据集路径")
    parser.add_argument("--cache-file", default=str(DEFAULT_CACHE_PATH), help="可比公司代码缓存文件路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    params = config_loader.load_params(args.params_file)
    dataset_path = Path(args.dataset_path)
    cache_path = Path(args.cache_file)

    dataset = param_tuning.load_replay_dataset(dataset_path)
    cache_mapping = _load_cache(cache_path)
    snapshot_cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    items = list(dataset.get("items") or [])
    method1_ready_count = 0
    for index, item in enumerate(items, start=1):
        code = str(item.get("SECURITY_CODE") or "").strip()
        comparable_codes = _resolve_comparable_codes(code, cache_mapping)
        comparable_data, comparable_summary = param_tuning._fetch_historical_comparable_data(
            comparable_codes,
            item.get("LISTING_DATE"),
            params,
            snapshot_cache,
        )
        method1 = valuation_engine.method1_comparable(
            issue_price=param_tuning._safe_float(item.get("ISSUE_PRICE")),
            issue_pe=param_tuning._safe_float(item.get("AFTER_ISSUE_PE")),
            comparable_data=comparable_data,
            params=params,
        )
        item["comparable_codes"] = comparable_codes
        item["comparable_data"] = comparable_data
        item["comparable_summary"] = comparable_summary
        item["method1_replay_available"] = bool(method1.get("available"))
        if method1.get("available"):
            method1_ready_count += 1
        print(
            f"[{index}/{len(items)}] {code} comparable_codes={len(comparable_codes)} "
            f"historical_snapshots={len(comparable_data)} method1_available={bool(method1.get('available'))}",
            flush=True,
        )

    evaluation_scope = (
        param_tuning.COMPOSITE_EVALUATION_SCOPE
        if method1_ready_count > 0
        else param_tuning.METHOD2_ONLY_SCOPE
    )
    dataset["generated_at"] = param_tuning._now_text()
    dataset["evaluation_scope"] = evaluation_scope
    dataset["method1_ready_count"] = method1_ready_count
    dataset["method1_ready_rate"] = (method1_ready_count / len(items)) if items else 0.0
    dataset["caveats"] = _build_caveats(evaluation_scope)
    param_tuning.save_replay_dataset(dataset, dataset_path)
    _save_cache(cache_path, cache_mapping)

    print(f"dataset_path={dataset_path}", flush=True)
    print(f"evaluation_scope={evaluation_scope}", flush=True)
    print(f"method1_ready_count={method1_ready_count}", flush=True)
    print(f"method1_ready_rate={dataset['method1_ready_rate']:.4f}", flush=True)
    print(f"cache_path={cache_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
