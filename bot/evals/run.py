"""Golden-set eval runner: real group messages -> expected extraction.

Hits the REAL configured providers (needs LLM_PRIMARY_TOKEN / GEMINI_API_KEY in
the env), so it's a manual tool, not pytest:

    cd bot && python -m evals.run                  # everything, once
    cd bot && python -m evals.run --runs 3         # flakiness check
    cd bot && python -m evals.run --only miles_uber_aep
    cd bot && python -m evals.run --provider primary
    cd bot && python -m evals.run --skip-edits

Each case's ``expect`` is a SUBSET of fields to verify — anything not listed
isn't checked. Names are compared by resolved participant id (aliases count),
amounts with a 0.01 tolerance, categories through categories.resolve.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import categories as cats
from llm import extractor
from llm.extractor import extract, extract_edit
from util import match_participant, normalize_name

HERE = Path(__file__).parent
AMOUNT_TOLERANCE = 0.01
CONFIDENCE_THRESHOLD = 0.7  # mirrors the bot's default gate


def _load_jsonl(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _pid(name: str, participants: list[dict]) -> str | None:
    p = match_participant(name, participants)
    return p["id"] if p else f"<unresolved:{name}>"


def _parts_key(parts, participants) -> list[tuple]:
    """(participant id, value) pairs, order-insensitive, None preserved."""
    out = []
    for part in parts:
        name = part["name"] if isinstance(part, dict) else part.name
        value = part["value"] if isinstance(part, dict) else part.value
        if value is not None:
            value = round(float(value), 2)
        out.append((_pid(name, participants), value))
    return sorted(out, key=lambda t: (str(t[0]), str(t[1])))


def _check(case: dict, extraction, participants: list[dict]) -> list[str]:
    """Return a list of human-readable mismatches (empty = pass)."""
    if extraction is None:
        return ["extraction is None (all providers failed)"]
    problems: list[str] = []
    expect = case["expect"]

    for key, expected in expect.items():
        got: object = None
        ok = True
        if key == "message_type":
            got = extraction.message_type
            ok = got == expected
        elif key == "amount":
            got = extraction.amount
            ok = got is not None and abs(got - expected) <= AMOUNT_TOLERANCE
        elif key == "currency":
            got = (extraction.currency or "").upper()
            ok = got == expected.upper()
        elif key in ("split_mode", "payer_mode"):
            got = getattr(extraction, key)
            ok = got == expected
        elif key == "title":
            got = extraction.title
            ok = normalize_name(got or "") == normalize_name(expected)
        elif key == "paid_by_name":
            got = extraction.paid_by_name
            ok = bool(got) and _pid(got, participants) == _pid(expected, participants)
        elif key == "paid_for_names":
            got = extraction.paid_for_names
            ok = sorted(str(_pid(n, participants)) for n in got) == sorted(
                str(_pid(n, participants)) for n in expected
            )
        elif key in ("split_parts", "payers"):
            got = [
                {"name": p.name, "value": p.value} for p in getattr(extraction, key)
            ]
            ok = _parts_key(got, participants) == _parts_key(expected, participants)
        elif key == "payers_min":
            got = len(extraction.payers)
            ok = got >= expected
        elif key == "category_id":
            got = f"{extraction.category!r} -> {cats.resolve(extraction.category, [])}"
            ok = cats.resolve(extraction.category, []) == expected
        elif key == "category_id_in":
            got = f"{extraction.category!r} -> {cats.resolve(extraction.category, [])}"
            ok = cats.resolve(extraction.category, []) in expected
        elif key == "needs_clarification":
            # "The bot would ask something" — low confidence, no amount, or an
            # explicit question. A chitchat verdict is a fail: the bot would
            # stay silent and the expense would vanish.
            asks = extraction.message_type == "expense" and (
                not extraction.amount
                or extraction.confidence < CONFIDENCE_THRESHOLD
                or bool(extraction.clarification_needed)
            )
            got = (
                f"type={extraction.message_type} amount={extraction.amount} "
                f"conf={extraction.confidence} q={extraction.clarification_needed!r}"
            )
            ok = asks == expected
        else:
            problems.append(f"unknown expect key {key!r}")
            continue
        if not ok:
            problems.append(f"{key}: esperado {expected!r}, salió {got!r}")
    return problems


def _filter_provider(name: str) -> None:
    """Keep only one provider in the chain (primary|secondary|gemini)."""
    wanted = {
        "primary": extractor._call_primary,
        "secondary": extractor._call_secondary,
        "gemini": extractor._call_gemini,
    }[name]
    extractor._providers = lambda: [(name, wanted)]  # type: ignore[assignment]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="run just this case id")
    ap.add_argument("--runs", type=int, default=1, help="times to run each case")
    ap.add_argument("--provider", choices=["primary", "secondary", "gemini"])
    ap.add_argument("--skip-edits", action="store_true")
    ap.add_argument(
        "--sleep", type=float, default=10.0,
        help="pause between LLM calls (Groq free tier 429s on back-to-back calls)",
    )
    args = ap.parse_args()

    if args.provider:
        _filter_provider(args.provider)

    ctx = json.loads((HERE / "context.json").read_text())
    participants = ctx["participants"]
    currencies = ctx["currencies"]
    today = ctx["today"]
    category_names = cats.prompt_names()

    suites: list[tuple[str, list[dict]]] = [("extract", _load_jsonl(HERE / "cases.jsonl"))]
    if not args.skip_edits:
        suites.append(("edit", _load_jsonl(HERE / "edit_cases.jsonl")))

    total = passed = 0
    flaky: list[str] = []
    for suite, cases in suites:
        for case in cases:
            if args.only and case["id"] != args.only:
                continue
            total += 1
            run_problems: list[list[str]] = []
            for _ in range(args.runs):
                if total > 1 or run_problems:
                    time.sleep(args.sleep)
                if suite == "extract":
                    extraction = extract(
                        case["text"], case["sender"], participants,
                        currencies, category_names, today,
                    )
                else:
                    extraction = extract_edit(
                        case["current"], case["text"], case["sender"], participants,
                        currencies, category_names, today,
                    )
                run_problems.append(_check(case, extraction, participants))

            ok_runs = sum(1 for p in run_problems if not p)
            if ok_runs == args.runs:
                passed += 1
                print(f"[PASS] {case['id']}" + (f" ({ok_runs}/{args.runs})" if args.runs > 1 else ""))
            else:
                if 0 < ok_runs < args.runs:
                    flaky.append(case["id"])
                print(f"[FAIL] {case['id']} ({ok_runs}/{args.runs})")
                worst = next(p for p in run_problems if p)
                for problem in worst:
                    print(f"       - {problem}")

    print(f"\n{passed}/{total} casos OK" + (f" — flaky: {', '.join(flaky)}" if flaky else ""))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
