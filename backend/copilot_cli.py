"""CLI to run the AI Market Copilot end-to-end on live data.

Usage:
    python -m backend.copilot_cli BTCUSDT
    python -m backend.copilot_cli ETHUSDT --no-kronos   # faster, skips CPU forecast
"""
from __future__ import annotations

import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser(description="AI Market Copilot")
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--no-kronos", action="store_true", help="skip the slow CPU range forecast")
    args = ap.parse_args()

    from backend.signals.copilot import analyze_symbol

    print(f"\n  Analyzing {args.symbol} ({args.interval})"
          f"{' [no Kronos]' if args.no_kronos else ''}...\n", file=sys.stderr)

    result = analyze_symbol(args.symbol, args.interval, include_kronos=not args.no_kronos)

    lean = result.get("lean", "?").upper()
    conv = result.get("conviction", "?")
    print("=" * 60)
    print(f"  {args.symbol}  ->  {lean}   (conviction {conv}/100)")
    print("=" * 60)
    print(f"\n  {result.get('summary', '')}\n")

    if result.get("drivers"):
        print("  DRIVERS:")
        for d in result["drivers"]:
            print(f"    + {d}")
    if result.get("risks"):
        print("\n  RISKS:")
        for r in result["risks"]:
            print(f"    ! {r}")
    if result.get("range_24h"):
        rng = result["range_24h"]
        print(f"\n  24h RANGE (Kronos): {rng.get('low')} - {rng.get('high')}")
    if result.get("suggested_invalidation"):
        print(f"  INVALIDATION: {result['suggested_invalidation']}")

    print(f"\n  cost: ${result.get('cost_usd', 0)}   |   {result.get('disclaimer','')}")
    print("\n--- raw JSON ---")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
