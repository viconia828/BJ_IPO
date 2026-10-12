"""Promote the verified research inputs to a versioned shadow-model reference.

This is structural reconstruction, not proof of original pre-listing vintages.
The production replay cache is not overwritten.
"""
from pathlib import Path
import csv
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "outputs/研究产物/最新/新股基本面与情绪检验_20261012"
    canonical = source / "canonical_samples.csv"
    overrides = source / "sources/yute_historical_comparables.json"
    with canonical.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    peers = json.loads(overrides.read_text(encoding="utf-8"))["data"]
    items = {}
    for r in rows:
        items[r["code"]] = {
            "ISSUE_PRICE": float(r["issue_price"]), "LISTING_DATE": r["date"],
            "AVERAGE_PRICE": float(r["vwap"]), "CLOSE_PRICE": float(r["close"]),
            "verified_float_shares": float(r["float_shares_wan"]),
            "verified_float_source": r["float_share_source"],
            "structure_vintage": "retrospective_reconstruction",
        }
    items["920157"]["comparable_data"] = peers
    out = {"schema": "quality_sentiment_historical_reference_v1", "prepared_date": "2026-10-12",
           "caveat": "日期、价格经日线核验；股数为上市时固定流通股数的事后结构核验，非原始事前PIT档案；仅用于影子模型",
           "sources": [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in (canonical, overrides, source / "sources/ifind_float_reference.json")], "items": items}
    target = ROOT / "data/quality_sentiment/historical_reference.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Prepared {len(items)} shadow reference rows; original replay files unchanged.")


if __name__ == "__main__":
    main()
