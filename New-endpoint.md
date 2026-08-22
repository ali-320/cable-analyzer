# Cable Analyzer — Battery.ai Ingest API (New Approach)

## Context

The Cable Analyzer device (`pi-zero-2w-01`) previously wrote telemetry data **directly to Supabase** using a publishable/anon key. This is being replaced with a new approach: the device now sends telemetry to a dedicated **Battery.ai ingest API** (a separate backend service), authenticated with a per-device secret token instead of a Supabase key. The device no longer needs to know anything about the underlying database schema — it just POSTs JSON to two fixed REST endpoints, and the Battery.ai server handles storage on its own.

This document summarizes that new API so it can be implemented in the device's firmware (`src/telemetry/remote.py`) or wherever the ingest logic lives.

## Base URL

```
https://battery-ai.replit.app/api
```

## Endpoints

|Purpose|Method + Path|
|---|---|
|Session upsert|`POST /api/ingest/v1/cable/sessions`|
|Sample batch upsert|`POST /api/ingest/v1/cable/samples`|
|Token rotation (guarded, owner-only)|`POST /api/ingest/v1/cable/rotate-token`|

Both ingest routes are **idempotent upserts**:

- Sessions are keyed on `session_id`.
- Samples are keyed on `(session_id, sample_index)`.

Re-sending a request after a timeout or crash is always safe — the row is updated in place, never duplicated. There is no ordering requirement between sending the session row and the sample batches.

Every payload must include `device_id` equal to `pi-zero-2w-01` (configurable via the `CABLE_INGEST_DEVICE_ID` env var). A mismatch returns `409 DEVICE_MISMATCH`.

## Authentication

- A single **static device token** authenticates the device — issued to the owner at provisioning time (see "Provisioning" below).
- The server only ever stores the token's **SHA-256 hash** (in the `cable_ingest_config` DB row). The plaintext exists only on the device and with the owner.
- Send it either way (both accepted, compared constant-time):
    - `Authorization: Bearer <token>`
    - HTTP Basic auth, token as the password, username ignored (`Authorization: Basic base64("pi-zero-2w-01:<token>")`)

## Provisioning (how the token comes to exist)

There is no self-service way for the device to obtain a token. It's a manual, server-side bootstrap step done by the owner of the Battery.ai deployment:

1. The owner picks a secret token and computes its SHA-256 hash.
2. That hash is set as a server config value (`CABLE_INGEST_TOKEN_SHA256`).
3. On first use, the server seeds the `cable_ingest_config` DB row from that hash.
4. The plaintext token is handed to the device owner out-of-band and stored only in the device's secret/config store (`CABLE_INGEST_TOKEN`), never committed to source control or logged.

To re-provision manually, the workspace owner sets `CABLE_INGEST_TOKEN_SHA256` to a new hash and deletes the `cable_ingest_config` row; it re-seeds from that hash on the next request.

## Rotating the token

Locked behind a separate **rotation code** held only by the owner (`CABLE_INGEST_ROTATION_CODE`, stored server-side only as a SHA-256 hash — plaintext exists nowhere in the repo or server env). This is a manual, guarded operation, not something the device does routinely.

```bash
curl -sS -X POST "$BASE/ingest/v1/cable/rotate-token" \
  -H "Content-Type: application/json" \
  -d '{ "rotation_code": "<the CABLE_INGEST_ROTATION_CODE value>" }'
```

- Success: `{ "ok": true, "token": "cat_..." }` — this is the **only time** the new plaintext token is shown. Copy it into `CABLE_INGEST_TOKEN` immediately.
- The old token stops working immediately (checked live, no restart needed).
- Wrong/missing code → `403 ROTATION_FORBIDDEN`, no hint given. Rate-limited to 5 attempts per 15 minutes per IP, and logged.

## Payload shapes

### Session upsert

Single object or a one-element array, both accepted.

```json
{
  "session_id": "20260812T093917321424",
  "device_id": "pi-zero-2w-01",
  "mode": "probe",
  "v_target": 5.0,
  "length_m": 1.0,
  "phone_expected": true,
  "started_at": 1755002357.0,
  "ended_at": 1755002392.0,
  "charging_detected": true,
  "v_present": true,
  "fault_reason": null,
  "probe_json": {
    "steps": [
      { "v_target": 5.0, "i": 1.0, "v_load": 4.921, "r_loop_mohm": 79.0, "r_cable_mohm": 17.9 }
    ],
    "r_fixture": 0.0
  },
  "verdict_json": {
    "session_id": "20260812T093917321424",
    "model": "rules-v1",
    "verdict": "Grade A: Excellent",
    "grade": "A",
    "tags": ["HIGH_LOSS"],
    "confidence": 0.873
  },
  "created_at": "2026-08-12T09:39:17.321424+00:00"
}
```

Response: `{ "ok": true, "session_id": "20260812T093917321424" }`

Notes:

- `started_at` / `ended_at` are epoch seconds and may be `null` (probe mode).
- `probe_json` / `verdict_json` are stored verbatim as JSONB; each blob is capped at 256 KB serialized.

### Sample batch upsert

Array of up to 500 rows per request.

```json
[
  {
    "session_id": "20260812T093917321424",
    "device_id": "pi-zero-2w-01",
    "sample_index": 0,
    "t": 1.2345,
    "voltage_v": 4.98,
    "current_a": 0.512,
    "power_w": 2.55,
    "state": "CHARGING",
    "valid": true
  }
]
```

Response: `{ "ok": true, "upserted": 2 }`

`state` must be one of: `NO_SOURCE | NO_PHONE | CHARGING | CHARGED | FAULT | VERIFICATION | PROBE`

## Batching & retry rules

- Max 500 samples per POST. A full 25 Hz session must be split into consecutive batches; `sample_index` must be globally unique per session, starting at 0.
- All writes are retry-safe upserts — on any network timeout or `500`, just re-send the same request. Duplicates are not possible.
- On any `4xx` other than `429`, do **not** blind-retry — the request itself is malformed or invalid; fix it first.

## Error contract

Every failure returns JSON in a stable shape (never HTML or a stack trace):

```json
{ "error_code": "...", "message": "..." }
```

|Status|error_code|Meaning|Device behavior|
|---|---|---|---|
|401|`AUTH_MISSING`|No/malformed `Authorization` header|Fix header format; don't retry as-is|
|401|`AUTH_INVALID`|Token doesn't match|Re-provision token (may have rotated); don't retry same token|
|403|`ROTATION_FORBIDDEN`|Rotation attempted with wrong/no code|Manual operator action only|
|400|`MALFORMED_JSON`|Body isn't valid JSON|Bug — fix serialization; don't retry|
|400|`VALIDATION_FAILED`|Schema violation (fields listed in message)|Fix payload; don't retry unchanged|
|413|`PAYLOAD_TOO_LARGE`|Body > 2 MB, JSONB blob > 256 KB, or > 500 samples/batch|Split into smaller batches, then retry|
|409|`DEVICE_MISMATCH`|Payload `device_id` isn't the expected device|Fix configured device id; don't retry unchanged|
|429|`RATE_LIMITED`|Too many requests (ingest: 300/min per IP; rotation: 5/15min per IP)|Back off and retry later|
|500|`INTERNAL`|Server-side failure|Safe to retry with backoff (upserts are idempotent)|

## Firmware config summary (`src/telemetry/remote.py`)

1. Base URL: `https://battery-ai.replit.app/api`
2. Two endpoint paths as above; no conflict/query params needed (conflict targets are fixed server-side).
3. Send `Authorization: Bearer <CABLE_INGEST_TOKEN>` (or Basic auth with the token as password).
4. Responses are compact JSON acknowledgements only. Payload shapes, batching, and retry-on-failure behavior are otherwise unchanged from existing logic.

## What changes vs. the previous (Supabase) approach

||Old (Supabase direct)|New (Battery.ai ingest)|
|---|---|---|
|Destination|Supabase directly|Battery.ai API server|
|Credential|Supabase publishable/anon key|Per-device secret token (`CABLE_INGEST_TOKEN`)|
|Access control|Supabase Row Level Security policies|Server validates `device_id` + token hash per request|
|Data contract|Raw Supabase table schema|Documented JSON contract, decoupled from underlying storage|
|Duplicate-safety|Handled by client code|Built into the API via keyed upserts|

## Env vars needed on the device

```
CABLE_INGEST_BASE_URL=https://battery-ai.replit.app/api
CABLE_INGEST_TOKEN=<secret token from provisioning — not yet obtained>
CABLE_INGEST_DEVICE_ID=pi-zero-2w-01
```

Do **not** put `CABLE_INGEST_ROTATION_CODE` on the device — it's an owner-only credential for the rotation endpoint, separate from normal device auth.

## Open questions (not yet resolved with the app dev team)

- Is the Supabase connection being fully replaced, or still needed for anything else?
- What is the actual `CABLE_INGEST_TOKEN` value for `pi-zero-2w-01`, and has it been provisioned yet?
- Does firmware need to read `CABLE_INGEST_DEVICE_ID` specifically, or can it keep using the existing `DEVICE_ID` var name?
- Should the base URL be an env var or hardcoded in firmware?
- Is there a data migration/backfill plan from the old Supabase-direct data?
- Is the 300 req/min rate limit sufficient for the device's 25 Hz sampling and batching pattern?