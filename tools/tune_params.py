from __future__ import annotations

import argparse
from pathlib import Path
import sys


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
import param_tuning


def _parse_sample_codes(raw_value: str | None) -> list[str] | None:
    if raw_value is None:
        return None
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _render_best_candidate(best: dict[str, object]) -> str:
    overrides = dict(best.get("overrides") or {})
    if best.get("label") == "baseline" and not overrides:
        return "baseline（当前参数）"
    lines = []
    for key, value in overrides.items():
        lines.append(f"{key}={value}")
    return ", ".join(lines) if lines else "候选参数"


def _progress(index: int, total: int, spec: dict[str, object]) -> None:
    if index != 1 and index != total and index % 25 != 0:
        return
    label = "baseline" if spec.get("label") == "baseline" else "candidate"
    print(f"[{index}/{total}] evaluating {label}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="北交所新股估值离线调参工具（当前聚焦方法二）")
    parser.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"), help="参数文件路径")
    parser.add_argument("--dataset-path", default=str(param_tuning.DEFAULT_DATASET_PATH), help="回放数据集路径")
    parser.add_argument("--rebuild-dataset", action="store_true", help="重新构建历史回放数据集")
    parser.add_argument("--months", type=int, default=12, help="构建数据集时回看最近多少个月")
    parser.add_argument("--page-size", type=int, default=100, help="历史样本抓取分页大小")
    parser.add_argument("--sample-codes", help="限定样本代码，逗号分隔；默认自动发现本地分时 CSV")
    parser.add_argument("--stage", default="quick_method2", choices=param_tuning.list_stage_names(), help="内置调参阶段")
    parser.add_argument("--grid-file", help="自定义候选参数文件，支持 list[dict] 或 dict[str, list]")
    parser.add_argument("--train-ratio", type=float, default=0.7, help="时间切分训练集比例")
    parser.add_argument("--min-train-samples", type=int, default=8, help="最小训练样本数")
    parser.add_argument("--top-n", type=int, default=10, help="输出 Top N 候选")
    parser.add_argument("--output-dir", default=str(param_tuning.DEFAULT_OUTPUT_DIR), help="报告输出目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    params = config_loader.load_params(args.params_file)
    dataset_path = Path(args.dataset_path)
    sample_codes = _parse_sample_codes(args.sample_codes)

    if args.rebuild_dataset or not dataset_path.exists():
        print("开始构建历史回放数据集...", flush=True)
        dataset = param_tuning.build_replay_dataset(
            params,
            months=args.months,
            sample_codes=sample_codes,
            page_size=args.page_size,
        )
        param_tuning.save_replay_dataset(dataset, dataset_path)
    else:
        dataset = param_tuning.load_replay_dataset(dataset_path)
    print(f"数据集样本数：{dataset.get('available_count', 0)}", flush=True)

    if args.grid_file:
        stage_name = Path(args.grid_file).stem
        candidates = param_tuning.load_search_candidates(args.grid_file)
    else:
        stage_name = args.stage
        candidates = param_tuning.build_stage_candidates(args.stage)
    print(f"开始调参：{stage_name}，候选数 {len(candidates)}（另含 baseline）", flush=True)

    ranking = param_tuning.rank_param_candidates(
        dataset,
        params,
        candidates,
        train_ratio=args.train_ratio,
        min_train_samples=args.min_train_samples,
        top_n=args.top_n,
        progress_callback=_progress,
    )
    json_path, md_path = param_tuning.write_search_outputs(
        dataset,
        ranking,
        output_dir=args.output_dir,
        stage_name=stage_name,
    )

    best = ranking.get("best") or {}
    scope = ranking.get("selection_metrics_scope") or "validation"
    metrics = best.get(f"{scope}_metrics") or {}

    print(f"离线调参完成：{stage_name}")
    print(f"数据集：{dataset_path}")
    print(f"样本数：{dataset.get('available_count', 0)}")
    print(f"评分依据：{scope} 集")
    print(f"最佳候选：{_render_best_candidate(best)}")
    print(f"MAE(涨幅)：{metrics.get('mae_change_pct')}")
    print(f"RMSE(涨幅)：{metrics.get('rmse_change_pct')}")
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
