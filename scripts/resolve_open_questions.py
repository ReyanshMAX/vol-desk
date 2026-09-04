#!/usr/bin/env python3
"""One-time helper to resolve OPEN_QUESTIONS.md Q-003 (strike increments)
and Q-004 (Groq model IDs) against the live Alpaca and Groq APIs, and
write the results into config/universe.yaml and config/params.yaml.

Must run from an environment that can actually reach paper-api.alpaca.markets
and api.groq.com -- it will not work from a network-restricted sandbox.

Usage:
    cd ~/vol-desk
    set -a; source .env; set +a
    python3 scripts/resolve_open_questions.py

Requires ALPACA_API_KEY, ALPACA_SECRET_KEY, GROQ_API_KEY in the environment.
Stdlib only (urllib), so it runs with a bare python3 -- no venv/pip needed
just to run this one script.

Nothing is written without an explicit y/N confirmation showing exactly
what would change -- this fills in config for a system that risks real
(paper) capital, so no value goes in unreviewed (CLAUDE.md rule 3).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = REPO_ROOT / "config" / "universe.yaml"
PARAMS_PATH = REPO_ROOT / "config" / "params.yaml"

SYMBOLS = ["SPY", "QQQ", "IWM", "GLD", "TLT", "XLE", "HYG"]

# Groq model ids that show up in /v1/models but aren't chat/completion
# models -- exclude them from the picker rather than let someone
# accidentally wire a moderation or audio model into llm.tiers.
_NON_CHAT_PATTERNS = ("whisper", "tts", "guard", "moderation", "prompt-guard")


def _get(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach {url}: {e.reason}") from e


def resolve_strike_increment(symbol: str, api_key: str, secret_key: str) -> float | None:
    """Q-003: GET /v2/options/contracts for a near-dated expiry, read the
    distinct strike spacing off the returned contracts (docs/DATA.md's own
    resolve-by instructions for this question)."""
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
    today = date.today()
    gte = (today + timedelta(days=1)).isoformat()
    lte = (today + timedelta(days=21)).isoformat()
    url = (
        "https://paper-api.alpaca.markets/v2/options/contracts"
        f"?underlying_symbols={symbol}&status=active"
        f"&expiration_date_gte={gte}&expiration_date_lte={lte}"
        "&limit=1000"
    )
    data = _get(url, headers)
    contracts = data.get("option_contracts", [])
    if not contracts:
        print(f"  {symbol}: no contracts returned in the next 21 days, skipping")
        return None

    by_expiry: dict[str, list[float]] = {}
    for c in contracts:
        by_expiry.setdefault(c["expiration_date"], []).append(float(c["strike_price"]))

    nearest_expiry = min(by_expiry)
    strikes = sorted(set(by_expiry[nearest_expiry]))
    if len(strikes) < 2:
        print(f"  {symbol}: only one strike at {nearest_expiry}, can't infer spacing")
        return None

    diffs = [round(b - a, 4) for a, b in zip(strikes, strikes[1:])]
    increment = min(diffs)
    print(f"  {symbol}: expiry {nearest_expiry}, {len(strikes)} strikes "
          f"({strikes[0]}..{strikes[-1]}) -> increment {increment}")
    return increment


def resolve_groq_models(groq_key: str) -> list[str]:
    headers = {"Authorization": f"Bearer {groq_key}"}
    data = _get("https://api.groq.com/openai/v1/models", headers)
    ids = [m["id"] for m in data.get("data", [])]
    return sorted(i for i in ids if not any(p in i.lower() for p in _NON_CHAT_PATTERNS))


def update_universe_yaml(increments: dict[str, float]) -> None:
    text = UNIVERSE_PATH.read_text()
    for symbol, increment in increments.items():
        pattern = rf"(ticker: {symbol}, cluster: \w+,\s+strike_increment: )null"
        new_text, n = re.subn(pattern, rf"\g<1>{increment}", text)
        if n == 0:
            print(f"  WARNING: could not find a line to update for {symbol}")
        text = new_text
    UNIVERSE_PATH.write_text(text)
    print(f"Updated {UNIVERSE_PATH}")


def update_params_yaml(fast_model: str, reasoning_model: str) -> None:
    text = PARAMS_PATH.read_text()
    text, n1 = re.subn(r"fast: null", f'fast: "{fast_model}"', text, count=1)
    text, n2 = re.subn(r"reasoning: null", f'reasoning: "{reasoning_model}"', text, count=1)
    if n1 == 0 or n2 == 0:
        print("  WARNING: could not find llm.tiers.fast/reasoning lines to update")
    PARAMS_PATH.write_text(text)
    print(f"Updated {PARAMS_PATH}")


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


def main() -> None:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")
    missing = [name for name, val in (
        ("ALPACA_API_KEY", api_key), ("ALPACA_SECRET_KEY", secret_key),
        ("GROQ_API_KEY", groq_key),
    ) if not val]
    if missing:
        print(f"FATAL: missing env vars {missing}. Source your .env first:\n"
              f"  set -a; source .env; set +a", file=sys.stderr)
        sys.exit(1)

    print("== Q-003: strike increments (GET /v2/options/contracts) ==")
    increments: dict[str, float] = {}
    for symbol in SYMBOLS:
        try:
            inc = resolve_strike_increment(symbol, api_key, secret_key)
        except RuntimeError as e:
            print(f"  {symbol}: FAILED -- {e}")
            continue
        if inc is not None:
            increments[symbol] = inc

    if increments:
        print(f"\nWould write {len(increments)}/{len(SYMBOLS)} strike_increment values "
              f"into {UNIVERSE_PATH}:")
        for symbol, inc in increments.items():
            print(f"  {symbol}: {inc}")
        if _confirm("Write these?"):
            update_universe_yaml(increments)
        else:
            print("Skipped -- universe.yaml left untouched.")
    else:
        print("No strike increments resolved; universe.yaml left untouched.")

    print("\n== Q-004: Groq model IDs (GET /openai/v1/models) ==")
    try:
        models = resolve_groq_models(groq_key)
    except RuntimeError as e:
        print(f"FAILED to list Groq models: {e}")
        return

    if not models:
        print("No chat models found; params.yaml left untouched.")
        return

    print(f"{len(models)} candidate chat/completion models:")
    for i, m in enumerate(models):
        print(f"  [{i}] {m}")

    def pick(label: str, default_idx: int) -> str:
        default_idx = min(default_idx, len(models) - 1)
        raw = input(f"\n{label} -- index [default {default_idx}: {models[default_idx]}]: ").strip()
        if not raw:
            return models[default_idx]
        return models[int(raw)]

    # No auto-pick without confirmation: model availability/naming drifts
    # (Q-004's own stated risk), so these are just starting suggestions --
    # first in the sorted list for "fast", last for "reasoning" -- not a
    # claim that either is actually the right choice. Confirm live.
    fast_model = pick("FAST tier (regime labeling)", 0)
    reasoning_model = pick("REASONING tier (structure construction)", len(models) - 1)

    print(f"\nWould write:\n  fast = {fast_model}\n  reasoning = {reasoning_model}")
    if _confirm(f"Write these to {PARAMS_PATH}?"):
        update_params_yaml(fast_model, reasoning_model)
    else:
        print("Skipped -- params.yaml left untouched.")


if __name__ == "__main__":
    main()
