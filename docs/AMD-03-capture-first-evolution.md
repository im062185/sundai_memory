---
artifact: AMD
id: AMD-03
title: Capture first, consolidate with hindsight, evolve per generation
amends: docs/TDD-engram-v2.md (v2.0), docs/CUJ-engram-v2.md (v2.1)
resolves: TDD §4 A-8 (the claude.ai artifact) — this file is that content
source: https://claude.ai/code/artifact/ceb1415f-eabe-476b-a163-084861407fa0 and PLAN.md §2b–2d
status: Proposed — human approves, then TDD v2 sections listed below are amended
date: 2026-09-06
---

# AMD-03 — what changes in TDD v2 and why

The TDD is good. Its process boundary (D-1), structural firing (D-2, C-4), compaction ownership
(D-8), the gate (§6.2), the DAG (§6.6), the five-metric scorecard and the LongMemEval slice all
stand. Six things change. Each is keyed to the section it amends.

## 1. Order of judgment (amends §6.1, §6.5, D-4, D-5)

Per-turn P1 stays, but its job is narrowed to **salience tagging**, not extraction of record:

- `p1/rules.py` runs on every user turn and handles only what must not wait: explicit
  preferences and constraints ("never", "always", "keep answers short"), **absence claims**
  (G3/G4 and the clarifying question, unchanged), and **refutations / supersedes / forget
  intents** ("actually it's 3.4", "forget that"). These write immediately. This is the
  mutation-path placement the control-plane study found necessary for forgetting.
- Every turn is also **appended verbatim** to an episodic log (`out/episodes.jsonl` in the
  store, one record per turn, never rewritten). Verbatim is free; no classifier protects it.
- `p1/llm_assist.py` is **moved out of the hot path into the consolidation DAG** as the node
  `encode` (see §3 below). It runs with hindsight: it sees the *following* human turn, so it can
  label each candidate with `user_reaction ∈ {approving, neutral, correcting, frustrated}`.
  It may add candidates and may propose `source_class`; the gate still decides.

Why: at turn time nothing is known about whether a fact matters; the human's reaction to the
agent's output arrives one turn later, so a per-turn extractor cannot see it.

## 2. Stores (amends D-10, §6.4, §2.1 `adapters/`)

Four adapters by index type collapse to **one SQLite file plus markdown exports**, behind the
same frozen `Store` interface:

| TDD adapter | Becomes |
|---|---|
| `sql.py` | `memories` table, FTS5 (`node:sqlite` / Python `sqlite3` both have FTS5) |
| `graph.py` | `links` table (src, dst, kind, weight); one-hop spreading activation at recall |
| `symbolic.py` | `USER.md` + `MEMORY.md`, generated from the `always` tier, character-capped, injected every turn |
| `vector.py` | **keep, naive numpy, as the A-3 demo arm only** — it is the store that keeps returning the refuted claim |

Store roles, not index types: `episodic` (raw turns), `semantic` (facts, bi-temporal
`valid_from`/`valid_to`), `procedural` (rules → MEMORY.md → AGENTS.md). D-11's fitted router
policy is **cut**: routing is a rules table keyed by claim kind (profile/preference/feedback →
always; verbatim/procedure → semantic or procedural; fact/episode → episodic until promoted).

## 3. Consolidation DAG (amends §6.6, D-9)

Nodes become: `expire → capture → encode → merge → adjudicate → promote → wire → reindex →
census → evolve`. New nodes:

- `encode` — the hindsight LLM pass from §1 (batched over the episodes since last run).
- `wire` — memories that were retrieved together in `out/retrieval_log.jsonl` get a
  `co_retrieved` link with weight incremented. "Fire together, wire together."
- `evolve` — §4 below.

Triggers: keep D-9 (compaction, Engram start, CLI) and add **every N turns** (`ENGRAM_MICRO_SLEEP`,
default 10). Short sessions never compact; micro-sleep covers them. Activation replaces a flat
`expire`: `importance × e^(−λ·days_since_access) + 0.2·ln(1+access_count)`; below threshold ⇒
`status: dormant`, never deleted; `core`/`procedural` kinds are immune to decay.

## 4. Evolution (new §6.9; amends §1.1 item 6, §7)

This is the hackathon theme and the TDD has no mechanism for it. Add `engram/evolve/`:

- **Genome** = four files: `weights.json` (λ, FTS/activation blend, k, `ENGRAM_RECALL_TOKENS`,
  promotion and dormancy thresholds, salience keyword weights), `encoder.lessons.md`,
  `persona.md`, `tiers.json` (kind → tier). Written to `out/generations/gen-NNN/`.
- **Signals**, logged during the session: per injected claim, used or ignored (string overlap
  with the reply is enough today); per turn, `user_reaction`; per session, the five metrics.
- **One generation per consolidation**: mutate the genome into three candidates (one change
  each), **replay** them against the retrieval log and the fixture sessions (no live model,
  seconds), keep the best. Revert on regression. Safety rules and G1–G9 are never mutated.
- Scorecard gains one line: **corrections per session**, memory-on vs memory-off, per generation.
  This is the number no existing system reports.

Acceptance: `test_evolve.py` — three generations on the fixtures produce a monotone or reverted
fitness table; a mutated candidate that lowers recall is never selected.

## 5. Chat model (amends CUJ interview 3.3, TDD §9, A-4) — flag, not a change

A 4B model on LM Studio is the largest showcase risk in the document: S3b already concedes it
may not use injected memory. The memory layer is provider-agnostic, so keep LM Studio as the
no-key path but **demo on an API model unless offline is a hard requirement**. Decide at
approval; it changes nothing in the code.

## 6. Timeline (amends §10.1 freeze)

TDD says showcase 19:00; the team said 20:00. **Confirm.** Either way, pre-cut now rather than
at T+160: step 11 (router fit) is gone per §2; step 13 (pass^k outcome bench) is cut; step 12
runs with **≥20 LongMemEval questions**, not 50, across all six categories. Never cut 08, 09, or
the `evolve` node.

## Benchmarks (amends D-12, C-6, C-9; new lane)

| Benchmark | Use today | Why |
|---|---|---|
| **LongMemEval** | **Yes, primary.** ≥20 Q slice, six categories, judged by claude-sonnet-5 offline | multi-session chat, knowledge updates, abstention — exactly refutation and recall |
| **STATE-Bench** | Adapter only (O-6a), run Monday | deterministic DB diffs and a user simulator, memory-layer agnostic; no LLM judge needed, but it needs its task harness |
| **BEAM** | No | 1M–10M tokens; not today |
| **τ-Bench** | No | measures agent decision-making, not memory |
| **Ours** | Yes | five-metric scorecard + corrections per session, per generation, on the six fixture sessions |

Benchmarking is its own lane (below). It is the lane that needs the least pi and the most
patience, so it suits whoever is least set up for the extension work.

## Lanes (amends §10.2)

Six works only if the four contracts freeze by 16:45: `schema/claim.schema.json` (+`origin`,
`user_reaction`, `valid_from/valid_to`), `adapters/base.py`, the stdio protocol §5.3, and
`pi-extension/HOOKS.md` (A-1). If they do not freeze, collapse to four (marked ◆).

| Lane | Owns | Steps | Done when |
|---|---|---|---|
| 1 ◆ Integrator + extension + server + DAG | `pi-extension/`, `engram/server.py`, `engram/consolidate/`, `schema/`, `adapters/base.py`, CI, merges | 00, 01, 03, 04, 08, 09, 14 | hooks fire 100 % in live pi; forced `/compact` returns a digest |
| 2 ◆ Capture + gate | `engram/p1/` (salience rules, episodic append), `engram/p2/`, fixtures | 02, 05, 06 | every G-rule has a failing fixture; refutation lands same turn |
| 3 Stores | `engram/adapters/{sql,vector}.py`, md export, links | 07 | conformance green; A-3 xfail recorded |
| 4 ◆ Encoder + evolution | `engram/p1/llm_assist.py` (as DAG node), `engram/evolve/`, retrieval log | new | three generations on fixtures produce the fitness table |
| 5 ◆ Eval + pitch | `bench/`, LongMemEval slice, judge, scorecard, demo script | 10, 12 | scorecard renders with the LongMemEval row and the corrections line |
| 6 Demo environment | LM Studio / API model config, `config/*.example`, AGENTS.md, rehearsal, timing | A-4, §9 | session 1 → consolidate → session 2 runs clean twice |

Four-lane fallback: 3 folds into 2 (SQLite is small), 6 folds into 5. Each lane is one person
and one Claude Code session on branch `build/engram-v2`, one directory each, PRs to the
integrator at :00 and :30.
