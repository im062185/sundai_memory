# HOOKS.md — probe evidence (TDD §4 A-1, A-2, A-4, A-6)

Recorded 2026-09-06 16:50 from the installed pi, **not** from memory.

- pi version: `pi --version` → **0.85.1**, package `@earendil-works/pi-coding-agent`
- Source of truth: `$(npm root -g)/@earendil-works/pi-coding-agent/dist/core/extensions/types.d.ts` (1350 lines)
- Extension load paths: `.pi/extensions/*.ts` (project), `~/.pi/agent/extensions/*.ts` (global), `settings.json → extensions[]`, or `pi install <path>`

## A-1 Event names and payloads (verbatim from types.d.ts)

| Hook we use | Event `type` | Payload | Return |
|---|---|---|---|
| session start | `session_start` | `{ reason: "startup"\|"reload"\|"new"\|"resume"\|"fork", previousSessionFile? }` | none |
| before each turn | `before_agent_start` | `{ prompt: string, images?, systemPrompt: string, systemPromptOptions }` | `{ message?: {customType, content, display, details}, systemPrompt?: string }` |
| after each turn | `agent_end` | `{ messages: AgentMessage[] }` (this run's messages) | none |
| per LLM turn | `turn_end` | `{ turnIndex, message: AgentMessage, toolResults: ToolResultMessage[] }` | none |
| compaction | `session_before_compact` | `{ preparation: CompactionPreparation, branchEntries: SessionEntry[], customInstructions?, reason: "manual"\|"threshold"\|"overflow", willRetry, signal }` | `{ cancel?: boolean, compaction?: CompactionResult }` |
| shutdown | `session_shutdown` | `{ reason: "quit"\|"reload"\|"new"\|"resume"\|"fork", targetSessionFile? }` | none |
| raw input | `input` | `{ text, images?, source, streamingBehavior? }` | `InputEventResult` |
| context edit | `context` | `{ messages: AgentMessage[] }` (deep copy) | `{ messages }` |

`CompactionPreparation` = `{ firstKeptEntryId, messagesToSummarize: AgentMessage[], turnPrefixMessages, isSplitTurn, tokensBefore, previousSummary?, fileOps, ... }`
`CompactionResult` = `{ summary: string, firstKeptEntryId: string, tokensBefore: number, estimatedTokensAfter?, usage?, details? }`

Registration: `pi.on(event, handler)`, `pi.registerTool({...})`, `pi.registerCommand(name, {description, handler})`.
Context: `ctx.sessionManager` (read-only: `getEntries()`, `getBranch()`), `ctx.modelRegistry`, `ctx.model`, `ctx.cwd`, `ctx.ui.notify(text, level)`, `ctx.compact()`.
Custom messages that enter model context: `pi.sendMessage({customType, content, display}, {deliverAs:"steer"|..., triggerTurn})`. Non-context state: `pi.appendEntry(type, data)`.

In-extension LLM call (for lane C if it ever needs one from TS): `import { complete } from "@earendil-works/pi-ai"`, `const model = ctx.modelRegistry.find(provider, id)`, `await complete(model, { messages: [...] })`. Engram (Python) never calls a model directly; it asks the extension (TDD §6.1).

Compaction return, from docs/extensions.md line 466:
```ts
return { compaction: { summary, firstKeptEntryId: preparation.firstKeptEntryId, tokensBefore: preparation.tokensBefore } };
```

## A-2 Thinking format

pi-ai `ThinkingContent` = `{ type: "thinking", thinking: string, thinkingSignature?, redacted? }` inside `AssistantMessage.content[]`. For `openai-completions` providers, reasoning arrives as a thinking block only if the server returns it in `reasoning_content`; otherwise it is inline `<think>…</think>` text. **Live probe against LM Studio: NOT RUN — LM Studio is not installed on this machine** (`/Applications` has none, port 1234 closed). `p1/think.py` must handle both shapes; lane D records the real shape on the demo machine.

## A-4 LM Studio config

Not probed (see A-2). Template from `docs/models.md`, to be confirmed by lane D on the demo machine: `config/models.json.example`.

## A-6 Does the compaction hook receive thinking?

`messagesToSummarize` is `AgentMessage[]`, so thinking blocks are present iff the provider produced them (A-2). Confirm with a forced `/compact` once the model is wired (lane A, step 09).

## Headless / JSON mode (for S11 and the bench)

`pi --mode json "prompt"` streams `JsonAgentSessionEvent` lines; `pi -p "prompt"` prints. Filter with `jq -c 'select(.type=="message_end")'`.

## LIVE VERIFICATION — 2026-09-06 18:25, pi 0.85.1, model openai/gpt-4.1-mini (S11 evidence)

Command shape: `pi --mode json --model openai/gpt-4.1-mini -a -p "<prompt>"` in the repo, twice (two separate sessions).
`-a` approves project trust for the run: **non-interactive pi skips `.pi/extensions` without a saved trust decision** (docs/security.md). Interactive users accept the trust prompt once; it is saved in `~/.pi/agent/trust.json`.

Observed in the JSON event stream and `out/*.jsonl`:
- Session 1: user turn → `remember` (user, promoted) → assistant reply → `remember` (assistant, promoted) → `feedback` logged. No memory injected (empty store). ✔
- Session 2 (fresh process): `message_start role=custom customType=engram-memory "Memory (provenance-tagged): …"` appears **before** `message_start role=assistant` → recall fired before the first model token. ✔
- Session 2 reply: "Our deploy target is Vercel, and your rule is to never include emojis in commit messages." → the model used the injected memory (S3a + S3b). ✔
- `retrieval_log.jsonl`: session 2 recall injected 2 claims, feedback marked 2 used. ✔
- Extension exit: server child is `unref()`'d so print mode exits when the turn ends (earlier hang fixed).
Not yet verified live: forced `/compact` returning the digest (A-6), and the clarifying-question notify path in the TUI.

---

# Lane D probe evidence — appended 2026-09-06, nothing above this line changed

Both probes were **run**, not reasoned about. Neither wrote to `~/.pi/agent`:
pi's own documented override `PI_CODING_AGENT_DIR=/tmp/pi-probe` pointed it at
a throwaway config directory (`docs/environment-variables.md` line 81).

LM Studio is still not installed on this machine. Rather than leave A-2 "not
run", the probe replaced the *server* with a scripted OpenAI-compatible one
(`bench/probe_thinking.py`) that emits each reasoning shape in turn. That
settles the half of A-2 that is a property of pi — how each shape is
surfaced — with certainty. The other half, *which* shape LM Studio emits for
a given model, is still a demo-machine question and is marked as such below.

## A-2 Thinking format — RUN (pi 0.85.1, api `openai-completions`)

Command, three turns, one per shape:

```
PI_CODING_AGENT_DIR=/tmp/pi-probe PI_OFFLINE=1 \
  pi -p --mode json --provider lmstudio --model probe-model --no-tools --no-session \
     "Where should I deploy?"
```

| Turn | What the server sent | What pi produced |
|---|---|---|
| 1 | `delta.reasoning_content` chunks, then `delta.content` | **a thinking block** — `thinking_start` / `thinking_delta` / `thinking_end` events, and in `message_end`: `{"type":"thinking","thinking":"…","thinkingSignature":"reasoning_content"}` followed by a separate `{"type":"text",…}` |
| 2 | `delta.content` containing `<think>…</think>` then the answer | **one text block, tags intact** — no thinking events at all; `message_end` content is a single `{"type":"text","text":"<think>Weighing the two options. Vercel is already configured.</think>Deploy to Vercel."}` |
| 3 | `delta.content` only (control) | one text block, no thinking events |

Consequences, verbatim from the events above:

- **pi does not parse `<think>` tags.** Turn 2's reasoning arrived inside the
  answer text. `p1/think.py` must strip it itself, and the episodic record
  must not treat that text as something the assistant *said*.
- A thinking block carries `thinkingSignature: "reasoning_content"` — the
  provenance of the reasoning is on the block, so the two shapes are
  distinguishable after the fact, not only at parse time.
- The turn-level event order is `message_start` → `thinking_*` → `text_*` →
  `message_end` → `turn_end` → `agent_end` → `agent_settled`. `agent_end`
  carries the full `messages[]` array, which is what the `agent_end` hook in
  A-1 receives.
- Still a demo-machine question: **which** shape a given LM Studio model
  emits. Both are now handled, so either answer is survivable.

## A-4 LM Studio config — RUN (schema + wire, pi 0.85.1)

Two probes. First, pi's own validator (`dist/core/model-config.js`,
`ModelConfig.load`) over eight candidate files; second, the request pi
actually put on the wire, recorded by the probe server.

**Schema-required** (everything else is `TOptional` in
`dist/core/model-config.d.ts`): the top-level `providers` object, and `id` on
every entry of `providers.<id>.models[]`. A file missing either is rejected
with `Invalid models.json schema`. Unknown provider fields are accepted
silently.

**Functionally required** — accepted by the schema, but the run fails:

| Omitted | What happens |
|---|---|
| `baseUrl` | `Error: Unknown provider "lmstudio"` — the provider never registers; no request is sent |
| `api` | `Error: Unknown provider "lmstudio"` — same |
| `apiKey` | `No API key found for lmstudio.` — pi stops before the first request even though LM Studio needs no key. Any placeholder string works |

So the minimum that actually runs is four fields: `baseUrl`, `api`,
`apiKey` (placeholder), and `models[].id`.

**What `compat` changes on the wire** (same prompt, same model, only the
compat block differs; keys are the JSON body pi POSTed to
`/v1/chat/completions`):

| compat | message roles | body keys |
|---|---|---|
| *(absent)* | `developer`, `user` | `max_completion_tokens`, `messages`, `model`, `reasoning_effort`, `store`, `stream`, `stream_options` |
| `supportsDeveloperRole:false`, `supportsReasoningEffort:false` | `system`, `user` | `max_completion_tokens`, `messages`, `model`, `store`, `stream`, `stream_options` |
| + `supportsStore:false`, `maxTokensField:"max_tokens"` | `system`, `user` | `max_tokens`, `messages`, `model`, `stream`, `stream_options` |

`config/models.json.example` carries the middle row's compat block, which is
what `docs/models.md` in the pi package recommends for OpenAI-compatible
local servers. The third row is the safest for a server that rejects unknown
body fields; add it if LM Studio 400s on `store` or `max_completion_tokens`.

Reproduce any of this with `python -m bench.probe_thinking --serve --port 1234`
and the commands above.
