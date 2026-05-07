from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
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


def _auto_progress(index: int, total: int, spec: dict[str, object]) -> None:
    if index != 1 and index != total and index % 10 != 0:
        return
    group = str(spec.get("group") or "auto")
    stage_level = spec.get("stage_level")
    stage_text = f"第{stage_level}轮" if stage_level else "自动调参"
    print(f"[{index}/{total}] {stage_text}搜索中：{group}", flush=True)


def _dataset_progress(index: int, total: int, spec: dict[str, object]) -> None:
    status = str(spec.get("status") or "")
    code = str(spec.get("code") or "")
    if status == "built":
        text = "新建回放条目"
    elif status == "cache_hit":
        text = "读取单样本缓存"
    elif status == "reused_dataset":
        text = "复用旧数据集条目"
    elif status == "upgraded_dataset":
        text = "补齐旧条目均价"
    elif status == "skipped":
        text = "跳过"
    else:
        text = "处理"

    if status in {"built", "upgraded_dataset", "skipped"} or index in {1, total} or index % 10 == 0:
        print(f"[{index}/{total}] 回放数据集同步：{text} {code}", flush=True)


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
    if args.mode not in {"offline", "observe", "auto"}:
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
    use_item_cache: bool = True,
    existing_dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dataset = param_tuning.build_replay_dataset(
        params,
        months=months,
        sample_codes=sample_codes,
        page_size=page_size,
        use_item_cache=use_item_cache,
        existing_dataset=existing_dataset,
        progress_callback=_dataset_progress,
    )
    param_tuning.save_replay_dataset(dataset, dataset_path)
    return dataset


def _load_or_refresh_dataset(
    args: argparse.Namespace,
    params: dict[str, Any],
    dataset_path: Path,
    sample_codes: list[str] | None,
) -> dict[str, Any]:
    if args.rebuild_dataset:
        print("开始构建历史回放数据集...", flush=True)
        return _build_and_save_dataset(
            params,
            dataset_path,
            months=args.months,
            sample_codes=sample_codes,
            page_size=args.page_size,
            use_item_cache=False,
        )

    if not dataset_path.exists():
        print("开始构建历史回放数据集...", flush=True)
        print("提示：将优先读取 data\\offline_tuning\\replay_items 下的单样本缓存。", flush=True)
        return _build_and_save_dataset(
            params,
            dataset_path,
            months=args.months,
            sample_codes=sample_codes,
            page_size=args.page_size,
            use_item_cache=True,
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
    if args.mode == "auto":
        print("提示：这是自动调参前的数据集同步步骤；如外部数据源不可用，会自动回退到旧回放数据集继续调参。", flush=True)
    for reason in sync_status.get("reasons") or []:
        print(f"- {reason}", flush=True)
    try:
        return _build_and_save_dataset(
            params,
            dataset_path,
            months=args.months,
            sample_codes=local_codes,
            page_size=args.page_size,
            use_item_cache=True,
            existing_dataset=dataset,
        )
    except Exception as exc:
        print(f"自动更新回放数据集失败：{exc}", flush=True)
        print("本次将继续使用旧回放数据集；训练集/验证集暂不包含上述新增 CSV。", flush=True)
        print("网络恢复后重新运行调参入口即可再次自动同步；如需强制刷新，可加 --rebuild-dataset。", flush=True)
        if args.mode == "auto":
            print("继续进入自动调参搜索...", flush=True)
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

    if args.mode == "auto":
        if has_manual_inputs:
            parser.error("--mode auto 不支持手动候选输入")
        if args.grid_file:
            parser.error("--grid-file 仅用于 --mode search")
        if args.codes:
            parser.error("--codes 仅用于 --mode observe")
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
        choices=["search", "offline", "observe", "auto"],
        help="search=按阶段批量调参，offline=手动候选离线复核，observe=手动候选 replay 观察，auto=自动调参并确认写入",
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
    parser.add_argument(
        "--auto-record-path",
        default=str(param_tuning.DEFAULT_AUTO_TUNING_RECORD_PATH),
        help="自动调参被接受后的记录文件；默认写入根目录 自动调参记录.txt",
    )
    parser.add_argument("--auto-max-passes", type=int, default=1, help="每轮自动调参内的模块循环次数，默认 1。")
    parser.add_argument("--auto-max-refine-stages", type=int, default=5, help="自动调参最多细化轮数，默认 5。")
    parser.add_argument(
        "--auto-stage-time-limit-seconds",
        type=float,
        default=param_tuning.AUTO_TUNE_STAGE_TIME_LIMIT_SECONDS,
        help="每轮自动调参时间上限，默认 180 秒。",
    )
    parser.add_argument(
        "--auto-stage-candidate-limit",
        type=int,
        default=param_tuning.AUTO_TUNE_STAGE_CANDIDATE_LIMIT,
        help="每轮自动调参候选评估上限，默认 650。",
    )
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


def _render_param_file_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _write_param_updates(params_file: str | Path, updates: dict[str, Any]) -> Path:
    path = Path(params_file)
    text = path.read_text(encoding="utf-8-sig")
    for key, value in updates.items():
        rendered = _render_param_file_value(value)
        pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?$)")
        if pattern.search(text):
            text = pattern.sub(lambda match: f"{match.group(1)}{rendered}{match.group(3)}", text, count=1)
        else:
            industry_section = re.search(r"(?m)^\[industry_mapping\]\s*$", text)
            insert_line = f"{key} = {rendered}\n"
            if industry_section:
                text = text[: industry_section.start()] + insert_line + text[industry_section.start() :]
            else:
                if not text.endswith("\n"):
                    text += "\n"
                text += insert_line
    path.write_text(text, encoding="utf-8")
    return path


def _score_text(score: dict[str, Any]) -> str:
    return (
        "排序分={score}，近期加权命中率={hit}，近30日权重={recent_share}，加权MAE={mae}，手动宽度诊断扣分={width_penalty}".format(
            score=param_tuning._fmt_metric(score.get("auto_score")),
            hit=param_tuning._fmt_metric(score.get("weighted_interval_hit_rate")),
            recent_share=param_tuning._fmt_metric(score.get("recent_weight_share")),
            mae=param_tuning._fmt_metric(score.get("weighted_mae_change_pct")),
            width_penalty=param_tuning._fmt_metric(score.get("width_diagnostic_penalty")),
        )
    )


def _prompt_auto_stage_action(stage_level: int, next_stage: int, has_next_stage: bool, can_accept: bool) -> str:
    print("")
    print(f"第 {stage_level} 轮后请选择下一步：")
    if can_accept:
        print("1. 接受本轮累计最优参数，并写入 策略参数.txt")
    else:
        print("1. 当前未产生可写入的参数修改")
    if has_next_stage:
        print(f"2. 进入第 {next_stage} 轮，围绕当前最优参数继续细步长调参")
    print("3. 暂不写入并退出")
    default_choice = "2" if has_next_stage else "3"
    try:
        raw_value = input(f"请输入选项 [默认 {default_choice}]：").strip()
    except EOFError:
        return "cancel"

    choice = raw_value or default_choice
    choice_lower = choice.lower()
    if choice in {"1"} or choice_lower in {"y", "yes", "是", "接受"}:
        if can_accept:
            return "accept"
        print("当前没有可写入的自动调参修改。")
        return "continue" if has_next_stage else "cancel"
    if has_next_stage and (choice in {"2"} or choice_lower in {"继续", "next"}):
        return "continue"
    return "cancel"


def _params_with_overrides(base_params: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_params)
    merged.update(overrides)
    return merged


def _print_auto_result_context(result: dict[str, Any], params: dict[str, Any]) -> None:
    baseline_score = ((result.get("baseline") or {}).get("auto_score") or {})
    best_score = ((result.get("best") or {}).get("auto_score") or {})
    overrides = dict(result.get("changed_overrides") or {})

    print(
        "方法二样本池截取窗口：{sample_days} 天；近期权重基准日：{date}，评分权重衰减窗口 {lookback_days} 天，最近 {floor_days} 天样本总权重至少 {share}。".format(
            sample_days=result.get("sample_window_days"),
            date=result.get("reference_date"),
            lookback_days=result.get("lookback_days"),
            floor_days=result.get("recent_floor_days"),
            share=param_tuning._fmt_metric(result.get("recent_min_total_weight")),
        )
    )
    print(f"baseline：{_score_text(baseline_score)}")
    print(f"当前累计最优：{_score_text(best_score)}")

    if not overrides:
        print("本轮暂未找到优于当前参数的自动修改方案。")
        return

    print("")
    print("当前累计建议修改的参数：")
    for line in param_tuning.build_auto_tune_change_lines(params, overrides):
        print(f"- {line}")


def _accept_auto_result(args: argparse.Namespace, params: dict[str, Any], result: dict[str, Any]) -> int:
    overrides = dict(result.get("changed_overrides") or {})
    if not overrides:
        print("当前没有可写入的自动调参修改。后续可以手动修改 策略参数.txt。")
        return 0

    params_path = _write_param_updates(args.params_file, overrides)
    config_loader.load_params(params_path)
    record_path = param_tuning.prepend_auto_tuning_record(
        args.auto_record_path,
        result,
        params,
        params_path,
    )
    print(f"已写入参数文件：{params_path}")
    print(f"已更新自动调参记录：{record_path}")
    return 0


def _run_auto_stage(
    args: argparse.Namespace,
    params: dict[str, Any],
    dataset: dict[str, Any],
    *,
    stage_level: int,
    center_params: dict[str, Any],
) -> dict[str, Any]:
    candidate_groups = param_tuning.build_auto_tune_candidate_groups(
        params,
        dataset,
        stage_level=stage_level,
        center_params=center_params,
    )
    total_candidates = sum(len(candidates) for _, candidates in candidate_groups) * max(int(args.auto_max_passes), 1)
    effective_total = min(total_candidates, max(int(args.auto_stage_candidate_limit), 0))
    group_text = "、".join(f"{name}{len(candidates)}组" for name, candidates in candidate_groups)
    stage_name = "粗步长" if stage_level == 1 else f"细步长第 {stage_level} 轮"
    print("")
    print(f"开始{stage_name}暴力搜索。候选组：{group_text}", flush=True)
    print(
        "本轮计划候选 {planned} 组，最多评估 {limit} 组，时间上限 {seconds:.0f} 秒。窗口出现 [当前/总数] 时表示正在搜索。".format(
            planned=total_candidates,
            limit=effective_total,
            seconds=float(args.auto_stage_time_limit_seconds),
        ),
        flush=True,
    )
    print("提示：时间上限只是最长保护；如果候选提前全部评估完，会立即进入下一步。", flush=True)
    result = param_tuning.auto_tune_params(
        dataset,
        params,
        top_n=args.top_n,
        max_passes=max(int(args.auto_max_passes), 1),
        stage_level=stage_level,
        center_params=center_params,
        candidate_limit=max(int(args.auto_stage_candidate_limit), 0),
        time_limit_seconds=float(args.auto_stage_time_limit_seconds),
        progress_callback=_auto_progress,
    )
    stage_start_score = ((result.get("stage_start") or {}).get("auto_score") or {})
    best_score = ((result.get("best") or {}).get("auto_score") or {})
    print(f"第 {stage_level} 轮完成：评估 {result.get('evaluated_step_count')} 组候选。")
    if result.get("stop_reason"):
        print(f"本轮提前停止：{result.get('stop_reason')}")
    else:
        print("本轮候选已全部评估完成。")
    print(f"本轮起点：{_score_text(stage_start_score)}")
    print(f"本轮最优：{_score_text(best_score)}")
    return result


def _run_auto_mode(
    args: argparse.Namespace,
    params: dict[str, Any],
    dataset: dict[str, Any],
) -> int:
    print("开始自动调参：按近期样本区间命中加权评分，并对中心误差扣分。", flush=True)
    print("估值区间宽度采用当前 策略参数.txt 的 price_range_width，仅作为诊断展示，不参与自动搜索和排序扣分。", flush=True)
    print("自动调参已拆成更细的参数模块；每轮完成后可接受写入，也可进入下一轮继续扩展搜索。", flush=True)

    result: dict[str, Any] | None = None
    center_params = dict(params)
    max_refine_stages = max(int(args.auto_max_refine_stages), 1)
    for stage_level in range(1, max_refine_stages + 1):
        result = _run_auto_stage(args, params, dataset, stage_level=stage_level, center_params=center_params)
        print("自动调参阶段完成。")
        _print_auto_result_context(result, params)

        has_next_stage = stage_level < max_refine_stages
        action = _prompt_auto_stage_action(
            stage_level,
            stage_level + 1,
            has_next_stage=has_next_stage,
            can_accept=bool(result.get("changed_overrides")),
        )
        if action == "accept":
            return _accept_auto_result(args, params, result)
        if action != "continue":
            print("已退出自动调参，未写入参数。后续可以手动修改 策略参数.txt。")
            return 0

        center_params = _params_with_overrides(params, dict(result.get("changed_overrides") or {}))

    if result is None:
        print("自动调参未执行。")
        return 0

    print("已达到自动调参最大轮数，未写入参数。后续可以手动修改 策略参数.txt。")
    return 0


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
    item_cache = dataset.get("item_cache") or {}
    if item_cache.get("enabled"):
        print(
            "回放条目缓存：复用旧数据集 {reused}，命中单样本缓存 {hits}，新建 {misses}，写入 {writes}。".format(
                reused=item_cache.get("existing_dataset_reused", 0),
                hits=item_cache.get("hits", 0),
                misses=item_cache.get("misses", 0),
                writes=item_cache.get("writes", 0),
            ),
            flush=True,
        )
    if args.mode == "auto":
        return _run_auto_mode(args, params, dataset)

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
