# engram — a write-gated, bitemporal memory layer for agents

SundAI hackathon project. A memory *writing* layer designed to sit on top of a flexible harness (Pi Agent, OpenClaw, or any loop that exposes hooks on turn-end and session-end).

## Where SOTA is lacking (research findings)

| System | Strength | Documented gap |
|---|---|---|
| Mem0 | Extract→consolidate→retrieve pipeline, ~91% LoCoMo | Indexing reliability at scale; update/supersession underperforms; graph variant removed from OSS SDK |
| Zep/Graphiti | Temporal knowledge graph, strong LongMemEval | ~600K tokens/conversation footprint (per Mem0's measurements); delayed post-ingestion retrieval |
| Letta/MemGPT | LLM-managed memory tiers, transparent blocks | OS-paging overhead/latency; their own blog shows a plain filesystem agent beats specialized tools on LoCoMo |
| Honcho | Reasoning-first ToM extraction, SOTA on LongMem-S/LoCoMo/BEAM at ~5% context | Users request: visible inference chains, user-defined forgetting, confidence surfacing |
| All of the above | recall benchmarks | **Invalidation**: append-only stores hit ~55% stale-answer rates on update benchmarks; "most recent wins" heuristics fail; cross-session contradictions go unresolved in up to 42% of cases (BeliefShift) |

**The gap is the write path, not retrieval.** Recent work (GovMem arXiv:2607.02579, TOKI arXiv:2606.06240, "Are We Ready For An Agent-Native Memory System?" arXiv:2606.24775, "Don't Ask the LLM to Track Freshness" arXiv:2606.01435) converges on the same conclusion: systems store happily but can't decide *when to write*, *when a repetition is evidence vs. an echo*, and *how to retract what was true last week*. That is exactly the "Processing" band on our whiteboard — so we scope there.

## Architecture (maps to the whiteboard)

```
INPUT (sensory)          PROCESSING II (fast/online)      STORAGE
conversation turns  ──►  declarative extraction      ──►  bitemporal SQLite
prev. memory             SPRT write gate                  (valid_time × system_time)
our prompts              salience weighting               + MD export for the agent

                         PROCESSING I ("sleep", offline)
                         candidate expiry · decay demotion · dedupe
```

## Mechanisms, with their math (SOTA neuro → algorithm)

1. **Write gate = optimal stopping.** "When has a candidate fact accrued enough evidence to persist?" is a sequential decision problem. We use Wald's **Sequential Probability Ratio Test**: each corroborating mention adds log-likelihood `ln(p₁/p₀)`; promote at `ln((1−β)/α)`, reject at `ln(β/(1−α))`. SPRT is provably the stopping rule minimizing expected sample count at fixed error rates — the "optimum stopping point" mechanism, and a principled answer to GovMem's "repetition is not evidence" problem (independent-channel salience weights, not raw counts).
2. **Forgetting = ACT-R base-level activation.** `Bᵢ = ln(Σⱼ (t_now − tⱼ)^−d)`, d≈0.5 — the rational analysis of memory (Anderson & Schooler 1991): retrieval odds should track environmental odds of reuse. Frequently/recently used facts stay retrievable; one-off trivia (a past deadline) decays below threshold and is *demoted, never deleted*. Retrieval itself re-strengthens (testing effect).
3. **Consolidation = Complementary Learning Systems.** Fast episodic candidate buffer (hippocampus-analogue) vs. slow, curated long-term store (neocortex-analogue), reconciled by an offline `sleep()` pass — mirroring the CLS framing now standard in the agent-memory literature (McClelland 1995; Kumaran, Hassabis & McClelland 2016; dual-layer agentic memory, arXiv:2608.22215). Whiteboard: "fire together wire together" = SPRT evidence accumulation; "sleep consolidation" = the offline pass.
4. **Contradiction = bitemporal supersession, not overwrite.** Facts carry `valid_from/valid_to` (world time) and `sys_from` (write time); a correction closes the old interval and links `superseded_by`. Preserves as-of audit queries and resolves the ~42% unresolved-contradiction failure deterministically instead of asking the LLM to track freshness.

## Pressure test (the "red-team subagent" run)

`python eval/pressure_test.py` — offline, deterministic; 5 synthetic sessions over 30 days including a preference update (vim→helix) and one-off trivia, probed with 5 questions. Three conditions share one answerer; only the memory layer varies.

| condition | recall | stale answers |
|---|---|---|
| no memory | 0/5 | 0 |
| append-only RAG (Mem0-style naive baseline) | 3/5 | **1** (answers "vim") |
| **engram** | **4/5** | **0** |

The engram miss is the expired one-off deadline — by design (decay). Verdict: **implementable for demo in ~250 LOC**; the with/without comparison works end-to-end. Swap `mock_answer` for a live model via `ANTHROPIC_API_KEY` for the demo.

## LoCoMo benchmark (retrieval-level, offline)

`python eval/locomo_bench.py` — 10 LoCoMo conversations, 1,540 QA (categories 1–4; 5=adversarial excluded). Metric: **answer-in-context recall** (≥50% of gold-answer content tokens in the supplied context), isolating the memory layer from answerer quality. Parameters tuned on convs 0–4 only; convs 5–9 held out.

| condition | tune 0–4 | **held-out 5–9** | tokens/q |
|---|---|---|---|
| full history (ceiling) | 0.938 | 0.938 | ~18,000 |
| keyword RAG k=10 | 0.593 | 0.595 | ~400 |
| engram facts-only (regex) | 0.000 | 0.000 | 0 |
| **engram (episodic)** | 0.818 | **0.767** | ~890 |

Held-out per-category: multihop 0.56, temporal 0.76, open 0.28, singlehop 0.89 — 82% of the full-history ceiling at **20× fewer tokens**, +17 pts over the keyword baseline (+9 over a budget-matched k=20 baseline).

## Fix log (what pressure-testing found, in order)

1. **Regex fact extraction: 0.000 on real dialogue.** Fixed: LLM extractor (`engram/extractor.py`) emitting (subject, predicate, object, confidence, valid_time); confidence becomes SPRT evidence `ln(c/(1−c))`, replacing hand-set salience boosts. Regex fallback when no API key; live benchmarking of this path still requires a key.
2. **Testing-effect strengthening caused cross-query interference** — first episodic run lost to keyword RAG. Fixed: strengthening is thread-scoped (`thread=` param); independent probes never pollute rankings.
3. **Winning retrieval mechanisms are memory-theory imports:** surprisal weighting (`1/ln(1+df)`, distinctiveness) + temporal contiguity (Howard & Kahana's TCM: seed hits reinstate ±2 adjacent turns).
4. **Activation term measurably hurts archival probes** (−0.6 pts on tune split). Fixed: `w_act=0` default for probe retrieval; activation retained for thread continuity and fact-layer forgetting.
5. **LSA semantic blending: ~0 held-out gain** (0.767 vs 0.771) — per-conversation LSA lacks signal; pretrained encoders can't download in the sandbox. Shipped as pluggable interface (`engram/semantic.py`), defaulted off. Open-domain (0.28 vs 0.66 ceiling) is the remaining lexical-matching gap; a sentence encoder is the drop-in fix.
6. **Gate recalibration regression, caught by the staleness test** (4/5 → 1/5): graduated facts discarded rehearsal history; re-mentions of held facts restarted candidacy instead of reinforcing. Fixed: candidate mention timestamps carry over as access history on promotion; re-mentions `_touch` the existing trace. Restored 4/5, 0 stale — and the one-off deadline is now filtered by the gate (never promoted) rather than by decay, the cleaner mechanism.

## Remaining known gaps

- Live end-to-end eval (real answerer + LLM extractor) — needs `ANTHROPIC_API_KEY`; everything here is retrieval-level upper bound.
- Pretrained embeddings for open-domain/inference questions (interface ready).
- Multi-hop assembly (0.56): contiguity finds neighbors in time, not content; entity-linked spreading activation needs the LLM extractor's entities.
- Harness hooks (`on_turn`/`on_session_end`/`pre_prompt`) for Pi Agent/OpenClaw; persona-context salience prior from the whiteboard.

## Layout

```
engram/core.py          # store + SPRT gate + decay + contiguity recall + sleep
engram/extractor.py     # LLM fact extraction (regex fallback offline)
engram/semantic.py      # pluggable semantic scorer (LSA reference impl)
eval/pressure_test.py   # staleness/contradiction eval (offline)
eval/locomo_bench.py    # LoCoMo tune/test benchmark (offline)
```
