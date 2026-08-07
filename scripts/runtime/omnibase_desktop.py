"""Small cross-platform capability and port diagnostic entry point.

This CLI intentionally reports facts and safe recommendations. It does not
start a production runtime, execute arbitrary commands, or read secret values.
"""

from __future__ import annotations

import argparse
import json

from omnibase.runtime.capabilities import ProductMode, probe_capabilities, suggest_port
from omnibase.runtime.diagnostics import diagnostics_payload, select_mode


def main() -> int:
    parser = argparse.ArgumentParser(description="OmniBase local capability diagnostics")
    parser.add_argument("--port", type=int, action="append", default=[8000, 3000])
    parser.add_argument("--suggest-port", type=int)
    parser.add_argument("--mode", choices=[item.value for item in ProductMode])
    args = parser.parse_args()

    if args.suggest_port is not None:
        suggestion = suggest_port(args.suggest_port)
        print(json.dumps({"requested": args.suggest_port, "suggested": suggestion}))
        return 0 if suggestion is not None else 2

    report = probe_capabilities(args.port)
    try:
        selected = select_mode(report, ProductMode(args.mode) if args.mode else None)
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "capabilities": report.to_dict()}))
        return 2

    payload = diagnostics_payload(report)
    payload["selected_mode"] = selected.value
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
