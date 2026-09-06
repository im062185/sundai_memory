"""LLM-based fact extraction (P0-1). Falls back to regex when no API key.

Emits (subject, predicate, object, confidence, valid_time) tuples per turn.
Confidence feeds the SPRT gate as the per-observation likelihood ratio
(replacing hand-set salience boosts): llr = ln(conf / (1 - conf)) clipped
to [REJECT-evidence, PROMOTE-evidence] scale.
"""
from __future__ import annotations
import json, os, re, urllib.request

MODEL = "claude-haiku-4-5-20251001"
PROMPT = """Extract durable facts about the speaker from this dialogue turn.
Return ONLY a JSON array (no prose, no fences). Each element:
{{"subject": "<speaker name or 'user'>", "predicate": "<attribute>",
  "object": "<value>", "confidence": <0.0-1.0>,
  "valid_time": "<ISO date if stated, else null>"}}
Durable = preferences, relationships, biographical facts, possessions,
recurring plans. NOT greetings, one-off logistics, or hedged speculation.
Confidence: 0.9+ direct self-report; 0.6-0.8 strong implication; <0.5 skip.
Speaker: {speaker}
Turn: {text}"""


def extract_llm(text: str, speaker: str) -> list[dict] | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = json.dumps({
        "model": MODEL, "max_tokens": 1024,
        "messages": [{"role": "user",
                      "content": PROMPT.format(speaker=speaker, text=text)}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.load(r)["content"][0]["text"]
        out = re.sub(r"^```(json)?|```$", "", out.strip(), flags=re.M)
        facts = json.loads(out)
        return [f for f in facts
                if {"subject", "predicate", "object"} <= f.keys()
                and float(f.get("confidence", 0)) >= 0.5]
    except Exception:
        return None


_FALLBACK_RE = re.compile(
    r"(?:my|the user's)\s+([\w' ]+?)\s+(?:is|are)\s+(?:now\s+)?"
    r"([\w@.\-']+(?:\s+[\w@.\-']+)*?)"
    r"(?=\s+(?:and|but|so|she|he|it|lol)\b|[,.!?;]|$)", re.I | re.M)


def extract(text: str, speaker: str = "user") -> list[dict]:
    facts = extract_llm(text, speaker)
    if facts is not None:
        return facts
    conf = 0.9 if speaker == "user" else 0.6
    return [{"subject": "user", "predicate": p.strip().lower(),
             "object": o.strip(), "confidence": conf, "valid_time": None}
            for p, o in _FALLBACK_RE.findall(text)]
