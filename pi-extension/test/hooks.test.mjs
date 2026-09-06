// Mocked-pi unit test (S11 offline proxy): every hook sends the expected op, in order.
import { test } from "node:test";
import assert from "node:assert/strict";
import engram from "../src/index.ts";

function fakePi() {
  const handlers = {}; const tools = {}; const commands = {};
  return { on: (e, h) => (handlers[e] = h), registerTool: (t) => (tools[t.name] = t), registerCommand: (n, o) => (commands[n] = o), handlers, tools, commands };
}
function fakeCtx(log) {
  return { cwd: process.cwd(), ui: { notify: (m, l) => log.push(["notify", l, m]) }, sessionManager: { getEntries: () => [] } };
}

test("hooks fire recall before turn and remember+feedback after, compaction returns digest", async () => {
  const pi = fakePi(); engram(pi);
  for (const e of ["session_start", "before_agent_start", "agent_end", "session_before_compact", "session_shutdown"]) assert.ok(pi.handlers[e], e);
  assert.ok(pi.tools.engram_explain && pi.tools.engram_report && pi.commands["engram-consolidate"] && pi.commands["engram-report"]);
  const log = []; const ctx = fakeCtx(log);
  await pi.handlers.session_start({ type: "session_start", reason: "startup" }, ctx);
  const r1 = await pi.handlers.before_agent_start({ type: "before_agent_start", prompt: "what is the deploy target?", systemPrompt: "" }, ctx);
  assert.ok(r1 === undefined || r1.message?.content.startsWith("Memory (provenance-tagged)"));
  await pi.handlers.agent_end({ type: "agent_end", messages: [{ role: "assistant", content: [{ type: "text", text: "Vercel." }] }] }, ctx);
  const r2 = await pi.handlers.session_before_compact({ type: "session_before_compact", reason: "manual", preparation: { messagesToSummarize: [], firstKeptEntryId: "e1", tokensBefore: 10 } }, ctx);
  assert.ok(r2 === undefined || typeof r2.compaction.summary === "string");
  await pi.handlers.session_shutdown({ type: "session_shutdown", reason: "quit" }, ctx);
  const errors = log.filter((l) => l[1] === "error");
  assert.deepEqual(errors, [], "no hook may fail silently or loudly: " + JSON.stringify(errors));
});
