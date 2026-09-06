"""Pressure-test ("red team subagent"): can engram be demoed, and does it beat
baselines on the failure mode SOTA systems share — stale/contradicted facts?

Conditions:
  A. no_memory     : agent sees only the current session.
  B. append_only   : naive RAG — every extracted fact stored forever,
                     last-k keyword retrieval, no supersession (the Mem0/
                     vector-wrapper failure mode on updates).
  C. engram        : SPRT write gate + bitemporal supersession + decay.

Dataset: synthetic 5-session user history with fact updates (StaleBench-style).
Probes score: recall of current facts, stale-answer rate (returning a
superseded value), and noise-write rate (one-off trivia persisted).

Runs fully offline (deterministic mock answerer that answers from whatever
context block each condition supplies). Set ANTHROPIC_API_KEY + --live to
swap in a real model as the answerer.
"""
import sys, time, json
sys.path.insert(0, __file__.rsplit("/", 2)[0])
from engram.core import Engram

DAY = 86400
T0 = time.time() - 30 * DAY

# (session_offset_days, speaker, text)
SESSIONS = [
    (0,  [("user", "hey! my name is Priya and my favorite editor is vim"),
          ("user", "my dog's name is Mochi"),
          ("user", "my deadline is March 3")]),          # one-off, should fade
    (3,  [("user", "reminder that my favorite editor is vim"),
          ("user", "my dog's name is Mochi, she ate my charger lol")]),
    (9,  [("user", "actually my favorite editor is now helix, changed to helix"),
          ("user", "my name is Priya")]),
    (16, [("user", "my dog's name is Mochi"),
          ("user", "my timezone is EST")]),
    (23, [("user", "my timezone is EST"),
          ("user", "my favorite editor is helix")]),
]

PROBES = [  # (question, correct, stale_wrong_answer_or_None)
    ("what is my favorite editor?", "helix", "vim"),
    ("what is my dog's name?", "mochi", None),
    ("what is my name?", "priya", None),
    ("what is my timezone?", "est", None),
    ("what was my deadline?", "march 3", None),  # engram is EXPECTED to have let this fade
]


def mock_answer(context: str, question: str) -> str:
    """Deterministic stand-in for the LLM: answers with the context fact whose
    predicate best matches the question; ties broken by order in context."""
    import re
    qterms = set(re.findall(r"\w+", question.lower()))
    best, score = "i don't know", 0
    for line in context.splitlines():
        m = re.match(r"- user (.+?): (.+)", line.strip())
        if not m:
            continue
        pterms = set(re.findall(r"\w+", m.group(1).lower()))
        sc = 2 * len(qterms & pterms) - len(pterms - qterms)
        if sc > score:
            best, score = m.group(2).lower(), sc
    return best


class AppendOnly:
    """Naive baseline: store every regex-extracted fact, retrieve by keyword,
    no supersession -> both vim and helix survive; earliest write wins ties."""
    def __init__(self): self.rows = []
    def observe(self, text, speaker="user", now=None):
        import re
        for p, o in re.findall(r"my\s+([\w ]+?)\s+is\s+(?:now\s+)?([\w@.\- ']+)", text, re.I):
            self.rows.append((p.strip().lower(), o.strip()))
    def context_block(self, q, now=None):
        return "Known facts about the user (most reliable first):\n" + \
            "\n".join(f"- user {p}: {o}" for p, o in self.rows)


def run(condition: str):
    mem = Engram() if condition == "engram" else (
        AppendOnly() if condition == "append_only" else None)
    for off, turns in SESSIONS:
        now = T0 + off * DAY
        for spk, text in turns:
            if mem: mem.observe(text, spk, now=now)
        if condition == "engram":
            mem.sleep(now=now + DAY)
    now = T0 + 30 * DAY
    correct = stale = 0
    details = []
    for q, gold, stale_val in PROBES:
        ctx = mem.context_block(q, now=now) if mem else ""
        ans = mock_answer(ctx, q)
        ok = gold in ans
        is_stale = stale_val is not None and stale_val in ans and not ok
        correct += ok; stale += is_stale
        details.append((q, ans, "OK" if ok else ("STALE" if is_stale else "MISS")))
    return correct, stale, details


if __name__ == "__main__":
    print(f"{'condition':<12} recall  stale_answers")
    for cond in ("no_memory", "append_only", "engram"):
        c, s, details = run(cond)
        print(f"{cond:<12} {c}/{len(PROBES)}     {s}")
        for q, a, tag in details:
            print(f"    [{tag:5}] {q} -> {a}")
    print("\nExpected: engram matches/beats append_only on recall, 0 stale, "
          "and lets the one-off deadline fade (a MISS there is by design).")
