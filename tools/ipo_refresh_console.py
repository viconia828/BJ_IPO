from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


CHOICE_EXIT_CODES = {"1": 1, "2": 2, "3": 3, "Q": 4}

MENU_LINES = (
    "",
    "请选择要执行的刷新任务：",
    "",
    "  1. 刷新新股首日走势",
    "  2. 刷新新上市新股数据（估值 replay / 申购 history / 缺公告重试）",
    "  3. Refresh Xueqiu reference from manual files in xueqiu folder",
    "  Q. 退出",
    "",
)

DATASET_LINES = (
    "",
    "正在刷新新上市新股数据：估值 replay、申购 history、手工阶梯标签上下文、样本 manifest...",
    "上市前招股文件按增量规则补齐：已上市代码不再更新；未上市且本地缺正式招股说明书的代码才查询官网。",
    "缺失的发行公告、发行结果公告会自动尝试下载；未取到或字段未齐的代码会保留待重试标记。",
)

INTRADAY_LINES = (
    "",
    "正在刷新新股首日走势...",
)


def _print_lines(lines: Sequence[str]) -> None:
    print("\n".join(lines), flush=True)


def _read_choice() -> int:
    _print_lines(MENU_LINES)
    prompt = "请输入选项 [1/2/3/Q]: "

    if sys.platform == "win32" and sys.stdin.isatty():
        import msvcrt

        print(prompt, end="", flush=True)
        while True:
            character = msvcrt.getwch()
            if character in {"\x00", "\xe0"}:
                msvcrt.getwch()
                continue
            choice = character.upper()
            if choice in CHOICE_EXIT_CODES:
                print(character, flush=True)
                return CHOICE_EXIT_CODES[choice]

    while True:
        try:
            choice = input(prompt).strip().upper()
        except EOFError:
            print(flush=True)
            return CHOICE_EXIT_CODES["Q"]
        if choice in CHOICE_EXIT_CODES:
            return CHOICE_EXIT_CODES[choice]
        print("请输入 1、2、3 或 Q。", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the IPO refresh console UI outside cmd.exe.")
    parser.add_argument("action", choices=("menu", "dataset", "intraday"))
    args = parser.parse_args(argv)

    if args.action == "menu":
        return _read_choice()
    if args.action == "dataset":
        _print_lines(DATASET_LINES)
    else:
        _print_lines(INTRADAY_LINES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
