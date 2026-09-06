"""python -m engram --serve | consolidate | report | explain | refute <id>"""
import json, sys
from engram.server import Engram, serve


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "--serve":
        serve()
        return 0
    eng = Engram()
    op = argv[0]
    req = {"op": op}
    if op == "refute" and len(argv) > 1:
        req["claim_id"] = argv[1]
    if op == "consolidate":
        req["reason"] = "cli"
    res = eng.handle(req)
    if "text" in res:
        print(res["text"])
    else:
        print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
