from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
AUTO_CONTEXT_LATEST = ROOT_DIR / "调参" / "valuation_auto_shadow_context_latest.json"


def resolve_scan_report(raw_path: str | Path | None = None) -> Path:
    if raw_path:
        path = Path(raw_path)
        if path.exists():
            return path
        raise FileNotFoundError(f"scan/auto context not found: {path}")
    if AUTO_CONTEXT_LATEST.exists():
        return AUTO_CONTEXT_LATEST
    candidates = sorted(
        (ROOT_DIR / "调参").glob("valuation_hit_rate_scan_*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError("no valuation scan report or auto shadow context found")
    return candidates[-1]


def latest_author_score(output_dir: Path) -> Path | None:
    candidates = sorted(output_dir.glob("xueqiu_author_rule_score_*.json"), key=lambda path: path.stat().st_mtime)
    return candidates[-1] if candidates else None


def _run_step(name: str, command: list[str]) -> dict[str, Any]:
    print("")
    print(f"[{name}] 开始", flush=True)
    completed = subprocess.run(command, cwd=ROOT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout.rstrip(), flush=True)
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr, flush=True)
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}")
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr, flush=True)
    payload = None
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        payload = None
    outputs = (payload or {}).get("outputs") or {}
    report_path = outputs.get("markdown") or outputs.get("json") or ""
    suffix = f"；报告：{report_path}" if report_path else ""
    print(f"[{name}] 完成{suffix}", flush=True)
    return {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "payload": payload,
    }


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    scan_report = resolve_scan_report(args.scan_report)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    author_report = Path(args.author_score_report) if args.author_score_report else latest_author_score(output_dir)
    python = sys.executable
    common = ["--dataset", str(args.dataset), "--params", str(args.params), "--scan-report", str(scan_report), "--output-dir", str(output_dir)]
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []

    if author_report and author_report.exists():
        for target in ("scan_sample", "all_actual"):
            steps.append(
                _run_step(
                    f"本地 proxy：{target}",
                    [
                        python,
                        "-X",
                        "utf8",
                        str(ROOT_DIR / "tools" / "evaluate_local_proxy_strategy.py"),
                        *common,
                        "--author-score-report",
                        str(author_report),
                        "--target",
                        target,
                    ],
                )
            )
    else:
        warnings.append("未找到 author-rule score，跳过 local proxy 榜单；regime-break 与盘中指导仍继续。")
        print(f"提示：{warnings[-1]}", flush=True)

    steps.append(
        _run_step(
            "Regime-break 兜底",
            [
                python,
                "-X",
                "utf8",
                str(ROOT_DIR / "tools" / "evaluate_regime_break_fallback.py"),
                *common,
                "--intraday-dir",
                str(args.intraday_dir),
            ],
        )
    )
    steps.append(
        _run_step(
            "估值与盘中指导",
            [
                python,
                "-X",
                "utf8",
                str(ROOT_DIR / "tools" / "evaluate_intraday_valuation_guidance.py"),
                *common,
                "--intraday-dir",
                str(args.intraday_dir),
            ],
        )
    )

    payload = {
        "schema": "valuation_shadow_pipeline_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "scan_report": str(scan_report),
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "author_score_report": str(author_report) if author_report else "",
            "intraday_dir": str(Path(args.intraday_dir)),
        },
        "warnings": warnings,
        "steps": steps,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = output_dir / f"valuation_shadow_pipeline_{timestamp}.json"
    md_path = output_dir / f"valuation_shadow_pipeline_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 估值影子学习流水线",
        "",
        f"> 生成时间：{payload['generated_at']}",
        f"> 调参上下文：`{scan_report}`",
        "",
        "## 执行结果",
        "",
    ]
    for step in steps:
        outputs = ((step.get("payload") or {}).get("outputs") or {}) if isinstance(step.get("payload"), dict) else {}
        lines.append(f"- {step['name']}：完成；JSON `{outputs.get('json', '')}`；Markdown `{outputs.get('markdown', '')}`")
    for warning in warnings:
        lines.append(f"- 提示：{warning}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh local valuation proxy, regime-break, and intraday shadow reports.")
    parser.add_argument("--scan-report", default="")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--author-score-report", default="")
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> int:
    try:
        payload = run_pipeline(build_parser().parse_args())
    except Exception as exc:
        print(f"估值影子学习刷新失败：{exc}", file=sys.stderr)
        return 1
    print("")
    print("估值影子报告刷新完成。")
    print(f"汇总报告：{payload['outputs']['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
