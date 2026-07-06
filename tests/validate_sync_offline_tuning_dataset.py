from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT_DIR / "tools"
CODE_DIR = ROOT_DIR / "code"
for path in (TOOLS_DIR, CODE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_subscription_history
import sync_offline_tuning_dataset
import subscription_ladder_labels


TEMP_ROOT = ROOT_DIR / ".tmp" / "validate_sync_offline_tuning_dataset"


def _reset_temp_dir() -> Path:
    if TEMP_ROOT.exists():
        shutil.rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEMP_ROOT


def _write_history_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(build_subscription_history.CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in build_subscription_history.CSV_COLUMNS})


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def retry_marker_same_day_download_cooldown_case(failures: list[str]) -> None:
    temp_dir = _reset_temp_dir()
    marker_path = temp_dir / "deferred_listing_data_codes.json"
    marker_path.write_text(
        json.dumps(
            {
                "schema": "offline_tuning_listing_data_retry_v1",
                "updated_at": "2026-07-01 10:00:00",
                "pending_codes": ["920001", "920002", "920003"],
                "reasons_by_code": {
                    "920001": ["download_errors", "missing_result_announcement"],
                    "920002": ["parse_errors"],
                    "920003": ["download_errors"],
                },
                "last_download_attempt_date_by_code": {
                    "920003": "2026-06-30",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    marker = sync_offline_tuning_dataset._load_retry_marker(marker_path)
    cooldown_codes = sync_offline_tuning_dataset._same_day_download_cooldown_codes(
        marker,
        current_date="2026-07-01",
    )
    if cooldown_codes != ["920001"]:
        failures.append(f"retry marker cooldown should only include same-day download failure, got {cooldown_codes}")

    sync_offline_tuning_dataset._save_retry_codes(
        marker_path,
        ["920001", "920003"],
        {
            "920001": ["download_errors"],
            "920003": ["download_errors"],
        },
        previous_marker=marker,
        download_attempted_codes={"920003"},
    )
    saved = json.loads(marker_path.read_text(encoding="utf-8"))
    saved_dates = saved.get("last_download_attempt_date_by_code") or {}
    today = sync_offline_tuning_dataset._today_text()
    if saved_dates.get("920001") != "2026-07-01":
        failures.append("retry marker save should preserve previous same-day attempt date")
    if saved_dates.get("920003") != today:
        failures.append("retry marker save should stamp attempted retry code with today")
    if "920002" in saved_dates:
        failures.append("retry marker save should drop cleared retry codes from attempt dates")


def manifest_includes_label_only_samples_case(failures: list[str]) -> None:
    temp_dir = _reset_temp_dir()
    dataset_path = temp_dir / "replay_dataset.json"
    history_path = temp_dir / "subscription_history_sample.csv"
    label_path = temp_dir / "subscription_ladder_labels.csv"
    replay_item_dir = temp_dir / "replay_items"
    intraday_dir = temp_dir / "intraday"
    replay_item_dir.mkdir()
    intraday_dir.mkdir()

    dataset = {
        "schema": "offline_tuning_replay_v1",
        "evaluation_scope": "composite",
        "requested_codes": ["920001"],
        "sample_codes": ["920001"],
        "available_count": 1,
        "method1_ready_count": 1,
        "items": [
            {
                "SECURITY_CODE": "920001",
                "SECURITY_NAME_ABBR": "样本一",
                "APPLY_DATE": "2026-01-01",
                "LISTING_DATE": "2026-01-10",
                "ISSUE_PRICE": 10.0,
                "TOP_APPLY_MARKETCAP": 500.0,
                "ONLINE_ISSUE_NUM": 10000000,
                "method1_replay_available": True,
                "has_intraday_file": True,
                "old_shares_meta": {"source_file_type": "上市公告书", "confidence": 0.98},
                "comparable_codes": ["688001.SH"],
                "comparable_summary": {"returned_codes": ["688001.SH"], "reason": ""},
            }
        ],
    }
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    (replay_item_dir / "920001.json").write_text(
        json.dumps(
            {
                "schema": "offline_tuning_replay_item_v1",
                "cache_version": 3,
                "generated_at": "2026-01-10 16:00:00",
                "code": "920001",
                "pdf_signature": {"listing": None, "old_shares": None, "comparables": None},
                "item": dataset["items"][0],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (intraday_dir / "920001.csv").write_text("time,price,volume,amount\n", encoding="utf-8")

    _write_history_csv(
        history_path,
        [
            {
                "security_code": "920001",
                "security_name_abbr": "样本一",
                "apply_date": "2026-01-01",
                "listing_date": "2026-01-10",
                "model_ready": "true",
                "allocation_fit_ready": "true",
                "allocation_fit_usable_for_tuning": "true",
                "online_issue_shares": "10000000",
                "top_apply_amount_wan": "500",
            }
        ],
    )
    subscription_ladder_labels.write_label_rows(
        [
            {
                "security_code": "920001",
                "security_name_abbr": "样本一",
                "apply_date": "2026-01-01",
                "issue_price": "10",
                "online_issue_shares": "10000000",
                "top_apply_amount_wan": "500",
                "manual_ladder": "1+0=300;1+1=500",
                "manual_note": "",
            },
            {
                "security_code": "920999",
                "security_name_abbr": "仅标签",
                "apply_date": "",
                "issue_price": "",
                "online_issue_shares": "",
                "top_apply_amount_wan": "",
                "manual_ladder": "0+1=顶格抢时间",
                "manual_note": "只有手工标签",
            },
        ],
        label_path,
    )

    manifest = sync_offline_tuning_dataset.build_sample_manifest(
        dataset=dataset,
        dataset_path=dataset_path,
        history_path=history_path,
        ladder_label_path=label_path,
        replay_item_dir=replay_item_dir,
        intraday_dir=intraday_dir,
        retry_reasons_by_code={"920001": ["parse_errors"], "920888": ["pending_code_not_in_current_history"]},
        pending_retry_before_codes=["920001", "920888"],
    )
    summary = manifest["summary"]
    samples = {item["security_code"]: item for item in manifest["samples"]}

    if summary.get("replay_dataset_count") != 1:
        failures.append("manifest replay_dataset_count should be 1")
    if summary.get("subscription_history_count") != 1:
        failures.append("manifest subscription_history_count should be 1")
    if summary.get("ladder_label_count") != 2:
        failures.append("manifest ladder_label_count should be 2")
    if summary.get("label_only_count") != 1:
        failures.append("manifest label_only_count should be 1")
    if summary.get("listing_data_retry_count") != 2:
        failures.append("manifest listing_data_retry_count should be 2")
    label_only = samples.get("920999")
    if not label_only:
        failures.append("manifest should include label-only code 920999")
    elif not label_only.get("in_ladder_labels") or label_only.get("in_replay_dataset"):
        failures.append("label-only sample flags are incorrect")
    if not samples.get("920001", {}).get("manual_ladder_label_ready"):
        failures.append("manifest should derive manual ladder readiness for 920001")
    retry_only = samples.get("920888")
    if not retry_only:
        failures.append("manifest should include retry-only code 920888")
    elif not retry_only.get("needs_listing_data_retry") or not retry_only.get("pending_listing_data_retry_before"):
        failures.append("retry-only sample flags are incorrect")


def replay_refresh_uses_unified_sample_source_case(failures: list[str]) -> None:
    temp_dir = _reset_temp_dir()
    dataset_path = temp_dir / "replay_dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema": "offline_tuning_replay_v1",
                "evaluation_scope": "composite",
                "source_months": 18,
                "replay_item_cache_version": sync_offline_tuning_dataset.param_tuning.REPLAY_ITEM_CACHE_VERSION,
                "requested_codes": ["920001"],
                "sample_codes": ["920001"],
                "available_count": 1,
                "method1_ready_count": 1,
                "items": [{"SECURITY_CODE": "920001"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        rebuild_dataset=False,
        mode="offline",
        no_auto_refresh_dataset=False,
        months=18,
        page_size=100,
    )
    captured: dict[str, object] = {}

    def fake_discover_replay_sample_codes() -> list[str]:
        return ["920001", "920999"]

    def fake_build_and_save_replay_dataset(*args: object, **kwargs: object) -> dict[str, object]:
        captured["sample_codes"] = list(kwargs.get("sample_codes") or [])
        return {
            "schema": "offline_tuning_replay_v1",
            "source_months": 18,
            "requested_codes": captured["sample_codes"],
            "sample_codes": captured["sample_codes"],
            "available_count": 2,
            "items": [{"SECURITY_CODE": "920001"}, {"SECURITY_CODE": "920999"}],
        }

    original_discover = sync_offline_tuning_dataset.param_tuning.discover_replay_sample_codes
    original_build = sync_offline_tuning_dataset.build_and_save_replay_dataset
    original_should_refresh = sync_offline_tuning_dataset._should_auto_refresh_dataset
    try:
        sync_offline_tuning_dataset.param_tuning.discover_replay_sample_codes = fake_discover_replay_sample_codes
        sync_offline_tuning_dataset.build_and_save_replay_dataset = fake_build_and_save_replay_dataset
        sync_offline_tuning_dataset._should_auto_refresh_dataset = lambda *args, **kwargs: True
        result = sync_offline_tuning_dataset.load_or_refresh_replay_dataset(
            args,
            {},
            dataset_path,
            sample_codes=None,
        )
    finally:
        sync_offline_tuning_dataset.param_tuning.discover_replay_sample_codes = original_discover
        sync_offline_tuning_dataset.build_and_save_replay_dataset = original_build
        sync_offline_tuning_dataset._should_auto_refresh_dataset = original_should_refresh

    if captured.get("sample_codes") != ["920001", "920999"]:
        failures.append(f"sync refresh should use unified replay sample source, got {captured.get('sample_codes')}")
    if result.get("available_count") != 2:
        failures.append("sync refresh should return rebuilt dataset from unified source")


def sync_rebuilds_account_pool_history_case(failures: list[str]) -> None:
    temp_dir = _reset_temp_dir()
    dataset_path = temp_dir / "replay_dataset.json"
    history_path = temp_dir / "subscription_history_sample.csv"
    label_path = temp_dir / "subscription_ladder_labels.csv"
    manifest_path = temp_dir / "sample_manifest.json"
    points_path = temp_dir / "account_pool_history_points.csv"
    thresholds_path = temp_dir / "account_pool_history_thresholds.csv"
    summary_path = temp_dir / "account_pool_history_summary.json"
    replay_item_dir = temp_dir / "replay_items"
    intraday_dir = temp_dir / "intraday"
    retry_marker_path = temp_dir / "deferred_listing_data_codes.json"
    replay_item_dir.mkdir()
    intraday_dir.mkdir()

    thresholds_path.write_text("security_code,accounts_ge_500w_estimate\n920001,999999\n", encoding="utf-8")

    dataset = {
        "schema": "offline_tuning_replay_v1",
        "evaluation_scope": "composite",
        "requested_codes": ["920001"],
        "sample_codes": ["920001"],
        "available_count": 1,
        "method1_ready_count": 1,
        "items": [
            {
                "SECURITY_CODE": "920001",
                "SECURITY_NAME_ABBR": "Sample",
                "APPLY_DATE": "2026-07-01",
                "LISTING_DATE": "2026-07-10",
                "ISSUE_PRICE": 8.14,
                "TOP_APPLY_MARKETCAP": 900.0,
                "ONLINE_ISSUE_NUM": 19089000,
                "method1_replay_available": True,
                "has_intraday_file": False,
            }
        ],
    }

    def fake_sync_replay_dataset(*args: object, **kwargs: object) -> dict[str, object]:
        return dataset

    def fake_build_subscription_history_table(**kwargs: object) -> dict[str, object]:
        output_path = Path(kwargs["output_path"])
        ladder_path = Path(kwargs["ladder_label_path"])
        fit = {
            "fit_quality": "rough_lot_account_fit",
            "fit_confidence": 0.7,
            "buckets": [
                {"allocated_lots": 2, "accounts": 87700, "threshold_amount_wan": 650},
                {"allocated_lots": 1, "accounts": 15500, "threshold_amount_wan": 430},
            ],
        }
        _write_history_csv(
            output_path,
            [
                {
                    "security_code": "920001",
                    "security_name_abbr": "Sample",
                    "apply_date": "2026-07-01",
                    "listing_date": "2026-07-10",
                    "issue_price": "8.14",
                    "online_issue_shares": "19089000",
                    "online_valid_accounts": "574968",
                    "online_allocated_accounts": "103206",
                    "top_apply_amount_wan": "900",
                    "allocation_fit_json": json.dumps(fit, ensure_ascii=False),
                    "model_ready": "true",
                    "allocation_fit_ready": "true",
                    "allocation_fit_usable_for_tuning": "true",
                }
            ],
        )
        subscription_ladder_labels.write_label_rows(
            [
                {
                    "security_code": "920001",
                    "security_name_abbr": "Sample",
                    "apply_date": "2026-07-01",
                    "issue_price": "8.14",
                    "online_issue_shares": "19089000",
                    "top_apply_amount_wan": "900",
                    "manual_ladder": "1+0=447;1+1=631",
                    "manual_note": "",
                }
            ],
            ladder_path,
        )
        return {
            "output_path": str(output_path),
            "row_count": 1,
            "model_ready_count": 1,
            "fractional_label_ready_count": 1,
            "result_pdf_count": 1,
            "issue_pdf_count": 1,
            "ladder_label_path": str(ladder_path),
            "ladder_label_rows": 1,
            "ladder_label_filled": 1,
            "ladder_label_added_codes": [],
        }

    args = SimpleNamespace(
        dataset_path=dataset_path,
        history_path=history_path,
        ladder_label_path=label_path,
        account_pool_points_path=points_path,
        account_pool_thresholds_path=thresholds_path,
        account_pool_summary_path=summary_path,
        manifest_path=manifest_path,
        replay_item_dir=replay_item_dir,
        intraday_dir=intraday_dir,
        retry_marker_path=retry_marker_path,
        no_download_missing_announcements=True,
        download_retries=1,
        download_delay_seconds=0.0,
        parse_prospectus=False,
    )

    original_sync_replay_dataset = sync_offline_tuning_dataset._sync_replay_dataset
    original_build_history = sync_offline_tuning_dataset.build_subscription_history.build_subscription_history_table
    try:
        sync_offline_tuning_dataset._sync_replay_dataset = fake_sync_replay_dataset
        sync_offline_tuning_dataset.build_subscription_history.build_subscription_history_table = (
            fake_build_subscription_history_table
        )
        result = sync_offline_tuning_dataset.sync_offline_tuning_dataset(args, {}, verbose=False)
    finally:
        sync_offline_tuning_dataset._sync_replay_dataset = original_sync_replay_dataset
        sync_offline_tuning_dataset.build_subscription_history.build_subscription_history_table = original_build_history

    threshold_rows = _read_csv(thresholds_path)
    point_rows = _read_csv(points_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    threshold_row = threshold_rows[0] if threshold_rows else {}

    if result.get("account_pool_summary", {}).get("sample_count") != 1:
        failures.append("sync should return account pool summary")
    if summary.get("sample_count") != 1 or summary.get("usable_point_count") != 2:
        failures.append("account pool summary should be rebuilt from latest history")
    if threshold_row.get("accounts_ge_500w_estimate") == "999999":
        failures.append("account pool thresholds should overwrite stale historical estimate with latest rebuild")
    if threshold_row.get("accounts_ge_447w_estimate") in {"", "999999"}:
        failures.append("account pool thresholds should materialize observed manual cutpoints")
    if threshold_row.get("accounts_ge_447w_basis") != "observed_threshold":
        failures.append("account pool 447w basis should come from rebuilt observed points")
    if not any(row.get("security_code") == "920001" and row.get("manual_ladder_item") == "1+0=447" for row in point_rows):
        failures.append("account pool points should be rebuilt from latest allocation fit and ladder")


def main() -> int:
    failures: list[str] = []
    retry_marker_same_day_download_cooldown_case(failures)
    manifest_includes_label_only_samples_case(failures)
    replay_refresh_uses_unified_sample_source_case(failures)
    sync_rebuilds_account_pool_history_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK sync offline tuning dataset validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
