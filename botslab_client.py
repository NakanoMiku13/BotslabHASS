#!/usr/bin/env python3
"""
botslab_client.py — read-only cloud client for Botslab (360 / Qihoo) robot vacuums.

Reconstructed by reverse-engineering the official "Botslab" Android app
(``com.visualworld.cloudsmart``, a Flutter app). It talks to the Botslab cloud
("sapp-api") and reads the device shadow (live + desired state), using a 360
account session captured once from the app.

Pure Python standard library — no third-party dependencies.

ALL secrets and account/device identifiers are read from the environment (see
``.env.example``). Nothing sensitive is hard-coded. Copy ``.env.example`` to
``.env`` and fill it in, or export the variables yourself.

Usage:
    python3 botslab_client.py state       # human-readable summary (default)
    python3 botslab_client.py list        # raw device list
    python3 botslab_client.py info        # device info
    python3 botslab_client.py property     # live reported state (richest)
    python3 botslab_client.py desired     # desired + reported shadow
    python3 botslab_client.py selftest    # offline signer sanity check

See docs/ for the full protocol and the reverse-engineering write-up.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


# --------------------------------------------------------------------------- #
# Minimal .env loader (no dependency on python-dotenv)
# --------------------------------------------------------------------------- #
def _load_dotenv(path: str = ".env") -> None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)
    except FileNotFoundError:
        pass


_load_dotenv(os.environ.get("BOTSLAB_ENV_FILE", ".env"))


def _env(name: str, default: str | None = None) -> str:
    return os.environ.get(name, default) or ""


# Required for live requests (validated lazily, so `selftest`/tests work offline).
_REQUIRED = ("BOTSLAB_SIGN_SECRET", "BOTSLAB_M2", "BOTSLAB_PRODUCT_KEY",
             "BOTSLAB_DEVICE_NAME", "BOTSLAB_Q", "BOTSLAB_T", "BOTSLAB_SID")


def _require_config() -> None:
    missing = [n for n in _REQUIRED if not os.environ.get(n)]
    if missing:
        sys.exit(
            "[config] Missing required environment variable(s): "
            + ", ".join(missing)
            + "\n         Copy .env.example to .env and fill it in (see docs/SESSION-CAPTURE.md)."
        )


# --------------------------------------------------------------------------- #
# Configuration (everything overridable via env / .env)
# --------------------------------------------------------------------------- #
REGION = _env("BOTSLAB_REGION", "na1")                 # na1 / ap1 / eu1 / eu2
APPKEY = _env("BOTSLAB_APPKEY", "botslabadr")          # public app identifier
APPVER = _env("BOTSLAB_APPVER", "2.31.2")

# Secrets / identifiers — never shipped in this repo; validated lazily.
SIGN_SECRET = _env("BOTSLAB_SIGN_SECRET")   # app signing secret (constant inside the APK)
M2 = _env("BOTSLAB_M2")                       # app installation UUID
PRODUCT_KEY = _env("BOTSLAB_PRODUCT_KEY")     # device product key
DEVICE_NAME = _env("BOTSLAB_DEVICE_NAME")     # device name / serial

# 360 account session (captured from the app). See docs/SESSION-CAPTURE.md.
SESSION_Q = _env("BOTSLAB_Q")                # 360 "Q" cookie (URL-encoded)
SESSION_T = _env("BOTSLAB_T")                # 360 "T" cookie (URL-encoded)
SESSION_SID = _env("BOTSLAB_SID")            # cloudspirit session id (MANDATORY for /v1/iot/*)

HOST = f"{REGION}-sapp-api.botslab.com"

# "Client info" parameters — describe the phone making the request. Not secret
# (sent in cleartext on every request); defaults mimic the reference Android
# client. The device/dio layer uses appch=fwq, ci_brand=google, skillflag=online.
CI_PARAMS = {
    "ci_net": _env("BOTSLAB_CI_NET", "WIFI"),
    "ci_model": _env("BOTSLAB_CI_MODEL", "sdk_gphone64_arm64"),
    "ci_lang": _env("BOTSLAB_CI_LANG", "en-US"),
    "ci_cy": _env("BOTSLAB_CI_CY", "US"),
    "ci_brand": _env("BOTSLAB_CI_BRAND", "google"),
    "ci_osver": _env("BOTSLAB_CI_OSVER", "35"),
    "ci_tz": _env("BOTSLAB_CI_TZ", "UTC-6"),
    "appver": APPVER,
    "appch": "fwq",
    "app_type_id": "1",
    "appflag": "online",
    "skillflag": "online",
}

TIMEOUT = int(_env("BOTSLAB_TIMEOUT", "12"))


def _ssl_ctx() -> ssl.SSLContext:
    if os.environ.get("BOTSLAB_INSECURE", "0") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi  # optional; only if installed

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL = _ssl_ctx()


# --------------------------------------------------------------------------- #
# Request signer
#   sign = md5(appkey + m2 + sign_ts + sign_no + secret).hexdigest().lower()
#   sign_ts = unix seconds ; sign_no = UUID v1 hex (time-based, 32 hex chars)
# --------------------------------------------------------------------------- #
def make_sign(appkey: str, m2: str, sign_ts: str, sign_no: str, secret: str) -> str:
    return hashlib.md5((appkey + m2 + sign_ts + sign_no + secret).encode()).hexdigest().lower()


def sign_fields() -> dict:
    sign_ts = str(int(time.time()))
    sign_no = uuid.uuid1().hex  # time-based UUID, as the app does
    return {
        "appkey": APPKEY,
        "m2": M2,
        "sign_ts": sign_ts,
        "sign_no": sign_no,
        "sign": make_sign(APPKEY, M2, sign_ts, sign_no, SIGN_SECRET),
    }


def _params(extra: dict | None = None) -> list[tuple[str, str]]:
    merged = dict(CI_PARAMS)
    merged.update(sign_fields())
    if extra:
        merged.update(extra)
    return list(merged.items())


# --------------------------------------------------------------------------- #
# Authorization token
#   authorization: "jws " + base64(json({"Q": Q, "T": T, "sid": SID}))
#   The "sid" field is MANDATORY for /v1/iot/* and /v1/user/* endpoints;
#   without it the gateway replies code 1015 "Bad request".
# --------------------------------------------------------------------------- #
def build_authorization(q: str = SESSION_Q, t: str = SESSION_T, sid: str = SESSION_SID) -> str:
    payload = {"Q": q, "T": t, "sid": sid}
    return "jws " + base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _get(path: str, extra: dict | None = None) -> tuple[int | None, dict | str]:
    _require_config()
    url = f"https://{HOST}{path}?" + urllib.parse.urlencode(_params(extra))
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "okhttp/4.2.0")
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("authorization", build_authorization())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            text = raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, f"<error: {exc}>"
    try:
        return 200, json.loads(text)
    except Exception:  # noqa: BLE001
        return 200, text


# --------------------------------------------------------------------------- #
# Read endpoints
# --------------------------------------------------------------------------- #
def device_list() -> tuple[int | None, dict | str]:
    return _get("/v1/iot/device/list", {"bind_type": "0"})


def get_info() -> tuple[int | None, dict | str]:
    return _get("/v1/iot/device/get_info", {"product_key": PRODUCT_KEY, "device_name": DEVICE_NAME})


def get_property() -> tuple[int | None, dict | str]:
    return _get("/v1/iot/device/get_property", {"product_key": PRODUCT_KEY, "device_name": DEVICE_NAME})


def get_desired_property() -> tuple[int | None, dict | str]:
    return _get(
        "/v1/iot/device/get_desired_property",
        {"product_key": PRODUCT_KEY, "device_name": DEVICE_NAME},
    )


# --------------------------------------------------------------------------- #
# State helpers
# --------------------------------------------------------------------------- #
def parse_property(resp: dict) -> dict:
    """Flatten /get_property -> {identifier: value} from data.result (a JSON string)."""
    try:
        result = resp["data"]["result"]
        return json.loads(result) if isinstance(result, str) else dict(result)
    except Exception:  # noqa: BLE001
        return {}


def parse_desired(resp: dict) -> dict:
    """Flatten /get_desired_property report[] -> {identifier: value}."""
    out: dict = {}
    try:
        for item in resp.get("data", {}).get("report", []):
            out[item["identifier"]] = item.get("value")
    except Exception:  # noqa: BLE001
        pass
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print(title: str, code: int | None, body) -> None:
    print(f"\n### {title}  -> HTTP {code}")
    if isinstance(body, (dict, list)):
        print(json.dumps(body, indent=2, ensure_ascii=False)[:4000])
    else:
        print(str(body)[:2000])


def cmd_state() -> None:
    code, body = get_property()
    if code != 200 or not isinstance(body, dict) or body.get("code") != 0:
        _print("get_property FAILED", code, body)
        return
    props = parse_property(body)
    interesting = [
        "BatteryLevel", "TEST_WorkingStatus", "CleanSubStatus", "ErrorCode",
        "AutoBoost", "MopModeSwitching", "MoppingPadInstalled", "PadWetness",
        "VolumeLevel", "CleaningArea", "CurrentLocation", "RobotOnChargePile",
        "FilterLifeTime", "EdgeBrushLifeTime",
    ]
    print(f"== Device {DEVICE_NAME} ({PRODUCT_KEY}) ==")
    for key in interesting:
        if key in props:
            print(f"  {key:22s}: {props[key]}")
    missing = [k for k in interesting if k not in props]
    if missing:
        print(f"  (not reported: {', '.join(missing)})")
    print(f"\n  total properties reported: {len(props)}")


def cmd_selftest() -> None:
    """Offline check that the signer concatenation order is stable."""
    sig = make_sign("APPKEY", "M2", "1700000000", "NONCE", "SECRET")
    expected = hashlib.md5(b"APPKEYM21700000000NONCESECRET").hexdigest()
    ok = sig == expected
    print(f"signer order check: {'OK' if ok else 'FAIL'} ({sig})")
    sys.exit(0 if ok else 1)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "state"
    if cmd == "selftest":
        cmd_selftest()
    elif cmd == "state":
        cmd_state()
    elif cmd == "list":
        _print("device/list", *device_list())
    elif cmd == "info":
        _print("device/get_info", *get_info())
    elif cmd == "property":
        _print("device/get_property", *get_property())
    elif cmd == "desired":
        _print("device/get_desired_property", *get_desired_property())
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
