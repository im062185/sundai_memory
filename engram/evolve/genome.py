# 세대마다 변이하고 선택되는 게놈 네 파일을 읽고 쓰고 비교하는 모듈
"""The genome: four files that decide how memory is stored and retrieved.

    weights.json         numbers the retrieval and promotion path reads
    encoder.lessons.md   what the encoder learned about its own usefulness
    persona.md           who the user is, input to the persona filter
    tiers.json           kind -> tier, mirroring engram/p3/policy.json

Code is never part of the genome, and neither are the gate rules G1-G9 or any
safety rule (AMD-03 §4). `WEIGHT_KEYS` is the whole mutable surface; anything
outside it is rejected by `validate()`.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "engram" / "p3" / "policy.json"

# The mutable surface. Nothing else may appear in weights.json.
WEIGHT_KEYS = (
    "decay_lambda",
    "fts_activation_blend",
    "k",
    "recall_token_budget",
    "promotion_threshold",
    "dormancy_threshold",
    "salience_keyword_weights",
)

# Numeric keys a mutation may nudge. `salience_keyword_weights` is a dict and is
# mutated entry-wise instead.
NUMERIC_KEYS = (
    "decay_lambda",
    "fts_activation_blend",
    "k",
    "recall_token_budget",
    "promotion_threshold",
    "dormancy_threshold",
)

DEFAULT_WEIGHTS: dict[str, Any] = {
    "decay_lambda": 0.05,
    "fts_activation_blend": 0.6,       # PLAN §3: 0.6 similarity + 0.4 activation
    "k": 5,
    "recall_token_budget": 400,        # matches ENGRAM_RECALL_TOKENS default
    "promotion_threshold": 0.5,
    "dormancy_threshold": 0.1,         # PLAN §3: dormant below 0.1
    "salience_keyword_weights": {
        "never": 1.5,
        "always": 1.5,
        "actually": 1.3,
        "no longer": 1.3,
        "forget that": 1.4,
        "prefer": 1.1,
    },
}

FALLBACK_TIERS = {
    "profile": "always",
    "preference": "always",
    "feedback": "always",
    "procedure": "procedural",
    "fact": "semantic",
    "absence": "semantic",
    "decision": "semantic",
    "capability": "semantic",
    "constraint": "semantic",
    "episode": "episodic",
}

DEFAULT_LESSONS = """# Encoder lessons

One line per (kind, origin), appended by evolve after each generation:
`kind X from origin Y: stored N, retrieved M, used U`. The encoder gets pickier
where it was wasteful and more generous where it missed things.
"""

DEFAULT_PERSONA = """# Persona

Seed template. Rewritten by evolve from profile and preference claims.

- Builds and ships quickly; prefers short, concrete answers.
- Wants corrections to stick the first time.
"""


def default_tiers() -> dict[str, str]:
    """Seed tiers.json from the router's own policy so the two cannot drift apart."""
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        tiers = policy.get("kind_to_tier")
        if isinstance(tiers, dict) and tiers:
            return dict(tiers)
    except (OSError, json.JSONDecodeError):
        pass
    return dict(FALLBACK_TIERS)


@dataclass
class Genome:
    weights: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_WEIGHTS)))
    lessons: str = DEFAULT_LESSONS
    persona: str = DEFAULT_PERSONA
    tiers: dict[str, str] = field(default_factory=default_tiers)

    def copy(self) -> "Genome":
        return Genome(
            weights=json.loads(json.dumps(self.weights)),
            lessons=self.lessons,
            persona=self.persona,
            tiers=dict(self.tiers),
        )


def validate(genome: Genome) -> None:
    """Gate and safety rules are not genome. Anything unexpected is a bug, not a mutation."""
    extra = set(genome.weights) - set(WEIGHT_KEYS)
    if extra:
        raise ValueError(f"weights.json may not carry {sorted(extra)}; the genome is not the gate")
    if genome.weights.get("k", 1) < 1:
        raise ValueError("k must stay >= 1")


def save(genome: Genome, directory: pathlib.Path) -> None:
    validate(genome)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "weights.json").write_text(json.dumps(genome.weights, indent=2) + "\n", encoding="utf-8")
    (directory / "encoder.lessons.md").write_text(genome.lessons, encoding="utf-8")
    (directory / "persona.md").write_text(genome.persona, encoding="utf-8")
    (directory / "tiers.json").write_text(json.dumps(genome.tiers, indent=2) + "\n", encoding="utf-8")


def load(directory: pathlib.Path) -> Genome:
    g = Genome()
    wpath = directory / "weights.json"
    if wpath.exists():
        g.weights = json.loads(wpath.read_text(encoding="utf-8"))
    lpath = directory / "encoder.lessons.md"
    if lpath.exists():
        g.lessons = lpath.read_text(encoding="utf-8")
    ppath = directory / "persona.md"
    if ppath.exists():
        g.persona = ppath.read_text(encoding="utf-8")
    tpath = directory / "tiers.json"
    if tpath.exists():
        g.tiers = json.loads(tpath.read_text(encoding="utf-8"))
    return g


def generations(generations_dir: pathlib.Path) -> list[pathlib.Path]:
    if not generations_dir.exists():
        return []
    return sorted(p for p in generations_dir.glob("gen-*") if p.is_dir())


def ensure_seed(generations_dir: pathlib.Path) -> pathlib.Path:
    """gen-000 from defaults when there is no history yet."""
    existing = generations(generations_dir)
    if existing:
        return existing[-1]
    seed = generations_dir / "gen-000"
    save(Genome(), seed)
    return seed


def diff(parent: Genome, child: Genome) -> str:
    """One readable line per change. A generation you cannot read is one you cannot revert."""
    lines: list[str] = []
    for key in WEIGHT_KEYS:
        before, after = parent.weights.get(key), child.weights.get(key)
        if before != after:
            lines.append(f"- weights.{key}: {before} -> {after}")
    for kind in sorted(set(parent.tiers) | set(child.tiers)):
        before, after = parent.tiers.get(kind), child.tiers.get(kind)
        if before != after:
            lines.append(f"- tiers.{kind}: {before} -> {after}")
    if parent.lessons != child.lessons:
        added = [ln for ln in child.lessons.splitlines() if ln not in parent.lessons.splitlines()]
        for ln in added:
            lines.append(f"- lessons += {ln.strip()}")
    if parent.persona != child.persona:
        lines.append("- persona.md rewritten")
    return "\n".join(lines) or "- no change"
