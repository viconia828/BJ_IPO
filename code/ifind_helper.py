"""Small, auditable iFind HTTP adapter. Credentials never enter saved evidence.

Only live-verified basic-data fields are registered here. Unknown indicators must
be verified before being added; MCP labels are not guessed into API field names.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid

BASE_URL = "https://quantapi.51ifind.com/api/v1/"
FIELDS = {"ths_revenue_stock": "revenue", "ths_np_atoopc_stock": "net_profit_parent"}
AUTH_KEYS = {"access_token", "refresh_token", "token", "authorization", "password"}


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="microseconds")


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in AUTH_KEYS else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IFindClient:
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.access = os.getenv("IFIND_AUTH_TOKEN", "")
        self.refresh = os.getenv("IFIND_REFRESH_TOKEN", "")
        self.secrets = [self.access, self.refresh]
        self.opener = urllib.request.build_opener(NoRedirect)

    def _post(self, endpoint, body, header, token):
        request = urllib.request.Request(BASE_URL + endpoint,
            data=json.dumps(body).encode("utf-8") if body is not None else b"",
            headers={"Content-Type": "application/json", "ifindlang": "cn", header: token})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw, status = response.read(8_000_000), response.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(100_000), exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"http_status": None, "transport_error": type(exc).__name__}
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeError):
            return {"http_status": status, "transport_error": "invalid_json"}
        return {"http_status": status, "response": parsed}

    def _refresh(self):
        if not self.refresh:
            return False
        result = self._post("get_access_token", None, "refresh_token", self.refresh)
        token = ((result.get("response") or {}).get("data") or {}).get("access_token")
        if isinstance(token, str) and token:
            self.access = token
            self.secrets.append(token)
            return True
        return False

    def basic_data(self, body):
        if not self.access and not self._refresh():
            return {"http_status": None, "transport_error": "credentials_unavailable"}
        result = self._post("basic_data_service", body, "access_token", self.access)
        if result.get("http_status") == 401 and self._refresh():
            result = self._post("basic_data_service", body, "access_token", self.access)
        return redact(result, self.secrets)


def validate_period(period: str) -> str:
    parsed = datetime.strptime(period.replace("-", ""), "%Y%m%d")
    if parsed.strftime("%m%d") not in {"0331", "0630", "0930", "1231"}:
        raise ValueError("财务报告期必须为季度末")
    return parsed.strftime("%Y%m%d")


def fetch_financials(codes: list[str], periods: list[str], root: Path, *, refresh=False, client=None):
    """Cache immutable request/response snapshots; reuse only successful requests."""
    codes = sorted(set(c.upper().strip() for c in codes))
    if not codes or any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ|NQ)", c) for c in codes):
        raise ValueError("请使用完整证券代码")
    client = client or IFindClient()
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for period in sorted(set(validate_period(p) for p in periods)):
        body = {"codes": ",".join(codes), "indipara": [
            {"indicator": field, "indiparams": [period, "100"]} for field in FIELDS]}
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:20]
        cached = None
        if not refresh:
            for path in sorted(root.glob(f"{key}_*.json"), reverse=True):
                try:
                    candidate = json.loads(path.read_text("utf-8"))
                except (ValueError, OSError):
                    continue
                if (candidate.get("request") == body and candidate.get("http_status") == 200
                        and candidate.get("response", {}).get("errorcode") == 0):
                    cached = (candidate, path)
                    break
        if cached:
            results.append({**cached[0], "evidence_path": str(cached[1]), "cache_hit": True})
            continue
        envelope = {"schema": "ifind_http_evidence_v1", "retrieved_at": now_iso(),
                    "source": BASE_URL + "basic_data_service", "request": body,
                    **client.basic_data(body)}
        filename = f"{key}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}.json"
        path = root / filename
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(envelope, handle, ensure_ascii=False, indent=2)
        results.append({**envelope, "evidence_path": str(path), "cache_hit": False})
    return results
