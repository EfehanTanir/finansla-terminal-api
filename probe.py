"""Dump real Fonoloji API response shapes so the mappers can be finalised.

Usage:
    export FONOLOJI_API_KEY="fon_..."
    python probe.py

It hits the endpoints the dashboard uses, prints each one's JSON (long arrays
truncated to 2 items), and reports status codes. Paste the output back and the
field mappings in app/providers/fonoloji.py can be locked in exactly.
Read-only GETs; safe to run. Uses ~8 requests of your monthly quota.
"""
import json
import os
import sys

import httpx

KEY = os.environ.get("FONOLOJI_API_KEY", "")
if not KEY:
    sys.exit("Set FONOLOJI_API_KEY first:  export FONOLOJI_API_KEY='fon_...'")

BASE = "https://fonoloji.com/v1"
PATHS = [
    "/market/live",
    "/gold/live",
    "/market/digest",
    "/summary/today",
    "/market/movers",
    "/categories",
    "/insights/flow",
    "/funds?sort=aum&order=desc&limit=3",
]


def trunc(obj, n=2):
    if isinstance(obj, list):
        return [trunc(x, n) for x in obj[:n]] + (["…(+%d)" % (len(obj) - n)] if len(obj) > n else [])
    if isinstance(obj, dict):
        return {k: trunc(v, n) for k, v in obj.items()}
    return obj


with httpx.Client(base_url=BASE, headers={"X-API-Key": KEY}, timeout=20.0) as c:
    for p in PATHS:
        print("\n" + "=" * 70)
        print("GET", p)
        try:
            r = c.get(p)
            print("status:", r.status_code, "| x-cache:", r.headers.get("x-cache", "-"),
                  "| remaining:", r.headers.get("x-ratelimit-remaining-monthly", "-"))
            if r.headers.get("content-type", "").startswith("application/json"):
                print(json.dumps(trunc(r.json()), ensure_ascii=False, indent=2)[:2500])
            else:
                print("(non-JSON body)")
        except Exception as e:  # noqa: BLE001
            print("ERROR:", e)
