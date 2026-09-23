#!/usr/bin/env python3
"""Pretty-print the (redacted) structure of a captured PayPay JSON response.

Usage:
    python tools/analyze_json.py response.json

Useful for mapping a real getP2PLinkInfo / token response onto the models in
src/paypay/models.py without leaking secrets.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from security.redaction import redact  # noqa: E402


def _schema(value: object, depth: int = 0) -> object:
    if isinstance(value, dict):
        return {k: _schema(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [f"list[{len(value)}]"] + (
            [_schema(value[0], depth + 1)] if value else []
        )
    return type(value).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json", type=Path)
    ap.add_argument("--schema", action="store_true", help="print type schema only")
    args = ap.parse_args()

    data = json.loads(args.json.read_text(encoding="utf-8"))
    if args.schema:
        print(json.dumps(_schema(data), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(redact(data), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
