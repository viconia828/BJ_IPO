from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PDF_DIR = ROOT_DIR / "公告文件"
DEFAULT_MANIFEST_PATH = ROOT_DIR / "data" / "offline_tuning" / "local_pdf_manifest.json"
MANIFEST_SCHEMA = "local_pdf_manifest_v1"
MANIFEST_VERSION = 1

PDF_HEADER_MARKER = b"%PDF-"
PDF_EOF_MARKER = b"%%EOF"
PDF_HEADER_SCAN_BYTES = 1024
PDF_EOF_SCAN_BYTES = 4096
SECURITY_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _default_manifest_path(directory: Path) -> Path:
    if _same_path(directory, DEFAULT_PDF_DIR):
        return DEFAULT_MANIFEST_PATH
    return directory / ".local_pdf_manifest.json"


def _directory_mtime_ns(directory: Path) -> int | None:
    try:
        return int(directory.stat().st_mtime_ns)
    except OSError:
        return None


def _file_stat(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        is_file = path.is_file()
    except OSError:
        return None
    if not is_file:
        return None
    return int(stat.st_size), int(stat.st_mtime_ns)


def _is_complete_pdf_file(path: Path) -> bool:
    signature = _file_stat(path)
    if signature is None or signature[0] <= 0:
        return False
    size = signature[0]
    try:
        with path.open("rb") as file_obj:
            head = file_obj.read(PDF_HEADER_SCAN_BYTES)
            file_obj.seek(max(0, size - PDF_EOF_SCAN_BYTES))
            tail = file_obj.read()
    except OSError:
        return False
    return PDF_HEADER_MARKER in head and PDF_EOF_MARKER in tail


def _security_codes(filename: str) -> list[str]:
    return sorted(set(SECURITY_CODE_PATTERN.findall(filename)))


class LocalPdfManifest:
    def __init__(self, directory: str | Path, manifest_path: str | Path | None = None) -> None:
        self.directory = Path(directory)
        self.manifest_path = Path(manifest_path) if manifest_path is not None else _default_manifest_path(self.directory)
        self._payload: dict[str, Any] | None = None
        self._last_action = ""
        self._last_reason = ""

    def _load(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("schema") != MANIFEST_SCHEMA or payload.get("version") != MANIFEST_VERSION:
            return None
        if payload.get("source_directory") != self.directory.name:
            return None
        files = payload.get("files")
        codes = payload.get("codes")
        if not isinstance(files, dict) or not isinstance(codes, dict):
            return None
        return payload

    def _save(self) -> bool:
        if self._payload is None:
            return False
        try:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(
                json.dumps(self._payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return False
        return True

    def _entry_for_path(self, path: Path) -> dict[str, Any] | None:
        signature = _file_stat(path)
        if signature is None:
            return None
        size, mtime_ns = signature
        return {
            "name": path.name,
            "size": size,
            "mtime_ns": mtime_ns,
            "complete": _is_complete_pdf_file(path),
            "codes": _security_codes(path.name),
        }

    @staticmethod
    def _build_code_index(files: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
        codes: dict[str, list[str]] = {}
        for filename, entry in sorted(files.items()):
            for code in entry.get("codes") or []:
                codes.setdefault(str(code), []).append(filename)
        return codes

    def _summary(self) -> dict[str, Any]:
        payload = self._payload or {}
        files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
        complete_count = sum(1 for entry in files.values() if entry.get("complete") is True)
        return {
            "manifest_path": str(self.manifest_path),
            "source_directory": str(self.directory),
            "action": self._last_action,
            "reason": self._last_reason,
            "file_count": len(files),
            "complete_count": complete_count,
            "incomplete_count": len(files) - complete_count,
            "indexed_code_count": len(payload.get("codes") or {}),
            "generated_at": str(payload.get("generated_at") or ""),
            "full_scan_count": int(payload.get("full_scan_count") or 0),
        }

    def _scan(self, reason: str) -> dict[str, Any]:
        previous_scan_count = int((self._payload or {}).get("full_scan_count") or 0)
        files: dict[str, dict[str, Any]] = {}
        try:
            pdf_paths = sorted(self.directory.glob("*.pdf")) if self.directory.exists() else []
        except OSError:
            pdf_paths = []
        for path in pdf_paths:
            entry = self._entry_for_path(path)
            if entry is not None:
                files[path.name] = entry

        self._payload = {
            "schema": MANIFEST_SCHEMA,
            "version": MANIFEST_VERSION,
            "source_directory": self.directory.name,
            "generated_at": _now_text(),
            "directory_mtime_ns": _directory_mtime_ns(self.directory),
            "full_scan_count": previous_scan_count + 1,
            "files": files,
            "codes": self._build_code_index(files),
        }
        self._save()

        # A non-canonical manifest may live inside the indexed directory. Its
        # first creation changes the directory mtime, so persist the post-write
        # value to avoid an immediate unnecessary rebuild.
        final_directory_mtime_ns = _directory_mtime_ns(self.directory)
        if self._payload.get("directory_mtime_ns") != final_directory_mtime_ns:
            self._payload["directory_mtime_ns"] = final_directory_mtime_ns
            self._save()

        self._last_action = "rebuilt"
        self._last_reason = reason
        return self._summary()

    def ensure_current(self, *, force: bool = False) -> dict[str, Any]:
        if self._payload is None:
            self._payload = self._load()
        if force:
            return self._scan("forced")
        if self._payload is None:
            return self._scan("missing_or_invalid_manifest")

        stored_mtime_ns = self._payload.get("directory_mtime_ns")
        current_mtime_ns = _directory_mtime_ns(self.directory)
        if stored_mtime_ns != current_mtime_ns:
            return self._scan("directory_changed")

        self._last_action = "cache_hit"
        self._last_reason = "directory_unchanged"
        return self._summary()

    def _save_files(self, files: dict[str, dict[str, Any]]) -> None:
        assert self._payload is not None
        self._payload["files"] = dict(sorted(files.items()))
        self._payload["codes"] = self._build_code_index(files)
        self._payload["generated_at"] = _now_text()
        self._payload["directory_mtime_ns"] = _directory_mtime_ns(self.directory)
        self._save()

    def register(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = self.directory / file_path
        try:
            same_parent = file_path.parent.resolve() == self.directory.resolve()
        except OSError:
            same_parent = file_path.parent.absolute() == self.directory.absolute()
        if not same_parent:
            return self.ensure_current()

        if self._payload is None:
            self.ensure_current()
        assert self._payload is not None
        files = dict(self._payload.get("files") or {})
        existing_entry = files.get(file_path.name)
        entry = self._entry_for_path(file_path)
        if entry is None or file_path.suffix.lower() != ".pdf":
            changed = files.pop(file_path.name, None) is not None
        else:
            files[file_path.name] = entry
            changed = existing_entry != entry
        if not changed:
            self._last_action = "cache_hit"
            self._last_reason = "registered_file_unchanged"
            return self._summary()
        self._save_files(files)
        self._last_action = "incremental_update"
        self._last_reason = "registered_file"
        return self._summary()

    def complete_files_for_code(self, code: str) -> list[Path]:
        self.ensure_current()
        assert self._payload is not None
        normalized_code = str(code or "").strip()
        files = dict(self._payload.get("files") or {})
        filenames = list((self._payload.get("codes") or {}).get(normalized_code) or [])
        changed = False
        result: list[Path] = []
        for filename in filenames:
            entry = files.get(filename)
            if not isinstance(entry, dict):
                changed = True
                continue
            path = self.directory / filename
            signature = _file_stat(path)
            expected_signature = (entry.get("size"), entry.get("mtime_ns"))
            if signature != expected_signature:
                refreshed_entry = self._entry_for_path(path)
                changed = True
                if refreshed_entry is None:
                    files.pop(filename, None)
                    continue
                files[filename] = refreshed_entry
                entry = refreshed_entry
            if entry.get("complete") is True:
                result.append(path)

        if changed:
            self._save_files(files)
            self._last_action = "incremental_update"
            self._last_reason = "indexed_file_changed"
        return sorted(result)


_MANIFESTS: dict[tuple[str, str], LocalPdfManifest] = {}


def _cache_key(directory: Path, manifest_path: Path) -> tuple[str, str]:
    try:
        return str(directory.resolve()), str(manifest_path.resolve())
    except OSError:
        return str(directory.absolute()), str(manifest_path.absolute())


def get_local_pdf_manifest(
    directory: str | Path = DEFAULT_PDF_DIR,
    *,
    manifest_path: str | Path | None = None,
) -> LocalPdfManifest:
    source_dir = Path(directory)
    target_manifest = Path(manifest_path) if manifest_path is not None else _default_manifest_path(source_dir)
    key = _cache_key(source_dir, target_manifest)
    if key not in _MANIFESTS:
        _MANIFESTS[key] = LocalPdfManifest(source_dir, target_manifest)
    return _MANIFESTS[key]


def refresh_local_pdf_manifest(
    directory: str | Path = DEFAULT_PDF_DIR,
    *,
    manifest_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    return get_local_pdf_manifest(directory, manifest_path=manifest_path).ensure_current(force=force)


def complete_pdf_files_for_code(
    directory: str | Path,
    code: str,
    *,
    manifest_path: str | Path | None = None,
) -> list[Path]:
    return get_local_pdf_manifest(directory, manifest_path=manifest_path).complete_files_for_code(code)


def register_pdf_file(path: str | Path, *, manifest_path: str | Path | None = None) -> dict[str, Any]:
    file_path = Path(path)
    try:
        file_path = file_path.resolve()
    except OSError:
        file_path = file_path.absolute()
    return get_local_pdf_manifest(file_path.parent, manifest_path=manifest_path).register(file_path)


def clear_local_pdf_manifest_cache() -> None:
    _MANIFESTS.clear()
