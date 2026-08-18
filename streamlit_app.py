"""
Phone-friendly web front end for the Novig value finder.
Reuses the engine in oddsfinder.py (get_events / get_event_odds / extract_edges /
select_value_spots) and renders a tap-to-run table. Deploy free on Streamlit Community
Cloud; open the URL on your phone.

Credit safety: the API is only called when you press "Run scan" (not on every rerun),
and an optional password gate stops others from spending your credits.
"""
import os
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

import oddsfinder as of

st.set_page_config(page_title="MLB Bet Finder", page_icon="⚾", layout="wide")

# Bump this whenever you push a change - it shows in the caption so you can tell from your
# phone whether Streamlit Cloud has redeployed the latest code.
APP_VERSION = "v7 · tennis (ATP/WTA moneyline)"


def _secret(name, default=""):
    """Read a Streamlit secret, tolerating the case where no secrets file exists (local run)."""
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


# --- API key: Streamlit secret when deployed, env var when running locally ---
of.API_KEY = _secret("ODDS_API_KEY", os.environ.get("ODDS_API_KEY", "")) or of.API_KEY

# --- optional password gate (set APP_PASSWORD in secrets to protect your credits) ---
_pw = _secret("APP_PASSWORD", "")
if _pw:
    if not st.session_state.get("authed"):
        entered = st.text_input("Password", type="password")
        if entered == _pw:
            st.session_state["authed"] = True
            st.rerun()
        elif entered:
            st.error("Wrong password")
        st.stop()

st.title("⚾ MLB Bet Finder")
st.caption(f"Novig value spots vs. the market consensus — back the side on Novig where Novig beats the field.  ·  build **{APP_VERSION}**")

if not of.API_KEY or of.API_KEY == "YOUR_API_KEY_HERE":
    st.error("No API key configured. Add ODDS_API_KEY in the app's Secrets (or ODDS_API_KEY env var locally).")
    st.stop()

# --- controls ---
names = sorted(of.MARKETS)
market_name = st.selectbox("Market", names, index=names.index("home_runs") if "home_runs" in names else 0)
base_cfg = of.MARKETS[market_name]

with st.expander("Filters"):
    if base_cfg.moneyline:
        st.caption("Match-winner (moneyline) market — back the player on Novig; boost the opponent on DK/FD.")
        side = base_cfg.your_side  # "ML" — no Over/Under choice for a 2-way market
    else:
        side = st.radio("Side to back on Novig", ["Under", "Over"],
                        index=0 if base_cfg.your_side == "Under" else 1, horizontal=True)
    min_edge = st.slider("Min edge vs. consensus (%)", 0.0, 10.0, float(base_cfg.min_edge * 100), 0.5)
    min_books = st.slider("Min other books on the line", 1, 12, base_cfg.min_books_on_line)
    top_n = st.slider("Max rows", 5, 60, 25)

cfg = replace(base_cfg, your_side=side, min_edge=min_edge / 100.0, min_books_on_line=min_books)
opp = "Opp" if base_cfg.moneyline else ("Over" if side == "Under" else "Under")


@st.cache_data(ttl=600, show_spinner=False)
def cached_resolve(sport, key):
    """Resolve a tour prefix (e.g. 'tennis_atp') to the active tournament key. Free call."""
    return of.resolve_sport(sport)


resolved_sport = cached_resolve(cfg.sport, of.API_KEY)
if resolved_sport is None:
    st.warning(f"No active tournament for {cfg.sport} right now — check back during a tournament.")
    st.stop()
cfg = replace(cfg, sport=resolved_sport)


@st.cache_data(ttl=300, show_spinner=False)
def cached_events(sport, key):
    """Free (no-quota) events call, cached 5 min so screen taps don't spam it."""
    r = requests.get(f"{of.BASE_URL}/sports/{sport}/events",
                     params={"apiKey": key, "dateFormat": "iso"})
    r.raise_for_status()
    return r.json(), r.headers.get("x-requests-remaining")


# --- cost estimate (uses only the free events call) ---
try:
    events, remaining = cached_events(cfg.sport, of.API_KEY)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    todays = [e for e in events if e.get("commence_time", "").startswith(today)]
    n_regions = len(cfg.regions.split(","))
    st.info(f"**{len(todays)} games today** · regions `{cfg.regions}` · "
            f"estimated cost **~{len(todays) * n_regions} credits** · "
            f"quota remaining **{remaining}**")
except Exception as e:
    st.warning(f"Couldn't load today's games: {e}")
    todays = []

run = st.button(f"🔎 Run {cfg.name} scan", type="primary", disabled=not todays, use_container_width=True)

if run:
    all_edges, live_remaining = [], remaining
    prog = st.progress(0.0, text="Fetching odds…")
    for i, ev in enumerate(todays):
        try:
            data, headers = of.get_event_odds(cfg, ev["id"])
            live_remaining = headers.get("x-requests-remaining")
            all_edges.extend(of.extract_edges(cfg, data))
        except requests.HTTPError as ex:
            if ex.response is not None and ex.response.status_code == 401:
                st.error("Quota exhausted (401) — stopping."); break
            st.warning(f"Skipped {ev.get('home_team')}: {ex}")
        prog.progress((i + 1) / len(todays), text=f"Fetching odds… {i+1}/{len(todays)}")
    prog.empty()
    st.session_state["result"] = {
        "spots": of.select_value_spots(all_edges, cfg),
        "remaining": live_remaining,
        "when": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "cfg_name": cfg.name, "side": side, "opp": opp,
    }

# --- render last result ---
res = st.session_state.get("result")
if res:
    spots = res["spots"]
    st.markdown(f"**{res['cfg_name']} — {res['side']}** · {len(spots)} spots · "
                f"run {res['when']} · quota left **{res['remaining']}**")
    if not spots:
        st.warning("No value spots matched the filters this run.")
    else:
        rows = []
        for e in spots[:top_n]:
            cons_prob = of.implied_prob(e["market_consensus_price"])
            fair_opp = of.american_from_prob(1 - cons_prob) if cons_prob else None
            hedge = of.format_odds(e["hedge_price"]) if e.get("hedge_price") is not None else "—"
            if e.get("hedge_book"):
                hedge += f" {e['hedge_book']}"
            rows.append({
                "Player": e["player"],
                "Line": e["label"],
                f"Novig {res['side']}": of.format_odds(e["novig_price"]),
                f"Novig {res['opp']}": of.format_odds(e["novig_opp_price"]),
                "Hold %": round(e["novig_hold"] * 100, 1) if e.get("novig_hold") is not None else None,
                "Consensus": of.format_odds(e["market_consensus_price"]),
                "Edge %": round(e["edge_vs_consensus"] * 100, 1),
                "#Beat": f"{e['n_books_better']}/{e['n_other_books']}",
                f"DK/FD {res['opp']}": hedge,
                f"Fair {res['opp']}": of.format_odds(fair_opp),
                "Game": e["game"],
                "Odds": of.player_search_url(e["player"], res["cfg_name"]),
            })
        df = pd.DataFrame(rows)
        edge_max = max(6.0, float(df["Edge %"].max()))
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Odds": st.column_config.LinkColumn("Odds", display_text="check"),
                "Edge %": st.column_config.ProgressColumn(
                    "Edge %", format="%.1f%%", min_value=0.0, max_value=edge_max),
                "Hold %": st.column_config.NumberColumn(
                    "Hold %", format="%.1f%%",
                    help="Novig's two-way margin on this market — a liquidity/tightness proxy. "
                         "Lower or negative = tighter & more active (better to arb); higher = thin."),
            },
        )
        st.caption(f"Back **{res['side']}** on Novig; check **{res['opp']}** on DK/FanDuel (tap 'check') "
                   f"and apply a boost. 'Fair {res['opp']}' is the number your boosted price should beat. "
                   f"'Hold %' is Novig's two-way margin — lower = tighter/more liquid (not dollar depth).")
else:
    st.caption("Pick a market and tap **Run scan** to pull today's spots. The scan is the only thing that spends credits.")
