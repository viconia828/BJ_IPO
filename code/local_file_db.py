from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class LocalFileDB:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.fixed_dir = self.root / "fixed_fields"
        self.variable_dir = self.root / "variable_fields"
        self.meta_dir = self.root / "meta"

    @staticmethod
    def normalize_code(code: str) -> str:
        return str(code).strip().upper()

    def _code_path(self, directory: Path, code: str) -> Path:
        return directory / f"{self.normalize_code(code)}.json"

    def load_fixed_record(self, code: str) -> dict[str, Any] | None:
        return _read_json(self._code_path(self.fixed_dir, code), None)

    def save_fixed_record(
        self,
        code: str,
        fields: dict[str, Any],
        source: str = "wind_api",
    ) -> dict[str, Any]:
        normalized_code = self.normalize_code(code)
        record = self.load_fixed_record(normalized_code) or {
            "schema": "wind_fixed_v1",
            "code": normalized_code,
            "fields": {},
        }
        record["fields"].update({key: value for key, value in fields.items() if value not in (None, "")})
        record["source"] = source
        record["updated_at"] = _now_iso()
        _write_json(self._code_path(self.fixed_dir, normalized_code), record)
        return record

    def load_variable_record(self, code: str) -> dict[str, Any] | None:
        return _read_json(self._code_path(self.variable_dir, code), None)

    def save_variable_record(
        self,
        code: str,
        fields: dict[str, Any],
        trade_date: str | None = None,
        source: str = "wind_api",
    ) -> dict[str, Any]:
        normalized_code = self.normalize_code(code)
        record = self.load_variable_record(normalized_code) or {
            "schema": "wind_variable_v1",
            "code": normalized_code,
            "fields": {},
        }
        record["fields"].update({key: value for key, value in fields.items() if value not in (None, "")})
        record["source"] = source
        record["updated_at"] = _now_iso()
        if trade_date:
            record["trade_date"] = trade_date
        _write_json(self._code_path(self.variable_dir, normalized_code), record)
        return record

    def request_log_path(self) -> Path:
        return self.meta_dir / "request_log.json"

    def load_request_log(self) -> dict[str, Any]:
        return _read_json(self.request_log_path(), {"schema": "wind_request_log_v1", "events": []})

    def append_request_event(self, event: dict[str, Any]) -> None:
        payload = self.load_request_log()
        events = payload.setdefault("events", [])
        entry = dict(event)
        entry.setdefault("timestamp", _now_iso())
        events.append(entry)
        payload["events"] = events[-500:]
        _write_json(self.request_log_path(), payload)

    def get_today_api_call_count(
        self,
        current_date: date | None = None,
        source: str | None = "wind",
    ) -> int:
        today = (current_date or date.today()).isoformat()
        payload = self.load_request_log()
        count = 0
        for event in payload.get("events", []):
            if event.get("event_type") != "api_call":
                continue
            if source is not None and event.get("source", "wind") != source:
                continue
            timestamp = str(event.get("timestamp", ""))
            if timestamp[:10] == today:
                count += 1
        return count
