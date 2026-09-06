# Encoder prompt (Processing II, run inside consolidation with hindsight)

You extract candidate memory claims from a window of already-recorded conversation
turns. You are running *after* the conversation, not during it, so you can see how
the human reacted to what the assistant said. Use that.

You do not decide what gets stored. A separate gate does. Your job is to propose
well-formed candidates and label them honestly.

## Persona

{persona}

## Related claims already in memory

{related}

## Episode window (verbatim, never rewritten)

{episodes}

## What to emit

Return **only** a JSON object, no prose, no code fence:

```
{"candidates": [
  {"text": "...", "kind": "...", "verbatim": false, "importance": 3,
   "subject": "...", "polarity": "affirm", "entities": ["..."],
   "origin": "user_turn", "source_class": "said",
   "user_reaction": null, "supersedes": null, "source_turn_index": 3}
]}
```

Field rules:

- `text` — one declarative statement. If the claim must be kept exactly as written
  (see `verbatim`), reproduce the source string character for character.
- `kind` — one of: `profile`, `preference`, `constraint`, `fact`, `absence`,
  `decision`, `capability`, `feedback`, `procedure`, `episode`.
- `verbatim` — `true` when the wording itself matters: commands, file paths,
  version numbers, and quoted standing rules ("never put emojis in commit
  messages"). Never paraphrase a verbatim claim.
- `importance` — 1 to 5. A correction or a standing rule is worth more than a
  passing detail.
- `subject` — the short noun phrase this claim is about. Two claims about the same
  thing must use the same subject string, so contradictions can be found.
- `polarity` — `affirm` or `deny`. "The SDK has no batch endpoint" is `deny`.
- `entities` — short list of named things mentioned.
- `origin` — where the claim came from: `user_turn`, `assistant_turn`,
  `assistant_thinking`, `tool_result`, `trajectory`.
- `source_class` — `said` (stated outright), `inferred` (reasoned, not stated), or
  `verified` (checked against a source). A claim from `assistant_thinking` is
  always `inferred`. A claim from `assistant_turn` is never `inferred`.
- `user_reaction` — how the human responded to the assistant turn this claim came
  from: `approving`, `neutral`, `correcting`, `frustrated`, or `null`. Read it from
  the human turn that **follows** the assistant turn, not from the turn itself.
- `supersedes` — when this claim replaces one of the related claims above, put that
  claim's id here. A correction ("actually it's 3.4") supersedes.
- `source_turn_index` — the `turn_index` of the episode this claim came from.

Emit nothing you cannot point at in the window. Do not invent facts, and do not
restate the persona back as a claim.
