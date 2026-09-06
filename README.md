# Engram

A self-managing memory organ for **pi**. It fires from pi's lifecycle hooks —
`recall` before every turn, `remember` after every user turn — so the model
never decides whether to remember. It cannot skip, defer, or disable it.

Built in one hackathon day across four lanes. This README is written from the
merge state on `lane/d`: what is actually in the tree, what was cut, and what is
open. Numbers that were not measured are printed as `—`, never as `0`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd pi-extension && npm ci && cd ..   # required — without it a pi turn hangs silently
.venv/bin/python -m pytest tests -q  # 104 passed, 1 xpassed
rm -rf out                           # the sqlite store persists; reseeding it skews the run
.venv/bin/python -m bench.component  # per-store retrieval, and the A-3 probe
.venv/bin/python -m engram report    # the five-metric scorecard
```

All six lines were run against a **fresh clone of `build/engram-v2`**, not this
working copy.

The three-minute demo, beat by beat, is **[docs/DEMO.md](docs/DEMO.md)** — every
command in it was run on this machine and every screen is copied from that run.

---

## What was built

**The organ fires structurally.** A 141-line TypeScript pi extension
(`pi-extension/src/index.ts`) spawns Engram as a Python stdio server and wires
four hooks: `session_start → consolidate`, `before_agent_start → recall` (which
injects a `Memory (provenance-tagged):` message), `agent_end → remember` for the
user turn and the assistant turn, then `feedback` with the injected ids.
Those four were verified live against pi 0.85.1 — `out/trace.jsonl` and
`out/retrieval_log.jsonl` show each one firing, and a claim written in one
session arriving in the next session's model context. A fifth hook,
`session_before_compact`, returns the consolidation digest as the compaction
summary instead of a model-written one; that code path was **not exercised** in
my rehearsal — it needs a forced `/compact` in an interactive session.

**Capture first.** Every turn is appended verbatim to the episodic log before
anything judges it (`engram/p1/episodic.py`). The per-turn path only *tags
salience* — explicit preferences, absence claims, refutations — with rules, not
a model (`engram/p1/rules.py`). Nothing is thrown away because a per-turn
extractor did not see the point of it yet.

**Only the gate writes.** `engram/p2/gate.py` is the sole writer to any store;
`tests/test_isolation.py` fails the build if anything else calls `write` or
`refute`. Nothing is ever hard-deleted — refutation sets `status` and
`valid_to`, so a corrected belief keeps its id and its history.

**Consolidation is a DAG, with hindsight.** `expire → capture → encode → merge →
adjudicate → promote → wire → reindex → census → evolve`, an explicit
`networkx.DiGraph` whose acyclicity is asserted at import
(`engram/consolidate/`). LLM encoding lives here, not in the turn loop, so it
judges a turn knowing what came after it.

**Two retrieval stores behind one frozen interface.** `sqlite` (FTS5, with a
link boost weighted 0.2) and a naive numpy `vector` arm, both implementing
`engram/adapters/base.py`. Two more adapters ship alongside them and are not
retrieval stores: `markdown` (export) and `null`. The demo says *two stores*,
and it means the two that answer queries.

**Measurement that refuses to flatter itself.** Five metrics in a fixed order —
speed · accuracy · tokens · precision · recall — memory-on then memory-off, then
a per-store table, then three labelled tiers (Outcome / Component / Provenance).
Component numbers are labelled *"the pipe works"* and are never presented as
benefit. Every accuracy number carries the judge's name or the words
`judge: not run`. Token counts carry `(est)` because the server's `tokens` field
is `len(text)//4`, not a tokenizer count.

---

## The A-3 outcome: the vector arm still returns refuted claims

**It reproduces.** `engram/adapters/vector.py:93` writes back the status it
found instead of `"refuted"`:

```python
def refute(self, claim_id: str, *, by: str | None = None) -> None:
    # Deliberately naive (A-3): keep refuted claims for the fallback vector path.
    if claim_id in self._claims:
        self._claims[claim_id]["status"] = self._claims[claim_id].get("status", "promoted")
```

So a claim that was promoted stays promoted. Refute it through the product's own
`refute` op and ask again:

```
A-3 sqlite: refuted a promoted claim → returned again: False, stats.refuted=2
A-3 vector: refuted a promoted claim → returned again: True,  stats.refuted=0
```

The vector arm hands the dead claim back **on the very next query**, and its
`stats()` reports **zero** refutations — the store cannot even tell you the
refutation happened. `tests/test_stores.py::test_vector_refute_keeps_returning`
is marked `xfail` and **xpasses**: the bug is present, recorded, and *not fixed*,
per TDD §9 — never fix the vector arm before the component snapshot records it.

Two things this exercise taught that were not in the plan:

1. **A-3 does not show up in the query table** on the CUJ corpus. The absence
   claim there is only ever *held*, so no store ever returns it, before or after
   the correction. Low precision in the table is ordinary retrieval noise, not
   A-3. The report now names A-3 **only** from the explicit probe
   (`bench/component.py:a3_probe`) and never infers it from a precision number.
2. **The 1/k rule needs a refuted id to bite.** A returned refuted id lowers
   precision by exactly 1/k and is a false positive even when it appears in gold
   (`bench/metrics.py`, asserted in `tests/test_metrics.py`). On this corpus that
   penalty never fires, which is exactly why the probe exists.

---

## Measured: LongMemEval oracle slice, 22 questions, 2026-09-06 evening

Answerer `openai/gpt-4.1-mini` through `pi -p --mode json`; judge `openai/gpt-4.1` (frozen, named on every line;
prompt in `bench/judge.py`). Four arms, run in this order as the retrieval path was fixed. Cost of all four: about six dollars.

| category | rules only | + hindsight encoder | + encoder + embedding rerank | memory-off, raw history |
|---|---|---|---|---|
| single-session-user | 4/4 | 2/4 | **4/4** | 3/4 |
| single-session-preference | 2/3 | 2/3 | **2/3** | 1/3 |
| single-session-assistant | 2/3 | 3/3 | **3/3** | 2/3 |
| multi-session | 3/4 | 2/4 | **3/4** | 3/4 |
| temporal-reasoning | 3/4 | 4/4 | **4/4** | 3/4 |
| knowledge-update | 0/4 | 0/4 | **2/4** | 2/4 |
| **total** | 0.64 | 0.59 | **0.82** | 0.64 |
| median memory tokens per question | 106 | 204 | 169 | ~1500 (6000-char history budget) |

How to read it, honestly:

- **The final arm beats the raw transcript by 18 points at roughly a ninth of the tokens.** It wins or ties every category.
- **Knowledge-update went from 0/4 to 2/4, tying memory-off.** The first two memory arms lost it because keyword
  ranking (bm25) prefers short on-topic chatter over the sentence carrying the number. The fix was not the encoder
  alone: it was an embedding rerank over the keyword candidates (`ENGRAM_EMBED=1`, `text-embedding-3-small`,
  cached, cosine-dominant). The two remaining failures are one conversation where the engineer count is stated
  ambiguously, plus its abstention twin; the top recalled line there is the user's own question, not the fact.
- **The encoder alone was not better (0.59).** It sharpened assistant-fact and temporal questions, but the
  abstention hint made the model decline on facts that were stored yet not recalled. Once retrieval was fixed the
  same hint stopped hurting.
- **22 questions is a pilot.** One question moves a category by 25 points. The pattern is the finding; no single cell is.
- Retrieval-level numbers (the per-store table above) are never placed in this table. They measure the pipe, not benefit.

Reproduce: `ENGRAM_BENCH_ENCODER=1 ENGRAM_EMBED=1 python -m bench.longmemeval --run --answerer pi --model openai/gpt-4.1-mini --arm on`,
then `--arm off`, then `python -m bench.judge out/bench/longmemeval_on.json` (and `_off`), then `python -m bench.fold_results`
and `python -m engram report`. Earlier arms are kept as `out/bench/*_rules.json` and `*_encoder.json`.

## The judge: how it is named (was: not run)

`bench/judge.py` scores LongMemEval answers with **`claude-sonnet-5`**, and only
when `ANTHROPIC_API_KEY` is present. It is not set on this machine, so:

```
LongMemEval slice — accuracy — · n=0 · judge: not run
  (no per-category result — slice not run)
```

`n=0` because the slice was never *run* here, not because the slice is empty:
it holds 22 questions and is checked into the tree. Accuracy is null by design,
never zero.

The judge also refuses to score any record whose answerer was a stub, whose run
errored, or whose answer is empty — so a run that never called a model can never
turn into an accuracy number. If every record is refused, the judge's own name
is replaced by `not run`, whatever client was configured.

The slice itself is real and was built by reading the release, not by assuming
its layout: **22 questions** from `longmemeval_oracle` on
`xiaowu0162/longmemeval-cleaned`, all six `question_type` values represented,
4 abstention (`_abs`) questions included, selected by a deterministic rule
recorded in the file. The layout evidence — variant names, file sizes, field
lists, and the places where the README and the actual file disagree — is in
[bench/LONGMEMEVAL.md](bench/LONGMEMEVAL.md).

---

## What was cut

| Cut | Why |
|---|---|
| **pass^k on the project scenarios** (CUJ S9) | Step 13 was cut before the build started (AMD-03 §6). The report prints `pass^k: not run` with that reason attached rather than omitting the line. |
| **A ≥50-question LongMemEval run** (CUJ S12 asks ≥50) | The slice is 22, sized so all six categories are covered and a full arm fits in the day. The run itself needs a chat model and a judge key; neither exists here. `n` is printed next to every accuracy number so the slice size is never hidden. |
| **STATE-Bench** | O-6a adapter only, no run today (CUJ C-9). |
| **A real LM Studio probe for A-2** | LM Studio is not installed on this machine. Split instead: *what pi does with each reasoning shape* is fixed by the pi build and was probed offline with a scripted server — pi turns `reasoning_content` into a real thinking block, and does **not** parse inline `<think>` tags, so `p1/think.py` must strip them itself. *What the server emits* is a demo-machine question and is flagged as such in `pi-extension/HOOKS.md`. |
| **Fixing A-3** | Deliberate. TDD §9. |

---

## What is open

**Retrieval quality is measured on 4 of 25 queries.** `bench/queries.json` has
25; lane B's two fixture sessions only ever state 6 of the 25 fact keys, so 21
queries have no resolvable gold. They are **skipped, not scored 0.00** — scoring
them would measure the corpus, not the retriever — and the skipped count is
printed and written into `out/results.json`. Closing this needs a corpus that
states the other facts, or a query set cut down to the corpus.

**Three defects found while rehearsing the demo**, written down rather than
patched — two live in other lanes' code and the first is pi's own behaviour
(details in [docs/DEMO.md](docs/DEMO.md)):

1. **An untrusted project silently has no memory.** pi loads `.pi/extensions/*.ts`
   only after the project is trusted, and `-p` / `--mode json` never prompt. Pass
   `-a`, or the demo runs with an empty memory and no error.
2. **Held claims are unreachable**, so on the correction turn `recall` injects
   nothing — the model is corrected without seeing what it is correcting.
3. **`export_markdown()` renders nothing**: no claim on the CUJ journey is
   assigned tier `always`, so `engram explain`'s "About you" and "Standing notes"
   are empty even with six promoted claims.

A fourth, the extension killing pi at exit, was **fixed by lane A** in
`unref()`-ing the server child (`pi-extension/src/index.ts:26`) and is no longer
open: re-rehearsed on the merged tree, both headless sessions exit 0 with an
empty stderr, and session 2 still receives the claim written in session 1.

**The component bench is not idempotent.** Rerun it without `rm -rf out` and
the sqlite row changes — the store persists, the gate dedups the reseed, so the
gold patterns resolve against one claim instead of twelve and precision drops
to 0.00 while the in-memory vector row stays put. The bench now prints a warning
when `out/component` already exists rather than reporting the second number as
if it were the first, but the real fix is a bench that seeds into a fresh
directory per run.

**The turn-level tagger merges facts.** "Keep answers short when summarizing and
coaching. The vendor SDK has no batch-write endpoint." becomes **one** claim of
`kind: absence`. When session 2 refutes the absence, the preference goes down
with it, and no promoted claim carries "keep answers short" any more. Capture
first is doing its job — the verbatim turn is in `out/episodes.jsonl` — but the
tagging granularity is a real gap, and hindsight encoding in consolidation is
where it should be closed.

**`ENGRAM_ASSIST` is named by TDD §6.8 and read by nothing.** The bench driver
sets it to disable the encoder during ingest; `engram/server.py` does not look at
it. The discrepancy is recorded in `bench/longmemeval.py` rather than resolved in
one direction silently.

**`corrections per session, per generation`** prints `not measured` until
`out/generations/gen-*/fitness.json` exists.

---

## Layout

```
engram/            the organ: server, p1 capture, p2 gate, p3 router,
                   encode, consolidate DAG, adapters, report
pi-extension/      the pi extension (TypeScript) + HOOKS.md probe evidence
bench/             metrics, query set, component bench, LongMemEval driver,
                   the judge, and the demo rehearsal harness
docs/              CUJ, TDD, AMD-03, LANES, PROTOCOL, DEMO
schema/            claim.schema.json (frozen)
```

Frozen contracts — changed only by agreement of all four lanes:
`schema/claim.schema.json`, `engram/adapters/base.py`, `docs/PROTOCOL.md`,
`pi-extension/HOOKS.md`.

No network in the tests or the chat loop. `bench/judge.py` is the only file that
reads `ANTHROPIC_API_KEY`, and nothing in this repo writes to `~/.pi/agent` —
configs ship as `config/*.example` with the copy command printed.
