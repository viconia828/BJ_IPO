from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
TUNE_PARAMS_PATH = ROOT_DIR / "tools" / "tune_params.py"
TUNE_SUBSCRIPTION_PATH = ROOT_DIR / "tools" / "tune_subscription_prediction.py"
DEFAULT_DATASET_PATH = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_SUBSCRIPTION_HISTORY_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_history_sample.csv"
DEFAULT_LADDER_LABEL_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_ladder_labels.csv"
DEFAULT_PARAMS_PATH = ROOT_DIR / "策略参数.txt"


if str(ROOT_DIR / "code") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "code"))

import config_loader
import sync_offline_tuning_dataset as offline_tuning_sync


def _print_header() -> None:
    params = config_loader.load_params(DEFAULT_PARAMS_PATH)
    tuning_settings = config_loader.get_tuning_runtime_settings(params)
    print("========================================")
    print("北交所新股估值 - 调参入口")
    print("========================================")
    print("说明：")
    print("1. 本入口可调用估值调参 tools\\tune_params.py，或申购资金调参 tools\\tune_subscription_prediction.py。")
    print("2. 每次执行前会扫描“首日分时走势”目录，必要时自动同步回放数据集。")
    print("3. 默认使用当前回放数据集：data\\offline_tuning\\replay_dataset.json。")
    print("4. 申购资金调参默认读取 data\\offline_tuning\\subscription_ladder_labels.csv 的手工分档标签。")
    print("5. 训练集切分比例等默认取 策略参数.txt 中“调参专用”分类。")
    print(
        "   当前默认：train_ratio={ratio}，min_train_samples={min_samples}，replay_months={months}。".format(
            ratio=tuning_settings["tuning_train_ratio"],
            min_samples=tuning_settings["tuning_min_train_samples"],
            months=tuning_settings["tuning_replay_months"],
        )
    )
    print("6. 如无特殊需要，按提示逐步输入即可。")
    print("7. 若只输入权重组中的一个因子：二因子会自动补足到 1，多因子组会按当前策略参数比例缩放其余权重。")
    print("8. 自动调参会先输出建议修改项，只有确认接受后才会写入 策略参数.txt。")
    print("9. 估值自动调参每轮都会用本地 proxy、动态宽度、regime-break 和滚动中枢重排核心候选。")
    print("10. 接受、退出或无变化时会继续刷新 proxy、滚动中枢、regime-break 和盘中指导影子报告。")
    print("")


def _prompt_choice(title: str, options: list[tuple[str, str]], default: str) -> str:
    option_map = {key: label for key, label in options}
    print(title)
    for key, label in options:
        print(f"{key}. {label}")
    while True:
        raw_value = input(f"请输入选项 [默认 {default}]：").strip()
        if not raw_value:
            return default
        if raw_value in option_map:
            return raw_value
        valid_text = " / ".join(option_map.keys())
        print(f"输入无效，请输入 {valid_text}。")


def _prompt_optional(prompt_text: str) -> str:
    return input(prompt_text).strip()


def _prompt_required(prompt_text: str) -> str:
    while True:
        raw_value = input(prompt_text).strip()
        if raw_value:
            return raw_value
        print("该项不能为空，请重新输入。")


def _prompt_positive_int(prompt_text: str, default: int) -> int:
    while True:
        raw_value = input(prompt_text).strip()
        if not raw_value:
            return default
        try:
            value = int(raw_value)
        except ValueError:
            print("请输入正整数。")
            continue
        if value > 0:
            return value
        print("请输入正整数。")


def _prompt_existing_path(prompt_text: str) -> str:
    while True:
        raw_value = _prompt_required(prompt_text)
        candidate_path = Path(raw_value)
        if not candidate_path.is_absolute():
            candidate_path = ROOT_DIR / candidate_path
        if candidate_path.exists():
            return raw_value
        print("文件不存在，请输入相对仓库根目录或绝对路径。")


def _collect_group_candidates() -> list[str]:
    print("请逐行输入候选组，格式为 key=value,key=value。")
    print("若某个权重组只填写一个因子，系统会自动补齐或按当前比例缩放同组其余权重。")
    print("直接回车表示结束输入。")
    groups: list[str] = []
    while True:
        raw_value = input(f"候选组 {len(groups) + 1}：").strip()
        if not raw_value:
            if groups:
                return groups
            print("至少需要输入 1 组候选参数。")
            continue
        groups.append(raw_value)


def _default_top_n() -> int:
    params = config_loader.load_params(DEFAULT_PARAMS_PATH)
    return int(params.get("tuning_top_n", 10))


def _refresh_sample_sets_before_tuning() -> None:
    if os.environ.get("BSE_TUNING_NO_AUTO_REFRESH") == "1":
        print("已按 BSE_TUNING_NO_AUTO_REFRESH=1 跳过调参前样本集自动更新。")
        return

    params = config_loader.load_params(DEFAULT_PARAMS_PATH)
    tuning_settings = config_loader.get_tuning_runtime_settings(params)
    refresh_args = SimpleNamespace(
        rebuild_dataset=False,
        mode="offline",
        no_auto_refresh_dataset=False,
        dataset_path=DEFAULT_DATASET_PATH,
        history_path=DEFAULT_SUBSCRIPTION_HISTORY_PATH,
        ladder_label_path=DEFAULT_LADDER_LABEL_PATH,
        no_download_missing_announcements=False,
        download_retries=1,
        download_delay_seconds=0.0,
        parse_prospectus=False,
        months=int(tuning_settings["tuning_replay_months"]),
        page_size=int(tuning_settings["tuning_page_size"]),
    )

    print("调参前调用统一样本同步入口...", flush=True)
    try:
        offline_tuning_sync.sync_offline_tuning_dataset(
            refresh_args,
            params,
            progress_callback=None,
            verbose=True,
        )
        return
    except Exception as exc:
        print(f"统一样本同步失败：{exc}", flush=True)
        if not DEFAULT_DATASET_PATH.exists() or not DEFAULT_SUBSCRIPTION_HISTORY_PATH.exists():
            raise
        print("本次将继续使用旧样本集。", flush=True)
        return

def _build_valuation_manual_command() -> tuple[list[str], dict[str, str]]:
    mode_choice = _prompt_choice(
        "请选择手动调参执行模式：",
        [
            ("1", "离线复核：比较候选参数在训练集 / 验证集上的表现"),
            ("2", "replay 观察：比较候选参数在指定样本上的逐样本结果"),
        ],
        default="1",
    )
    input_mode_choice = _prompt_choice(
        "请选择候选输入方式：",
        [
            ("1", "单参数多候选值：适合只比较一个参数；若属于权重组会自动联动"),
            ("2", "手动候选组：适合一次比较多组参数组合"),
            ("3", "候选文件：适合复用已有 JSON 候选集"),
        ],
        default="1",
    )

    manual_name = _prompt_optional("任务名（可留空，留空会自动生成）：")

    command = [
        sys.executable,
        "-u",
        str(TUNE_PARAMS_PATH),
        "--mode",
        "offline" if mode_choice == "1" else "observe",
        "--dataset-path",
        str(DEFAULT_DATASET_PATH),
        "--no-auto-refresh-dataset",
    ]

    if manual_name:
        command.extend(["--manual-name", manual_name])

    summary = {
        "category": "估值",
        "mode": "估值手动调参 - " + ("离线复核" if mode_choice == "1" else "replay 观察"),
        "input_mode": "",
        "manual_name": manual_name or "自动生成",
        "dataset_path": str(DEFAULT_DATASET_PATH.relative_to(ROOT_DIR)),
    }

    if input_mode_choice == "1":
        summary["input_mode"] = "单参数多候选值"
        param_name = _prompt_required("参数名（示例：small_cap_premium）：")
        candidate_values = _prompt_required("候选值列表，逗号分隔（示例：0.10,0.15；若属于权重组会自动联动其余权重）：")
        command.extend(["--param-name", param_name, "--candidate-values", candidate_values])
        summary["candidate_summary"] = f"{param_name} -> {candidate_values}"
    elif input_mode_choice == "2":
        summary["input_mode"] = "手动候选组"
        candidate_groups = _collect_group_candidates()
        for group in candidate_groups:
            command.extend(["--candidate", group])
        summary["candidate_summary"] = " | ".join(candidate_groups)
    else:
        summary["input_mode"] = "候选文件"
        candidate_file = _prompt_existing_path(
            "候选文件路径（支持相对仓库根目录，示例：data\\offline_tuning\\candidate_sets\\quick_method2_pe_candidate_set_v1.json）："
        )
        command.extend(["--candidate-file", candidate_file])
        summary["candidate_summary"] = candidate_file

    if mode_choice == "2":
        codes = _prompt_optional("观察代码，逗号分隔；留空表示观察当前回放数据集中的全部样本：")
        if codes:
            command.extend(["--codes", codes])
        summary["codes"] = codes or "数据集内全部样本"

    return command, summary


def _build_valuation_auto_command() -> tuple[list[str], dict[str, str]]:
    command = [
        sys.executable,
        "-u",
        str(TUNE_PARAMS_PATH),
        "--mode",
        "auto",
        "--dataset-path",
        str(DEFAULT_DATASET_PATH),
        "--no-auto-refresh-dataset",
    ]
    summary = {
        "category": "估值",
        "mode": "估值自动调参",
        "input_mode": "系统自动搜索",
        "manual_name": "不适用",
        "candidate_summary": "按近期样本区间命中加权评分，自动选择参数组合；确认接受后写入参数文件",
        "dataset_path": str(DEFAULT_DATASET_PATH.relative_to(ROOT_DIR)),
        "auto_kind": "valuation",
    }
    return command, summary


def _build_subscription_auto_command() -> tuple[list[str], dict[str, str]]:
    top_n = _default_top_n()
    command = [
        sys.executable,
        "-u",
        str(TUNE_SUBSCRIPTION_PATH),
        "--mode",
        "auto",
        "--no-auto-refresh-history",
        "--top-n",
        str(top_n),
    ]
    summary = {
        "category": "申购资金",
        "mode": "申购资金自动调参",
        "input_mode": "系统枚举申购主参数网格",
        "manual_name": "不适用",
        "candidate_summary": f"按正股门槛、手工分档误差和抢时间漏判排序；Top N={top_n}；确认接受后写入参数文件",
        "history_path": str(DEFAULT_SUBSCRIPTION_HISTORY_PATH.relative_to(ROOT_DIR)),
        "ladder_label_path": str(DEFAULT_LADDER_LABEL_PATH.relative_to(ROOT_DIR)),
        "auto_kind": "subscription",
    }
    return command, summary


def _build_subscription_manual_command() -> tuple[list[str], dict[str, str]]:
    mode_choice = _prompt_choice(
        "请选择申购资金手动调参执行模式：",
        [
            ("1", "baseline：查看当前策略参数的申购资金回放指标"),
            ("2", "search：枚举主参数候选网格并排序展示"),
            ("3", "robustness：用不同历史窗口复核当前候选稳健性"),
            ("4", "account-pool：查看不同申购金额阈值以上的大户资金池"),
            ("5", "account-pool-prior：离线检查大户资金池 prior"),
        ],
        default="2",
    )
    mode_map = {
        "1": ("baseline", "baseline 回放"),
        "2": ("search", "候选搜索"),
        "3": ("robustness", "稳健性复核"),
        "4": ("account-pool", "大户资金池参考"),
        "5": ("account-pool-prior", "资金池 prior 检查"),
    }
    mode_value, mode_label = mode_map[mode_choice]
    command = [
        sys.executable,
        "-u",
        str(TUNE_SUBSCRIPTION_PATH),
        "--mode",
        mode_value,
        "--no-auto-refresh-history",
    ]

    top_n_text = "不适用"
    if mode_choice in {"2", "3", "5"}:
        default_top_n = _default_top_n()
        top_n = _prompt_positive_int(f"输出 Top N 候选数量 [默认 {default_top_n}]：", default_top_n)
        command.extend(["--top-n", str(top_n)])
        top_n_text = str(top_n)

    summary = {
        "category": "申购资金",
        "mode": f"申购资金手动调参 - {mode_label}",
        "input_mode": mode_label,
        "manual_name": "不适用",
        "candidate_summary": f"子模式={mode_value}；Top N={top_n_text}",
        "history_path": str(DEFAULT_SUBSCRIPTION_HISTORY_PATH.relative_to(ROOT_DIR)),
        "ladder_label_path": str(DEFAULT_LADDER_LABEL_PATH.relative_to(ROOT_DIR)),
    }
    return command, summary


def _build_command() -> tuple[list[str], dict[str, str]]:
    tuning_mode = _prompt_choice(
        "请选择调参模式：",
        [
            ("1", "估值自动调参：系统搜索估值参数组合，确认后写入 策略参数.txt"),
            ("2", "估值手动调参：候选参数复核 / replay 观察"),
            ("3", "申购资金自动调参：系统搜索申购资金参数组合，确认后写入 策略参数.txt"),
            ("4", "申购资金手动调参：baseline / search / robustness 等诊断"),
        ],
        default="1",
    )
    if tuning_mode == "1":
        return _build_valuation_auto_command()
    if tuning_mode == "2":
        return _build_valuation_manual_command()
    if tuning_mode == "3":
        return _build_subscription_auto_command()
    return _build_subscription_manual_command()


def _print_summary(summary: dict[str, str]) -> None:
    print("")
    print("即将执行：")
    print(f"- 分类：{summary.get('category', '-')}")
    print(f"- 模式：{summary['mode']}")
    print(f"- 候选输入方式：{summary['input_mode']}")
    print(f"- 任务名：{summary['manual_name']}")
    print(f"- 候选内容：{summary['candidate_summary']}")
    if "codes" in summary:
        print(f"- 观察代码：{summary['codes']}")
    if "dataset_path" in summary:
        print(f"- 回放数据集：{summary['dataset_path']}")
    if "history_path" in summary:
        print(f"- 申购历史样本：{summary['history_path']}")
    if "ladder_label_path" in summary:
        print(f"- 手工分档标签：{summary['ladder_label_path']}")
    print("")


def main() -> int:
    _print_header()
    command, summary = _build_command()
    _print_summary(summary)
    _refresh_sample_sets_before_tuning()
    print("")
    if summary.get("auto_kind") == "valuation":
        print("正在启动自动调参子程序。它会先检查回放数据集，再进入粗步长暴力搜索；每轮完成后可选择是否继续细步长搜索。")
        print("未出现“自动调参完成”前表示仍未跑完。")
        print("")
    elif summary.get("auto_kind") == "subscription":
        print("正在启动申购资金自动调参子程序。它会读取申购历史样本和手工分档标签，完成搜索后再询问是否写入。")
        print("")
    sys.stdout.flush()

    completed = subprocess.run(command, cwd=ROOT_DIR, check=False)
    print("")
    if completed.returncode == 0:
        if summary.get("auto_kind") == "valuation":
            print("自动调参流程已结束。若上方没有出现“本次建议修改的参数”，表示本轮未找到优于当前参数的修改方案。")
        elif summary.get("auto_kind") == "subscription":
            print("申购资金自动调参流程已结束。若上方没有写入提示，表示本轮未写入参数。")
        else:
            print("执行完成。报告路径请以上方输出的 JSON / Markdown 报告为准。")
    else:
        print(f"执行失败，退出码：{completed.returncode}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
