# OMAR_READ.md — lane D's review of `a307b92` (activation decay)

Written 2026-09-06 by lane D (measurement / report / demo) against
`a307b92 lane-a: activation decay in the sqlite store; expire node reads
lambda/threshold from the latest genome`.

Everything below was **run**, not read. Repro commands are inline. Nothing in
this document changes your code — lane D reports, it does not patch other
lanes' files.

---

## 1. The wiring is real — I checked the whole chain

This is the part that is usually half-connected, so I traced it end to end:

| Link | Verified |
|---|---|
| `decay_lambda`, `dormancy_threshold` are tunable genome | both present in `engram/evolve/genome.py:DEFAULT_WEIGHTS` |
| the mutator can actually move them | both in `mutate.py`'s numeric ranges — `decay_lambda (0.001, 1.0)`, `dormancy_threshold (0.01, 0.9)` |
| `expire` reads the file that is really written | `nodes.py` reads `gen-*/weights.json`; `genome.save()` writes exactly that name |
| the immune kinds exist | `procedure`, `feedback`, `profile` are all in the **frozen** `schema/claim.schema.json` `kind` enum — not dead code |
| the stated half-life is right | `ln2 / 0.05` = 13.86 days, as the docstring claims |

So the evolve loop can genuinely tune decay. Good.

---

## 2. The finding: **one access makes a claim immortal**

```python
activation = importance/5 · e^(−λ·days) + 0.2·ln(1 + access_count)
```

The use term has **no time component**. It is a permanent floor, not a
reinforcement that itself fades. And the floor clears the default threshold
immediately:

```
access= 0  floor=0.0000  can go dormant
access= 1  floor=0.1386  IMMORTAL      ← 0.2·ln(2) > 0.1
access= 2  floor=0.2197  IMMORTAL
access=10  floor=0.4796  IMMORTAL
```

Empirically, on a real `SQLiteStore`, two identical claims, one touched a
single time, then **five years** with no use:

```
after 5 years unused: newly_dormant=1 promoted=1 dormant=1
  clm_untouched      status=dormant   activation=0.0      access_count=0
  clm_touched_once   status=promoted  activation=0.1386   access_count=1
```

**Why this matters more than it looks:** `engram/server.py:69` calls
`store.touch()` on **every recall**. So any claim the memory has ever returned
— once — can never fade again. Decay can only ever retire claims that were
never recalled at all. AMD-03 §3's "fades unless reinforced" becomes "ever
used, therefore permanent."

Time to dormancy with **no** access, for reference: 13.9 days at importance 1,
35.8 at importance 3, 46.1 at importance 5. With one access: never.

### Why the tests pass

`tests/test_decay.py` is not wrong, it just never tests the boundary. Both
liveness tests touch **20×** and **5×**:

```python
s.touch(["clm_usedfact1"] * 20)
s.touch(["clm_reviveme1"] * 5)
```

and they assert the intended *direction* — use keeps memories alive — which is
true. What is untested is that the effect is **permanent and unbounded**. A
test with `touch([...] * 1)` and a far-future `now` fails today.

### Repro

```bash
python - <<'PY'
import tempfile, pathlib
from datetime import datetime, timezone, timedelta
from engram.adapters.sqlite import SQLiteStore
d = pathlib.Path(tempfile.mkdtemp()); s = SQLiteStore(d/"engram.db")
now = datetime.now(timezone.utc).isoformat()
base = {"kind":"fact","status":"promoted","importance":3,"provenance":"said",
        "origin":"user","source_class":"user_stated","subject":"x","polarity":"affirm",
        "text":"the vendor SDK ships batch writes","created_at":now,"valid_from":now,
        "session_id":"s1","turn_id":"t1","confidence":0.9,"tier":"context"}
for cid in ("clm_untouched","clm_touched_once"): s.write({**base,"id":cid})
s.touch(["clm_touched_once"])
far = datetime.now(timezone.utc)+timedelta(days=365*5)
print("newly dormant:", s.decay(now=far))
for cid in ("clm_untouched","clm_touched_once"):
    r = s._conn.execute("SELECT id,status,activation,access_count FROM memories WHERE id=?",(cid,)).fetchone()
    print(f"  {r['id']:18s} status={r['status']:9s} activation={r['activation']:<8} access={r['access_count']}")
PY
```

### The shape of a fix — your call, your file

Let reinforcement raise the **ceiling** rather than set a **floor**, so time
still pulls everything down:

```python
activation = (importance / 5.0 + 0.2 * math.log1p(access)) * math.exp(-decay_lambda * days)
```

A heavily-used claim then survives far longer than an unused one, which is the
intent, but nothing is permanent. The immune-kinds clause still does the
"never fades" job for the things that genuinely should not fade, and it does it
explicitly. Whatever you pick, `engram/adapters/sqlite.py` is lane A/B's file —
I have not touched it.

---

## 3. Smaller: the isolation guard no longer covers every status write

`tests/test_isolation.py` enforces "only p2 writes" with a regex:

```python
ALLOWED_WRITE = {"p2", "consolidate", "server.py"}
pat = re.compile(r"\.(write|refute)\(")
```

`decay()` is a **third** status-mutating operation — it flips
`promoted ↔ dormant` with raw SQL inside the adapter — and that regex cannot
see it. This is **not a violation today**: it is called from
`consolidate/nodes.py`, which is on the allow-list. But the guard now detects a
subset of the transitions it exists to make impossible, and the revive branch
(`dormant → promoted`) is a promotion that does not pass the gate.

Worth either adding `decay` to the pattern, or noting in the test why it is
deliberately exempt. A guard that silently covers less than its docstring
claims is worse than one that is explicitly narrow.

---

## 4. Demo and measurement impact: **none**

Checked, so nobody has to guess on stage:

- `python -m engram consolidate` on the demo journey still prints
  `Memory census: promoted 3, held 0, refuted 0, dormant 0`, with
  `"expired": 0`. Demo claims are seconds old; nothing fades mid-demo.
- `python -m bench.component` on a clean `out/` is unchanged: sqlite precision
  0.33 / recall 0.67, vector 0.12 / 1.00, and A-3 still reproduces on vector
  (`returned again: True, stats.refuted=0`).
- Full suite: **107 passed, 1 xpassed**. The xpass is
  `test_vector_refute_keeps_returning` — A-3, present and deliberately unfixed
  per TDD §9.

`docs/DEMO.md` needs no edit for this change.

---

## 5. One thing still waiting on you

`lane/d-setup` (PR #2, docs only) records a setup defect found by cloning
`build/engram-v2` fresh: **skipping `npm ci` in `pi-extension/` is not an
error, it is a 60-second hang** — `index.ts` imports `@sinclair/typebox`, pi's
TS loader cannot resolve it, and the run stalls with empty stdout, empty
stderr, no `out/` directory and no exit. After `npm ci` the identical command
exits 0. Take it or leave it, but the symptom names nothing, so someone will
lose ten minutes to it otherwise.
