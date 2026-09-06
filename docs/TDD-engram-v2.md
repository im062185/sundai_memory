---
artifact: TDD
feature: engram-memory-organ
version: v2.0
supersedes: [TDD v1.0, TDD v1.1, AMD-01 §4, AMD-02 §8]
depends_on: docs/CUJ-engram-v2.md (v2.1, approved)
status: Draft — awaiting human approval
repo: https://github.com/im062185/sundai_memory
branch: build/engram-v2
target: "Working prototype of the full system" by 19:00 showcase (interview 6.2)
executor: Claude Code (interview 6.3), multi-lane per §10.2
approvals:
  human_approver:
  approved_at:
---

# TDD v2 — Engram in pi: structural memory for a builder's agent on a local model

## §0 Reading order for the executing agent

1. `CLAUDE.md` (repo root) — scope, git rules, non-negotiables
2. `docs/CUJ-engram-v2.md` — the human's journey; §7 lists decisions C-1…C-9 this document inherits
3. This document. **NORMATIVE** sections bind. **INFORMATIVE** sections explain why a gate exists so you do not remove it.
4. `docs/AMD-02-five-metrics.md` §3 — metric definitions, still normative

Superseded documents (`TDD v1.x`, `AMD-01 §4`) are history. Do not build from them.

### What v2 changes from v1.1 (INFORMATIVE)

| v1.1 | v2 |
|---|---|
| Custom Python chat loop | **pi is the chat surface.** `chat/loop.py` is deleted |
| Anthropic API for replies | **LM Studio, ~4B model, offline** for all chat |
| LLM-backed P1 with rule fallback | **Rule-based P1 primary**; the 4B model may add candidates, never override |
| Memory called by the loop | **Memory fires from pi lifecycle hooks.** The model cannot skip it |
| `--sleep` command | Consolidation DAG runs from pi's **compaction hook**, on Engram start, and on CLI |
| Own fixtures only | **LongMemEval slice** as outcome benchmark, judged by `claude-sonnet-5` offline |
| Retrieval metrics | **Five-metric scorecard** (AMD-02) is the top line everywhere |

---

## §1 Purpose and scope

### 1.1 What "full system" means at the showcase (NORMATIVE)

The prototype is complete when a person can, in pi, on LM Studio, with no key:

1. State facts and preferences → they are extracted, gated, routed, stored, without the model being asked to do anything.
2. State an unverified absence → one clarifying question appears, rendered by the extension.
3. Close pi, run consolidation (or let compaction trigger it), reopen → the first reply already has the facts injected.
4. Correct a fact in passing → it is refuted in all four stores in the same turn; the trace shows which store kept returning it.
5. Ask "what do you remember" → provenance-tagged list.
6. Ask "is your memory helping" → scorecard line, memory-on vs off, four-store table, three labeled tiers, LongMemEval per-category row with the judge named.

Everything else is scaffolding for those six.

### 1.2 Out of scope (NORMATIVE)

Auth; multi-user; any web UI; database servers; MCP; STATE-Bench official run (O-6c); tuning the 4B model's reply quality (S3b is a finding, not a task).

---

## §2 Repository, lanes, boundaries (NORMATIVE)

### 2.1 Layout

```
sundai_memory/
├── CLAUDE.md  README.md  requirements.txt  .env.example  .gitignore  package.json
├── docs/                      CUJ-engram-v2.md  TDD-engram-v2.md  AMD-02-five-metrics.md
├── schema/claim.schema.json
├── engram/                    Python package (the organ)
│   ├── __main__.py            CLI + stdio JSON-lines server
│   ├── server.py              request dispatch: recall | remember | refute | explain | report | consolidate
│   ├── p1/                    extract.py  rules.py  think.py  contradiction.py  llm_assist.py
│   ├── p2/                    gate.py  rules.py
│   ├── p3/                    router.py  policy.json
│   ├── adapters/              base.py  __init__.py  null.py  graph.py  vector.py  sql.py  symbolic.py
│   ├── consolidate/           dag.py  nodes.py
│   └── report/                scorecard.py  render.py
├── pi-extension/              TypeScript, ~150 lines total
│   ├── package.json  tsconfig.json
│   └── src/index.ts           hooks + tools; spawns `python -m engram --serve`
├── bench/                     metrics.py  component.py  outcome.py  longmemeval.py  judge.py  queries.json
├── tests/                     test_conformance.py  test_gate.py  test_isolation.py  test_dag.py  test_cuj.py
│   └── fixtures/              claims.json  gate_cases.json  session1.jsonl  session2.jsonl  longmemeval_slice.json
├── config/                    models.json.example  compaction.example.json  AGENTS.md
├── .github/workflows/ci.yml
└── out/                       generated, gitignored
```

### 2.2 Write scope

Claude Code may create/modify anything under the tree above **except** `LICENSE`, `IMG_*.jpeg`, `.git/`, `.env`. Nothing outside the repo. Home-directory pi config (`~/.pi/agent/*`) is **human-applied** from `config/*.example`; the agent writes the example files and prints the copy command, never writes to `~`.

### 2.3 Git

Branch `build/engram-v2`. One commit per §10.1 step, subject `step-NN: …`. No force-push, no history rewrite, no `reset --hard`. PR at the end, **not merged** by the agent. Dirty tree you did not cause → stop and report.

### 2.4 Network

| Path | Network |
|---|---|
| `pip`, `npm` | registries only |
| pi chat, Engram, component bench, CI | **none** |
| `bench/judge.py` | Anthropic API, `ANTHROPIC_API_KEY`, `claude-sonnet-5` |
| O-6a STATE-Bench adapter | none (import guarded) |

Absence of the key must never break anything except the judge step, which then writes `accuracy: null, judge: "not run"`.

---

## §3 Decision record

| ID | Decision | Chosen | Rejected | Why |
|---|---|---|---|---|
| D-1 | Process boundary | Extension spawns `python -m engram --serve`; JSON-lines over stdio | HTTP, MCP | pi's own RPC mode is this pattern; one process, no ports, no adapter |
| D-2 | Structural firing | pi lifecycle hooks for recall/remember; compaction hook for consolidate | model-initiated tools | CUJ C-4 |
| D-3 | Hook names | **probe first** (§4 A-1): read `packages/coding-agent/extensions/types.ts`; `session_before_compact` is confirmed | assume names | never guess an API |
| D-4 | P1 primary | rule-based (`p1/rules.py`) | LLM | 4B JSON reliability; rules are testable with fixtures |
| D-5 | P1 assist | 4B model proposes *additional* candidate claims; each still passes rules for `source_class`; assist can only add, never relabel | LLM decides `source_class` | CUJ C-3 |
| D-6 | Thinking | captured at compaction from structured messages; parsed as thinking block **or** inline `<think>…</think>` (probe A-2); always `source_class=inferred` → G2 never promotes | ignore thinking | whiteboard input #1c; G2 makes it safe |
| D-7 | Clarifying question | extension renders as system-origin message in the TUI | inject into model context | CUJ S4: not left to the model |
| D-8 | Compaction | extension returns a provenance-tagged digest as the summary; reads `messagesToSummarize` structurally (not the truncating serializer) | default summary | CUJ C-5; §6.6 |
| D-9 | Consolidation triggers | (a) compaction hook, (b) Engram start, (c) `engram consolidate` | compaction only | short sessions never compact |
| D-10 | Stores | networkx / numpy-cosine over hashing vectorizer / sqlite FTS5 / `.md` files | servers | zero infra, offline |
| D-11 | Router policy | fitted per AMD-02 §5 (F1 s.t. precision-after-refute ≥0.8, tokens ≤1.25×min, speed tiebreak) | hand table | CUJ C-8 |
| D-12 | Outcome benchmark | LongMemEval slice ≥50 Qs, all six categories; ingest via rule-based P1 only; answer via Engram recall + 4B model through `pi -p --mode json` | full set; STATE-Bench | CUJ C-6, 4B context |
| D-13 | Judge | `claude-sonnet-5`, batch, offline from demo, named in every accuracy cell | local judge | CUJ C-7 |
| D-14 | Compaction config | `config/compaction.example.json` sets `reserveTokens`/`keepRecentTokens` for the 4B context size | defaults (16384 reserve) | defaults compact every turn on small contexts |
| D-15 | CI | Python tests + `tsc --noEmit`; no pi in CI | pi-driven CI | pi is a local dependency |

---

## §4 Probes and assumptions (evidence contract)

| ID | Question | Resolve by | Blocks |
|---|---|---|---|
| A-1 | Exact pi event names/payloads for before-turn and after-turn hooks | read `extensions/types.ts` in the installed pi; record in `pi-extension/HOOKS.md` | step 08 |
| A-2 | Does the LM Studio model's reasoning reach pi as a thinking block or inline `<think>` tags? | 3-turn probe in pi with `--mode json`; save the raw event | step 05 (`p1/think.py`) |
| A-3 | Naive vector arm returns refuted claims (precision collapse) | conformance test 3, xfail until reindex | headline finding; do not fix early |
| A-4 | LM Studio config needed: context length, `compat.supportsDeveloperRole`, dummy `apiKey` | first `pi` launch against LM Studio; record working `models.json` | step 01 |
| A-5 | LongMemEval release has a smaller "oracle"/evidence-only variant and its file layout | read the dataset repo README; **do not assume variant names** | step 12 |
| A-6 | Compaction hook receives thinking for `openai-completions` providers | inspect `messagesToSummarize` in a forced `/compact` | step 09 |
| A-7 | CI < 90s with Python + tsc | first run | step 04 |
| A-8 | pending human input: contents of the claude.ai artifact the human referenced | human pastes it | unknown; may amend any section |

A-1, A-2, A-4 are **the first thirty minutes**. Nothing in `pi-extension/` is written before A-1 is recorded.

---

## §5 Contracts

### 5.1 Claim record — `schema/claim.schema.json`

Unchanged from TDD v1.0 §5.1. One addition: `"origin": {"enum": ["user_turn","assistant_turn","assistant_thinking","tool_result","trajectory"]}` (required). `assistant_thinking` implies `source_class: "inferred"`; the gate rejects any record violating that.

### 5.2 Store interface — `engram/adapters/base.py`

TDD v1.1 §5.2 (`write`, `query`, `refute`, `stats`, `by_subject`). Frozen at step 03.

### 5.3 Engram stdio protocol (NORMATIVE) — `engram/server.py`

One JSON object per line, request → response, same order.

```json
{"op":"recall",   "text":"…", "k":5, "session":"s1"}
  → {"ok":true, "claims":[{"id":"clm_…","text":"…","source_class":"…","store":"sql","score":0.8}],
     "tokens":312, "ms":41}

{"op":"remember", "turn":{"role":"user","text":"…","thinking":null}, "session":"s1"}
  → {"ok":true, "extracted":["clm_…"], "gate":{"clm_…":{"verdict":"promoted"}},
     "routed":{"clm_…":"sql"}, "refuted":["clm_…"], "question":"…or null", "ms":57}

{"op":"consolidate", "messages":[…structured…], "reason":"threshold|manual|start|cli"}
  → {"ok":true, "digest":"…provenance-tagged text…", "counts":{"expired":0,"captured":0,"merged":0,
     "adjudicated":0,"promoted":0,"reindexed":true}}

{"op":"explain"}  → {"ok":true, "text":"You said … / I inferred … / Verified: …"}
{"op":"report"}   → {"ok":true, "text":"speed … · accuracy … · tokens … · precision … · recall …\n…"}
{"op":"refute", "claim_id":"clm_…"} → {"ok":true}
```

Every response carries `ms`. Every `remember` appends a trace record to `out/trace.jsonl` (schema as TDD v1.1 §5.3 plus `scorecard_turn`).

### 5.4 Extension ↔ pi (NORMATIVE) — `pi-extension/src/index.ts`

| pi event (name per A-1) | Extension does |
|---|---|
| session start | spawn Engram; send `consolidate` with `reason:"start"` over the session file's prior entries |
| before each turn | `recall(last user text)`; inject returned claims as a context message: `Memory (provenance-tagged):\n- [said] …\n- [inferred] …` |
| after each turn | `remember(user turn)`; if `question` non-null, render it to the TUI as a system-origin message |
| `session_before_compact` | `consolidate(messagesToSummarize, reason)`; return `{compaction:{summary: digest, firstKeptEntryId, tokensBefore}}` |
| registered tools | `engram_explain`, `engram_report` — the two things the *builder* may ask for by name |

No hook may be conditional on model output. If a hook cannot fire (Engram crashed), the extension writes a visible error line and the turn proceeds without memory — never silently.

### 5.5 Bench output — `out/results.json`

AMD-02 §6.1 `scorecard` block first, then `component`, `outcome`, `longmemeval` (per-category accuracy + judge name + n), `provenance` census.

---

## §6 Stage specifications

### 6.1 P1 extraction — `engram/p1/`

- `rules.py`: sentence split → patterns for preference / constraint / fact / capability / absence / decision. Every emitted record has `origin`, `source_class`, `subject`, `polarity`, `support_set`, `refutation_trigger` filled by rule. Absence patterns: "has no", "doesn't support", "there's no", "can't", "not available".
- `think.py`: extracts thinking from a structured assistant message; handles both thinking-block and inline `<think>` (A-2). All output `origin:"assistant_thinking"`, `source_class:"inferred"`.
- `llm_assist.py`: optional; prompts the 4B model (through the extension, never directly from Python, so Engram stays provider-free) for *additional* candidates as JSON; each candidate re-passes `rules.py` for labeling; invalid JSON → drop silently with a trace count. **Off by default; on via `ENGRAM_ASSIST=1`.**
- `contradiction.py`: same subject + opposite polarity → refute older. Exact-rule only (no LLM judge on the 4B path).
- No `import adapters` under `p1/`. Test-enforced.

### 6.2 P2 gate — `engram/p2/`

G1–G8 as TDD v1.0 §6.2, plus **G9**: `origin=="assistant_thinking"` ⇒ `source_class` must be `inferred` (reject otherwise). `promote()` returns `(verdict, fired, question)`. Only `p2` imports `adapters`.

### 6.3 P3 router — `engram/p3/router.py`

Loads `policy.json`; fallback documented and logged as fallback.

### 6.4 Stores — `engram/adapters/`

As TDD v1.1 §6.6. Vector `refute` stays naive until reindex (A-3).

### 6.5 Recall — `engram/server.py:recall`

Query all four; merge; dedupe by id; drop `status!="promoted"`; rank by fused score; cap by `k` and by a token budget (`ENGRAM_RECALL_TOKENS`, default 400 — a 4B context is small). Return with per-store attribution for the trace.

### 6.6 Consolidation DAG — `engram/consolidate/dag.py`

AMD-01 §4.1 nodes, as an explicit `networkx.DiGraph`, acyclicity asserted at import. Input is structured messages (from compaction or session file). Output: counts + a **digest** — one line per promoted claim, provenance-tagged, ≤ `ENGRAM_DIGEST_TOKENS` (default 600). `reindex` runs **after** the pre-reindex component snapshot so A-3 is recorded.

### 6.7 Report — `engram/report/`

Renders AMD-02 §6.2: scorecard line (memory-on), memory-off line, four-store table, then Outcome / Component / Provenance tiers with labels. LongMemEval row shows per-category accuracy, `n`, and `judge: claude-sonnet-5` or `judge: not run`.

### 6.8 LongMemEval slice — `bench/longmemeval.py`

1. Load ≥50 questions covering all six categories (A-5 for layout).
2. For each: fresh Engram store → ingest history sessions via `remember` (rule-based only, `ENGRAM_ASSIST=0`) → `recall(question)` → answer via `pi -p --mode json` on LM Studio with the recalled claims injected as the same context message the extension uses.
3. Record answer, tokens, latency. `bench/judge.py` scores against gold with `claude-sonnet-5` when a key exists.
4. Write per-category accuracy + P/R of recalled claims vs. evidence sessions where the dataset marks them.

Same run with memory off (full history truncated to the model's context) gives the memory-off row.

---

## §7 Tests (NORMATIVE)

| File | Asserts |
|---|---|
| `test_conformance.py` | v1.1 §7.1 + `by_subject` live-only; parametrized over `REGISTRY` |
| `test_gate.py` | one rejecting fixture per G1–G9 + passing control |
| `test_isolation.py` | no `import adapters` outside `p2/`; `Store.write/refute` called only from `p2/` |
| `test_dag.py` | acyclic; each node once per pass; only `promote` touches adapters; counts schema |
| `test_metrics.py` | a returned refuted id lowers precision by exactly 1/k; scorecard order |
| `test_cuj.py` | S1, S2, S3a, S4 (question emitted by server, not model), S5, S6, S7, S8, S9, S10, S13 — offline, driven through `server.py` with `session1.jsonl`/`session2.jsonl` |
| `pi-extension/` | `tsc --noEmit`; a mocked-pi unit test that each hook calls the expected `op` (S11 offline proxy) |
| local only | S11 real: run `pi --mode json` over the replay, assert `recall` before first token and `remember` after each user turn = 100%; S12 the LongMemEval slice |

---

## §8 CI

```yaml
name: ci
on: [push]
jobs:
  py:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12', cache: pip }
      - run: pip install -r requirements.txt
      - run: python -m pytest tests/ -q
      - run: python -m bench.component --smoke --n 5
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: out, path: out/ }
  ts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '22', cache: npm, cache-dependency-path: pi-extension/package-lock.json }
      - run: cd pi-extension && npm ci && npx tsc --noEmit && npm test
```

No key in CI. No pi in CI.

---

## §9 Configuration the human applies (INFORMATIVE, with exact files)

- `config/models.json.example` → `~/.pi/agent/models.json`: LM Studio provider, `openai-completions`, `baseUrl` `http://localhost:1234/v1`, dummy `apiKey`, `compat.supportsDeveloperRole: false` (A-4 confirms the rest).
- `config/compaction.example.json` → pi settings `compaction.*` sized for the 4B context (D-14).
- `config/AGENTS.md` → project root: two lines telling the model memory context arrives automatically and that `engram_explain` / `engram_report` exist.
- `pi install /path/to/sundai_memory/pi-extension` (or `-e` for dev). Extension path is printed by step 08.
- `.env`: `ANTHROPIC_API_KEY` only for `bench/judge.py`.

---

## §10 Build sequence

### 10.1 Steps (one commit each)

| Step | Deliverable | Gate |
|---|---|---|
| 00 | **Probes A-1, A-2, A-4** recorded in `pi-extension/HOOKS.md` and `config/*.example` | all three have evidence, not guesses |
| 01 | `requirements.txt`, `package.json`, `.gitignore`, `.env.example`, `CLAUDE.md`, branch | installs |
| 02 | `schema/`, `fixtures/claims.json` (40+, from today's design conversation: ≥8 absence, ≥5 gate-failing), `session1.jsonl`, `session2.jsonl`, `gate_cases.json` | validate |
| 03 | `adapters/base.py`, `__init__.py`, `null.py`, `test_conformance.py`, `test_isolation.py` | green on Null |
| 04 | `ci.yml` (both jobs) | green (A-7) |
| 05 | `p1/rules.py`, `p1/think.py`, `p1/contradiction.py`, `p1/extract.py` | schema-valid output on fixtures; `<think>` path per A-2 |
| 06 | `p2/` + `test_gate.py` (G1–G9) | each rule has a failing fixture that fails |
| 07 | `adapters/sql.py`, `symbolic.py`, `graph.py`, `vector.py` | conformance green / A-3 xfail documented |
| 08 | `engram/server.py` + `__main__.py` (stdio protocol §5.3) + `pi-extension/src/index.ts` (§5.4) | `test_cuj.py` S1–S10 offline; hooks fire in a live pi (S11 local) |
| 09 | `consolidate/dag.py` + compaction hook path + `test_dag.py` | forced `/compact` returns digest; A-6 recorded |
| 10 | `bench/metrics.py`, `component.py`, `queries.json` (25, multi-gold), `test_metrics.py`; `report/` | scorecard renders; S10 |
| 11 | `p3/router.py` + `--fit` | `policy.json` carries the five numbers per choice |
| 12 | `bench/longmemeval.py` + `judge.py` (A-5) | ≥50 Qs run on LM Studio; judge row present or `not run` |
| 13 | `bench/outcome.py` (pass^k on S2–S5, memory on/off) | `outcome.json` |
| 14 | `README.md` (built / cut / open / A-3 outcome / judge named), PR open, unmerged | — |

**Freeze at T+160.** "Full system" per §1.1 requires steps 00–10 and 12. Cut order: 13, 11, then 12's judge (keep the run, mark `not run`). **Never cut 08 or 09** — structural firing and the DAG are the human's two explicit asks.

### 10.2 Lanes

| Lane | Steps | Owns |
|---|---|---|
| 1 integrator | 00 (A-1, A-4), 01, 03, 04, 14, pitch | `schema/`, `adapters/base.py`, `adapters/__init__.py`, `tests/test_conformance.py`, `tests/test_isolation.py`, `.github/`, `config/` |
| 2 pi extension + server | 00 (A-2), 08, 09 | `pi-extension/`, `engram/server.py`, `engram/__main__.py`, `engram/consolidate/` |
| 3 P1 + P2 + fixtures | 02, 05, 06 | `engram/p1/`, `engram/p2/`, `tests/fixtures/`, `tests/test_gate.py`, `tests/test_cuj.py` |
| 4 stores | 07 | `engram/adapters/{graph,vector,sql,symbolic}.py` |
| 5 bench + report | 10, 11, 13 | `bench/{metrics,component,outcome}.py`, `bench/queries.json`, `engram/p3/`, `engram/report/`, `tests/test_metrics.py` |
| 6 LongMemEval | 12 | `bench/longmemeval.py`, `bench/judge.py`, `tests/fixtures/longmemeval_slice.json` |

Lane 1 finishes 03 before lanes 4–6 start. Lane 3 finishes 02 before lane 2 starts 08. Lane 2's A-1 gates lane 2's own step 08 — nothing else waits on it. Merge to `main` at :00 and :30.

---

## §11 Optional

| ID | Extension | Cost | If |
|---|---|---|---|
| O-1 | `llm_assist.py` on by default | 10 min | A-2 shows clean JSON from the 4B |
| O-2 | Wolfram Foundation Tool as pre-promotion verifier for checkable claims | 20 min | ahead at T+120; sponsor hook |
| O-6a | `agents/engram_agent.py` — STATE-Bench `retrieve_learnings` hook, import-guarded | 15 min | ahead at T+120 |
| O-7 | `/tree` branch demo: same conversation memory-on vs off from one point | 10 min | step 08 landed early |

---

## §12 Rules for the executing agent (NORMATIVE)

1. Never remove a gate rule to make a test pass. Report and stop.
2. Never fix the vector arm's refutation before the component snapshot records it.
3. Never make a hook conditional on model output. Structural means structural.
4. Never write to `~/.pi/agent/*`. Write `config/*.example` and print the copy command.
5. Never invent hook names, dataset file layouts, or bench numbers. A-1/A-5 are read, not recalled.
6. Never patch an invalid LLM-produced claim into validity. Drop and count.
7. State what was verified and by what. "Hook fired in a live pi session" ≠ "unit test with a mocked pi".
8. One-paragraph report per step: changed / passed / open.
9. Blocked > 5 min → stop and ask. Do not redesign around the blocker.
10. The builder never sees a gate, a store, a DAG, or a schema. If you add an approval prompt, you have misread the CUJ.
11. A-8 (the unread artifact) may amend anything. Until it arrives, build to this document.
