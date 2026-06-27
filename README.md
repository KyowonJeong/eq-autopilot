# EQ-Exec — EdgeQuant local close-all executor

> ⚠️ **This tool can close real positions on your funded prop account.**
> You run it, on your machine, with your keys, under your own responsibility.
> EdgeQuant never receives your API keys and never trades for you.
> **Always test on a DEMO/eval account first. It ships in `dry_run` mode by default.**

## What it does (Phase 1)
At a time you configure, it flattens (closes) all open futures positions on your broker
account — so you never violate a prop-firm time rule or hold something you forgot.

## Quick start
```bash
cd executor
python -m venv venv && ./venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml      # then fill in YOUR broker creds + cutoff time
./venv/bin/python -m eqexec.runner --config config.yaml          # dry-run (safe)
./venv/bin/python -m eqexec.runner --config config.yaml --once   # test one flatten now (dry-run)
```
Nothing is sent to the broker while `live: false`. Watch the logs first. Only set `live: true`
once you've confirmed on demo that it reads your positions and the flatten plan is correct.

## Safety model
- **Default dry-run.** `live: false` → logs the flatten plan, sends no orders.
- **Hard cutoff is local.** Works even if EdgeQuant sends nothing.
- **Confirm-after-act.** Re-reads positions after flattening; alerts loudly if not flat.
- **Pair it with your broker's native flatten** (e.g. Tradovate "Flatten Today") as a backstop —
  if this tool or the API is down, the broker's own timer still closes you.

See `PLAN.md` for architecture and roadmap.
