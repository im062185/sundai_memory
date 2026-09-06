# sundai_memory — Engram, a self-managing memory for a builder's agent in pi

Read in this order: this file → docs/LANES.md (your lane and prompt) → docs/AMD-03-capture-first-evolution.md → docs/TDD-engram-v2.md → docs/CUJ-engram-v2.md → pi-extension/HOOKS.md → docs/PROTOCOL.md.

## Non-negotiables
- Memory fires from pi lifecycle hooks, never at the model's request. `recall` before every turn, `remember` after every user turn.
- Capture first: every turn is appended verbatim to the episodic log before anything judges it. Per-turn rules only tag salience (explicit preferences, absence claims, refutations / forget intents). LLM encoding runs inside consolidation, with hindsight.
- Only `engram/p2` (the gate) writes to stores. Never remove a gate rule to make a test pass.
- Never hard-delete. Refute sets `status` and `valid_to`.
- Frozen contracts (change only by agreement of all lanes): `schema/claim.schema.json`, `engram/adapters/base.py`, `docs/PROTOCOL.md`, `pi-extension/HOOKS.md`.
- Never write to `~/.pi/agent/*`. Write `config/*.example` and print the copy command.
- Never invent hook names, dataset layouts, or benchmark numbers. Read; do not recall.
- Stay inside your lane's directories (docs/LANES.md). Cross-lane needs → tell the integrator.

## Git
Base branch `build/engram-v2`. Lane branches `lane/<a|b|c|d>` off it. Small commits, subject `lane-X: …`. PR into `build/engram-v2`; the integrator merges at :00 and :30. No force-push, no history rewrite, no `reset --hard`.

## Toolchain
Python 3.12+ (`pip install -r requirements.txt`), Node 22+ for `pi-extension/` (`npm ci && npx tsc --noEmit`). pi 0.85.1 installed globally. Tests: `python -m pytest tests -q`. No network in tests or in the chat loop; only `bench/judge.py` uses `ANTHROPIC_API_KEY`.

## Report format per step
One paragraph: changed / passed (and how it was verified: unit test vs live pi) / open.
