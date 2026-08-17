# Home Run Odds Price Checker

Finds MLB player-prop spots where **Novig** beats the market consensus, so you can back the
value side on Novig and hedge the other side on DraftKings/FanDuel (with a boost). Built on
[The Odds API](https://the-odds-api.com/). Home runs is the primary, credit-optimized market.

## Run locally (CLI)
```bash
# PowerShell
$env:ODDS_API_KEY="your-odds-api-key"
python oddsfinder.py home_runs        # or: strikeouts, hits, total_bases, ...
python oddsfinder.py --list           # show all markets
```
Outputs a console table, a timestamped CSV, and an auto-opening HTML report.

## Run from your phone (web app)
A tap-to-run Streamlit app (`streamlit_app.py`) renders the same results in the browser.
See **[DEPLOY.md](DEPLOY.md)** for free one-time setup on Streamlit Community Cloud.

## Files
- `oddsfinder.py` — the engine + market registry + CLI
- `streamlit_app.py` — phone/web front end
- `homeruns.py`, `strikeouts.py` — convenience CLI shims
- `requirements.txt` — dependencies for the web app
- `DEPLOY.md` — phone deploy guide

Your API key is **never** stored in the code — pass it via the `ODDS_API_KEY` environment
variable (local) or Streamlit Secrets (deployed).
