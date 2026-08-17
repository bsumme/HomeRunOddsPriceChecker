"""
MLB Player-Prop Bet Finder (generalized engine)
================================================
Finds spots where Novig beats the market consensus on a given side of a given player-prop
market, so you can back the value side on Novig and (optionally) hedge the other side on
DraftKings/FanDuel with a boost.

Usage:
    python oddsfinder.py <market> [options]
    python oddsfinder.py --list                 # show available markets
    python oddsfinder.py strikeouts             # defaults for that market
    python oddsfinder.py home_runs --under --min-edge 1.5 --min-price -550 --max-price -300
    python oddsfinder.py hits --over --min-books 5 --top 30 --no-open

Each market's sensible defaults live in the MARKETS registry below; every default can be
overridden on the command line. Outputs a console table, a timestamped CSV, and a slick
auto-opening HTML report.
"""

import argparse
import csv
import html
import os
import statistics
import sys
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# ---------------------------------------------------------------------------
# Shared config (same for every market)
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("ODDS_API_KEY", "YOUR_API_KEY_HERE")
# us_ex = exchanges (Novig), us = sportsbooks (DK/FD/etc.), us2 = more books, eu = Pinnacle
REGIONS = "us,us_ex,us2,eu"
ODDS_FORMAT = "american"
BASE_URL = "https://api.the-odds-api.com/v4"

# Books shown first in the verbose per-game table; others appended alphabetically.
PREFERRED_BOOKS = ["Novig", "Pinnacle", "DraftKings", "FanDuel"]


@dataclass
class MarketConfig:
    """Everything that differs from one market to the next."""
    key: str                       # Odds API market key, e.g. "pitcher_strikeouts"
    name: str                      # human name, e.g. "Pitcher Strikeouts"
    slug: str                      # short id for filenames, e.g. "k"
    sport: str = "baseball_mlb"    # Odds API sport key, e.g. "basketball_wnba"
    regions: str = REGIONS         # billed as markets x regions per game - fewer = cheaper
    subject: str = "Player"        # table header word: "Batter" / "Pitcher"
    your_side: str = "Under"       # side you back on Novig
    price_range: tuple | None = None      # (low, high) American-odds band on Novig's price, or None
    min_edge: float = 0.02         # min fraction Novig must beat consensus by
    min_books_on_line: int = 4     # min OTHER books on the same exact line to trust the consensus
    hedge_books: list = field(default_factory=lambda: ["DraftKings", "FanDuel"])
    dk_url: str = "https://sportsbook.draftkings.com/leagues/baseball/mlb?category=batter-props"
    fd_url: str = "https://sportsbook.fanduel.com/navigation/mlb?tab=player-props"


# ---------------------------------------------------------------------------
# Market registry - add a new market by dropping a line in here
# ---------------------------------------------------------------------------
MARKETS = {
    "home_runs": MarketConfig(
        key="batter_home_runs", name="Home Runs", slug="hr", subject="Batter",
        # Novig (us_ex) + us2 sportsbooks returns 8 HR books - enough for a solid consensus at
        # HALF the credit cost of the full 4-region set. DK/FD don't carry HR props anyway, and
        # us2 already includes Pinnacle-free but plenty of books, so nothing we use is lost.
        regions="us_ex,us2",
        your_side="Under", price_range=(-550, -300), min_edge=0.015, min_books_on_line=3,
        dk_url="https://sportsbook.draftkings.com/leagues/baseball/mlb?category=batter-props&subcategory=home-runs",
        fd_url="https://sportsbook.fanduel.com/navigation/mlb?tab=player-props",
    ),
    "strikeouts": MarketConfig(
        key="pitcher_strikeouts", name="Pitcher Strikeouts", slug="k", subject="Pitcher",
        your_side="Under", price_range=None, min_edge=0.02, min_books_on_line=4,
        dk_url="https://sportsbook.draftkings.com/leagues/baseball/mlb?category=pitcher-props&subcategory=strikeouts-thrown",
        fd_url="https://sportsbook.fanduel.com/navigation/mlb?tab=pitcher-props",
    ),
    "hits": MarketConfig(
        key="batter_hits", name="Hits", slug="hits", subject="Batter",
        your_side="Under", price_range=None, min_edge=0.02, min_books_on_line=4,
        dk_url="https://sportsbook.draftkings.com/leagues/baseball/mlb?category=batter-props&subcategory=hits",
        fd_url="https://sportsbook.fanduel.com/navigation/mlb?tab=player-props",
    ),
    "total_bases": MarketConfig(
        key="batter_total_bases", name="Total Bases", slug="tb", subject="Batter",
        your_side="Under", price_range=None, min_edge=0.02, min_books_on_line=4,
        dk_url="https://sportsbook.draftkings.com/leagues/baseball/mlb?category=batter-props&subcategory=total-bases",
        fd_url="https://sportsbook.fanduel.com/navigation/mlb?tab=player-props",
    ),
    "rbis": MarketConfig(
        key="batter_rbis", name="RBIs", slug="rbi", subject="Batter",
        your_side="Under", price_range=None, min_edge=0.02, min_books_on_line=4,
        dk_url="https://sportsbook.draftkings.com/leagues/baseball/mlb?category=batter-props&subcategory=runs-batted-in",
        fd_url="https://sportsbook.fanduel.com/navigation/mlb?tab=player-props",
    ),
    "strikeouts_alt": MarketConfig(
        key="pitcher_strikeouts_alternate", name="Pitcher Strikeouts (Alt Lines)", slug="kalt",
        subject="Pitcher", your_side="Under", price_range=None, min_edge=0.03, min_books_on_line=3,
        dk_url="https://sportsbook.draftkings.com/leagues/baseball/mlb?category=pitcher-props&subcategory=strikeouts-thrown",
        fd_url="https://sportsbook.fanduel.com/navigation/mlb?tab=pitcher-props",
    ),
    "wnba_points": MarketConfig(
        key="player_points", name="WNBA Points", slug="wnba_pts", sport="basketball_wnba",
        subject="Player", your_side="Under", price_range=None, min_edge=0.02, min_books_on_line=4,
        dk_url="https://sportsbook.draftkings.com/leagues/basketball/wnba?category=player-points",
        fd_url="https://sportsbook.fanduel.com/navigation/wnba?tab=player-points",
    ),
}


# ---------------------------------------------------------------------------
# Odds math helpers
# ---------------------------------------------------------------------------
def format_odds(price):
    """Format american odds with + sign for positives."""
    if price is None:
        return "N/A"
    return f"+{price}" if price > 0 else str(price)


def implied_prob(price):
    """Convert american odds to implied probability (0-1)."""
    if price is None:
        return None
    if price > 0:
        return 100 / (price + 100)
    return -price / (-price + 100)


def decimal_odds(price):
    """Convert american odds to decimal odds (payout multiplier per $1 staked)."""
    if price is None:
        return None
    if price > 0:
        return price / 100 + 1
    return 100 / -price + 1


def american_from_prob(p):
    """Convert an implied probability (0-1) back to american odds (int)."""
    if not p or p <= 0 or p >= 1:
        return None
    if p >= 0.5:
        return -round(p / (1 - p) * 100)
    return round((1 - p) / p * 100)


def opposite_label(label):
    """Return the flip-side label for a line, e.g. 'Over 0.5' -> 'Under 0.5'."""
    if label.startswith("Over "):
        return "Under " + label[len("Over "):]
    if label.startswith("Under "):
        return "Over " + label[len("Under "):]
    return None


def player_search_url(player, market_name):
    """Google search jumping to a player's live prop odds on DK/FanDuel."""
    q = urllib.parse.quote_plus(f"{player} {market_name} prop odds draftkings fanduel")
    return f"https://www.google.com/search?q={q}"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def get_events(cfg):
    """Get today's events for cfg.sport. (Free - doesn't cost quota.) Returns (events, remaining)."""
    url = f"{BASE_URL}/sports/{cfg.sport}/events"
    resp = requests.get(url, params={"apiKey": API_KEY, "dateFormat": "iso"})
    resp.raise_for_status()
    remaining = resp.headers.get("x-requests-remaining")
    print(f"Events quota: {resp.headers.get('x-requests-used')} used / {remaining} remaining")
    try:
        remaining = int(remaining)
    except (TypeError, ValueError):
        remaining = None
    return resp.json(), remaining


def get_event_odds(cfg, event_id):
    """Get `cfg.key` odds for a specific event across all configured regions."""
    url = f"{BASE_URL}/sports/{cfg.sport}/events/{event_id}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": cfg.regions,
        "markets": cfg.key,
        "oddsFormat": ODDS_FORMAT,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json(), resp.headers


# ---------------------------------------------------------------------------
# Core: turn one event's odds into Novig value-edge records
# ---------------------------------------------------------------------------
def extract_edges(cfg, event_data, verbose=False):
    """
    Build per-player price maps for one game and return the Novig edge records.
    If verbose, also print the full per-book price table for the game.
    """
    home = event_data.get("home_team", "")
    away = event_data.get("away_team", "")
    bookmakers = event_data.get("bookmakers", [])
    if not bookmakers:
        return []

    # player -> {book -> {label -> price}}
    player_odds = {}
    for bm in bookmakers:
        bm_title = bm["title"]
        for market in bm.get("markets", []):
            if market["key"] != cfg.key:
                continue
            for outcome in market.get("outcomes", []):
                player = outcome.get("description", outcome.get("name", "Unknown"))
                side = outcome.get("name", "")
                price = outcome.get("price")
                point = outcome.get("point")
                label = f"{side} {point}" if point is not None else side
                player_odds.setdefault(player, {}).setdefault(bm_title, {})[label] = price

    if not player_odds:
        return []

    if verbose:
        _print_game_table(cfg, away, home, event_data.get("commence_time", ""), player_odds)

    edges = []
    for player, bm_data in player_odds.items():
        novig_lines = bm_data.get("Novig")
        if not novig_lines:
            continue
        for label, novig_price in novig_lines.items():
            other_prices = [
                (book, lines[label])
                for book, lines in bm_data.items()
                if book != "Novig" and label in lines
            ]
            if not other_prices:
                continue

            novig_dec = decimal_odds(novig_price)
            market_best_book, market_best_price = max(other_prices, key=lambda x: x[1])
            edge_pct = novig_dec / decimal_odds(market_best_price) - 1
            side = label.split(" ")[0]

            n_other_books = len(other_prices)
            n_books_better = sum(1 for _, p in other_prices if decimal_odds(p) > novig_dec)

            # Consensus = median of the OTHER books' implied probs -> back to american.
            other_probs = [implied_prob(p) for _, p in other_prices]
            consensus_prob = statistics.median(other_probs)
            consensus_price = american_from_prob(consensus_prob)
            consensus_dec = decimal_odds(consensus_price)
            edge_vs_consensus = novig_dec / consensus_dec - 1 if consensus_dec else 0.0

            # Novig's own price on the opposite side of the same line (so you can see both
            # sides of the Novig market at a glance), plus the best opposite-side price among
            # the hedge books (DK/FanDuel).
            opp_label = opposite_label(label)
            novig_opp_price = novig_lines.get(opp_label) if opp_label else None
            # Novig "hold" = the exchange's implied margin on this two-sided market
            # (implied prob of both sides minus 1). A free proxy for how tight/liquid the
            # market is: low/negative = tight & active, high = thin. NOT dollar depth.
            p_side = implied_prob(novig_price)
            p_opp = implied_prob(novig_opp_price)
            novig_hold = (p_side + p_opp - 1) if (p_side is not None and p_opp is not None) else None
            hedge_book, hedge_price = None, None
            if opp_label:
                cands = [
                    (book, bm_data[book][opp_label])
                    for book in cfg.hedge_books
                    if book in bm_data and opp_label in bm_data[book]
                ]
                if cands:
                    hedge_book, hedge_price = max(cands, key=lambda x: x[1])

            edges.append({
                "game": f"{away} @ {home}",
                "player": player,
                "label": label,
                "side": side,
                "novig_price": novig_price,
                "novig_opp_price": novig_opp_price,
                "novig_hold": novig_hold,
                "market_best_book": market_best_book,
                "market_best_price": market_best_price,
                "market_consensus_price": consensus_price,
                "edge_pct": edge_pct,
                "edge_vs_consensus": edge_vs_consensus,
                "n_books_better": n_books_better,
                "n_other_books": n_other_books,
                "hedge_book": hedge_book,
                "hedge_price": hedge_price,
            })

    return edges


def _print_game_table(cfg, away, home, commence, player_odds):
    """Print the full per-book price grid for one game (verbose mode only)."""
    print(f"\n{'='*70}")
    print(f"  {away} @ {home}  |  {commence[:16].replace('T', ' ')} UTC")
    print(f"{'='*70}")
    all_books = set()
    for bm_data in player_odds.values():
        all_books.update(bm_data.keys())
    books = [b for b in PREFERRED_BOOKS if b in all_books]
    books += sorted(b for b in all_books if b not in PREFERRED_BOOKS)

    col_w = 16
    print(f"  {'Player':<32}", end="")
    for b in books:
        print(f"  {b:<{col_w}}", end="")
    print()
    for player, bm_data in sorted(player_odds.items()):
        labels = set()
        for lines in bm_data.values():
            labels.update(lines.keys())
        for label in sorted(labels):
            print(f"  {player + ' ' + label:<32}", end="")
            for book in books:
                print(f"  {format_odds(bm_data.get(book, {}).get(label)):<{col_w}}", end="")
            print()


# ---------------------------------------------------------------------------
# Selection + outputs
# ---------------------------------------------------------------------------
def select_value_spots(edges, cfg):
    """
    Spots where Novig beats the market-consensus (median of other books on the same line)
    on cfg.your_side by at least cfg.min_edge, within cfg.price_range, with at least
    cfg.min_books_on_line other books on the line. Sorted by biggest edge first.
    """
    spots = [e for e in edges
             if e["side"] == cfg.your_side and e["edge_vs_consensus"] >= cfg.min_edge]
    if cfg.price_range:
        lo, hi = cfg.price_range
        spots = [e for e in spots if lo <= e["novig_price"] <= hi]
    if cfg.min_books_on_line:
        spots = [e for e in spots if e["n_other_books"] >= cfg.min_books_on_line]
    return sorted(spots, key=lambda e: e["edge_vs_consensus"], reverse=True)


def print_value_spots(spots, cfg, top_n=25):
    """Console table of the top value spots."""
    if not spots:
        print(f"\nNo Novig value spots found on {cfg.name} {cfg.your_side} matching the filters.")
        return
    opp = opposite_label(f"{cfg.your_side} 0.5").split(" ")[0]
    print(f"\n{'='*112}")
    print(f"  TOP NOVIG VALUE SPOTS — {cfg.name} {cfg.your_side} (Novig beats consensus; back it on Novig)")
    print(f"{'='*112}")
    header = (f"  {cfg.subject + ' / Line':<32}{'Novig ' + cfg.your_side[0]:<9}{'Novig ' + opp[0]:<9}"
              f"{'Consensus':<11}{'Edge%':<8}{'#Beat':<7}{'DK/FD ' + opp:<12}{'Game'}")
    print(header)
    print(f"  {'-'*32}{'-'*9}{'-'*9}{'-'*11}{'-'*8}{'-'*7}{'-'*12}{'-'*22}")
    for e in spots[:top_n]:
        hedge = (f"{format_odds(e['hedge_price'])} {e['hedge_book'][:2]}"
                 if e.get("hedge_price") is not None else "-")
        print(
            f"  {e['player'] + ' ' + e['label']:<32}"
            f"{format_odds(e['novig_price']):<9}"
            f"{format_odds(e['novig_opp_price']):<9}"
            f"{format_odds(e['market_consensus_price']):<11}"
            f"{e['edge_vs_consensus']*100:>5.1f}%  "
            f"{e['n_books_better']}/{e['n_other_books']:<4}"
            f"{hedge:<12}"
            f"{e['game']}"
        )


def write_edges_csv(edges, cfg):
    """Write all edges (not just top N) to a timestamped CSV for review/tracking."""
    if not edges:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    path = f"novig_{cfg.slug}_edges_{stamp}.csv"
    fieldnames = [
        "game", "player", "label", "side", "novig_price", "novig_opp_price", "novig_hold",
        "market_consensus_price", "market_best_book", "market_best_price",
        "edge_pct", "edge_vs_consensus", "n_books_better", "n_other_books",
        "hedge_book", "hedge_price",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for e in sorted(edges, key=lambda e: e["edge_vs_consensus"], reverse=True):
            row = dict(e)
            row["edge_pct"] = round(row["edge_pct"] * 100, 2)
            row["edge_vs_consensus"] = round(row["edge_vs_consensus"] * 100, 2)
            if row.get("novig_hold") is not None:
                row["novig_hold"] = round(row["novig_hold"] * 100, 2)
            writer.writerow(row)
    print(f"\nWrote {len(edges)} edges to {path}")


def write_html_report(spots, cfg, total_remaining=None, auto_open=True):
    """Render the value spots as a slick, self-contained, auto-opening HTML report."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    side = cfg.your_side
    opp = opposite_label(f"{side} 0.5").split(" ")[0]
    path = f"{cfg.slug}_report.html"

    rows_html = []
    for i, e in enumerate(spots, 1):
        edge = e["edge_vs_consensus"] * 100
        intensity = max(0.0, min(edge / 5.0, 1.0))
        bg = f"rgba(34,197,94,{0.12 + 0.45 * intensity:.2f})"
        cons_prob = implied_prob(e["market_consensus_price"])
        fair_opp = american_from_prob(1 - cons_prob) if cons_prob else None
        hedge = format_odds(e["hedge_price"]) if e.get("hedge_price") is not None else "—"
        hedge_book = (f' <span class="book">{html.escape(e["hedge_book"])}</span>'
                      if e.get("hedge_book") else "")
        rows_html.append(f"""
      <tr>
        <td class="rank">{i}</td>
        <td class="player"><a href="{player_search_url(e['player'], cfg.name)}" target="_blank" rel="noopener">{html.escape(e['player'])}</a>
            <span class="line">{html.escape(e['label'])}</span></td>
        <td class="game">{html.escape(e['game'])}</td>
        <td class="num novig">{format_odds(e['novig_price'])}</td>
        <td class="num novigopp">{format_odds(e['novig_opp_price'])}</td>
        <td class="num">{format_odds(e['market_consensus_price'])}</td>
        <td class="num edge" style="background:{bg}">+{edge:.1f}%</td>
        <td class="num">{e['n_books_better']}/{e['n_other_books']}</td>
        <td class="num hedge">{hedge}{hedge_book}</td>
        <td class="num fair">{format_odds(fair_opp)}</td>
      </tr>""")
    if not rows_html:
        rows_html.append('<tr><td colspan="10" class="empty">No spots matched the current filters.</td></tr>')

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{cfg.name} Bet Finder — {stamp}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0b0f17; color:#e6edf3; margin:0; padding:24px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:#8b98a9; font-size:13px; margin-bottom:18px; }}
  .bar {{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:18px; }}
  .btn {{ display:inline-block; padding:10px 16px; border-radius:8px; font-weight:600; text-decoration:none; font-size:14px; }}
  .dk {{ background:#53d337; color:#04120a; }}
  .fd {{ background:#1493ff; color:#02101f; }}
  .note {{ background:#141b27; border:1px solid #223; border-radius:8px; padding:12px 14px; font-size:13px; color:#b9c4d2; margin-bottom:18px; line-height:1.5; }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th, td {{ padding:9px 11px; text-align:left; border-bottom:1px solid #1c2533; }}
  th {{ color:#8b98a9; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; position:sticky; top:0; background:#0b0f17; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .rank {{ color:#6b7689; width:28px; }}
  .player a {{ color:#e6edf3; text-decoration:none; font-weight:600; }}
  .player a:hover {{ color:#53d337; text-decoration:underline; }}
  .line {{ color:#8b98a9; font-weight:400; font-size:12px; margin-left:6px; }}
  .game {{ color:#8b98a9; font-size:12px; }}
  .novig {{ color:#53d337; font-weight:700; }}
  .novigopp {{ color:#8fb8a0; }}
  .book {{ color:#6b7689; font-size:11px; }}
  .edge {{ font-weight:700; border-radius:5px; }}
  .hedge {{ color:#e6edf3; font-weight:600; }}
  .fair {{ color:#1493ff; }}
  .empty {{ text-align:center; color:#6b7689; padding:24px; }}
</style></head>
<body>
  <h1>{cfg.name} Bet Finder</h1>
  <div class="sub">{stamp} &nbsp;·&nbsp; your side on Novig: <b>{side}</b> &nbsp;·&nbsp; {len(spots)} spots &nbsp;·&nbsp; API quota left: {total_remaining or 'n/a'}</div>
  <div class="bar">
    <a class="btn dk" href="{cfg.dk_url}" target="_blank" rel="noopener">▸ Open DraftKings board</a>
    <a class="btn fd" href="{cfg.fd_url}" target="_blank" rel="noopener">▸ Open FanDuel board</a>
  </div>
  <div class="note">
    Spots where <b>Novig's {side} price beats the market consensus</b> on the same line — Novig is the value side, so
    <b>back {side} on Novig</b> at the best available price. <b>DK/FD {opp}</b> is DraftKings/FanDuel's live price on the
    opposite side (their actual hedge price; blank if they don't carry this market). Bet the <b>{opp}</b> there — ideally
    with a <b>boost</b> on top (boosts aren't in any feed, check in-app). <b>Fair {opp}</b> is the consensus {opp} price;
    your boosted DK/FD {opp} should beat it for a clean arb. <b>Edge</b> = how much more Novig pays vs <b>Consensus</b>
    (median of the other books on that line, robust to one outlier). <b>#Beat</b> = other books pricing {side} better
    than Novig (lower is better).
  </div>
  <table>
    <thead><tr>
      <th>#</th><th>{cfg.subject} / Line</th><th>Game</th>
      <th class="num">Novig {side}</th><th class="num">Novig {opp}</th><th class="num">Consensus</th>
      <th class="num">Edge</th><th class="num">#Beat</th><th class="num">DK/FD {opp}</th><th class="num">Fair {opp}</th>
    </tr></thead>
    <tbody>{''.join(rows_html)}
    </tbody>
  </table>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    abspath = os.path.abspath(path)
    print(f"Wrote HTML report to {abspath}")
    if auto_open:
        try:
            os.startfile(abspath)  # Windows: opens in default browser
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run(cfg, top_n=25, auto_open=True, verbose=False):
    """Fetch today's games, compute edges, and emit console table + CSV + HTML report."""
    print(f"\n{cfg.name} Bet Finder — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Market: {cfg.key} | regions: {cfg.regions} | side: {cfg.your_side} | "
          f"min edge: {cfg.min_edge*100:.1f}% | min books/line: {cfg.min_books_on_line} | "
          f"price band: {cfg.price_range or 'any'}")

    if API_KEY == "YOUR_API_KEY_HERE":
        print("\nERROR: set your key first:  $env:ODDS_API_KEY=\"...\"  (PowerShell)")
        return []

    events, remaining = get_events(cfg)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_events = [e for e in events if e.get("commence_time", "").startswith(today)]
    n_regions = len(cfg.regions.split(","))
    est_cost = len(today_events) * n_regions
    print(f"Found {len(today_events)} games today (of {len(events)} upcoming).")
    print(f"Estimated cost: {len(today_events)} games x {n_regions} regions = ~{est_cost} credits"
          + (f"  (have {remaining})" if remaining is not None else ""))
    if not today_events:
        print("No games today.")
        return []
    if remaining is not None and remaining <= 0:
        print("Quota is exhausted (0 remaining) — not spending calls. Leaving any prior report intact.")
        return []

    total_remaining, all_edges = remaining, []
    for event in today_events:
        try:
            event_data, headers = get_event_odds(cfg, event["id"])
            total_remaining = headers.get("x-requests-remaining")
            all_edges.extend(extract_edges(cfg, event_data, verbose=verbose))
        except requests.HTTPError as e:
            # 401 here means quota ran out mid-run - stop rather than hammer the API.
            if e.response is not None and e.response.status_code == 401:
                print(f"  Quota exhausted mid-run (401) — stopping after {len(all_edges)} edges collected.")
                break
            print(f"  Error fetching {event.get('home_team')}: {e}")

    print(f"\nQuota remaining after this run: {total_remaining}")

    # Don't clobber a good prior report/CSV with an empty run (e.g. all calls failed on quota).
    if not all_edges:
        print("No odds data fetched (quota or errors) — leaving previous report/CSV untouched.")
        return []

    spots = select_value_spots(all_edges, cfg)
    print_value_spots(spots, cfg, top_n=top_n)
    write_edges_csv(all_edges, cfg)
    write_html_report(spots, cfg, total_remaining=total_remaining,
                      auto_open=auto_open and not os.environ.get("HR_NO_OPEN"))
    return spots


def _build_cli_config(args):
    """Apply command-line overrides on top of a market's registry defaults."""
    cfg = MARKETS[args.market]
    overrides = {}
    if args.over:
        overrides["your_side"] = "Over"
    if args.under:
        overrides["your_side"] = "Under"
    if args.min_edge is not None:
        overrides["min_edge"] = args.min_edge / 100.0
    if args.min_books is not None:
        overrides["min_books_on_line"] = args.min_books
    if args.min_price is not None or args.max_price is not None:
        lo = args.min_price if args.min_price is not None else -100000
        hi = args.max_price if args.max_price is not None else 100000
        overrides["price_range"] = (lo, hi)
    return replace(cfg, **overrides) if overrides else cfg


def main():
    parser = argparse.ArgumentParser(description="Find Novig value spots in an MLB prop market.")
    parser.add_argument("market", nargs="?", choices=sorted(MARKETS), help="which market to scan")
    parser.add_argument("--list", action="store_true", help="list available markets and exit")
    side = parser.add_mutually_exclusive_group()
    side.add_argument("--under", action="store_true", help="back the Under (default for most markets)")
    side.add_argument("--over", action="store_true", help="back the Over instead")
    parser.add_argument("--min-edge", type=float, metavar="PCT",
                        help="min edge over consensus, in percent (e.g. 2 = 2%%)")
    parser.add_argument("--min-books", type=int, metavar="N",
                        help="min OTHER books required on the same line")
    parser.add_argument("--min-price", type=int, metavar="ODDS", help="min American odds for Novig's price")
    parser.add_argument("--max-price", type=int, metavar="ODDS", help="max American odds for Novig's price")
    parser.add_argument("--top", type=int, default=25, help="rows to show in the console table")
    parser.add_argument("--no-open", action="store_true", help="write the HTML report but don't open it")
    parser.add_argument("--verbose", action="store_true", help="also print the full per-game price grid")
    args = parser.parse_args()

    if args.list or not args.market:
        print("Available markets:")
        for name, m in sorted(MARKETS.items()):
            print(f"  {name:<16} {m.key:<32} default: {m.your_side}, "
                  f"edge>={m.min_edge*100:.1f}%, books>={m.min_books_on_line}")
        if not args.market:
            print("\nUsage: python oddsfinder.py <market> [options]   (see --help)")
        return

    cfg = _build_cli_config(args)
    run(cfg, top_n=args.top, auto_open=not args.no_open, verbose=args.verbose)


if __name__ == "__main__":
    main()
