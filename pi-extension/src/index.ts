// Lane A. Engram pi extension: structural memory. Hook names/payloads verified in HOOKS.md.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "@sinclair/typebox";

type Req = Record<string, unknown> & { op: string };
type Res = { ok: boolean; ms?: number; error?: string } & Record<string, any>;

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

class EngramClient {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private queue: Array<(r: Res) => void> = [];
  private dead = false;
  private notify: (msg: string, level: "info" | "warning" | "error") => void;
  constructor(notify: (msg: string, level: "info" | "warning" | "error") => void) { this.notify = notify; }

  start(cwd: string) {
    const py = [join(REPO, ".venv", "bin", "python"), "python3", "python"].find((p) => p === "python3" || p === "python" || existsSync(p))!;
    this.proc = spawn(py, ["-m", "engram", "--serve"], { cwd: REPO, env: { ...process.env, ENGRAM_OUT: join(cwd, "out") } });
    // never keep pi's event loop alive (print mode must exit when the turn ends)
    this.proc.unref();
    for (const s of [this.proc.stdin, this.proc.stdout, this.proc.stderr]) (s as any).unref?.();
    const rl = createInterface({ input: this.proc.stdout });
    rl.on("line", (line) => {
      const next = this.queue.shift();
      if (!next) return;
      try { next(JSON.parse(line)); } catch (e) { next({ ok: false, error: `bad json from engram: ${line.slice(0, 80)}` }); }
    });
    this.proc.stderr.on("data", (d) => this.notify(`engram: ${String(d).trim().slice(0, 200)}`, "warning"));
    this.proc.on("exit", (code) => {
      this.dead = true;
      this.notify(`engram server exited (code ${code}); continuing WITHOUT memory`, "error");
      for (const q of this.queue.splice(0)) q({ ok: false, error: "engram exited" });
    });
  }

  call(req: Req, timeoutMs = 15000): Promise<Res> {
    if (!this.proc || this.dead) return Promise.resolve({ ok: false, error: "engram not running" });
    return new Promise((resolve) => {
      const t = setTimeout(() => resolve({ ok: false, error: `engram timeout on ${req.op}` }), timeoutMs);
      this.queue.push((r) => { clearTimeout(t); resolve(r); });
      this.proc!.stdin.write(JSON.stringify(req) + "\n");
    });
  }

  stop() { this.proc?.kill(); }
}

function memoryMessage(claims: Array<{ text: string; source_class?: string }>, always: Record<string, string> = {}): string {
  const tag = (s?: string) => (s === "said" ? "said" : s === "verified" ? "verified" : s === "inferred" ? "inferred" : s ?? "said");
  const parts = ["Memory (provenance-tagged):"];
  for (const k of ["USER.md", "MEMORY.md"]) if (always[k]) parts.push(always[k].trim());
  if (claims.length) parts.push("## RECALLED\n" + claims.map((c) => `- [${tag(c.source_class)}] ${c.text}`).join("\n"));
  parts.push("If the answer is not in the memory above, say that you do not have that information rather than guessing.");
  return parts.join("\n");
}

export default function engram(pi: ExtensionAPI) {
  let client: EngramClient | null = null;
  let session = "s-" + Date.now().toString(36);
  let turn = 0;
  let lastInjected: string[] = [];
  let lastPrompt = "";

  const ensure = (ctx: ExtensionContext) => {
    if (!client) { client = new EngramClient((m, l) => ctx.ui.notify(m, l)); client.start(ctx.cwd); }
    return client;
  };
  const visible = (ctx: ExtensionContext, r: Res, op: string) => {
    if (!r.ok) ctx.ui.notify(`engram ${op} failed: ${r.error} — turn continues without memory`, "error");
    return r.ok;
  };

  // session start → spawn + consolidate(reason start) over the prior session entries
  pi.on("session_start", async (event, ctx) => {
    const c = ensure(ctx);
    const entries = ctx.sessionManager.getEntries?.() ?? [];
    const messages = entries.filter((e: any) => e.type === "message").map((e: any) => e.message);
    const r = await c.call({ op: "consolidate", messages, reason: "start" }, 60000);
    if (visible(ctx, r, "consolidate") && r.counts) ctx.ui.notify(`engram: consolidated on start (${JSON.stringify(r.counts)})`, "info");
  });

  // before each turn → recall, inject. Structural: cannot be skipped by the model.
  pi.on("before_agent_start", async (event, ctx) => {
    const c = ensure(ctx);
    turn += 1;
    lastPrompt = event.prompt;
    const r = await c.call({ op: "recall", text: event.prompt, k: 8, session, turn_index: turn });
    const hasAlways = r.always && Object.keys(r.always).length > 0;
    if (!visible(ctx, r, "recall") || (!r.claims?.length && !hasAlways)) { lastInjected = []; return; }
    lastInjected = (r.claims ?? []).map((x: any) => x.id);
    return { message: { customType: "engram-memory", content: memoryMessage(r.claims ?? [], r.always ?? {}), display: true, details: { ids: lastInjected, tokens: r.tokens } } };
  });

  // after each run → remember(user turn), then feedback with the reply text
  pi.on("agent_end", async (event, ctx) => {
    const c = ensure(ctx);
    const r = await c.call({ op: "remember", turn: { role: "user", text: lastPrompt, thinking: null }, session, turn_index: turn });
    if (visible(ctx, r, "remember") && r.question) ctx.ui.notify(`engram asks: ${r.question}`, "info");
    const last = [...event.messages].reverse().find((m: any) => m.role === "assistant") as any;
    const reply = last ? (Array.isArray(last.content) ? last.content.filter((p: any) => p.type === "text").map((p: any) => p.text).join("\n") : String(last.content ?? "")) : "";
    const thinking = last && Array.isArray(last.content) ? last.content.filter((p: any) => p.type === "thinking").map((p: any) => p.thinking).join("\n") : null;
    await c.call({ op: "remember", turn: { role: "assistant", text: reply, thinking: thinking || null }, session, turn_index: turn });
    await c.call({ op: "feedback", session, turn_index: turn, injected: lastInjected, reply });
  });

  // compaction → consolidate over the messages about to be summarized; digest becomes the summary
  pi.on("session_before_compact", async (event, ctx) => {
    const c = ensure(ctx);
    const r = await c.call({ op: "consolidate", messages: event.preparation.messagesToSummarize, reason: event.reason === "manual" ? "manual" : "threshold" }, 120000);
    if (!visible(ctx, r, "consolidate") || !r.digest) return; // fall back to pi's default summary
    return { compaction: { summary: r.digest, firstKeptEntryId: event.preparation.firstKeptEntryId, tokensBefore: event.preparation.tokensBefore } };
  });

  pi.on("session_shutdown", async () => { client?.stop(); });

  pi.registerTool({
    name: "engram_explain", label: "Engram explain", description: "What the memory system remembers about this project, in plain language (said / inferred / verified).",
    parameters: Type.Object({}),
    async execute(_id, _params, _signal, _onUpdate, ctx) {
      const r = await ensure(ctx).call({ op: "explain" });
      return { content: [{ type: "text", text: r.ok ? r.text : `engram unavailable: ${r.error}` }], details: r };
    },
  });
  pi.registerTool({
    name: "engram_report", label: "Engram report", description: "Five-metric memory scorecard: speed, accuracy, tokens, precision, recall; memory-on vs off.",
    parameters: Type.Object({}),
    async execute(_id, _params, _signal, _onUpdate, ctx) {
      const r = await ensure(ctx).call({ op: "report" }, 60000);
      return { content: [{ type: "text", text: r.ok ? r.text : `engram unavailable: ${r.error}` }], details: r };
    },
  });
  pi.registerCommand("engram-consolidate", { description: "Run memory consolidation now", handler: async (_args, ctx) => {
    const r = await ensure(ctx).call({ op: "consolidate", messages: [], reason: "manual" }, 120000);
    ctx.ui.notify(r.ok ? `engram: ${JSON.stringify(r.counts)}` : `engram: ${r.error}`, r.ok ? "info" : "error");
  } });
  pi.registerCommand("engram-report", { description: "Show the memory scorecard", handler: async (_args, ctx) => {
    const r = await ensure(ctx).call({ op: "report" }, 60000);
    ctx.ui.notify(r.ok ? r.text : `engram: ${r.error}`, r.ok ? "info" : "error");
  } });
}
