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
