import json, subprocess, sys, pathlib
from engram.server import Engram
from engram.adapters.null import NullStore
from conftest import make_claim

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_ops_roundtrip(tmp_path):
    eng = Engram(store=NullStore(), out=tmp_path)
    for op in ("recall", "remember", "feedback", "consolidate", "explain", "report"):
        req = {"op": op, "text": "batch endpoint", "turn": {"role": "user", "text": "The SDK has no batch endpoint"}, "session": "s1", "turn_index": 1, "reply": ""}
        res = eng.handle(req)
        assert res["ok"], (op, res)
        assert "ms" in res
    assert not eng.handle({"op": "nope"})["ok"]
    assert (tmp_path / "trace.jsonl").exists() and (tmp_path / "retrieval_log.jsonl").exists()


def test_stdio_server_one_line_per_request(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "engram", "--serve"], input='{"op":"recall","text":"x"}\n{"op":"explain"}\nnot json\n',
                          capture_output=True, text=True, cwd=ROOT, env={"ENGRAM_OUT": str(tmp_path), "ENGRAM_STORE": "null", "PATH": "/usr/bin:/bin"}, timeout=30)
    lines = [json.loads(l) for l in proc.stdout.strip().splitlines()]
    assert len(lines) == 3 and lines[0]["ok"] and lines[1]["ok"] and not lines[2]["ok"]
