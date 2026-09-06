from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
import math

from .base import Claim, RecallHit, Store, StoreStats
from .markdown import render_memory_md, render_user_md, USER_CHAR_LIMIT, MEMORY_CHAR_LIMIT


CREATE_MEMORIES = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    source_class TEXT NOT NULL,
    subject TEXT NOT NULL,
    polarity TEXT NOT NULL,
    support_set TEXT NOT NULL,
    refutation_trigger TEXT,
    user_reaction TEXT,
    importance REAL NOT NULL,
    tier TEXT NOT NULL,
    status TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    supersedes TEXT,
    session TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_accessed TEXT,
    access_count INTEGER NOT NULL,
    activation REAL NOT NULL,
    verbatim INTEGER NOT NULL
)
"""

CREATE_LINKS = """
CREATE TABLE IF NOT EXISTS links (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (src, dst, kind)
)
"""

CREATE_EPISODES = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    thinking TEXT,
    ts TEXT NOT NULL
)
"""

CREATE_RETRIEVAL = """
CREATE TABLE IF NOT EXISTS retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    text TEXT NOT NULL,
    result_ids TEXT NOT NULL,
    scores TEXT NOT NULL,
    ts TEXT NOT NULL
)
"""

CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id, text)
"""


class SQLiteStore(Store):
    name = "sqlite"

    def __init__(self, path: str = "out/engram.sqlite") -> None:
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._path = path
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        for sql in (CREATE_MEMORIES, CREATE_LINKS, CREATE_EPISODES, CREATE_RETRIEVAL, CREATE_FTS):
            cur.execute(sql)
        self._conn.commit()

    def _normalize_claim(self, claim: Claim) -> Claim:
        c = dict(claim)
        now = datetime.now(timezone.utc).isoformat()
        c.setdefault("support_set", [])
        c.setdefault("refutation_trigger", None)
        c.setdefault("user_reaction", None)
        c.setdefault("importance", 3)
        c.setdefault("tier", "semantic")
        c.setdefault("status", "held")
        c.setdefault("valid_from", now)
        c.setdefault("valid_to", None)
        c.setdefault("supersedes", None)
        c.setdefault("session", "default")
        c.setdefault("turn_index", 0)
        c.setdefault("last_accessed", None)
        c.setdefault("access_count", 0)
        c.setdefault("activation", 1.0)
        c.setdefault("created_at", now)
        c.setdefault("verbatim", False)
        return c

    def _row_to_claim(self, row: sqlite3.Row) -> Claim:
        return {
            "id": row["id"],
            "text": row["text"],
            "kind": row["kind"],
            "origin": row["origin"],
            "source_class": row["source_class"],
            "subject": row["subject"],
            "polarity": row["polarity"],
            "support_set": json.loads(row["support_set"]),
            "refutation_trigger": row["refutation_trigger"],
            "user_reaction": row["user_reaction"],
            "importance": row["importance"],
            "tier": row["tier"],
            "status": row["status"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "supersedes": row["supersedes"],
            "session": row["session"],
            "turn_index": row["turn_index"],
            "created_at": row["created_at"],
            "last_accessed": row["last_accessed"],
            "access_count": row["access_count"],
            "activation": row["activation"],
            "verbatim": bool(row["verbatim"]),
        }

    def write(self, claim: Claim) -> None:
        claim = self._normalize_claim(claim)
        self._conn.execute(
            """
            INSERT OR REPLACE INTO memories (
                id, text, kind, origin, source_class, subject, polarity,
                support_set, refutation_trigger, user_reaction, importance,
                tier, status, valid_from, valid_to, supersedes, session,
                turn_index, created_at, last_accessed, access_count,
                activation, verbatim
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim["id"],
                claim["text"],
                claim["kind"],
                claim["origin"],
                claim["source_class"],
                claim["subject"],
                claim["polarity"],
                json.dumps(claim["support_set"]),
                claim["refutation_trigger"],
                claim["user_reaction"],
                claim["importance"],
                claim["tier"],
                claim["status"],
                claim["valid_from"],
                claim["valid_to"],
                claim["supersedes"],
                claim["session"],
                claim["turn_index"],
                claim["created_at"],
                claim["last_accessed"],
                claim["access_count"],
                claim["activation"],
                int(bool(claim["verbatim"])),
            ),
        )
        self._conn.execute("INSERT INTO memories_fts (id, text) VALUES (?, ?)", (claim["id"], claim["text"]))
        self._conn.commit()

    def query(self, text: str, k: int = 5, *, session: str | None = None) -> list[RecallHit]:
        if not text:
            return []
        cur = self._conn.cursor()
        # Keep it permissive: if FTS parse fails, return deterministic empty list.
        try:
            fts_rows = cur.execute(
                """
                SELECT id, bm25(memories_fts) AS fts
                FROM memories_fts
                WHERE memories_fts MATCH ?
                ORDER BY fts
                LIMIT 100
                """,
                (text,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        claims: list[RecallHit] = []
        ids = [row["id"] for row in fts_rows]
        for row in fts_rows:
            claim_rows = cur.execute("SELECT * FROM memories WHERE id = ?", (row["id"],)).fetchone()
            if not claim_rows:
                continue
            claim = self._row_to_claim(claim_rows)
            if claim.get("status") != "promoted":
                continue
            if session is not None and claim.get("session") != session:
                pass
            activation = float(claim.get("activation", 1.0) or 1.0)
            fts_score = float(row["fts"]) if row["fts"] is not None else 0.0
            base = 1.0 / (1.0 + abs(fts_score))
            score = base * activation
            score += self._link_boost(claim["id"]) * 0.2
            claim_id = claim["id"]
            claims.append(
                RecallHit(
                    claim=claim,
                    score=float(score),
                    store=self.name,
                    why=f"fts:{base:.3f} act:{activation:.3f} link:{score-base:.3f}",
                )
            )

        claims.sort(key=lambda hit: hit.score, reverse=True)
        top = claims[:k]
        if session is not None:
            return top
        return top

    def _link_boost(self, claim_id: str) -> float:
        cur = self._conn.cursor()
        row = cur.execute("SELECT COALESCE(SUM(ABS(weight)), 0) AS s FROM links WHERE src = ? OR dst = ?", (claim_id, claim_id)).fetchone()
        return float(row["s"]) if row else 0.0

    def refute(self, claim_id: str, *, by: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE memories SET status='refuted', valid_to=? WHERE id=?",
            (now, claim_id),
        )
        self._conn.commit()


    # ---- activation decay (AMD-03 §3) — integrator addition, called by the DAG's expire node ----
    IMMUNE_KINDS = {"procedure", "feedback", "profile"}   # core/procedural memories never fade

    def decay(self, *, decay_lambda: float = 0.05, dormancy_threshold: float = 0.1, now: datetime | None = None) -> int:
        """Recompute activation from age, use and importance; mark dormant below threshold.

        activation = importance/5 · e^(−λ·days_since_last_access) + 0.2·ln(1 + access_count)
        Half-life at λ=0.05 ≈ 14 days for an untouched memory. Dormant claims stay in the
        table (never deleted) and are revived automatically when their activation recovers
        (e.g. importance raised or touched via deep search). Returns the number newly dormant.
        """
        now = now or datetime.now(timezone.utc)
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT id, kind, importance, access_count, last_accessed, created_at, status FROM memories WHERE status IN ('promoted', 'dormant')"
        ).fetchall()
        newly_dormant = 0
        for row in rows:
            ref = row["last_accessed"] or row["created_at"]
            try:
                then = datetime.fromisoformat(str(ref).replace("Z", "+00:00"))
                if then.tzinfo is None:
                    then = then.replace(tzinfo=timezone.utc)
                days = max(0.0, (now - then).total_seconds() / 86400.0)
            except (TypeError, ValueError):
                days = 0.0
            importance = float(row["importance"] or 3) / 5.0
            access = int(row["access_count"] or 0)
            activation = importance * math.exp(-decay_lambda * days) + 0.2 * math.log1p(access)
            if row["kind"] in self.IMMUNE_KINDS:
                activation = max(activation, 1.0)
            status = row["status"]
            if activation < dormancy_threshold and status == "promoted":
                status = "dormant"
                newly_dormant += 1
            elif activation >= dormancy_threshold and status == "dormant":
                status = "promoted"
            cur.execute("UPDATE memories SET activation = ?, status = ? WHERE id = ?", (round(activation, 4), status, row["id"]))
        self._conn.commit()
        return newly_dormant

    def touch(self, claim_ids: Any) -> None:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        for claim_id in claim_ids:
            cur.execute(
                "UPDATE memories SET access_count = COALESCE(access_count, 0) + 1, last_accessed = ? WHERE id = ?",
                (now, claim_id),
            )
        self._conn.commit()

    def link(self, src: str, dst: str, kind: str, weight_delta: float = 1.0) -> None:
        cur = self._conn.cursor()
        cur.execute("SELECT weight FROM links WHERE src=? AND dst=? AND kind=?", (src, dst, kind))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE links SET weight = weight + ? WHERE src=? AND dst=? AND kind=?",
                (weight_delta, src, dst, kind),
            )
        else:
            cur.execute(
                "INSERT INTO links (src, dst, kind, weight) VALUES (?, ?, ?, ?)",
                (src, dst, kind, weight_delta),
            )
        self._conn.commit()

    def by_subject(self, subject: str) -> list[Claim]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE subject = ? ORDER BY created_at ASC",
            (subject,),
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    def stats(self) -> StoreStats:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as n FROM memories GROUP BY status"
        ).fetchall()
        counts = defaultdict(int)
        for row in rows:
            counts[row["status"]] += int(row["n"])
        return StoreStats(
            name=self.name,
            held=counts.get("held", 0),
            promoted=counts.get("promoted", 0),
            refuted=counts.get("refuted", 0),
            dormant=counts.get("dormant", 0),
            extra={
                "total": sum(counts.values()),
            },
        )

    def export_markdown(self) -> dict[str, str]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM memories WHERE status = 'promoted'"
        ).fetchall()
        user_claims = [self._row_to_claim(r) for r in rows if r["tier"] == "always" and r["kind"] in {"profile", "preference"}]
        memory_claims = [self._row_to_claim(r) for r in rows if r["tier"] == "always" and r["kind"] in {"feedback", "procedure"}]
        return {
            "USER.md": render_user_md(user_claims, cap=USER_CHAR_LIMIT),
            "MEMORY.md": render_memory_md(memory_claims, cap=MEMORY_CHAR_LIMIT),
        }
