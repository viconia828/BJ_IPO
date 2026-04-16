from __future__ import annotations

from math import isclose
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pdf_parser


REL_TOL = 1e-6
ABS_TOL = 1e-6

GOLDEN_CASES = (
    {
        "label": "920181 listing old shares",
        "path": "公告文件/920181赛英电子上市公告书.pdf",
        "expected_value": 30.0,
        "expected_file_type": "上市公告书",
        "expected_rule": "listing_table",
        "anchor_contains": "本次发行前后的股本结构变动情况",
    },
    {
        "label": "920177 listing zero old shares",
        "path": "公告文件/920177恒道科技上市公告书.pdf",
        "expected_value": 0.0,
        "expected_file_type": "上市公告书",
        "expected_rule": "listing_table",
        "anchor_contains": "本次发行前后的股本结构变动情况",
    },
    {
        "label": "920166 listing large old shares",
        "path": "公告文件/920166海圣医疗上市公告书.pdf",
        "expected_value": 1360.0,
        "expected_file_type": "上市公告书",
        "expected_rule": "listing_table",
        "anchor_contains": "本次发行前后的股本结构变动情况",
    },
    {
        "label": "920036 listing unit correction",
        "path": "公告文件/920036觅睿科技上市公告书.pdf",
        "expected_value": 81.6327,
        "expected_file_type": "上市公告书",
        "expected_rule": "listing_table",
        "expected_unit": "股",
        "anchor_contains": "本次发行前后的股本结构变动情况",
    },
    {
        "label": "920069 listing unit correction",
        "path": "公告文件/920069普昂医疗上市公告书.pdf",
        "expected_value": 525.3215,
        "expected_file_type": "上市公告书",
        "expected_rule": "listing_table",
        "expected_unit": "股",
        "anchor_contains": "本次发行前后的股本结构变动情况",
    },
    {
        "label": "920086 listing zero old shares",
        "path": "公告文件/920086科马材料上市公告书.pdf",
        "expected_value": 0.0,
        "expected_file_type": "上市公告书",
        "expected_rule": "listing_table",
        "expected_unit": "股",
        "anchor_contains": "本次发行前后的股本结构变动情况",
    },
    {
        "label": "920180 listing zero old shares",
        "path": "公告文件/920180爱得科技上市公告书.pdf",
        "expected_value": 0.0,
        "expected_file_type": "上市公告书",
        "expected_rule": "listing_table",
        "expected_unit": "股",
        "anchor_contains": "本次发行前后的股本结构变动情况",
    },
    {
        "label": "920177 prospectus negative phrase",
        "path": "公告文件/920177恒道科技招股说明书.pdf",
        "expected_value": 0.0,
        "expected_file_type": "招股文件",
    },
    {
        "label": "920036 prospectus negative phrase",
        "path": "公告文件/920036觅睿科技招股说明书.pdf",
        "expected_value": 0.0,
        "expected_file_type": "招股文件",
    },
    {
        "label": "920186 intention book negative phrase",
        "path": "公告文件/920186中科仪招股意向书.pdf",
        "expected_value": 0.0,
        "expected_file_type": "招股文件",
    },
)


def _is_close(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def main() -> int:
    failures: list[str] = []

    for case in GOLDEN_CASES:
        file_path = ROOT_DIR / case["path"]
        result = pdf_parser.extract_old_shares_result(file_path)
        if result is None:
            failures.append(f"{case['label']}: result is None")
            print(f"FAIL {case['label']}: result is None")
            continue

        if not _is_close(result.value_wan_shares, float(case["expected_value"])):
            failures.append(
                f"{case['label']}: expected value {case['expected_value']}, got {result.value_wan_shares}"
            )

        expected_file_type = case.get("expected_file_type")
        if expected_file_type and result.source_file_type != expected_file_type:
            failures.append(
                f"{case['label']}: expected file type {expected_file_type}, got {result.source_file_type}"
            )

        expected_rule = case.get("expected_rule")
        if expected_rule and result.source_rule != expected_rule:
            failures.append(
                f"{case['label']}: expected rule {expected_rule}, got {result.source_rule}"
            )

        expected_unit = case.get("expected_unit")
        if expected_unit and result.unit != expected_unit:
            failures.append(
                f"{case['label']}: expected unit {expected_unit}, got {result.unit}"
            )

        anchor_contains = case.get("anchor_contains")
        if anchor_contains and anchor_contains not in result.source_anchor:
            failures.append(
                f"{case['label']}: expected anchor containing {anchor_contains}, got {result.source_anchor}"
            )

        print(
            "OK {label}: value={value:.2f}, file_type={file_type}, rule={rule}, anchor={anchor}".format(
                label=case["label"],
                value=result.value_wan_shares,
                file_type=result.source_file_type,
                rule=result.source_rule,
                anchor=result.source_anchor,
            )
        )

    if failures:
        print("\nGolden sample validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print(f"\nGolden sample validation passed: {len(GOLDEN_CASES)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
