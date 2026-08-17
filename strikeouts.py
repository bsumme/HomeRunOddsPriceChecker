"""Convenience shim: run the Pitcher Strikeouts market via the shared engine.
Equivalent to `python oddsfinder.py strikeouts`. Tune defaults in oddsfinder.MARKETS,
or pass flags through oddsfinder.py directly (e.g. --over, --min-edge, --min-books).
"""
from oddsfinder import run, MARKETS

if __name__ == "__main__":
    run(MARKETS["strikeouts"])
