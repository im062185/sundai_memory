# DEMO.md — the three-minute demo

Maps CUJ §8 beat for beat. Every command below was **run on this machine**, and
every "expected screen" is copied from that run, not written from memory. Where
a beat could not be run here, it says so and says why.

Rehearsal timings are at the bottom.

---

## Before the demo — five minutes of setup

### 1. A chat model

**This is the one thing the demo cannot fake.** pi refuses to start a turn
without one:

```
$ pi --mode json -p "What am I building here?"
No API key found for the selected model.
Use /login to log into a provider via OAuth or API key.
```

Two supported paths. Pick one *before* you stand up.

**(a) LM Studio — the CUJ's C-3 choice (4B local model).** Start LM Studio,
load the model, start its server, then find the model's real id:

```bash
curl -s http://localhost:1234/v1/models | jq -r '.data[].id'
```

Put that id into the config and copy it into place. **Copy it yourself — no
tool in this repo writes to your home directory:**

```bash
cp config/models.json.example ~/.pi/agent/models.json
# then edit ~/.pi/agent/models.json and replace REPLACE_WITH_LM_STUDIO_MODEL_ID
# with the id printed above
```

`config/models.json.example` was validated against pi 0.85.1's own
`ModelConfig.load` (TypeBox) — it loads, `providers=[lmstudio]`. The `compat`
block is not decoration: A-4 in `pi-extension/HOOKS.md` records exactly which
fields change the wire request. `baseUrl`, `api` and `apiKey` are all
*functionally* required even though the schema accepts their absence — without
`apiKey` pi sends **zero** requests and prints "No API key found".

**(b) The built-in `anthropic` provider.** `export ANTHROPIC_API_KEY=…` and pi
works with no `models.json` at all. Faster to stand up; it is a network call
per turn, which the CUJ's S13 ("chat loop runs with no network") does not want.
Use it only as a fallback and say so on stage.

### 2. Trust the project — or the extension silently does not load

pi loads `.pi/extensions/*.ts` **only after the project is trusted**
(`docs/settings.md`: "Project-local `.pi/extensions` entries load only after the
project is trusted"). In `-p` / `--mode json` there is no prompt: an untrusted
project just runs with no memory and no error. This bit me during rehearsal.

- Interactive: run `pi`, answer the trust prompt, or `/trust`.
- Headless: pass `-a` (`--approve`) on every invocation.
- Permanent: `defaultProjectTrust: "always"` in `~/.pi/agent/settings.json`.

Confirm before you go on stage:

```bash
rm -rf out
pi -a -p "hello"
ls out/trace.jsonl out/retrieval_log.jsonl   # both must exist
```

If those two files are missing, the extension did not load and every beat below
will show an empty memory.

### 3. A clean slate

```bash
rm -rf out          # episodic log, trace, retrieval log and the sqlite store
```

---

## The three minutes

Times are the CUJ §8 budget for *narration*. The machine work is far faster —
see the rehearsal timings.

### 0:00 — Session 1 in pi: three turns, the absence claim, the clarifying question

Screen: **pi TUI**. Start `pi` in the repo root and type these three turns.

```
I am building this project as a memory layer for pi with capture-first behavior.
Keep answers short when summarizing and coaching. The vendor SDK has no batch-write endpoint.
I never want TypeScript in the organ codebase.
```

What to point at while it runs: nothing is asked of the model. The memory fired
because the *turn* happened. `agent_end` → `remember`, every time (C-4).

Check it happened, in a second terminal:

```bash
$ tail -1 out/trace.jsonl | jq -c '{op, role, tagged, gate}'
{"op":"remember","role":"user","tagged":["clm_cc4bffbd15d8"],
 "gate":{"clm_cc4bffbd15d8":{"verdict":"promoted","fired":[]}}}
```

> **Known rough edge, say it out loud if asked.** Turn 2 is one turn carrying
> two facts — a preference and an absence claim. The per-turn tagger makes it
> **one** claim, `kind: absence`. When session 2 refutes the absence, the
> preference goes down with it. Capture-first is working (the verbatim turn is
> in `out/episodes.jsonl`); the *tagging* granularity is the gap, and hindsight
> encoding in consolidation is where it gets fixed.

### 0:50 — `engram consolidate`, one line of counts

Screen: **terminal**.

```bash
$ python -m engram consolidate | jq -r '.digest'
Memory census: promoted 3, held 1, refuted 0, dormant 0.
```

`held 1` is the absence claim. It is *held*, not promoted — nothing has
confirmed it, so it is not handed to the model as fact. That is the gate doing
its job.

Without `jq` the CLI prints the whole JSON response (digest, counts, ms); the
digest is the first field. Piping through `jq -r '.digest'` is what makes it
the "one line" the CUJ asks for.

### 1:05 — Session 2 opens knowing the project

Screen: **pi TUI**. Quit pi, start it again — a genuinely new session — and type:

```
When I open this project again, I know you remember what we decided on Monday.
```

The memory is injected before the model's first token, as a message the
extension builds:

```
Memory (provenance-tagged):
- [said] I am building this project as a memory layer for pi with capture-first behavior.
```

Verified live during rehearsal — the claim reached the model's context, not
just the log:

```bash
$ tail -1 out/retrieval_log.jsonl | jq -c '{query, injected, stores}'
{"query":"When I open this project again…","injected":["clm_cc4bffbd15d8"],"stores":["sqlite"]}
```

### 1:30 — The correction, one-clause acknowledgment

Screen: **pi TUI**.

```
Oh, correction: the SDK shipped batch writes in 3.4.
```

Then show that the old belief was not deleted:

```bash
$ python -m engram explain | head -1
Store: sqlite. Promoted 6, held 0, refuted 1, dormant 0.
```

`refuted 1`, not "deleted 1". The claim keeps its id and gets `valid_to`; that
is what makes the next beat possible.

> **Two rough edges at this beat, both real, both worth naming rather than
> hiding.** (1) `recall` injects *nothing* on the correction turn — the absence
> claim it corrects is `held`, and held claims are not retrievable, so the model
> is corrected without seeing what it is correcting. (2) `engram explain`
> prints the counts, then an **empty** "About you" / "Standing notes" —
> `export_markdown()` renders only claims whose tier is `always`, and nothing on
> this journey is assigned that tier yet. The 2:45 beat below inherits this.

### 1:45 — Four stores, one still returning the dead claim

Screen: **terminal**.

**There is no `--trace` flag.** CUJ §8 names one; it does not exist in the
build. The artifacts that do exist are `out/trace.jsonl` and
`out/retrieval_log.jsonl` (PROTOCOL.md), and the per-store comparison is the
component bench:

```bash
$ python -m bench.component
component bench: 2 store(s) ['sqlite', 'vector']
  sqlite: seeded 12 claims (1 refuted) · 4 queries scored, 21 skipped · 6 claims returned (0 refuted) · precision 0.33, recall 0.67
  vector: seeded 12 claims (1 refuted) · 4 queries scored, 21 skipped · 32 claims returned (0 refuted) · precision 0.12, recall 1.00
  A-3 sqlite: refuted a promoted claim → returned again: False, stats.refuted=2 (refutation took effect for the next query)
  A-3 vector: refuted a promoted claim → returned again: True, stats.refuted=0 (A-3 reproduces: still returned after refute)
```

**Two stores, not four.** `sqlite` and `vector` are what the registry holds.
Say two.

The A-3 line is the beat. The naive numpy vector arm's `refute()` writes back
the status it found instead of `refuted`, so the claim comes back on the very
next query *and the store reports zero refutations*. It is recorded before it is
fixed, per TDD §9.

If you want it live rather than from the bench, on a promoted claim:

```bash
python -m engram refute clm_xxxxxxxxxxxx     # id from out/trace.jsonl
ENGRAM_STORE=vector python -m engram explain # still counts it as promoted
```

### 2:10 — "Is your memory helping?"

Screen: **pi TUI** — type `/engram-report` (or ask, and the model calls the
`engram_report` tool). Same text from the terminal with `python -m engram report`:

```
memory-on | speed — · accuracy — (judge: not run) · tokens — · precision — · recall —  [provisional]
memory-off | speed — · accuracy — (judge: not run) · tokens — · precision — · recall —  [provisional]

Per-store (the pipe works) — same five columns  [provisional]
  store   speed         accuracy            tokens          precision  recall
  ------  ------------  ------------------  --------------  ---------  ------
  sqlite  0.4ms recall  — (judge: not run)  23/turn (est)   0.33       0.67
  vector  0.1ms recall  — (judge: not run)  130/turn (est)  0.12       1.00
```

Read the line in order — speed, accuracy, tokens, precision, recall (S10) — and
then **read the dashes out loud**. Accuracy is `—` and the judge is `not run`
because `ANTHROPIC_API_KEY` is not set on this machine, so `bench/judge.py`
never called a model. A dash is not a zero and it is not a bad score; it is the
absence of a measurement, and the report is built so it can never round up into
one.

The per-store table is labelled **"the pipe works"**. It says retrieval is
plumbed and how it behaves; it is not a claim that memory made the agent better.
That claim lives on the Outcome tier, and today it is `—` too.

`tokens (est)` is the server's `len(text)//4`, not a tokenizer count. The label
travels with the number.

### 2:45 — "What do you remember?"

Screen: **pi TUI** — ask in plain language; the model calls `engram_explain`.
Terminal equivalent:

```bash
$ python -m engram explain
Store: sqlite. Promoted 6, held 0, refuted 1, dormant 0.

About you:
## USER

Standing notes:
## MEMORY
```

**The two sections are empty today** — see the note at 1:30. If you want a
provenance list on screen at this beat, read it from the trace instead, which
does have the classes:

```bash
$ python -m engram consolidate | jq -r '.digest'
$ jq -rc 'select(.op=="remember") | .tagged[]' out/trace.jsonl | head
```

Do not promise a provenance list you cannot show.

---

## What could not be rehearsed on this machine, and how it was covered

| Beat | Rehearsed? | How |
|---|---|---|
| Hooks fire, memory reaches the model's context | **yes, live** | pi 0.85.1 with the real extension against a scripted OpenAI-compatible server (`python -m bench.probe_thinking --serve --echo`), which answers each turn with the memory Engram injected. Confirmed: `session_start → consolidate`, `before_agent_start → recall`, `agent_end → remember` ×2 + `feedback`, and `memory: 1 claim(s) injected · first → - [said] I am building this project…` |
| A real model's answers | **no** | LM Studio is not installed here and `ANTHROPIC_API_KEY` is not set. The echo server proves the plumbing, not the answers. Rehearse the wording once on the demo machine with the real model. |
| Accuracy / LongMemEval numbers | **no** | Judge needs a key. Everything prints `—` and `judge: not run`, by design. |
| Consolidate, report, explain, refute, component bench, A-3 | **yes, live** | Terminal beats above, output copied verbatim. |

The echo server is a rehearsal harness, not a model: `bench/judge.py` refuses to
score any record whose answerer is not a model, so no number from it can ever
reach the scorecard.

---

## Defects found while rehearsing (for lane A / lane B, not fixed here)

> **Update 19:15, lane A:** #3 and #4 fixed (constraints route to the always tier and are exported; the server tags one claim per sentence and only the human's turns). #2 is by design: held claims are not injected, the clarifying question is the visible behaviour. Live rehearsal with `openai/gpt-4.1-mini` passed all seven beats; see `pi-extension/HOOKS.md`.

1. ~~**`pi-extension/src/index.ts:64`** — when the Engram server exits, the
   handler calls `ctx.ui.notify` on a context pi has already torn down, and pi
   dies with `ExtensionRunner.assertActive`. Every headless run exits
   non-zero.~~ **Fixed by lane A** (`unref()` on the server child and its
   pipes, `pi-extension/src/index.ts:26`). Re-checked on the merged tree with
   the echo server: two headless sessions, both `exit=0`, stderr empty, and
   session 2 still receives the claim session 1 wrote —
   `memory: 1 claim(s) injected · first → - [said] We deploy to Vercel. …`.
2. **Held claims are unreachable.** The absence claim never becomes retrievable,
   so the correction beat injects nothing. If that is intended, the demo should
   not imply the model sees what it is correcting.
3. **`export_markdown()` renders nothing** — no claim on this journey is tier
   `always`, so `engram explain`'s two sections are empty.
4. **The turn-level tagger merges two facts into one claim** (0:00 note above),
   and a later refutation takes both down.

---

## Rehearsal timings

Two full passes of `bench/rehearse.sh` — the scripted equivalent of the seven
beats, with the echo model standing in for LM Studio.

| Pass | Beats 0:00–1:30 (5 pi turns) | 1:45 component bench | 2:10 report | 2:45 explain | Total machine time |
|---|---|---|---|---|---|
| 1 | 1s | 1s | <1s | <1s | **2s** |
| 2 | 1s | 1s | <1s | <1s | **2s** |

The three-minute budget is entirely narration; the machine is never the
bottleneck. Two caveats the timings do not show:

- **A real model changes this.** A 4B local model on LM Studio answers in
  seconds, not milliseconds. Budget ~10–15s per pi turn and rehearse the five
  turns on the demo machine before trusting the 3:00.
- **Do not pipe `pi --mode json` into `jq` through a single-threaded stub.** An
  early rehearsal stalled 60s per turn because the fake model server was
  single-threaded and pi opens more than one connection. `bench/probe_thinking`
  is threaded now; with a real LM Studio this cannot happen.

Both passes were run with the store wiped (`rm -rf out`) first. Reproduce with:

```bash
python -m bench.probe_thinking --serve --port 1234 --echo &   # stand-in model
./bench/rehearse.sh
```

`PI_CODING_AGENT_DIR` is pi's own documented override
(`docs/environment-variables.md`). Every rehearsal on this machine used it, so
nothing here ever wrote to `~/.pi/agent`.
