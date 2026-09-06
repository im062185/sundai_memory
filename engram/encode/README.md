# Lane C — hindsight encoder and evolution

Two functions, both called from the consolidation DAG, neither on the hot path.

| Entry point | DAG lookup | Node |
|---|---|---|
| `encode(episodes, persona, related) -> list[dict]` | `_opt("engram.encode.encode", "encode")` | `encode` |
| `evolve(store, signals, generations_dir) -> GenerationReport` | `_opt("engram.evolve", "evolve")` | `evolve` |

## Why it runs late

At turn time nothing is known about whether a fact matters, and the human's
reaction to the assistant's output arrives *one turn later*. So capture is
verbatim and immediate, and judgment waits for consolidation, where the
following human turn is visible. `user_reaction` is the signal no other system
uses; reading it is the entire reason this code is not in the hot path.

## Naming, and a trap worth not repeating

- `engram/encode/__init__.py` deliberately re-exports **nothing**. The DAG reaches
  the encoder as the submodule `engram.encode.encode`, and exporting a function
  called `encode` from the package shadows it: `import engram.encode.encode as m`
  then binds the *function*, and `m.LAST_STATS` raises `AttributeError`.
  Import as `from engram.encode.encode import encode`.
- `engram/evolve/` has the mirror-image constraint. The DAG reads
  `engram.evolve.evolve` as a **package attribute**, so `__init__.py` must export
  `evolve` — and therefore the driver module is `run.py`, not `evolve.py`.

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `ENGRAM_ENCODER_MODEL` | `claude-haiku-4-5-20251001` | model for the live encoder call |
| `ANTHROPIC_API_KEY` | unset | required only for the live call |
| `ENGRAM_ENCODER_FAKE` | unset | `1` replays `tests/fixtures/encode_*.json`; the whole test suite runs this way |

No key and no fake is **not** an error: `encode()` records the failure on
`LAST_STATS.model_error`, returns no candidates, and consolidation finishes
(TDD §2.4).

## Contracts the encoder honours

- It **never writes to a store.** It returns candidates; `engram/p2` decides.
  `encode()` takes no store argument, and `tests/test_encode.py` asserts the
  package imports neither `adapters` nor `p1`/`p2`.
- Candidates carry no `id`, `status` or `created_at` — `consolidate/nodes.py::_normalize`
  fills those before the gate.
- A malformed candidate is **dropped and counted**, never repaired: shape
  violations of G2 (`assistant_turn` may not be `inferred`) and G9
  (`assistant_thinking` must be `inferred`), and anything the frozen schema
  rejects. Counts land on `LAST_STATS`.
- `verbatim` is a **flag, never a rewrite.** Text quoted from an episode, or
  containing a command, path, version number or quoted rule, is flagged. The
  claim text itself is left exactly as the model wrote it — substituting a "more
  exact" source sentence drags conversational framing (`"Oh, correction: ..."`)
  into what has to stay a declarative claim.

### Known contract conflict: `entities`

The lane spec requires `entities` on each candidate, but `schema/claim.schema.json`
is `additionalProperties: false` and has no such property, and
`SQLiteStore.write()` has no such column. `entities` (and `source_turn_index`)
ride along as sidecar keys and are stripped before schema validation. Nothing
breaks today because the store ignores unknown keys, but **the integrator should
decide** whether `entities` gets a schema property or leaves the spec.

## Fixture coverage

All four fixtures are recorded model responses replayed by `FakeClient`. Each is
`{"name", "note", "match", "response"}`, where `response` is the raw string a
model would return, so the parsing path is exercised exactly as it is live.

| Fixture | Selected by | Covers |
|---|---|---|
| `encode_session1.json` | `session1` | six candidates over the CUJ session 1: a standing rule quoted verbatim, an absence claim the model wrongly marked `verbatim: false`, and a thinking-derived candidate |
| `encode_session2.json` | `session2` | the hindsight case — the model records `user_reaction: "approving"` for an assistant turn, and the following human turn is `"Oh, correction: ..."`, so `encode` must return `correcting` |
| `encode_invalid.json` | default | five malformed entries (non-object, G9, G2, bad `kind`, empty `text`) and one survivor |
| `encode_badjson.json` | `badjson` | prose instead of JSON; the whole response is dropped and counted |

## Evolution

One consolidation is one generation. `out/generations/gen-NNN/` holds the genome
(`weights.json`, `encoder.lessons.md`, `persona.md`, `tiers.json`) plus
`fitness.json` and a readable `DIFF.md`, so a bad generation is a `git`-style
revert rather than an archaeology exercise.

Fitness, replayed over the logged queries with no live model:

| Metric | Direction | Selectable |
|---|---|---|
| `recall_at_k` | up | yes — and never traded away |
| `corrections` | down | yes |
| `tokens` | down | only while `recall_at_k > 0` |
| `median_ms` | down | **no** — reported only |

The last two rows are guards found by running the loop, not by reading it. With
recall tied at zero, minimising tokens is solved perfectly by injecting nothing,
which ratchets `k` and the token budget down every generation and starves
retrieval for good. And a replayed recall takes ~0.1 ms, so run-to-run jitter
exceeds any real effect — selecting on it makes generations churn on noise and
makes "the parent survives when nothing improves" a coin flip.

Gate rules G1–G9 and safety rules are **not genome**. `genome.WEIGHT_KEYS` is the
entire mutable surface and `genome.validate()` raises on anything else;
`tests/test_evolve.py` asserts it across every generation written.

## Running

```bash
uv run python -m pytest tests/test_encode.py tests/test_evolve.py -q
```

Both files run offline. Nothing in the suite makes a network call.
