#!/usr/bin/env python3
"""extract_session.py — turn a decrypted capture into botslab-cloud config.

Given a PCAPdroid pcap + its keylog (TLS secrets), this decrypts the HTTP/2
stream with tshark, finds a /v1/iot/device/* request, and prints the values
you need (Q, T, sid, m2, product_key, device_name) as .env lines — so refreshing
an expired session is one command instead of manual decoding.

Usage:
    python3 tools/extract_session.py --pcap capture.pcap --keylog capture.keylog
    python3 tools/extract_session.py --pcap capture.pcap --keylog capture.keylog --env > .env

Requires tshark (Wireshark CLI) on PATH.

See docs/SESSION-CAPTURE.md for how to produce the pcap + keylog.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.parse


def _tshark_rows(pcap: str, keylog: str) -> list[list[str]]:
    cmd = [
        "tshark", "-r", pcap, "-o", f"tls.keylog_file:{keylog}",
        "-Y", 'http2.header.name == ":path"',
        "-T", "fields", "-e", "http2.header.value",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    except FileNotFoundError:
        sys.exit("tshark not found on PATH (install Wireshark).")
    return [ln.split(",") for ln in out.splitlines() if "iot/device" in ln]


def _decode_auth(token: str) -> dict:
    token = token.strip()
    if token.lower().startswith("jws "):
        token = token[4:]
    return json.loads(base64.b64decode(token + "=" * (-len(token) % 4)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcap", required=True)
    ap.add_argument("--keylog", required=True)
    ap.add_argument("--env", action="store_true", help="print as .env lines")
    args = ap.parse_args()

    for row in _tshark_rows(args.pcap, args.keylog):
        path = next((c for c in row if c.startswith("/v1/iot/device/")), None)
        auth = next((c for c in row if c.strip().lower().startswith("jws ")), None)
        if not path or not auth:
            continue
        qs = urllib.parse.parse_qs(path.split("?", 1)[1]) if "?" in path else {}
        sess = _decode_auth(auth)
        out = {
            "BOTSLAB_Q": sess.get("Q", ""),
            "BOTSLAB_T": sess.get("T", ""),
            "BOTSLAB_SID": sess.get("sid", ""),
            "BOTSLAB_M2": qs.get("m2", [""])[0],
            "BOTSLAB_PRODUCT_KEY": qs.get("product_key", [""])[0],
            "BOTSLAB_DEVICE_NAME": qs.get("device_name", [""])[0],
        }
        if not out["BOTSLAB_SID"]:
            continue
        if args.env:
            for k, v in out.items():
                print(f"{k}={v}")
        else:
            print(json.dumps(out, indent=2))
        return
    sys.exit("No decryptable /v1/iot/device/* request found (check keylog matches pcap).")


if __name__ == "__main__":
    main()
