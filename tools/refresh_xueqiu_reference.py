from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT_DIR / "xueqiu"
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shadow = _load_module("run_valuation_shadow_pipeline", ROOT_DIR / "tools" / "run_valuation_shadow_pipeline.py")


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
        output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        output_path = Path(output_lines[-1]) if output_lines else None
        payload = (
            {"outputs": {"json": str(output_path), "markdown": ""}}
            if output_path is not None and output_path.is_file() and output_path.suffix.lower() == ".json"
            else None
        )
    outputs = (payload or {}).get("outputs") or {}
    report_path = outputs.get("markdown") or outputs.get("json") or ""
    suffix = f"；报告：{report_path}" if report_path else ""
    print(f"[{name}] 完成{suffix}", flush=True)
    return {"name": name, "command": command, "returncode": completed.returncode, "payload": payload}


def _manual_files(input_dir: Path) -> list[Path]:
    return sorted(input_dir.glob("*.mhtml")) + sorted(input_dir.glob("*.txt"))


def _write_summary(payload: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = output_dir / f"xueqiu_reference_refresh_{timestamp}.json"
    md_path = output_dir / f"xueqiu_reference_refresh_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 雪球手工参考刷新",
        "",
        f"> 生成时间：{payload['generated_at']}",
        f"> 状态：{payload['status']}",
        f"> 输入目录：`{payload['inputs']['input_dir']}`",
        f"> 手工文件：{payload['summary']['manual_file_count']} 个",
        "",
        "## 执行结果",
        "",
    ]
    if payload["steps"]:
        for step in payload["steps"]:
            outputs = ((step.get("payload") or {}).get("outputs") or {}) if isinstance(step.get("payload"), dict) else {}
            lines.append(f"- {step['name']}：完成；JSON `{outputs.get('json', '')}`；Markdown `{outputs.get('markdown', '')}`")
    else:
        lines.append("- 未发现 `.mhtml` 或 `.txt`；未导入、未联网。")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def run_refresh(args: argparse.Namespace) -> dict[str, Any]:
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    files = _manual_files(input_dir)
    scan_report = shadow.resolve_scan_report(args.scan_report) if files else None
    payload: dict[str, Any] = {
        "schema": "xueqiu_manual_reference_refresh_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "waiting_for_files" if not files else "running",
        "inputs": {
            "input_dir": str(input_dir),
            "scan_report": str(scan_report) if scan_report else "",
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "intraday_dir": str(Path(args.intraday_dir)),
            "network_collection_enabled": False,
        },
        "summary": {
            "manual_file_count": len(files),
            "mhtml_count": sum(path.suffix.lower() == ".mhtml" for path in files),
            "text_count": sum(path.suffix.lower() == ".txt" for path in files),
        },
        "steps": [],
    }
    if not files:
        print(f"未发现手工雪球文章：{input_dir}", flush=True)
        print("请把 .mhtml 或 .txt 文件放入该目录后重新运行第 3 项。", flush=True)
        print("本次不会访问或下载雪球网页。", flush=True)
        payload["outputs"] = _write_summary(payload, output_dir)
        return payload

    assert scan_report is not None

    python = sys.executable
    steps: list[dict[str, Any]] = payload["steps"]
    steps.append(
        _run_step(
            "导入手工 MHTML/TXT",
            [python, "-X", "utf8", str(ROOT_DIR / "tools" / "import_manual_xueqiu_mhtml.py"), "--input-dir", str(input_dir), "--output-dir", str(output_dir)],
        )
    )
    steps.append(_run_step("抽取并验证作者区间", [python, "-X", "utf8", str(ROOT_DIR / "tools" / "validate_xueqiu_author_ranges.py"), "--dataset", str(args.dataset), "--intraday-dir", str(args.intraday_dir), "--output-dir", str(output_dir)]))
    steps.append(_run_step("审计本地样本覆盖", [python, "-X", "utf8", str(ROOT_DIR / "tools" / "audit_xueqiu_local_sample_coverage.py"), "--dataset", str(args.dataset), "--intraday-dir", str(args.intraday_dir), "--output-dir", str(output_dir)]))
    steps.append(_run_step("导出作者覆盖表", [python, "-X", "utf8", str(ROOT_DIR / "tools" / "export_xueqiu_author_coverage_table.py"), "--dataset", str(args.dataset), "--intraday-dir", str(args.intraday_dir), "--output-dir", str(output_dir)]))
    score_step = _run_step(
        "刷新 author-rule score",
        [
            python,
            "-X",
            "utf8",
            str(ROOT_DIR / "tools" / "evaluate_xueqiu_author_rule_score.py"),
            "--dataset",
            str(args.dataset),
            "--scan-report",
            str(scan_report),
            "--intraday-dir",
            str(args.intraday_dir),
            "--output-dir",
            str(output_dir),
        ],
    )
    steps.append(score_step)
    author_report_text = str((((score_step.get("payload") or {}).get("outputs") or {}).get("json") or "")).strip()
    author_report = Path(author_report_text) if author_report_text else shadow.latest_author_score(output_dir)
    if author_report is None or not author_report.is_file():
        raise FileNotFoundError("author-rule score report was not generated")

    common = ["--dataset", str(args.dataset), "--params", str(args.params), "--scan-report", str(scan_report), "--author-score-report", str(author_report), "--output-dir", str(output_dir)]
    for target in ("scan_sample", "author_scored", "all_actual"):
        steps.append(_run_step(f"作者/模型融合：{target}", [python, "-X", "utf8", str(ROOT_DIR / "tools" / "evaluate_xueqiu_author_model_blend.py"), *common, "--target", target]))
        steps.append(_run_step(f"作者逻辑本地蒸馏：{target}", [python, "-X", "utf8", str(ROOT_DIR / "tools" / "analyze_xueqiu_author_logic_distillation.py"), *common, "--target", target]))

    steps.append(_run_step("本地 proxy：author_scored", [python, "-X", "utf8", str(ROOT_DIR / "tools" / "evaluate_local_proxy_strategy.py"), *common, "--target", "author_scored"]))
    shadow_payload = shadow.run_pipeline(
        argparse.Namespace(
            scan_report=str(scan_report),
            dataset=str(args.dataset),
            params=str(args.params),
            author_score_report=str(author_report),
            intraday_dir=str(args.intraday_dir),
            output_dir=str(output_dir),
        )
    )
    steps.append(
        {
            "name": "本地估值影子流水线",
            "command": ["internal", "run_valuation_shadow_pipeline.run_pipeline"],
            "returncode": 0,
            "payload": shadow_payload,
        }
    )
    payload["status"] = "completed"
    payload["summary"]["author_score_report"] = str(author_report)
    payload["summary"]["step_count"] = len(steps)
    payload["outputs"] = _write_summary(payload, output_dir)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh Xueqiu reference learning from manually saved MHTML/TXT only.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--scan-report", default="")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> int:
    try:
        payload = run_refresh(build_parser().parse_args())
    except Exception as exc:
        print(f"刷新雪球参考失败：{exc}", file=sys.stderr)
        return 1
    if payload["status"] == "waiting_for_files":
        return 0
    print("")
    print(f"雪球参考刷新完成：处理 {payload['summary']['manual_file_count']} 个手工文件。")
    print(f"汇总报告：{payload['outputs']['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
