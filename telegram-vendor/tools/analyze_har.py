"""Analyze a HAR capture to discover PayPay API request/response shapes.

Usage:
    python tools/analyze_har.py capture.har [--host paypay.ne.jp]

Prints, per matching request: METHOD, URL path, request content-type, and the
top-level keys of JSON request/response bodies. Sensitive values are redacted
so the output is safe to paste into docs/paypay-api.md.

This does NOT contact PayPay; it only parses a HAR you captured yourself.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from security.redaction import redact


def _json_keys(text: str | None) -> object:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return "<non-json>"
    if isinstance(data, dict):
        return sorted(data.keys())
    return type(data).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("har", type=Path)
    ap.add_argument("--host", default="paypay.ne.jp")
    args = ap.parse_args()

    har = json.loads(args.har.read_text(encoding="utf-8"))
    entries = har.get("log", {}).get("entries", [])
    for e in entries:
        req = e.get("request", {})
        resp = e.get("response", {})
        url = req.get("url", "")
        if args.host not in url:
            continue
        req_body = req.get("postData", {}).get("text")
        resp_body = resp.get("content", {}).get("text")
        print("=" * 70)
        print(f"{req.get('method')} {url}")
        print(f"  status: {resp.get('status')}")
        print(f"  request keys : {redact(_json_keys(req_body))}")
        print(f"  response keys: {redact(_json_keys(resp_body))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
