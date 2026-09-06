// Lane A owns this file. Skeleton only — see pi-extension/HOOKS.md for verified event shapes.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function engram(pi: ExtensionAPI) {
  pi.on("session_start", async (_event, ctx) => {
    ctx.ui.notify("engram: extension loaded (skeleton — no server yet)", "info");
  });
}
