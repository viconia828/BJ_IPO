from __future__ import annotations

from pathlib import Path
import subprocess
import sys


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
TUNE_PARAMS_PATH = ROOT_DIR / "tools" / "tune_params.py"
DEFAULT_DATASET_PATH = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS_PATH = ROOT_DIR / "策略参数.txt"


if str(ROOT_DIR / "code") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "code"))

import config_loader


def _print_header() -> None:
    params = config_loader.load_params(DEFAULT_PARAMS_PATH)
    tuning_settings = config_loader.get_tuning_runtime_settings(params)
    print("========================================")
    print("北交所新股估值 - 手动调参入口")
    print("========================================")
    print("说明：")
    print("1. 本入口会调用 tools\\tune_params.py。")
    print("2. 默认使用当前回放数据集：data\\offline_tuning\\replay_dataset.json。")
    print("3. 训练集切分比例等默认取 策略参数.txt 中“调参专用”分类。")
    print(
        "   当前默认：train_ratio={ratio}，min_train_samples={min_samples}，replay_months={months}。".format(
            ratio=tuning_settings["tuning_train_ratio"],
            min_samples=tuning_settings["tuning_min_train_samples"],
            months=tuning_settings["tuning_replay_months"],
        )
    )
    print("4. 如无特殊需要，按提示逐步输入即可。")
    print("5. 若只输入权重组中的一个因子：二因子会自动补足到 1，多因子组会按当前策略参数比例缩放其余权重。")
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


def _build_command() -> tuple[list[str], dict[str, str]]:
    mode_choice = _prompt_choice(
        "请选择执行模式：",
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
        str(TUNE_PARAMS_PATH),
        "--mode",
        "offline" if mode_choice == "1" else "observe",
        "--dataset-path",
        str(DEFAULT_DATASET_PATH),
    ]

    if manual_name:
        command.extend(["--manual-name", manual_name])

    summary = {
        "mode": "离线复核" if mode_choice == "1" else "replay 观察",
        "input_mode": "",
        "manual_name": manual_name or "自动生成",
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


def _print_summary(summary: dict[str, str]) -> None:
    print("")
    print("即将执行：")
    print(f"- 模式：{summary['mode']}")
    print(f"- 候选输入方式：{summary['input_mode']}")
    print(f"- 任务名：{summary['manual_name']}")
    print(f"- 候选内容：{summary['candidate_summary']}")
    if "codes" in summary:
        print(f"- 观察代码：{summary['codes']}")
    print(f"- 回放数据集：{DEFAULT_DATASET_PATH.relative_to(ROOT_DIR)}")
    print("")


def main() -> int:
    _print_header()
    command, summary = _build_command()
    _print_summary(summary)

    completed = subprocess.run(command, cwd=ROOT_DIR, check=False)
    print("")
    if completed.returncode == 0:
        print("执行完成。报告路径请以上方输出的 JSON / Markdown 报告为准。")
    else:
        print(f"执行失败，退出码：{completed.returncode}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
