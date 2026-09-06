"""engram: write-gated, bitemporal agent memory with decay-based retrieval.

Three processes (mirrors the whiteboard):
  INPUT       -> observe(): raw turns become candidate declarative statements
  PROCESSING  -> SPRT write gate (optimal stopping) + sleep() consolidation
  STORAGE     -> bitemporal SQLite (valid_time vs system_time) + activation scores

Math:
  * Write gate: Wald's Sequential Probability Ratio Test. Each corroborating
    observation of a candidate fact adds log-likelihood ratio evidence toward
    H1 ("stable fact worth persisting") vs H0 ("noise/ephemeral"). Promote when
    Lambda >= log((1-beta)/alpha); reject when Lambda <= log(beta/(1-alpha)).
    This is the optimal stopping rule minimizing expected observations for
    fixed error rates (Wald & Wolfowitz, 1948).
  * Retrieval ranking / forgetting: ACT-R base-level activation
    B_i = ln( sum_j (t_now - t_j)^-d ), d ~= 0.5 (Anderson & Schooler, 1991).
    Frequent + recent access -> high activation; unused traces decay below a
    retrieval threshold (graceful forgetting without deletion).
  * Contradiction: bitemporal supersession. Old fact keeps its record with
    valid_to closed; nothing is destroyed (audit + "as-of" queries survive).
"""
from __future__ import annotations
import json, math, re, sqlite3, time
from dataclasses import dataclass

# --- SPRT write gate -------------------------------------------------------
ALPHA, BETA = 0.05, 0.10                # false-promote / false-reject rates
P1, P0 = 0.8, 0.3                       # P(corroboration | stable) vs | noise
LLR_HIT = math.log(P1 / P0)             # evidence per corroborating mention
LLR_MISS = math.log((1 - P1) / (1 - P0))
PROMOTE = math.log((1 - BETA) / ALPHA)  # upper stopping boundary
REJECT = math.log(BETA / (1 - ALPHA))   # lower stopping boundary
# High-salience cues (user self-report, explicit correction) get a prior boost:
SALIENCE_BOOST = {"correction": PROMOTE, "self_report": LLR_HIT * 2, "plain": 0.0}

DECAY_D = 0.5
# Calibrated so a single unreinforced access fades within ~3 weeks while
# twice-reinforced traces stay retrievable (ages in seconds, d=0.5):
RETRIEVAL_THRESHOLD = -6.9              # activation below this -> "forgotten"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT,
  source TEXT, valid_from REAL, valid_to REAL,        -- valid time (world)
  sys_from REAL, superseded_by INTEGER,               -- system time (db)
  accesses TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS candidates(
  key TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT,
  llr REAL, mentions INTEGER, first_seen REAL, source TEXT, times TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS episodes(
  id INTEGER PRIMARY KEY, text TEXT, speaker TEXT, t REAL,
  accesses TEXT DEFAULT '[]');
"""

_FACT_RE = re.compile(
    r"(?:my|the user's)\s+([\w' ]+?)\s+(?:is|are)\s+(?:now\s+)?"
    r"([\w@.\-']+(?:\s+[\w@.\-']+)*?)"
    r"(?=\s+(?:and|but|so|she|he|it|lol)\b|[,.!?;]|$)", re.I | re.M)
_CORRECTION_RE = re.compile(r"actually|no longer|changed to|correction|now", re.I)


@dataclass
class Fact:
    id: int; subject: str; predicate: str; object: str; activation: float


class Engram:
    def __init__(self, db_path: str = ":memory:"):
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)

    # -------- INPUT: turn -> candidate declarative statements --------------
    def observe(self, text: str, speaker: str = "user", now: float | None = None):
        """Extraction via LLM when ANTHROPIC_API_KEY is set, regex fallback
        otherwise (see extractor.py). Extractor confidence becomes SPRT
        evidence: llr = ln(c/(1-c)); a correction cue promotes immediately."""
        from . import extractor
        now = now or time.time()
        correction = bool(_CORRECTION_RE.search(text))
        for f in extractor.extract(text, speaker):
            c = min(max(float(f.get("confidence", 0.6)), 0.05), 0.95)
            evidence = PROMOTE if correction else math.log(c / (1 - c))
            self._gate(f["subject"], f["predicate"].strip().lower(),
                       str(f["object"]).strip(), evidence, now)

    # -------- PROCESSING II: SPRT write gate -------------------------------
    def _gate(self, subj, pred, obj, evidence, now):
        # Re-mention of an already-held identical fact reinforces the trace
        # (rehearsal), it does not restart evidence accumulation.
        held = self.db.execute(
            "SELECT id FROM facts WHERE subject=? AND predicate=? AND "
            "lower(object)=lower(?) AND valid_to IS NULL",
            (subj, pred, obj)).fetchone()
        if held:
            self._touch(held[0], now); self.db.commit(); return
        key = f"{subj}|{pred}|{obj.lower()}"
        row = self.db.execute(
            "SELECT llr, mentions, times FROM candidates WHERE key=?",
            (key,)).fetchone()
        llr = (row[0] if row else 0.0) + evidence
        mentions = (row[1] if row else 0) + 1
        times = (json.loads(row[2]) if row else []) + [now]
        if llr >= PROMOTE:
            # Rehearsals during candidacy carry over as access history —
            # the graduated trace inherits its hippocampal rehearsal count.
            self._commit(subj, pred, obj, f"sprt:{mentions} mentions", now,
                         times=times)
            self.db.execute("DELETE FROM candidates WHERE key=?", (key,))
        elif llr <= REJECT:
            self.db.execute("DELETE FROM candidates WHERE key=?", (key,))
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?)",
                (key, subj, pred, obj, llr, mentions, now, "gate",
                 json.dumps(times)))
        self.db.commit()

    # -------- STORAGE: bitemporal commit with supersession -----------------
    def _commit(self, subj, pred, obj, source, now, times=None):
        cur = self.db.execute(
            "SELECT id, object FROM facts WHERE subject=? AND predicate=? "
            "AND valid_to IS NULL", (subj, pred))
        for fid, old in cur.fetchall():
            if old.lower() != obj.lower():                  # contradiction
                self.db.execute(
                    "UPDATE facts SET valid_to=? WHERE id=?", (now, fid))
                new_id = self._insert(subj, pred, obj, source, now, times)
                self.db.execute(
                    "UPDATE facts SET superseded_by=? WHERE id=?", (new_id, fid))
                return
            else:                                           # duplicate: reinforce
                self._touch(fid, now); return
        self._insert(subj, pred, obj, source, now, times)

    def _insert(self, subj, pred, obj, source, now, times=None) -> int:
        cur = self.db.execute(
            "INSERT INTO facts(subject,predicate,object,source,valid_from,"
            "valid_to,sys_from,accesses) VALUES(?,?,?,?,?,NULL,?,?)",
            (subj, pred, obj, source, now, now,
             json.dumps((times or [now])[-50:])))
        return cur.lastrowid

    def _touch(self, fid, now):
        acc = json.loads(self.db.execute(
            "SELECT accesses FROM facts WHERE id=?", (fid,)).fetchone()[0])
        acc.append(now)
        self.db.execute("UPDATE facts SET accesses=? WHERE id=?",
                        (json.dumps(acc[-50:]), fid))

    # -------- Retrieval: ACT-R activation over *currently valid* facts -----
    def _activation(self, accesses: list[float], now: float) -> float:
        s = sum(max(now - t, 1.0) ** -DECAY_D for t in accesses)
        return math.log(s) if s > 0 else -math.inf

    def recall(self, query: str, k: int = 5, now: float | None = None) -> list[Fact]:
        now = now or time.time()
        terms = set(re.findall(r"\w+", query.lower()))
        out = []
        for fid, s, p, o, acc in self.db.execute(
                "SELECT id,subject,predicate,object,accesses FROM facts "
                "WHERE valid_to IS NULL"):
            act = self._activation(json.loads(acc), now)
            if act < RETRIEVAL_THRESHOLD:
                continue
            overlap = len(terms & set(re.findall(r"\w+", f"{p} {o}".lower())))
            if overlap:
                out.append((overlap + act, Fact(fid, s, p, o, act)))
        out.sort(key=lambda t: -t[0])
        hits = [f for _, f in out[:k]]
        for f in hits:
            self._touch(f.id, now)      # retrieval strengthens (testing effect)
        self.db.commit()
        return hits

    def context_block(self, query: str, now: float | None = None) -> str:
        hits = self.recall(query, now=now)
        if not hits:
            return ""
        return "Known facts about the user (most reliable first):\n" + "\n".join(
            f"- user {f.predicate}: {f.object}" for f in hits)

    # -------- Episodic layer (CLS fast store): raw traces, decay-ranked ----
    def observe_episode(self, text: str, speaker: str, now: float):
        """Store a raw turn as an episodic trace AND run the fact gate on it.
        Decay affects *ranking* of episodes, not existence: archival QA showed
        hard demotion of old episodes destroys recall; forgetting belongs to
        the semantic fact layer, salience-ranking to the episodic layer."""
        self.db.execute(
            "INSERT INTO episodes(text,speaker,t,accesses) VALUES(?,?,?,?)",
            (text, speaker, now, json.dumps([now])))
        self.observe(text, speaker, now)
        self.db.commit()

    def recall_episodes(self, query: str, k: int = 6, now: float | None = None,
                        thread: str | None = None, neighbors: int = 2,
                        w_act: float = 0.0, w_sem: float = 0.0,
                        semantic_scorer=None,
                        as_of: float | None = None) -> list[str]:
        """Two memory principles drive ranking:
        1. Distinctiveness: query-term match weighted by surprisal,
           w = 1/ln(1+df) — rare (informative) terms dominate (rational
           analysis of memory), not raw overlap counts.
        2. Temporal contiguity (Howard & Kahana's Temporal Context Model):
           the k seed hits reinstate +/-`neighbors` adjacent turns — in
           dialogue, questions and answers live in adjacent turns.
        Calibration on LoCoMo tune split (convs 0-4): k=6, neighbors=2,
        w_act=0 (activation *hurts* independent archival probes, -0.6pts;
        pass thread=<id> to strengthen retrieved traces and w_act>0 only when
        continuing a task thread — the testing effect is thread-local).
        w_sem blends a semantic_scorer (see semantic.py); LSA measured ~0 gain
        held-out, so default 0 until a pretrained encoder drops in.
        as_of filters to episodes with t <= as_of (bitemporal audit)."""
        now = now or time.time()
        terms = set(w for w in re.findall(r"\w+", query.lower()) if len(w) > 2)
        q = "SELECT id,text,speaker,t,accesses FROM episodes"
        args = ()
        if as_of is not None:
            q += " WHERE t<=?"; args = (as_of,)
        rows = self.db.execute(q + " ORDER BY id", args).fetchall()
        if not rows:
            return []
        etoks = [set(w for w in re.findall(r"\w+", f"{r[2]} {r[1]}".lower())
                     if len(w) > 2) for r in rows]
        df = {}
        for ts in etoks:
            for w in ts:
                df[w] = df.get(w, 0) + 1
        sem = semantic_scorer.score(query) if (w_sem and semantic_scorer) else None
        def score(i):
            s = sum(1.0 / math.log(1 + df[w]) for w in terms & etoks[i])
            if w_act:
                s += w_act * self._activation(json.loads(rows[i][4]), now)
            if sem is not None:
                s = (1 - w_sem) * s + w_sem * float(sem[i])
            return s
        seeds = sorted(range(len(rows)), key=lambda i: -score(i))[:k]
        keep = set()
        for i in seeds:
            keep.update(range(max(0, i - neighbors),
                              min(len(rows), i + neighbors + 1)))
        if thread is not None:            # testing effect, thread-local only
            for i in keep:
                acc = json.loads(rows[i][4])[-49:] + [now]
                self.db.execute("UPDATE episodes SET accesses=? WHERE id=?",
                                (json.dumps(acc), rows[i][0]))
            self.db.commit()
        return [f"[{rows[i][2]}] {rows[i][1]}" for i in sorted(keep)]


    def sleep(self, now: float | None = None) -> dict:
        """Offline pass: expire stale candidates, prune forgotten traces to an
        archive flag. Analogue of CLS slow consolidation: episodic candidates
        either graduate (already handled by SPRT) or fade; long-term traces
        below retrieval threshold are demoted, not deleted."""
        now = now or time.time()
        expired = self.db.execute(
            "DELETE FROM candidates WHERE ? - first_seen > 86400*7", (now,)).rowcount
        demoted = 0
        for fid, acc in self.db.execute(
                "SELECT id, accesses FROM facts WHERE valid_to IS NULL"):
            if self._activation(json.loads(acc), now) < RETRIEVAL_THRESHOLD:
                self.db.execute("UPDATE facts SET valid_to=? WHERE id=?", (now, fid))
                demoted += 1
        self.db.commit()
        return {"expired_candidates": expired, "demoted_facts": demoted}

    # -------- Audit: as-of queries survive supersession --------------------
    def as_of(self, t: float) -> list[tuple]:
        return self.db.execute(
            "SELECT subject,predicate,object FROM facts WHERE valid_from<=? "
            "AND (valid_to IS NULL OR valid_to>?)", (t, t)).fetchall()
