from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
import config_loader
import param_tuning
from industry_mapping import INDUSTRY_CLASSIFICATION_VERSION, INDUSTRY_REFERENCE_SIGNATURE, IndustryMapper

CLASSIFICATION_FIELDS = {
    "industry_primary", "industry_secondary", "industry_tertiary", "industry_source",
    "statutory_industry", "statutory_industry_code", "valuation_peer_group", "business_tags", "chain_tags",
    "industry_confidence", "industry_evidence", "industry_status", "industry_candidates",
    "industry_review_reasons", "industry_classification_version",
}


def migrate(dataset_path: Path, cache_dir: Path, audit_dir: Path, *, dry_run: bool = False) -> dict:
    mapper = IndustryMapper(config_loader.load_params(ROOT / "策略参数.txt"))
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    originals = list(dataset.get("items") or [])
    updated = mapper.enrich_recent_ipos(originals)
    changes = []
    for before, after in zip(originals, updated):
        assert {k: v for k, v in before.items() if k not in CLASSIFICATION_FIELDS} == {
            k: v for k, v in after.items() if k not in CLASSIFICATION_FIELDS
        }, "Classification migration changed financial or price evidence"
        changes.append({
            "code": before.get("SECURITY_CODE"), "name": before.get("SECURITY_NAME_ABBR"),
            "before": before.get("valuation_peer_group") or f"{before.get('industry_primary')} / {before.get('industry_secondary')}",
            "after": after["valuation_peer_group"], "chain_tags": after["chain_tags"],
            "needs_review": after["industry_status"] == "needs_review",
        })
    summary = {
        "version": INDUSTRY_CLASSIFICATION_VERSION, "reference_signature": INDUSTRY_REFERENCE_SIGNATURE,
        "sample_count": len(updated), "changed_groups": sum(x["before"] != x["after"] for x in changes),
        "review_codes": [x["code"] for x in changes if x["needs_review"]],
        "financial_and_price_fields_preserved": True, "dry_run": dry_run, "changes": changes,
    }
    audit_dir.mkdir(parents=True, exist_ok=True)
    cache_writes = 0
    if not dry_run:
        backup_dir = audit_dir / "before"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if not (backup_dir / dataset_path.name).exists():
            shutil.copy2(dataset_path, backup_dir / dataset_path.name)
        for path in cache_dir.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            revised = mapper.enrich_recent_ipos([item])[0]
            assert {k: v for k, v in item.items() if k not in CLASSIFICATION_FIELDS} == {
                k: v for k, v in revised.items() if k not in CLASSIFICATION_FIELDS
            }
            if not (backup_dir / path.name).exists():
                shutil.copy2(path, backup_dir / path.name)
            payload["item"] = revised
            signature = dict(payload.get("record_signature") or {})
            for key in param_tuning.REPLAY_RECORD_SIGNATURE_KEYS:
                if key in CLASSIFICATION_FIELDS:
                    signature[key] = param_tuning._replay_cache_scalar(revised.get(key))
                elif key not in signature:
                    signature[key] = param_tuning._replay_cache_scalar(item.get(key))
            payload["record_signature"] = signature
            payload["industry_reclassification_version"] = INDUSTRY_CLASSIFICATION_VERSION
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            cache_writes += 1
        dataset["items"] = updated
        dataset["industry_reclassification"] = {k: v for k, v in summary.items() if k != "changes"}
        # Leave parser/version evidence intact: taxonomy-only migration did not reparse PDFs.
        contract = dict(dataset.get("replay_refresh_contract") or {})
        contract["industry_classification_version"] = INDUSTRY_CLASSIFICATION_VERSION
        contract["industry_reference_signature"] = INDUSTRY_REFERENCE_SIGNATURE
        contract["record_signature_keys"] = list(param_tuning.REPLAY_RECORD_SIGNATURE_KEYS)
        dataset["replay_refresh_contract"] = contract
        dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["cache_writes"] = cache_writes
    (audit_dir / "migration_audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Only migrate market-industry labels; preserve all replay price and financial data.")
    parser.add_argument("--dataset", type=Path, default=param_tuning.DEFAULT_DATASET_PATH)
    parser.add_argument("--cache-dir", type=Path, default=param_tuning.DEFAULT_REPLAY_ITEM_CACHE_DIR)
    parser.add_argument("--audit-dir", type=Path, default=ROOT / ".tmp" / f"industry_reclassification_{datetime.now():%Y%m%d_%H%M%S}")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = migrate(args.dataset, args.cache_dir, args.audit_dir, dry_run=args.dry_run)
    print(json.dumps({k: v for k, v in result.items() if k != "changes"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
