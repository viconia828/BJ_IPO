from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
import data_fetcher
import param_tuning


def _parse_csv_codes(raw_value: str | None) -> list[str] | None:
    if raw_value is None:
        return None
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _parse_scalar(raw_value: str) -> Any:
    return config_loader._parse_scalar(raw_value)


def _parse_candidate_values(raw_value: str | None) -> list[Any]:
    if not raw_value:
        return []
    return [_parse_scalar(item.strip()) for item in raw_value.split(",") if item.strip()]


def _parse_manual_override(raw_value: str) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for part in raw_value.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"手动候选格式非法，缺少 '='：{chunk}")
        key, value = chunk.split("=", 1)
        param_name = key.strip()
        if not param_name:
            raise ValueError(f"手动候选参数名为空：{chunk}")
        overrides[param_name] = _parse_scalar(value.strip())
    if not overrides:
        raise ValueError("手动候选不能为空")
    return overrides


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


def _review_progress(index: int, total: int, spec: dict[str, object]) -> None:
    if index != 1 and index != total and index % 10 != 0:
        return
    name = str(spec.get("name") or "candidate")
    print(f"[{index}/{total}] reviewing {name}", flush=True)


def _observe_progress(index: int, total: int, spec: dict[str, object]) -> None:
    if index != 1 and index != total and index % 10 != 0:
        return
    name = str(spec.get("name") or "candidate")
    print(f"[{index}/{total}] observing {name}", flush=True)


def _build_manual_candidate_payload(args: argparse.Namespace, params: dict[str, Any]) -> dict[str, Any]:
    if args.candidate_file:
        return param_tuning.load_manual_candidate_payload(
            args.candidate_file,
            name=args.manual_name,
            description=args.manual_description or "",
            base_params=params,
        )

    override_groups = [_parse_manual_override(raw_value) for raw_value in args.candidate or []]
    return param_tuning.build_manual_candidate_payload(
        name=args.manual_name,
        description=args.manual_description or "",
        param_name=args.param_name,
        values=_parse_candidate_values(args.candidate_values),
        override_groups=override_groups,
        base_params=params,
    )


def _resolve_output_dir(args: argparse.Namespace) -> str:
    if args.output_dir:
        return args.output_dir
    if args.mode == "observe":
        return str(param_tuning.DEFAULT_OBSERVE_OUTPUT_DIR)
    return str(param_tuning.DEFAULT_OUTPUT_DIR)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _should_auto_refresh_dataset(
    args: argparse.Namespace,
    dataset_path: Path,
    sample_codes: list[str] | None,
) -> bool:
    if os.environ.get("BSE_TUNING_NO_AUTO_REFRESH") == "1":
        return False
    if args.no_auto_refresh_dataset:
        return False
    if args.mode not in {"offline", "observe"}:
        return False
    if sample_codes is not None:
        return True
    return _same_path(dataset_path, Path(param_tuning.DEFAULT_DATASET_PATH))


def _build_and_save_dataset(
    params: dict[str, Any],
    dataset_path: Path,
    *,
    months: int,
    sample_codes: list[str] | None,
    page_size: int,
) -> dict[str, Any]:
    dataset = param_tuning.build_replay_dataset(
        params,
        months=months,
        sample_codes=sample_codes,
        page_size=page_size,
    )
    param_tuning.save_replay_dataset(dataset, dataset_path)
    return dataset


def _load_or_refresh_dataset(
    args: argparse.Namespace,
    params: dict[str, Any],
    dataset_path: Path,
    sample_codes: list[str] | None,
) -> dict[str, Any]:
    if args.rebuild_dataset or not dataset_path.exists():
        print("开始构建历史回放数据集...", flush=True)
        return _build_and_save_dataset(
            params,
            dataset_path,
            months=args.months,
            sample_codes=sample_codes,
            page_size=args.page_size,
        )

    dataset = param_tuning.load_replay_dataset(dataset_path)
    if not _should_auto_refresh_dataset(args, dataset_path, sample_codes):
        return dataset

    local_codes = sample_codes if sample_codes is not None else param_tuning.discover_local_sample_codes()
    sync_status = param_tuning.inspect_replay_dataset_sync(
        dataset,
        local_sample_codes=local_codes,
        months=args.months,
    )
    if not sync_status["needs_refresh"]:
        print(
            "回放数据集已同步本地首日分时走势：CSV {csv_count} 个，可用样本 {sample_count} 个。".format(
                csv_count=len(sync_status.get("local_codes") or []),
                sample_count=dataset.get("available_count", 0),
            ),
            flush=True,
        )
        return dataset

    print("检测到本地首日分时走势与回放数据集不一致，开始自动更新数据集...", flush=True)
    for reason in sync_status.get("reasons") or []:
        print(f"- {reason}", flush=True)
    try:
        return _build_and_save_dataset(
            params,
            dataset_path,
            months=args.months,
            sample_codes=local_codes,
            page_size=args.page_size,
        )
    except Exception as exc:
        print(f"自动更新回放数据集失败：{exc}", flush=True)
        print("本次将继续使用旧回放数据集；训练集/验证集暂不包含上述新增 CSV。", flush=True)
        print("网络恢复后重新运行手动调参即可再次自动同步；如需强制刷新，可加 --rebuild-dataset。", flush=True)
        return dataset


def _resolve_params_file(argv: list[str] | None) -> str:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--params-file", default=str(ROOT_DIR / "策略参数.txt"))
    known_args, _ = bootstrap.parse_known_args(argv)
    return str(known_args.params_file)


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    has_param_values = bool(args.param_name or args.candidate_values)
    has_group_candidates = bool(args.candidate)
    has_candidate_file = bool(args.candidate_file)
    has_manual_inputs = has_param_values or has_group_candidates or has_candidate_file

    if args.mode == "search":
        if has_manual_inputs:
            parser.error("--mode search 不支持 --param-name / --candidate-values / --candidate / --candidate-file")
        return

    if args.grid_file:
        parser.error("--grid-file 仅用于 --mode search；手动模式请改用 --candidate-file")
    if args.mode == "offline" and args.codes:
        parser.error("--codes 仅用于 --mode observe")

    if bool(args.param_name) != bool(args.candidate_values):
        parser.error("--param-name 与 --candidate-values 需要同时提供")

    input_modes = sum([has_param_values, has_group_candidates, has_candidate_file])
    if input_modes != 1:
        parser.error("--mode offline/observe 需要且只能使用一种手动候选输入方式")

    if not has_manual_inputs:
        parser.error("手动模式缺少候选参数输入")


def build_parser(params_file: str, tuning_settings: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="北交所新股估值调参工具")
    parser.add_argument(
        "--mode",
        default="search",
        choices=["search", "offline", "observe"],
        help="search=按阶段批量调参，offline=手动候选离线复核，observe=手动候选 replay 观察",
    )
    parser.add_argument("--params-file", default=params_file, help="参数文件路径")
    parser.add_argument("--dataset-path", default=str(param_tuning.DEFAULT_DATASET_PATH), help="回放数据集路径")
    parser.add_argument("--rebuild-dataset", action="store_true", help="重新构建历史回放数据集")
    parser.add_argument(
        "--no-auto-refresh-dataset",
        action="store_true",
        help="手动调参时不根据首日分时走势 CSV 自动同步回放数据集",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=int(tuning_settings["tuning_replay_months"]),
        help="构建数据集时回看最近多少个月；默认取策略参数.txt 的 tuning_replay_months",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=int(tuning_settings["tuning_page_size"]),
        help="历史样本抓取分页大小；默认取策略参数.txt 的 tuning_page_size",
    )
    parser.add_argument("--sample-codes", help="限定构建数据集时纳入的样本代码，逗号分隔；默认自动发现本地分时 CSV")
    parser.add_argument("--stage", default="quick_method2", choices=param_tuning.list_stage_names(), help="内置调参阶段")
    parser.add_argument("--grid-file", help="search 模式下自定义候选参数文件，支持 list[dict] 或 dict[str, list]")
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
    parser.add_argument(
        "--top-n",
        type=int,
        default=int(tuning_settings["tuning_top_n"]),
        help="search 模式输出 Top N 候选；默认取策略参数.txt 的 tuning_top_n",
    )
    parser.add_argument("--output-dir", help="报告输出目录；默认 search/offline 输出到 调参，observe 输出到 观察期")
    parser.add_argument("--manual-name", help="手动候选任务名；影响报告标题与文件名")
    parser.add_argument("--manual-description", help="手动候选说明")
    parser.add_argument("--param-name", help="手动候选参数名，例如 small_cap_premium")
    parser.add_argument("--candidate-values", help="单参数候选值列表，逗号分隔，例如 0.10,0.15")
    parser.add_argument(
        "--candidate",
        action="append",
        help="手动候选组，可重复传入；格式如 price_range_width=0.12,float_size_threshold=1500",
    )
    parser.add_argument(
        "--candidate-file",
        help="手动候选文件；可为候选参数集 dict/list，或已命名的 candidate payload",
    )
    parser.add_argument("--codes", help="observe 模式下限定 replay 观察代码，逗号分隔；默认观察数据集内全部代码")
    return parser


def main(argv: list[str] | None = None) -> int:
    params_file = _resolve_params_file(argv)
    params = config_loader.load_params(params_file)
    tuning_settings = config_loader.get_tuning_runtime_settings(params)
    parser = build_parser(params_file, tuning_settings)
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    dataset_path = Path(args.dataset_path)
    sample_codes = _parse_csv_codes(args.sample_codes)
    output_dir = _resolve_output_dir(args)

    try:
        dataset = _load_or_refresh_dataset(args, params, dataset_path, sample_codes)
    except data_fetcher.DataFetcherError as exc:
        print(f"数据集构建失败：{exc}", flush=True)
        return 1
    print(f"数据集样本数：{dataset.get('available_count', 0)}", flush=True)
    train_codes, validation_codes = param_tuning.split_target_codes(
        dataset,
        train_ratio=args.train_ratio,
        min_train_samples=args.min_train_samples,
    )
    print(f"训练集样本数：{len(train_codes)}，验证集样本数：{len(validation_codes)}", flush=True)

    if args.mode == "search":
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
            output_dir=output_dir,
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

    candidate_payload = _build_manual_candidate_payload(args, params)

    if args.mode == "offline":
        print(
            "开始手动候选离线复核：{name}，候选数 {count}".format(
                name=candidate_payload.get("name") or "manual_candidates",
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
            progress_callback=_review_progress,
        )
        json_path, md_path = param_tuning.write_candidate_review_outputs(
            dataset,
            review_result,
            output_dir=output_dir,
            review_name=args.manual_name or str(candidate_payload.get("name") or ""),
        )

        best_candidate = review_result.get("best_candidate") or {}
        scope = review_result.get("selection_metrics_scope") or "validation"
        metrics = best_candidate.get(f"{scope}_metrics") or {}

        print(f"手动候选离线复核完成：{candidate_payload.get('name') or 'manual_candidates'}")
        print(f"数据集：{dataset_path}")
        print(f"评分依据：{scope} 集")
        print(f"最佳候选：{best_candidate.get('name') or '无'}")
        print(f"MAE(涨幅)：{metrics.get('mae_change_pct')}")
        print(f"RMSE(涨幅)：{metrics.get('rmse_change_pct')}")
        print(f"JSON 报告：{json_path}")
        print(f"Markdown 报告：{md_path}")
        return 0

    target_codes = _parse_csv_codes(args.codes)
    print(
        "开始手动候选 replay 观察：{name}，候选数 {count}".format(
            name=candidate_payload.get("name") or "manual_observe",
            count=len(candidate_payload.get("candidates") or []),
        ),
        flush=True,
    )
    observe_result = param_tuning.observe_candidate_sets(
        dataset,
        params,
        candidate_payload,
        target_codes=target_codes,
        progress_callback=_observe_progress,
    )
    json_path, md_path = param_tuning.write_manual_observe_outputs(
        dataset,
        observe_result,
        output_dir=output_dir,
        observe_name=args.manual_name or str(candidate_payload.get("name") or ""),
    )

    best_candidate = observe_result.get("best_candidate") or {}
    metrics = best_candidate.get("observe_metrics") or {}

    print(f"手动候选 replay 观察完成：{candidate_payload.get('name') or 'manual_observe'}")
    print(f"数据集：{dataset_path}")
    print(f"观察代码数：{len(observe_result.get('target_codes') or [])}")
    print(f"最佳候选：{best_candidate.get('name') or '无'}")
    print(f"MAE(涨幅)：{metrics.get('mae_change_pct')}")
    print(f"RMSE(涨幅)：{metrics.get('rmse_change_pct')}")
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
