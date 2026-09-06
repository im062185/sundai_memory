#!/bin/zsh
# Demo rehearsal — the seven CUJ §8 beats, scripted, with a stand-in model.
#
#   python -m bench.probe_thinking --serve --port 1234 --echo &
#   ./bench/rehearse.sh
#
# The stand-in answers each turn with the memory Engram injected, so the
# extension → server → recall → injection path is exercised for real on a
# machine with no LM Studio and no API key. It is not a model and nothing it
# says is ever scored. docs/DEMO.md records what this does and does not cover.
#
# PI_CODING_AGENT_DIR keeps every pi invocation out of ~/.pi/agent.
# -a (--approve) is required or pi will not load .pi/extensions and the whole
# run silently has no memory.
export PI_CODING_AGENT_DIR=/tmp/pi-demo
turn() { perl -e 'alarm 60; exec @ARGV' pi --mode json -a --model lmstudio/probe-model -p "$1" 2>/dev/null \
  | jq -rc 'select(.type=="message_end") | (.message.content | if type=="array" then .[0].text else . end) // ""' | tail -1 }
T0=$(date +%s)
say() { print ""; print "### $1  [t+$(( $(date +%s) - T0 ))s]" }
rm -rf out
say "0:00 session 1 — three turns"
turn "I am building this project as a memory layer for pi with capture-first behavior."
turn "Keep answers short when summarizing and coaching. The vendor SDK has no batch-write endpoint."
turn "I never want TypeScript in the organ codebase."
say "0:50 consolidate"
.venv/bin/python -m engram consolidate | jq -r '.digest'
say "1:05 session 2 opens knowing the project"
turn "When I open this project again, I know you remember what we decided on Monday."
say "1:30 the correction"
turn "Oh, correction: the SDK shipped batch writes in 3.4."
say "1:45 per-store table and A-3"
.venv/bin/python -m bench.component 2>&1 | grep -E "A-3 |sqlite:|vector:"
say "2:10 is your memory helping"
.venv/bin/python -m engram report | head -9
say "2:45 what do you remember"
.venv/bin/python -m engram explain | head -10
say "END"
