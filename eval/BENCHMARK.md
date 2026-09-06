# Running LoCoMo end-to-end (branch: benchmark-test)

## One-time setup
1. `git clone https://github.com/snap-research/locomo` next to this repo (we only need `data/locomo10.json`).
2. `export ANTHROPIC_API_KEY=sk-...` (Console -> API keys). Fresh accounts get $5 free credit, which covers the pilot below.
3. Validate the pipeline with zero spend: `python eval/locomo_live.py --dry-run` — this exercises ingestion, retrieval, answering, judging, checkpointing and the report with mocks. If it prints a table, the harness is wired correctly.

## Pilot (do this before the full run)
`python eval/locomo_live.py --data ../locomo/data/locomo10.json --conditions engram keyword_rag --limit 50`
Roughly 1,000 API calls, well under $1. Read 10 transcripts in `results/*.jsonl` by hand to sanity-check the judge before trusting aggregates.

## Full run
`python eval/locomo_live.py --data ../locomo/data/locomo10.json --conditions engram keyword_rag`
1,540 questions x 2 conditions x (answer + judge) = ~6,200 calls. Expect a few hours serial and single-digit dollars on Haiku 4.5. It checkpoints after every question (`results/*.jsonl`), so Ctrl-C and rerun to resume; nothing is recomputed.
Optional ceiling: add `full_history` to `--conditions` (~40M input tokens, ~$40 — decide deliberately).

## Protocol notes (for the paper)
- The judge model, answer prompt and judge prompt are frozen in the script and dumped to `results/*.meta.json` per run — cite that file, never paraphrase the prompt in the paper.
- We report BOTH LLM-judge accuracy and token-F1 (the original paper's metric). F1 is deterministic and free; the judge number is what recent memory papers report.
- Same judge for every condition, always. To try a different answerer, pass `--answer-model` (results go to a separate file keyed by model name).
- Category 5 (adversarial) is excluded, matching common practice. If we later want it, the answerer prompt already supports abstention ("No information available.").
- Hand-check ~50 judge verdicts per full run and note the agreement rate; reviewers ask.
