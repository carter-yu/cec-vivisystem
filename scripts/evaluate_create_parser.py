"""Synthetic extraction evaluation. No Slack/Google access; live xAI is explicit.

Run from the checkout: python -m scripts.evaluate_create_parser --mode rules
For live calls, export credentials separately and add --mode llm --live.
This command never loads .env or writes logs/stores. Exit 1 means any case failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from cec_vivisystem.logging import setup_logging
from cec_vivisystem.parse_fallback import (
    PROMPT_PATH,
    load_live_llm_parsers,
    parse_with_fallback,
)
from cec_vivisystem.parser import parse

NOW = datetime.fromisoformat("2026-10-05T09:00:00+08:00")
CASES_PATH = Path(__file__).resolve().parents[1] / "evals" / "create_cases.json"


def mismatches(result, expected: dict) -> list[str]:
    """Exact expected-field gate; omitted fields are deliberately not scored."""
    failures = []
    for name, value in expected.items():
        actual = getattr(result, name)
        if isinstance(actual, datetime):
            actual = actual.isoformat()
        elif hasattr(actual, "value"):
            actual = actual.value
        if actual != value:
            failures.append(name)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["rules", "llm"], default="rules")
    parser.add_argument(
        "--live", action="store_true", help="Authorize paid synthetic xAI calls"
    )
    args = parser.parse_args()
    primary = secondary = None
    if args.mode == "llm":
        if not args.live:
            parser.error(
                "--mode llm requires --live; this contacts the configured provider"
            )
        primary, secondary = load_live_llm_parsers()
        if primary is None:
            parser.error("Export XAI_API_KEY or LLM_API_KEY; .env is not read")
    cases_bytes = CASES_PATH.read_bytes()
    rows = []
    # Keep machine-readable report on stdout and diagnostics on stderr.
    with redirect_stdout(sys.stderr):
        setup_logging(level="ERROR", enable_file_logging=False)
        for case in json.loads(cases_bytes):
            started = time.perf_counter()
            result = (
                parse(case["message"], now=NOW)
                if args.mode == "rules"
                else parse_with_fallback(
                    case["message"],
                    now=NOW,
                    llm=primary,
                    llm_fallback=secondary,
                    llm_first=True,
                )
            )
            rows.append(
                {
                    "id": case["id"],
                    "failed_fields": mismatches(result, case["expected"]),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "notes": result.notes,
                }
            )
    passed = sum(not row["failed_fields"] for row in rows)
    print(
        json.dumps(
            {
                "mode": args.mode,
                "passed": passed,
                "total": len(rows),
                "model": primary.model if primary else None,
                "secondary_model": secondary.model if secondary else None,
                "prompt_hash": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
                "corpus_hash": hashlib.sha256(cases_bytes).hexdigest(),
                "reference_time": NOW.isoformat(),
                "cases": rows,
                "usage_note": "Response notes expose successful-call tokens; failed-call usage is unknown. "
                "Schema/request tokens are included in provider input usage. "
                "No complete billing total is inferred.",
            },
            indent=2,
        )
    )
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
