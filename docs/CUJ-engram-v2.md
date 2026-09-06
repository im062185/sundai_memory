---
artifact: CUJ
feature: engram-memory-organ
version: v2.1
supersedes: [CUJ v1.0, AMD-01 §3, AMD-02 §7]
status: Draft — human approves before TDD v2 is written
interview_rounds: 6 (2026-09-06)
repo: https://github.com/im062185/sundai_memory
---

# CUJ v2 — Engram: a self-managing memory for a builder's agent, running in pi on a local model

## §1 Interview record (verbatim answers, four rounds)

| # | Question | Answer |
|---|---|---|
| 1.1 | Primary human? | "Human wanting to build products, do anything. This could be used by a downstream company, or individual consumer" |
| 1.2 | Where does the human intervene? | "This will be an autonomous system that will, over time, learn the human's behavior and needs as they relate to a project. It will self-manage." |
| 1.3 | First screen? | "A conversational process, effectively a chat with the agent system." |
| 2.1 | Consolidation pathway | "Effectively for the human, the memory consolidation pathway would be a DAG and the human would not know what happens under the surface here" |
| 2.2 | Review surface | "the human is very concerned about the performance of the memory system and will want to review the system based on memory benchmarks like State Bench (from Microsoft)" |
| 2.3 | What matters most | "speed, accuracy, tokens, precision, recall" |
| 3.1 | Which Pi? | https://pi.dev/ (Earendil's pi coding agent) |
| 3.2 | Test world | https://mem0.ai/blog/state-of-ai-agent-memory-2026 (→ LoCoMo / LongMemEval / BEAM family) |
| 3.3 | Local model | Small ~4B, served by LM Studio |
| 4.1 | Benchmark slice today | LongMemEval slice |
| 4.2 | Judge | Sonnet 5 (Anthropic API, offline batch) |
| 4.3 | pi integration | Python service behind MCP |
| 5.1 | Firing | "Memory fires structurally, not at the request of the agent. I don't care what the structure is." |
| 6.1 | Demo world | "Who is Sam??" → persona dropped; "the full workflow" → the demo conversation exercises the whole pipeline, subject = this project |
| 6.2 | Showcase bar | "Working prototype of the full system" |
| 6.3 | Who writes the TS extension | "Claude Code from the TDD" |

## §2 Persona and setting

**The builder** — a person building a product, any domain (interview 1.1).
Not a memory architect, does not want to be one. Talks to an agent and
expects it to remember correctly, forget correctly, and need no supervision
to do either. For the demo, the project under discussion is **this one**:
the fixtures are drawn from today's design conversation.

**The builder's tooling, fixed by interview:**
- Chat surface: **pi** (terminal agent, `pi.dev`), interactive mode
- Model: a **~4B open-weight model served by LM Studio**, configured in
  `~/.pi/agent/models.json`. Free, offline.
- Memory: **Engram**, a Python process driven by a thin pi extension whose
  lifecycle hooks fire recall and remember on every turn. the builder never
  configures it beyond install.
- Judge for the performance report: **claude-sonnet-5** via the Anthropic
  API, run as an offline batch — never in the chat loop.

The builder never sees a gate, a store, a schema, a DAG, or a benchmark harness
unless the builder asks.

## §3 The journey

### Session 1 — Monday

1. The builder opens pi and describes the project: what it is, the deploy target, a
   vendor SDK in use, two preferences ("no TypeScript", "keep answers short").
2. the builder states one thing as fact that is an unverified absence:
   *"The vendor SDK has no batch-write endpoint."*
3. The agent replies normally. Underneath, Engram extracts claims from
   the builder's turns. Preferences and stated facts promote. The absence claim is
   held: one non-enumerative source (the builder's say-so).
4. **The one visible behavior:** the agent asks one clarifying question,
   conversationally — *"On the batch endpoint — from the SDK reference, or
   from trying it?"* the builder: "from the docs, a while ago." The claim stays
   held with that provenance. the builder moves on.
5. the builder closes pi.

### Between sessions

6. Consolidation runs unattended (on the next Engram start, or on demand).
   It is a DAG: expire → capture → merge → adjudicate → promote → reindex →
   census. the builder does nothing and sees nothing.

### Session 2 — Wednesday

7. the builder reopens pi. The first reply already reflects Monday: the builder's
   preferences are applied unprompted; the project is referred to by the
   facts the builder gave.
8. the builder, in passing: *"Oh — the SDK shipped batch writes in 3.4."*
9. **Refutation.** Engram recognizes this contradicts a held claim about
   the same subject, refutes it in every store, and the agent confirms in
   one clause. No dialog, no approval.
10. Later the builder asks something that would have surfaced the old belief. It
    does not surface. *(Demo: the trace shows three stores forgot and one
    kept returning the dead claim until the router routed around it.)*
11. the builder asks *"what do you actually remember about this project?"* and gets
    a plain-language list: what the builder said, what the agent inferred, what
    was verified. No schema.

### Session 3 — Friday

12. the builder has shipped something and asks: *"Is your memory actually making
    you better at this project?"*
13. The agent returns a **performance report** whose first line is the
    scorecard — **speed · accuracy · tokens · precision · recall** —
    memory-on, then memory-off, then the four-store table with the same
    five columns, then three labeled tiers:
    - **Outcome** — pass^k on the project scenarios, memory-on vs off;
      plus the LongMemEval slice result with its categories (knowledge
      update, multi-session, preference recall, temporal), judged by
      claude-sonnet-5, stated as such.
    - **Component** — the four-store bench. Labeled "the pipe works," not
      proof of benefit.
    - **Provenance** — counts of said / inferred / verified / refuted since
      last report.
14. If the builder asks "what happened overnight," the answer is one paragraph of
    counts from the consolidation DAG. Never the mechanism.

## §4 What the builder sees vs. what the system does

| the builder sees | System does |
|---|---|
| pi, talking to a local model | Extension hook calls `recall` before the turn; model cannot skip it |
| Nothing | P1 extraction on the user turn — rule-based primary, 4B model as second opinion |
| Nothing | P2 gate: promote / hold / reject; only P2 writes to stores |
| One clarifying question, at most, per session | G3/G4 firing on an operator-stated absence claim |
| Nothing | P3 routing to graph / vector / SQL / symbolic |
| Correct recall next session | Merged four-store retrieval injected via `recall` |
| A correction acknowledged in one clause | Contradiction → refutation event → all stores |
| Nothing | Consolidation DAG on Engram start |
| Five-number scorecard on request | Component bench + outcome bench + LongMemEval slice + provenance census |
| A paragraph of counts | `out/consolidation.json` rendered |

## §5 Success criteria (NORMATIVE — become TDD acceptance tests)

| ID | Criterion |
|---|---|
| S1 | No memory-management UI is required to complete the journey |
| S2 | A conversational correction refutes the claim in every store within the same turn |
| S3a | Before the first model token of Session 2, recall has injected ≥1 preference and ≥1 fact from Session 1 (asserted from pi's event stream) |
| S3b | The first reply of Session 2 uses at least one of them (LLM-judged, reported separately; a 4B failure here is a model finding, not an Engram defect) |
| S4 | At most one clarifying question per session, only for an absence claim with insufficient provenance; the **extension renders it** to the TUI as a system-origin message — it is never left for the model to decide to ask |
| S5 | "What do you remember?" renders `source_class` in plain language (said / inferred / verified) |
| S6 | Trace is off by default; `--trace` shows per-turn extracted / gate / routed / retrieval |
| S7 | Consolidation is an explicit DAG: acyclic, each node once per pass, only `promote` writes to stores |
| S8 | Performance report labels outcome / component / provenance tiers; never presents component numbers as benefit |
| S9 | Report includes pass^k (k≥3) on S2–S5, memory-on vs off |
| S10 | Report's first line is the five-metric scorecard in this order: speed, accuracy, tokens, precision, recall |
| S11 | `recall` fires before the model's first token and `remember` fires after every user turn, 100% of turns in the replay fixture, measured from pi's JSON event stream. Below 100% is a defect |
| S12 | LongMemEval slice (≥50 questions, all six categories represented) runs end to end on LM Studio, judged by claude-sonnet-5, with per-category accuracy and the judge named next to every accuracy number |
| S13 | Chat loop runs with no network; only the judge and (optionally) STATE-Bench hook need a key |

## §6 Non-goals

- the builder approving or rejecting individual memories
- the builder choosing a storage backend
- Multi-user, shared memory, identity resolution
- Official STATE-Bench numbers (locked GPT-5.4 judge; Monday task O-6c)
- Web UI of any kind
- Anything requiring the builder to read a schema

## §7 Decisions fixed by this CUJ that the TDD inherits

| ID | Decision | Consequence for TDD |
|---|---|---|
| C-1 | pi is the chat surface | No custom chat loop; `chat/loop.py` from TDD v1.1 is retired |
| C-2 | Engram is a Python process; pi reaches it through a thin pi extension | Extension (TS, ~80 lines) with two lifecycle hooks: before-turn → `recall`; after-turn → `remember`. Same extension registers `explain` and `report` as model-callable tools. Engram runs as a local stdio/HTTP server started by the extension. No `pi-mcp-adapter`. |
| C-3 | 4B model on LM Studio | Rule-based P1 is primary; model extraction is a second opinion that can only *add* candidates, never override a rule-based `source_class` |
| C-4 | **Memory fires structurally, never at the agent's request** (human, round 5: "I don't care what the structure is") | Recall and remember are hook-driven; the model cannot skip, defer, or disable them. S11 asserts 100% firing from pi's event stream; anything less is a defect, not a tuning problem |
| C-5 | Consolidation trigger | On Engram start, on `engram consolidate` CLI, and via pi's custom-compaction hook when the extension owns compaction (extract → gate → store instead of summarize) |
| C-6 | LongMemEval slice is the outcome benchmark | Bench driver uses `pi -p` / `--mode json` headless; ≥50 questions, six categories |
| C-7 | claude-sonnet-5 judges | Batch, offline from the demo, key required, judge named in every accuracy line |
| C-8 | Five metrics are the scorecard | Per AMD-02 §3; refutation survival retired into precision |
| C-9 | STATE-Bench hook stays | O-6a adapter only; no run today |

## §8 Demo mapping (3 minutes)

| Min | Beat | On screen |
|---|---|---|
| 0:00 | Session 1 in pi: three turns, the absence claim, the clarifying question | pi TUI |
| 0:50 | `engram consolidate` — one line of counts | terminal |
| 1:05 | Session 2 opens knowing the project | pi TUI |
| 1:30 | The correction; one-clause acknowledgment | pi TUI |
| 1:45 | `--trace`: four stores, one still returning the dead claim | terminal |
| 2:10 | "Is your memory helping?" — scorecard line first, then the LongMemEval per-category row | pi TUI |
| 2:45 | "What do you remember?" — provenance list | pi TUI |

## §9 Approval

Human approver: ______________________  Date: __________

Amendments after approval re-open the TDD.
