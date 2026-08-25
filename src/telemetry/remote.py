"""REST synchronization for completed local telemetry sessions.

The acquisition loop never calls this module. Local SQLite/CSV storage remains
authoritative; this module uploads completed sessions from the local outbox in
bounded, retry-safe batches to the Battery.ai ingest API.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


class RemoteSyncError(RuntimeError):
    """Raised when a remote request cannot be completed."""


def load_env_file(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE entries without overriding shell variables.

    This intentionally supports the small dotenv subset needed by the Pi:
    blank lines, comments, optional ``export``, and single/double quotes.
    Environment variables already set by the shell always take precedence.
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name:
            os.environ.setdefault(name, value)


class IngestRestClient:
    """Standard-library-only client for the Battery.ai ingest API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        retry_base_s: float = 1.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = float(timeout_s)
        self.max_retries = max(0, int(max_retries))
        self.retry_base_s = max(0.0, float(retry_base_s))
        self.opener = opener
        self.sleep = sleep
        if not self.base_url or not self.token:
            raise ValueError("BASE URL and CABLE_INGEST_TOKEN are required")

    def _post(
        self,
        endpoint: str,
        rows: list[dict[str, Any]],
    ) -> None:
        url = f"{self.base_url}/ingest/v1/cable/{endpoint}"
        request = urllib.request.Request(
            url,
            data=json.dumps(rows, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )

        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.timeout_s) as response:
                    status = int(getattr(response, "status", 200))
                    response.read()
                if 200 <= status < 300:
                    return
                error = f"Ingest API {endpoint} returned HTTP {status}"
                if status not in (408, 425, 429) and not 500 <= status <= 599:
                    raise RemoteSyncError(error)
            except urllib.error.HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")
                error = f"Ingest API {endpoint} returned HTTP {exc.code}: {details[:500]}"
                if exc.code not in (408, 425, 429) and not 500 <= exc.code <= 599:
                    raise RemoteSyncError(error) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                error = f"Ingest API {endpoint} request failed: {exc}"

            if attempt >= self.max_retries:
                raise RemoteSyncError(error)
            self.sleep(self.retry_base_s * (2**attempt))

    def insert_session(self, row: dict[str, Any]) -> None:
        self._post("sessions", [row])

    def insert_samples(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._post("samples", rows)


def _json_or_none(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


# The Battery.ai ingest API only accepts probe / charge / auto.
# Local-only modes must be normalised at the API boundary so the
# session row is accepted; the original mode is preserved in probe_json.
_API_ACCEPTED_MODES = frozenset({"probe", "charge", "auto"})


def _inject_raw_mode(probe: dict | None, raw_mode: str, api_mode: str) -> dict | None:
    """Attach the original local mode when it was normalised for the API."""
    if raw_mode == api_mode:
        return probe
    probe = dict(probe) if probe else {}
    probe["_raw_mode"] = raw_mode
    return probe


def session_payload(row: Any, device_id: str) -> dict[str, Any]:
    """Map one local SQLite session row to the ingest API schema."""
    raw_mode = row["mode"] or "auto"
    api_mode = raw_mode if raw_mode in _API_ACCEPTED_MODES else "probe"
    return {
        "session_id": row["session_id"],
        "device_id": device_id,
        "mode": api_mode,
        "v_target": row["v_target"],
        "length_m": row["length_m"],
        "phone_expected": bool(row["phone_expected"]),
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "charging_detected": bool(row["charging_detected"]),
        "v_present": bool(row["v_present"]),
        "fault_reason": row["fault_reason"],
        "probe_json": _inject_raw_mode(_json_or_none(row["probe_json"]), raw_mode, api_mode),
        "verdict_json": _json_or_none(row["verdict_json"]),
        "created_at": row["created_at"],
    }


def client_from_config(cfg: dict) -> IngestRestClient | None:
    """Build a client from environment variables, or return None if unconfigured."""
    remote = cfg.get("remote", {})
    if not bool(remote.get("enabled", False)):
        return None
    load_env_file()
    url = os.environ.get("BASE", "").strip()
    token = os.environ.get("CABLE_INGEST_TOKEN", "").strip()
    if not url or not token:
        return None
    return IngestRestClient(
        url,
        token,
        timeout_s=float(remote.get("timeout_s", 10.0)),
        max_retries=int(remote.get("max_retries", 2)),
        retry_base_s=float(remote.get("retry_base_s", 1.0)),
    )


def sync_pending(storage, cfg: dict, client: IngestRestClient | None = None) -> dict[str, int]:
    """Upload pending completed sessions and return a compact sync summary."""
    remote = cfg.get("remote", {})
    result = {
        "attempted": 0,
        "completed": 0,
        "failed": 0,
        "pending": 0,
        # 1 means a client was created and credentials were available.
        # This distinguishes deferred retries from missing configuration.
        "configured": 0,
    }
    if not bool(remote.get("enabled", False)):
        result["pending"] = storage.remote_pending_count()
        return result
    client = client or client_from_config(cfg)
    if client is None:
        result["pending"] = storage.remote_pending_count()
        return result

    result["configured"] = 1
    device_id = os.environ.get("CABLE_INGEST_DEVICE_ID", remote.get("device_id", "pi-zero-2w-01"))
    batch_size = max(1, int(remote.get("batch_size", 500)))
    for queued in storage.pending_remote_sessions():
        session_id = queued["session_id"]
        result["attempted"] += 1
        try:
            session = storage.get_session(session_id)
            if session is None:
                raise RemoteSyncError(f"local session {session_id} does not exist")
            client.insert_session(session_payload(session, device_id))
            offset = 0
            for batch in storage.iter_sample_batches(session_id, batch_size):
                rows = [
                    {
                        "session_id": sample["session_id"],
                        "device_id": device_id,
                        "sample_index": offset + index,
                        "t": sample["t"],
                        "voltage_v": sample["voltage"],
                        "current_a": sample["current"],
                        "power_w": sample["power"],
                        "state": sample["state"],
                        "valid": bool(sample["valid"]),
                    }
                    for index, sample in enumerate(batch)
                ]
                client.insert_samples(rows)
                offset += len(batch)
            storage.mark_remote_complete(session_id)
            result["completed"] += 1
        except Exception as exc:  # remote failure must not stop local analysis
            delay = float(remote.get("retry_base_s", 1.0)) * (2 ** min(int(queued["attempt_count"]), 6))
            storage.mark_remote_failed(session_id, str(exc), delay)
            result["failed"] += 1

    result["pending"] = storage.remote_pending_count()
    return result
