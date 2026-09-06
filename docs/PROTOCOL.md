# Engram stdio protocol — FROZEN CONTRACT (TDD §5.3 + AMD-03)

One JSON object per line on stdin → one per line on stdout, same order. Every response has `ok` and `ms`.
Start: `python -m engram --serve` (spawned by the pi extension). CLI: `python -m engram consolidate|report|explain`.

| op | request | response |
|---|---|---|
| `recall` | `{"op":"recall","text":"…","k":5,"session":"s1"}` | `{"ok":true,"claims":[{"id","text","source_class","store","score","kind"}],"tokens":312,"ms":41}` |
| `remember` | `{"op":"remember","turn":{"role":"user"\|"assistant","text":"…","thinking":null},"session":"s1","turn_index":3}` | `{"ok":true,"episode_id":"ep_…","tagged":["clm_…"],"gate":{"clm_…":{"verdict":"promoted"\|"held"\|"rejected","fired":["G3"]}},"refuted":["clm_…"],"question":"…or null","ms":57}` |
| `consolidate` | `{"op":"consolidate","messages":[…AgentMessage…],"reason":"threshold"\|"manual"\|"start"\|"cli"\|"micro"}` | `{"ok":true,"digest":"…provenance-tagged…","counts":{"expired":0,"captured":0,"encoded":0,"merged":0,"adjudicated":0,"promoted":0,"wired":0,"reindexed":true,"generation":4},"ms":…}` |
| `feedback` | `{"op":"feedback","session":"s1","turn_index":3,"injected":["clm_…"],"reply":"…assistant text…"}` | `{"ok":true,"used":["clm_…"],"ms":…}` — logs retrieval usage for evolve |
| `explain` | `{"op":"explain"}` | `{"ok":true,"text":"You said … / I inferred … / Verified: …","ms":…}` |
| `report` | `{"op":"report"}` | `{"ok":true,"text":"speed … · accuracy … · tokens … · precision … · recall …\n…","ms":…}` |
| `refute` | `{"op":"refute","claim_id":"clm_…"}` | `{"ok":true,"ms":…}` |

Errors: `{"ok":false,"error":"…","ms":…}`. The extension prints a visible line and continues without memory.
Trace: every `remember` and `recall` appends one record to `out/trace.jsonl`; every `recall` appends to `out/retrieval_log.jsonl`.
