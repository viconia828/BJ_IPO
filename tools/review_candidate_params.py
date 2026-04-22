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


def _progress(index: int, total: int, spec: dict[str, object]) -> None:
    name = str(spec.get("name") or "candidate")
    print(f"[{index}/{total}] reviewing {name}", flush=True)


def _resolve_params_file(argv: list[str] | None) -> str:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"))
    known_args, _ = bootstrap.parse_known_args(argv)
    return str(known_args.params_file)


def build_parser(params_file: str, tuning_settings: dict[str, object]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="北交所新股估值候选参数集综合回放复核工具")
    parser.add_argument("--params-file", default=params_file, help="参数文件路径")
    parser.add_argument("--dataset-path", default=str(param_tuning.DEFAULT_DATASET_PATH), help="历史回放数据集路径")
    parser.add_argument("--candidate-file", required=True, help="候选参数集文件路径")
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=float(tuning_settings["tuning_train_ratio"]),
        help="时间切分训练集比例；默认取策略参数.txt 的 tuning_train_ratio",
    )
    parser.add_argument(
        "--min-train-samples",
        type=int,
        default=int(tuning_settings["tuning_min_train_samples"]),
        help="最小训练样本数；默认取策略参数.txt 的 tuning_min_train_samples",
    )
    parser.add_argument("--output-dir", default=str(param_tuning.DEFAULT_OUTPUT_DIR), help="报告输出目录")
    parser.add_argument("--review-name", help="输出文件名后缀，默认取候选集名称")
    return parser


def main(argv: list[str] | None = None) -> int:
    params_file = _resolve_params_file(argv)
    params = config_loader.load_params(params_file)
    tuning_settings = config_loader.get_tuning_runtime_settings(params)
    parser = build_parser(params_file, tuning_settings)
    args = parser.parse_args(argv)
    dataset = param_tuning.load_replay_dataset(args.dataset_path)
    candidate_payload = param_tuning.load_named_candidate_sets(args.candidate_file)

    print(f"数据集样本数：{dataset.get('available_count', 0)}", flush=True)
    print(
        "开始复核候选集：{name}，候选数 {count}".format(
            name=candidate_payload.get("name") or Path(args.candidate_file).stem,
            count=len(candidate_payload.get("candidates") or []),
        ),
        flush=True,
    )

    review_result = param_tuning.review_candidate_sets(
        dataset,
        params,
        candidate_payload,
        train_ratio=args.train_ratio,
        min_train_samples=args.min_train_samples,
        progress_callback=_progress,
    )
    json_path, md_path = param_tuning.write_candidate_review_outputs(
        dataset,
        review_result,
        output_dir=args.output_dir,
        review_name=args.review_name or str(candidate_payload.get("name") or ""),
    )

    best_candidate = review_result.get("best_candidate") or {}
    metrics_scope = review_result.get("selection_metrics_scope") or "validation"
    metrics = best_candidate.get(f"{metrics_scope}_metrics") or {}

    print(f"候选集复核完成：{candidate_payload.get('name') or Path(args.candidate_file).stem}")
    print(f"最佳候选：{best_candidate.get('name')}")
    print(f"MAE(涨幅)：{metrics.get('mae_change_pct')}")
    print(f"RMSE(涨幅)：{metrics.get('rmse_change_pct')}")
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
