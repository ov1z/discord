"""Scan an HTTP log file for PayPay endpoints and redact secrets.

Usage:
    python tools/analyze_logs.py app.log

Extracts lines mentioning app4.paypay.ne.jp / www.paypay.ne.jp endpoints and
prints them with tokens/cookies/credentials masked, so logs can be reviewed
or attached to docs/paypay-api.md safely.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from security.redaction import redact_text

_ENDPOINT_RE = re.compile(r"https?://[\w.-]*paypay\.ne\.jp[^\s\"']*")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--summary", action="store_true", help="count endpoints only")
    args = ap.parse_args()

    counter: Counter[str] = Counter()
    for line in args.log.read_text(encoding="utf-8", errors="replace").splitlines():
        matches = _ENDPOINT_RE.findall(line)
        if not matches:
            continue
        for m in matches:
            counter[m.split("?")[0]] += 1
        if not args.summary:
            print(redact_text(line))

    if args.summary:
        for endpoint, n in counter.most_common():
            print(f"{n:5d}  {endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
