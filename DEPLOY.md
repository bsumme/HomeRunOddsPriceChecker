# Run the HR finder from your phone (Streamlit Community Cloud — free)

You'll open a URL on your phone, tap **Run**, and see today's Novig value spots. Setup is
one-time (~10 min). After that it's just a bookmark on your home screen.

## Files the app needs (already in this folder)
- `oddsfinder.py` — the engine (unchanged)
- `streamlit_app.py` — the phone front end
- `requirements.txt` — dependencies

You do **not** need `homeruns.py`, the CSVs, or the HTML reports for the web app.

## Step 1 — put the code on GitHub (free)
1. Create a free account at https://github.com if you don't have one.
2. Make a new **private** repository (e.g. `mlb-bet-finder`).
3. Upload `oddsfinder.py`, `streamlit_app.py`, and `requirements.txt` to it
   (GitHub's web UI: "Add file" → "Upload files" → drag them in → Commit).

   ⚠️ Do **not** upload your API key or any file containing it (skip `notes`).

## Step 2 — deploy on Streamlit (free)
1. Go to https://share.streamlit.io and sign in **with your GitHub account**.
2. Click **Create app** → **Deploy a public app from GitHub** (we'll lock it down in Step 4).
3. Pick your repo, branch `main`, main file `streamlit_app.py`. Click **Deploy**.

## Step 3 — add your secrets
1. In the app page, open **⋮ → Settings → Secrets**.
2. Paste this (keep the quotes), then **Save**:

   ```toml
   ODDS_API_KEY = "paste-your-odds-api-key-here"
   APP_PASSWORD = "pick-a-password"
   ```

   `APP_PASSWORD` is optional but recommended — it stops anyone with the link from
   running scans and burning your API credits.

## Step 4 — lock it down (protect your credits)
Do **either** (or both):
- **App password:** set `APP_PASSWORD` above — the app asks for it before anything runs.
- **Private app:** app **Settings → Sharing** → limit viewers to your Google email(s).

## Step 5 — use it on your phone
1. Open the app URL (looks like `https://<something>.streamlit.app`) in your phone browser.
2. iPhone: Share → **Add to Home Screen**. Android: menu → **Add to Home screen**.
3. Tap the icon, enter the password, pick **home_runs**, tap **Run scan**.

## Notes
- The **scan is the only thing that spends credits** (it shows an estimate first). Just
  opening the app or changing filters costs nothing.
- To change defaults (regions, min edge, etc.), edit `oddsfinder.py`'s `MARKETS`, commit
  to GitHub, and Streamlit redeploys automatically.
- Test locally first if you want: `pip install -r requirements.txt` then
  `streamlit run streamlit_app.py` (set `ODDS_API_KEY` in your environment first).
