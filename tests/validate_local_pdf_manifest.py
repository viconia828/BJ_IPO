from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_ipo_valuation
import local_pdf_manifest


TEMP_DIR = ROOT_DIR / "tests" / "_tmp" / "local_pdf_manifest"


def _reset() -> None:
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True)
    local_pdf_manifest.clear_local_pdf_manifest_cache()


def _write_pdf(name: str, *, complete: bool = True) -> Path:
    path = TEMP_DIR / name
    tail = b"%%EOF\n" if complete else b"truncated\n"
    path.write_bytes(b"%PDF-1.7\nmanifest validation\n" + tail)
    return path


def main() -> int:
    failures: list[str] = []
    _reset()
    full_prospectus = _write_pdf("920001_样本一_招股说明书.pdf")
    summary_prospectus = _write_pdf("920001_样本一_招股说明书摘要.pdf")
    listing = _write_pdf("920001_样本一_上市公告书.pdf")
    _write_pdf("920001_样本一_发行结果公告.pdf")
    _write_pdf("920002_样本二_招股说明书.pdf", complete=False)

    first = local_pdf_manifest.refresh_local_pdf_manifest(TEMP_DIR)
    second = local_pdf_manifest.refresh_local_pdf_manifest(TEMP_DIR)
    manifest_path = TEMP_DIR / ".local_pdf_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    if first.get("action") != "rebuilt" or first.get("file_count") != 5:
        failures.append(f"initial manifest should scan five PDFs once, got {first}")
    if second.get("action") != "cache_hit" or second.get("full_scan_count") != 1:
        failures.append(f"unchanged directory should reuse manifest, got {second}")
    if payload.get("codes", {}).get("920001") is None or payload.get("codes", {}).get("920002") is None:
        failures.append("manifest should persist code-to-file mappings")
    if second.get("complete_count") != 4 or second.get("incomplete_count") != 1:
        failures.append("manifest completeness summary mismatch")

    if bse_ipo_valuation._find_pdf(TEMP_DIR, "920001", "上市公告书") != listing:
        failures.append("listing lookup should resolve through manifest")
    if bse_ipo_valuation._pick_prospectus_pdf(TEMP_DIR, "920001", "old_shares") != full_prospectus:
        failures.append("old-shares lookup should prefer full prospectus")
    if bse_ipo_valuation._pick_prospectus_pdf(TEMP_DIR, "920001", "business") != summary_prospectus:
        failures.append("business lookup should preserve summary prospectus priority")
    if bse_ipo_valuation._pick_prospectus_pdf(TEMP_DIR, "920002", "old_shares") is not None:
        failures.append("incomplete PDF should not be returned from manifest")

    unchanged_registration = local_pdf_manifest.register_pdf_file(listing)
    if unchanged_registration.get("action") != "cache_hit" or unchanged_registration.get("full_scan_count") != 1:
        failures.append("registering an unchanged local PDF should not rewrite or rescan the manifest")

    new_file = _write_pdf("920003_样本三_发行公告.pdf")
    registered = local_pdf_manifest.register_pdf_file(new_file)
    after_register = local_pdf_manifest.refresh_local_pdf_manifest(TEMP_DIR)
    if registered.get("action") != "incremental_update":
        failures.append(f"downloaded file should update manifest incrementally, got {registered}")
    if after_register.get("action") != "cache_hit" or after_register.get("full_scan_count") != 1:
        failures.append(f"registered file should not force a full rescan, got {after_register}")
    if bse_ipo_valuation._find_pdf(TEMP_DIR, "920003", "发行公告") != new_file:
        failures.append("incrementally registered file should be queryable")

    exact_listing = _write_pdf("920004_上市公告书.pdf")
    alternate_listing = _write_pdf("920004_样本四_上市公告书.pdf")
    local_pdf_manifest.register_pdf_file(exact_listing)
    local_pdf_manifest.register_pdf_file(alternate_listing)
    if bse_ipo_valuation._find_pdf(TEMP_DIR, "920004", "上市公告书") != exact_listing:
        failures.append("exact legacy filename should retain priority over other manifest candidates")

    full_prospectus.write_bytes(b"%PDF-1.7\ntruncated replacement\n")
    if bse_ipo_valuation._pick_prospectus_pdf(TEMP_DIR, "920001", "old_shares") != summary_prospectus:
        failures.append("changed indexed file should be revalidated and excluded when incomplete")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK local PDF manifest validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
