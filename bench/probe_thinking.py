"""A-2 probe support: a fake OpenAI-compatible server that emits both
reasoning shapes, so we can see which one pi turns into a thinking block.

TDD §4 A-2 asks: with an openai-completions provider, does reasoning arrive
as a `thinking` block or as inline `<think>…</think>` text? The answer has
two halves and only one of them depends on the demo machine:

  1. what the SERVER emits — LM Studio's choice, per model and per runtime;
  2. what PI does with each shape — fixed by the pi build, testable offline.

This server settles (2) deterministically by emitting, on successive
requests: reasoning_content deltas, then inline <think>…</think> text, then
plain text. Run it, point pi at it with PI_CODING_AGENT_DIR (never
~/.pi/agent), and read the --mode json events.

  python -m bench.probe_thinking --serve --port 1234

It is a probe, not part of the product: nothing in engram/ imports it, and
it never runs in the test suite.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "probe-model"

# One scripted reply per request, in order. Each is a list of SSE deltas.
SCRIPTS: list[list[dict]] = [
    # 1 — reasoning in the separate `reasoning_content` field (the shape
    #     pi's openai-completions provider can map to a thinking block)
    [
        {"reasoning_content": "Weighing the two options."},
        {"reasoning_content": " Vercel is already configured."},
        {"content": "Deploy to Vercel."},
    ],
    # 2 — reasoning inline in `content`, wrapped in <think> tags
    [
        {"content": "<think>Weighing the two options."},
        {"content": " Vercel is already configured.</think>"},
        {"content": "Deploy to Vercel."},
    ],
    # 3 — no reasoning at all, the control
    [
        {"content": "Deploy to Vercel."},
    ],
]


MEMORY_HEADER = "Memory (provenance-tagged):"


def echo_reply(messages: list[dict]) -> str:
    """What the demo rehearsal needs a model for: proof the memory arrived.

    `--echo` answers every turn with what Engram actually put in the model's
    context this turn. It is not an answer to the question and is never scored
    (bench/judge.py refuses records whose answerer is not a model). It exists
    so the extension → server → recall → injection path can be rehearsed on a
    machine with no LM Studio and no API key, which is what S11 asks about:
    did the hook fire, and did the claims reach the context.
    """
    def flatten(content) -> str:
        # OpenAI content is a string OR a list of parts. str() on the list
        # escapes the newlines and the claim lines vanish — flatten properly.
        if isinstance(content, list):
            return "\n".join(str(p.get("text", "")) if isinstance(p, dict) else str(p)
                             for p in content)
        return str(content or "")

    injected = [m for m in messages if MEMORY_HEADER in flatten(m.get("content"))]
    if not injected:
        return "memory: none injected this turn"
    body = flatten(injected[-1].get("content"))
    claims = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("-")]
    first = claims[0][:90] if claims else "(header only, no claims)"
    return f"memory: {len(claims)} claim(s) injected · first → {first}"


class Handler(BaseHTTPRequestHandler):
    turn = 0
    echo = False

    def log_message(self, fmt, *a):  # keep the probe output readable
        sys.stderr.write("  [server] " + (fmt % a) + "\n")

    def _json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [
                {"id": MODEL_ID, "object": "model", "owned_by": "probe"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode() if length else "{}"
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}
        # The request pi actually sent, recorded for the A-4 half of the probe.
        sys.stderr.write("  [request] " + json.dumps({
            "model": req.get("model"),
            "stream": req.get("stream"),
            "roles": [m.get("role") for m in req.get("messages", [])],
            "keys": sorted(req),
        }) + "\n")

        if Handler.echo:
            script = [{"content": echo_reply(req.get("messages", []))}]
        else:
            script = SCRIPTS[Handler.turn % len(SCRIPTS)]
        Handler.turn += 1
        if req.get("stream"):
            self._stream(script)
        else:
            delta: dict = {"role": "assistant", "content": "", "reasoning_content": ""}
            for part in script:
                for key, value in part.items():
                    delta[key] = delta.get(key, "") + value
            self._json({
                "id": "probe", "object": "chat.completion", "created": int(time.time()),
                "model": MODEL_ID,
                "choices": [{"index": 0, "message": delta, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

    def _stream(self, script: list[dict]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(delta: dict, finish: str | None = None) -> None:
            chunk = {
                "id": "probe", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": MODEL_ID,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()

        send({"role": "assistant"})
        for part in script:
            send(part)
        send({}, finish="stop")
        self.wfile.write(b'data: {"id":"probe","object":"chat.completion.chunk","choices":[],'
                         b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--echo", action="store_true",
                    help="reply with the memory Engram injected, for demo rehearsal")
    args = ap.parse_args(argv)
    if not args.serve:
        ap.print_help()
        return 0
    Handler.echo = args.echo
    # Threading: pi opens more than one connection (a /models probe
    # alongside the streaming POST). A single-threaded server deadlocks.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    mode = "echo (rehearsal)" if args.echo else "scripted (A-2 probe)"
    sys.stderr.write(f"probe server on http://127.0.0.1:{args.port}/v1 "
                     f"(model {MODEL_ID}, {mode})\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
