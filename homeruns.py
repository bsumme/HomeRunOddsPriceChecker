"""Convenience shim: run the Home Runs market via the shared engine.
Equivalent to `python oddsfinder.py home_runs`. Tune defaults in oddsfinder.MARKETS,
or pass flags through oddsfinder.py directly (e.g. --over, --min-edge, --min-price).
"""
from oddsfinder import run, MARKETS

if __name__ == "__main__":
    run(MARKETS["home_runs"])
