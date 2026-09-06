# HANDOFF-A.md — lane A status board and pickup prompt

Lane A = integrator + pi extension + stdio server + consolidation DAG. This file is updated after every lane A step. Whoever picks up lane A reads the STATUS block, then pastes the PROMPT block into a fresh Claude Code session.

## STATUS (update after each step)

| Step | Deliverable | State | Verified how |
|---|---|---|---|
| 00 | probes, contracts, LANES/PROMPTS | done, pushed (ffd136d, 728526c) | fresh clone test |
| 03 | tests/test_conformance.py, tests/test_isolation.py | done | pytest 8 passed on NullStore |
| 04 | .github/workflows/ci.yml | done, first run pending | local pytest; CI result unverified |
| 08 | engram/server.py, engram/__main__.py, pi-extension/src/index.ts | code done; LIVE PI CHECK PENDING (needs a model: ANTHROPIC_API_KEY or `pi` → /login) | pytest test_server (stdio roundtrip), node --test mocked-pi hooks, tsc clean |
| 09 | engram/consolidate/dag.py + nodes.py, tests/test_dag.py; compaction path in index.ts | done except live forced /compact (A-6) | pytest test_dag; node hook test covers the compaction handler with a mocked pi |
| merges | lane/b, lane/c, lane/d into build/engram-v2 | none yet | |
| 14 | README pointers, final PR | pending | |

Open items: A-2/A-4 probes (lane D on the demo machine). Showcase 19:00 vs 20:00 unconfirmed.

## PROMPT (paste into a new Claude Code session)

```
SETUP FIRST. Repo: https://github.com/im062185/sundai_memory, base branch build/engram-v2. I am taking over lane A (integrator) mid-way.
1. If the current directory is not a clone, run: git clone https://github.com/im062185/sundai_memory.git sundai_memory — then work inside it with absolute paths.
2. git checkout build/engram-v2 && git pull. Lane A commits directly on build/engram-v2 (it is the integration branch).
3. python3 -m venv .venv && .venv/bin/pip install -r requirements.txt. Check pi --version (need 0.85.x; if missing: npm install -g --ignore-scripts @earendil-works/pi-coding-agent) and node --version (22+).
4. Read, in order: CLAUDE.md, docs/HANDOFF-A.md (this file: the STATUS table is the truth), docs/LANES.md lane A section, docs/PROTOCOL.md, pi-extension/HOOKS.md, docs/AMD-03-capture-first-evolution.md sections 1 and 3, docs/TDD-engram-v2.md sections 5.3, 5.4, 6.5, 6.6, 7, 8, 10. Then run .venv/bin/python -m pytest tests -q and tell me in five lines: which steps in the STATUS table are done, which tests pass, and what you will do next. Wait for my go.

THEN CONTINUE LANE A. Work only in the files lane A owns (docs/LANES.md): pi-extension/, .pi/, engram/server.py, engram/__main__.py, engram/consolidate/, schema/, engram/adapters/base.py, engram/adapters/null.py, tests/test_conformance.py, tests/test_isolation.py, tests/test_dag.py, .github/, CLAUDE.md, docs/HANDOFF-A.md, README.md at the end. Never edit the frozen contracts (schema/claim.schema.json, engram/adapters/base.py, docs/PROTOCOL.md, pi-extension/HOOKS.md) except by agreement of all lanes.
Pick up at the first pending row of the STATUS table and follow the step specs in docs/LANES.md lane A prompt. Use only hook names and payload shapes from pi-extension/HOOKS.md. After each step: run the tests, commit with subject "lane-a: step-NN …", push, and update the STATUS table in docs/HANDOFF-A.md in the same commit.
Integration duty: at :00 and :30 run git fetch, and for each of lane/b, lane/c, lane/d that has new commits, merge it into build/engram-v2 (git merge origin/lane/x), run the tests, fix only integration breakage (imports, REGISTRY wiring, DAG node hookup), push. If a lane's tests fail on their own code, do not fix it; tell me which lane and what failed. Wire lane B's stores into REGISTRY and the server, and lane C's encode() and evolve() into the DAG nodes encode and evolve, as soon as they land.
Live verification is required for step 08: run pi --mode json in the repo with the extension loaded and show that recall fires before the first model token and remember after each user turn; say exactly what was verified live vs. with a mocked pi.
Rules: never make a hook conditional on model output; never remove a gate rule; never write to ~/.pi/agent/*; never invent hook names. One-paragraph report per step: changed / passed / open. Blocked more than 5 minutes → stop and ask me.
```
