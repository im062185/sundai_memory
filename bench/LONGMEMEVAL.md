# LongMemEval — the release layout, as read (TDD §4 A-5)

A-5 says: *read the dataset repo README; **do not assume variant names***.
Everything below was read on **2026-09-06** from the sources named, then
**verified against the downloaded file itself** — where the README and the
file disagree, the file wins and the disagreement is recorded.

## Sources read

| Source | What it gave |
|---|---|
| `https://github.com/xiaowu0162/LongMemEval` (README) | variant names, download commands, instance field list, question-type list |
| `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned` (dataset card) | "This version removes noisy history sessions that interfere with the answer correctness." Replaces the original release. |
| the downloaded file | everything in "Verified against the file" below |

## Variants distributed

Three, on the HuggingFace repo `xiaowu0162/longmemeval-cleaned`:

| File in the release | Size (Content-Length, checked with `curl -sIL`) |
|---|---|
| `longmemeval_oracle.json` | 15,388,478 bytes |
| `longmemeval_s_cleaned.json` | 277,383,467 bytes |
| `longmemeval_m_cleaned.json` | (not fetched — ~3 GB repo total) |

Download command, from the README verbatim:

```
wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json
wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json
wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_m_cleaned.json
```

Note the naming inconsistency in the release itself: the oracle file is
`longmemeval_oracle.json` (no `_cleaned` suffix), the other two carry it.
Do not "correct" this.

**We use `longmemeval_oracle.json`.** It carries only the evidence sessions,
so a 22-question slice ingests in seconds on a laptop and the memory-off arm
fits a small context. It is the honest choice to *state*, not to hide: the
oracle variant is an easier retrieval setting than `_s`, and every number we
publish from it says `longmemeval_oracle` next to it.

Local copy (gitignored, not committed — 15 MB):

```
bench/data/longmemeval_oracle.json
sha256 821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c
```

## Verified against the file

```
top-level: JSON list, 500 instances
```

Instance fields, in the order they appear in the file:

| Field | Type | Note |
|---|---|---|
| `question_id` | str | e.g. `gpt4_2655b836`; **abstention instances end in `_abs`** |
| `question_type` | str | one of the six below |
| `question` | str | |
| `answer` | str \| **int** | 468 str, **32 int** — the dataset card notes the viewer breaks on this; the judge must stringify |
| `question_date` | str | `YYYY/MM/DD (Day) HH:MM`, e.g. `2023/04/10 (Mon) 23:07` |
| `haystack_dates` | list[str] | same format, one per session, parallel to the two lists below |
| `haystack_session_ids` | list[str] | e.g. `answer_4be1b6b4_2` |
| `haystack_sessions` | list[list[turn]] | |
| `answer_session_ids` | list[str] | the sessions holding the evidence |

The README lists these fields but in a different order and does **not**
mention that `answer` is sometimes an int. Read from the file.

A turn inside `haystack_sessions[i]` has exactly these keys:

```json
{ "role": "user" | "assistant", "content": "…", "has_answer": true }
```

`has_answer` is present only on evidence turns. This is the per-turn
evidence marker we score claim precision/recall against (TDD §6.8 step 4).

Counts in `longmemeval_oracle.json`:

```
sessions per instance: min 1, max 6
turns per instance:    min 2, max 72
```

### The six question types (`question_type`), with counts in the oracle file

| `question_type` | n |
|---|---|
| `single-session-user` | 70 |
| `single-session-assistant` | 56 |
| `single-session-preference` | 30 |
| `multi-session` | 133 |
| `knowledge-update` | 78 |
| `temporal-reasoning` | 133 |

**Abstention is not a seventh `question_type`.** It is an overlay: 30
instances have a `question_id` ending `_abs`, spread across four of the six
types (`multi-session` 12, `temporal-reasoning` 6, `knowledge-update` 6,
`single-session-user` 6). Their gold `answer` is a refusal string, e.g.
*"The information provided is not enough. You mentioned fixing the fence but
did not mention purchasing cows from Peter."* Anything that reports
"abstention" as a category alongside the six is reporting it wrong.

## Our slice

`tests/fixtures/longmemeval_slice.json`, built by
`python -m bench.longmemeval --build-slice`. Selection rule, fixed and
reproducible (rerunning reproduces the file byte for byte):

1. group the 500 instances by `question_type`;
2. within each type, sort by `question_id` (lexicographic — arbitrary but
   deterministic, and **not** correlated with difficulty or length; we
   deliberately do not sort by turn count, which would quietly select the
   easy instances);
3. take the first **3 non-abstention** instances of each of the six types;
4. plus the first **1 abstention** instance of each type that has any (4 do).

= **22 questions, all six categories, abstention represented.** AMD-03 §6
lowered the bar from the TDD's 50 and CUJ S12's 50 to ≥20; 22 is what we
run, and the report prints `n` next to every accuracy number so the slice
size is never in doubt.

## What this costs us against published numbers

Nothing we report is comparable to a published LongMemEval score: different
variant, 22 of 500 questions, our own judge prompt. The report says
`longmemeval_oracle, n=22` and names the judge on every line. It is a
measurement of *this* memory layer over time, not a leaderboard entry.
