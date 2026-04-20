from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import cache_listing_day_intraday
import config_loader


DEFAULT_OUTPUT_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_CACHE_ROOT = ROOT_DIR / "data" / "tushare_intraday_db"


def _build_runtime_params() -> dict[str, object]:
    params = dict(config_loader.load_params())
    # Keep the standalone cache job independent from the main workflow's request log.
    params["tushare_cache_root"] = str(DEFAULT_CACHE_ROOT)
    params["tushare_daily_request_quota"] = max(int(params.get("tushare_daily_request_quota", 200)), 9999)
    params["tushare_request_pause_seconds"] = 0.0
    return params


def _format_item(item: dict[str, object]) -> str:
    code = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    listing_date = str(item.get("listing_date") or "").strip()
    suffix = f"（{listing_date}）" if listing_date else ""
    if name:
        return f"{code} {name}{suffix}"
    return f"{code}{suffix}"


def _emit_progress(event: str, payload: dict[str, Any]) -> None:
    if event == "start":
        pending = list(payload.get("pending_deferred_before") or [])
        print("正在扫描最新上市的新股首日走势缓存...", flush=True)
        if pending:
            print(f"检测到 {len(pending)} 只待重试新股，本次会继续尝试补缓存。", flush=True)
        return

    if event == "scan_completed":
        matched_count = int(payload.get("matched_count") or 0)
        print(f"扫描完成，本次命中 {matched_count} 只候选新股。", flush=True)
        return

    if event == "checking":
        print(f"正在检查 {_format_item(payload)} ...", flush=True)
        return

    if event == "cached":
        print(f"已缓存 {_format_item(payload)}。", flush=True)
        return

    if event == "deferred":
        reason = str(payload.get("reason") or "分时数据暂不可用")
        print(f"暂不缓存 {_format_item(payload)}：{reason}", flush=True)
        return

    if event == "existing":
        print(f"已存在缓存 {_format_item(payload)}。", flush=True)
        return

    if event == "stop_at_existing":
        print(f"命中已有缓存，扫描将在 {_format_item(payload)} 处停止。", flush=True)
        return

    if event == "error":
        reason = str(payload.get("reason") or "未知错误")
        print(f"缓存失败 {_format_item(payload)}：{reason}", flush=True)
        return

    if event == "scan_error":
        print(f"扫描失败：{payload.get('reason')}", flush=True)
        return

    if event == "finished":
        print("本轮扫描处理完毕，正在汇总结果...", flush=True)


def _print_summary(summary: dict[str, object]) -> None:
    cached = list(summary.get("cached") or [])
    deferred = list(summary.get("deferred") or [])
    errors = list(summary.get("errors") or [])
    stop_at_existing = summary.get("stop_at_existing")

    if cached:
        print(f"本次已缓存 {len(cached)} 只新股首日走势：")
        for item in cached:
            print(f"- {_format_item(item)}")
    else:
        print("本次没有扫描到需要新增缓存的新股。")

    if deferred:
        print("以下新股本次未缓存，留待下次执行补缓存程序再取：")
        for item in deferred:
            reason = str(item.get("reason") or "分时数据暂不可用")
            print(f"- {_format_item(item)}：{reason}")

    if stop_at_existing:
        print(f"扫描在已有缓存处停止：{_format_item(stop_at_existing)}")

    if errors:
        print("以下新股未能完成缓存：")
        for item in errors:
            reason = str(item.get("reason") or "未知错误")
            print(f"- {_format_item(item)}：{reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description="补齐最新上市北交所新股的首日分时走势缓存。")
    parser.add_argument("--months", type=int, default=18, help="向前扫描东方财富新股列表的月份数，默认 18。")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="CSV 输出目录，默认 首日分时走势。")
    args = parser.parse_args()

    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=max(args.months, 1),
        output_dir=args.output_dir,
        params=_build_runtime_params(),
        progress_callback=_emit_progress,
    )
    _print_summary(summary)

    if summary.get("error_count") and not summary.get("cached_count"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
