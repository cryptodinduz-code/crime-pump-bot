"""
Crypto Crime Bot 🚨  —  v5  "TOSSICO+" Edition
Monitors BloFin and MEXC perpetual futures for pre-pump setups.

Crime Score (0–100):
  +35  Extreme funding rate  (> 0.03% or < -0.03%)
  +25  15m price move > 3%  (3 × 5m candles)
  +20  5m volume spike > 4×  (latest candle vs avg of prior)
  +20  Open Interest grew > 15% in last minute
  +15  Futures/Spot volume ratio > 3×
  +15  Spot volume > 50% above rolling baseline
  +10  Low market cap proxy (futures 24h vol < $50M)
  +10  Order book buy pressure (bid > ask depth by > 20%)

Alert fires when Crime Score ≥ 60.
Telegram alert is sent for every new hit (deduped by score per symbol).

Architecture:
  - Flask web server on port 8080 (UptimeRobot health check)
  - Bot scan loop runs in a background thread
  - State (OI history, vol baselines, alert history) saved to memory.json every 10 min
  - State loaded on startup so signals survive restarts

No exchange API keys needed — all public data via ccxt.
"""

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────
import ccxt
import os
import time
import json
import datetime
import threading
import requests
from collections import deque
from tabulate import tabulate
from flask import Flask

# ─────────────────────────────────────────────────────────────────────────────
# FLASK  — lightweight web server so UptimeRobot can ping us
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    """Health check endpoint — returns bot status as plain text."""
    uptime = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        f"🤖 Crypto Crime Bot v5 is alive!\n"
        f"Server time: {uptime}\n"
        f"Exchanges: BloFin + MEXC\n"
        f"Alert threshold: score >= {ALERT_MIN_SCORE}"
    )

@app.route("/status")
def status():
    """Returns a JSON summary of current bot state."""
    return {
        "alive": True,
        "tracked_symbols": len(prev_oi),
        "vol_baselines": len(vol_history),
        "alerted_symbols": len(alerted),
        "last_save": last_save_time,
    }

def run_web():
    """Run Flask in its own thread. Suppress default werkzeug logs."""
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=8099, use_reloader=False)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
LOOP_SECONDS         = 60        # scan interval
MEMORY_FILE          = "memory.json"
SAVE_EVERY_N_RUNS    = 10        # save state every N runs (= ~10 minutes)

# Score weights
W_FUNDING            = 35
W_PRICE_MOVE         = 25
W_VOL_SPIKE          = 20
W_OI_GROWTH          = 20
W_FS_RATIO           = 15
W_SPOT_VOL_BASELINE  = 15
W_LOW_MCAP           = 10
W_ORDERBOOK          = 10

SCORE_MAX_RAW        = 150       # theoretical max (normalised to 100)
ALERT_MIN_SCORE      = 60

# Thresholds
FUNDING_THRESHOLD    = 0.0003    # 0.03% decimal
PRICE_MOVE_THRESHOLD = 3.0       # % across 3 × 5m candles
VOL_SPIKE_MULT       = 4.0       # latest candle vs avg of prior
OI_GROWTH_THRESHOLD  = 15.0      # % OI increase vs last run
FS_RATIO_THRESHOLD   = 3.0       # futures/spot vol ratio
SPOT_VOL_PREMIUM_PCT = 50.0      # spot vol % above rolling baseline
LOW_MCAP_VOL_PROXY   = 50_000_000  # $50M futures vol proxy for small-cap
OB_BUY_PRESSURE_PCT  = 20.0      # bid depth > ask depth by this %
OB_LEVELS            = 10

CANDLE_TIMEFRAME     = "5m"
NUM_CANDLES          = 3

PREFILTER_CHANGE_PCT = 2.0       # skip 24h movers below this
MAX_INVESTIGATE      = 60        # max symbols deeply scanned per exchange
VOL_HISTORY_LEN      = 168       # max readings per symbol (~2.8h at 1min)

TOP_N                = 10

EXCHANGE_LINKS = {
    "BloFin": "https://www.blofin.com/futures/{pair}",
    "MEXC":   "https://futures.mexc.com/exchange/{pair}",
}

# Telegram
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_ENABLED       = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


# ─────────────────────────────────────────────────────────────────────────────
# ROLLING STATE  — persists across loop iterations AND restarts (via memory.json)
# ─────────────────────────────────────────────────────────────────────────────
prev_oi:       dict[str, float]  = {}   # OI value from previous run
vol_history:   dict[str, deque]  = {}   # rolling spot vol readings per symbol
alerted:       dict[str, int]    = {}   # last score Telegram'd per symbol (dedup)
last_save_time: str              = "never"


# ─────────────────────────────────────────────────────────────────────────────
# MEMORY  — save / load state to JSON so history survives restarts
# ─────────────────────────────────────────────────────────────────────────────
def save_memory():
    """
    Serialize state dictionaries to memory.json.
    deque objects are converted to plain lists for JSON compatibility.
    """
    global last_save_time
    try:
        data = {
            "prev_oi":     prev_oi,
            # Convert each deque to a list so json.dump can handle it
            "vol_history": {k: list(v) for k, v in vol_history.items()},
            "alerted":     alerted,
            "saved_at":    datetime.datetime.utcnow().isoformat(),
        }
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f)
        last_save_time = data["saved_at"]
        print(f"  [MEM] State saved to {MEMORY_FILE} ({len(prev_oi)} OI entries, "
              f"{len(vol_history)} vol histories)")
    except Exception as e:
        print(f"  [MEM] Save failed: {e}")


def load_memory():
    """
    Load state from memory.json on startup.
    Converts vol_history lists back to deque objects with the correct maxlen.
    """
    global prev_oi, vol_history, alerted, last_save_time
    if not os.path.exists(MEMORY_FILE):
        print(f"  [MEM] No {MEMORY_FILE} found — starting fresh.")
        return
    try:
        with open(MEMORY_FILE) as f:
            data = json.load(f)
        prev_oi  = data.get("prev_oi", {})
        # Restore deques with the correct maxlen
        vol_history = {
            k: deque(v, maxlen=VOL_HISTORY_LEN)
            for k, v in data.get("vol_history", {}).items()
        }
        alerted = data.get("alerted", {})
        saved_at = data.get("saved_at", "unknown")
        last_save_time = saved_at
        print(f"  [MEM] Loaded from {MEMORY_FILE} (saved {saved_at})")
        print(f"  [MEM] Restored: {len(prev_oi)} OI entries, "
              f"{len(vol_history)} vol histories, {len(alerted)} alert records")
    except Exception as e:
        print(f"  [MEM] Load failed ({e}) — starting fresh.")


# ─────────────────────────────────────────────────────────────────────────────
# EXCHANGE SETUP
# ─────────────────────────────────────────────────────────────────────────────
def make_exchanges() -> dict:
    """Futures (swap) exchange instances — public access only."""
    return {
        "BloFin": ccxt.blofin({"enableRateLimit": True, "options": {"defaultType": "swap"}}),
        "MEXC":   ccxt.mexc({"enableRateLimit": True,   "options": {"defaultType": "swap"}}),
    }

def make_spot_exchanges() -> dict:
    """Spot exchange instances for Futures/Spot ratio and vol baseline."""
    return {
        "BloFin": ccxt.blofin({"enableRateLimit": True}),
        "MEXC":   ccxt.mexc({"enableRateLimit": True}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def candle_vol_spike(candles: list) -> tuple[bool, float]:
    """Latest candle volume >= VOL_SPIKE_MULT × avg of prior candles?"""
    if len(candles) < 2:
        return False, 0.0
    vols      = [c[5] for c in candles]
    avg_prior = sum(vols[:-1]) / len(vols[:-1])
    if avg_prior == 0:
        return False, 0.0
    ratio = vols[-1] / avg_prior
    return ratio >= VOL_SPIKE_MULT, round(ratio, 2)


def candle_price_move(candles: list) -> tuple[bool, float]:
    """Price moved > PRICE_MOVE_THRESHOLD% from open(first) to close(last)?"""
    if len(candles) < 2:
        return False, 0.0
    o, c = candles[0][1], candles[-1][4]
    if o == 0:
        return False, 0.0
    pct = abs((c - o) / o) * 100
    return pct >= PRICE_MOVE_THRESHOLD, round(pct, 2)


def calc_oi_change(exchange_name: str, symbol: str, current_oi: float | None) -> float | None:
    """% OI change vs last run. Updates prev_oi in place."""
    key    = f"{exchange_name}:{symbol}"
    change = None
    if current_oi is not None and key in prev_oi and prev_oi[key]:
        change = ((current_oi - prev_oi[key]) / abs(prev_oi[key])) * 100
    if current_oi is not None:
        prev_oi[key] = current_oi
    return change


def calc_spot_vol_vs_baseline(exchange_name: str, symbol: str, spot_vol: float | None) -> float | None:
    """
    % deviation of current spot 24h vol vs rolling average of prior readings.
    Positive = above baseline (more vol than usual).
    Needs at least 2 readings before returning a value.
    """
    if spot_vol is None:
        return None
    key = f"{exchange_name}:{symbol}:spot_vol"
    if key not in vol_history:
        vol_history[key] = deque(maxlen=VOL_HISTORY_LEN)
    vol_history[key].append(spot_vol)
    history = list(vol_history[key])
    if len(history) < 2:
        return None
    baseline = sum(history[:-1]) / len(history[:-1])
    return None if baseline == 0 else ((spot_vol - baseline) / baseline) * 100


def calc_ob_pressure(exchange, symbol: str) -> tuple[bool, float | None]:
    """
    Fetch top OB_LEVELS order book levels and compute bid/ask depth ratio.
    Returns (buy_pressure_hit, pct_difference). Safe — never crashes.
    """
    try:
        ob      = exchange.fetch_order_book(symbol, limit=OB_LEVELS)
        bid_vol = sum(lvl[1] for lvl in ob.get("bids", []))
        ask_vol = sum(lvl[1] for lvl in ob.get("asks", []))
        if ask_vol == 0:
            return False, None
        pct = (bid_vol / ask_vol - 1) * 100
        return pct >= OB_BUY_PRESSURE_PCT, round(pct, 1)
    except Exception:
        return False, None


def crime_score_and_signals(
    fr, price_moved, vol_spiked, oi_change,
    fs_ratio, spot_vs_base, futures_vol, ob_pressure,
) -> tuple[int, list[str]]:
    """
    Compute Crime Score (0–100) and collect human-readable signal strings.
    Each signal is binary — full points or nothing.
    Raw score is normalised to 100 via SCORE_MAX_RAW.
    """
    raw, sigs = 0, []

    if fr is not None and abs(fr) > FUNDING_THRESHOLD:
        raw += W_FUNDING
        label = "very extreme" if abs(fr) > 0.001 else "extreme"
        sigs.append(f"Funding rate {label} ({fr*100:+.4f}%)")

    if price_moved:
        raw += W_PRICE_MOVE
        sigs.append("Price moved >3% in last 15 minutes")

    if vol_spiked:
        raw += W_VOL_SPIKE
        sigs.append("Volume spike on latest 5m candle")

    if oi_change is not None and oi_change > OI_GROWTH_THRESHOLD:
        raw += W_OI_GROWTH
        sigs.append(f"OI surging fast (+{oi_change:.1f}%)")

    if fs_ratio is not None and fs_ratio >= FS_RATIO_THRESHOLD:
        raw += W_FS_RATIO
        sigs.append(f"Futures/Spot ratio {fs_ratio:.1f}x 🔥")

    if spot_vs_base is not None and spot_vs_base >= SPOT_VOL_PREMIUM_PCT:
        raw += W_SPOT_VOL_BASELINE
        sigs.append(f"Spot volume {spot_vs_base:.0f}% above baseline")

    if futures_vol is not None and futures_vol < LOW_MCAP_VOL_PROXY:
        raw += W_LOW_MCAP
        sigs.append("Small-cap / low liquidity token (+bonus)")

    if ob_pressure:
        raw += W_ORDERBOOK
        sigs.append("Order book: heavy buy-side pressure")

    return min(100, int(raw * 100 / SCORE_MAX_RAW + 0.5)), sigs


def crime_probability_label(score: int) -> str:
    if score >= 90: return "Extreme 🔴"
    if score >= 75: return "High 🟠"
    if score >= 60: return "Medium 🟡"
    return "Low ⚪"


# ─────────────────────────────────────────────────────────────────────────────
# LINK BUILDERS
# ─────────────────────────────────────────────────────────────────────────────
def futures_link(exchange_name: str, symbol: str) -> str:
    pair     = symbol.split(":")[0]
    template = EXCHANGE_LINKS.get(exchange_name, "")
    if not template:
        return ""
    formatted = pair.replace("/", "") if exchange_name == "BloFin" else pair.replace("/", "_")
    return template.format(pair=formatted)

def cross_exchange_link(primary: str, symbol: str) -> str:
    other = "BloFin" if primary == "MEXC" else "MEXC"
    return futures_link(other, symbol)


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def fmt_fr(fr)   -> str: return "N/A" if fr is None else f"{fr*100:+.5f}%"
def fmt_oi(v)    -> str: return "N/A" if v is None else (f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%")
def fmt_ratio(v) -> str: return "N/A" if v is None else f"{v:.1f}x"
def fmt_svb(v)   -> str: return "N/A" if v is None else (f"+{v:.0f}%" if v >= 0 else f"{v:.0f}%")
def fmt_vol(v)   -> str:
    if v is None:          return "N/A"
    if v >= 1_000_000:     return f"${v/1_000_000:.2f}M"
    if v >= 1_000:         return f"${v/1_000:.1f}K"
    return f"${v:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def tg_send(text: str):
    """Send HTML-formatted message to the group. Silent on failure."""
    if not TG_ENABLED:
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        if not resp.ok:
            print(f"  [TG] Send failed: {resp.text[:200]}")
    except Exception as e:
        print(f"  [TG] Error: {e}")


def tg_startup():
    tg_send(
        f"🤖 <b>Crime Bot v5 online</b>\n"
        f"Exchanges: BloFin + MEXC  |  Threshold: Score ≥ {ALERT_MIN_SCORE}\n"
        f"Scanning every {LOOP_SECONDS}s — Memory persistence active"
    )


def tg_alert(c: dict):
    """
    Send professional Telegram alert for one crime candidate.
    Deduped — fires only if score is higher than last alert for this symbol.
    """
    key = f"{c['exchange']}:{c['symbol']}"
    if alerted.get(key, 0) >= c["score"]:
        return
    alerted[key] = c["score"]

    pair     = c["symbol"].split(":")[0].replace("/", "-")
    fr_pct   = (c["fr"] or 0) * 100
    sign     = "+" if c["change_24h"] >= 0 else ""
    prob     = crime_probability_label(c["score"])
    bullets  = "\n".join(f"  • {s}" for s in c["signals"]) or "  • No secondary signals"

    main_link  = futures_link(c["exchange"], c["symbol"])
    other_link = cross_exchange_link(c["exchange"], c["symbol"])
    base_token = c["symbol"].split("/")[0].lower()
    bubble_url = f"https://app.bubblemaps.io/eth/token/{base_token}"

    link_lines = []
    if main_link:  link_lines.append(f"• {c['exchange']} Futures: {main_link}")
    if other_link: link_lines.append(f"• {'BloFin' if c['exchange']=='MEXC' else 'MEXC'}: {other_link}")
    link_lines.append(f"• Bubblemaps (ETH): {bubble_url}")

    oi_str  = f"+{c['oi_chg']:.1f}%" if c["oi_chg"] is not None else "N/A"
    fs_str  = f"{c['fs_ratio']:.1f}x 🔥" if c["fs_ratio"] is not None else "N/A"
    ob_str  = f"Buy pressure +{c['ob_pct']:.0f}% (bids > asks)" if c["ob_pressure"] and c["ob_pct"] else "Neutral / N/A"
    svb_str = f"+{c['spot_vs_base']:.0f}% above baseline" if c["spot_vs_base"] is not None else "N/A"

    tg_send(
        f"🚨 <b>CRIME ALERT (Score: {c['score']}/100)</b>\n"
        f"🔥 <b>{pair}</b> on {c['exchange']}\n\n"
        f"<b>Price:</b> ${c['last_price']:.6g}\n"
        f"<b>24h Futures Vol:</b> {fmt_vol(c['futures_vol'])}\n"
        f"<b>24h Spot Vol:</b> {fmt_vol(c['spot_vol'])} ({svb_str})\n"
        f"<b>Futures/Spot Ratio:</b> {fs_str}\n"
        f"<b>Open Interest:</b> {fmt_vol(c['oi_value'])} ({oi_str} in 1min)\n"
        f"<b>Funding Rate:</b> {fr_pct:+.5f}%\n"
        f"<b>Order Book:</b> {ob_str}\n\n"
        f"<b>Signals:</b>\n{bullets}\n\n"
        f"<b>Links:</b>\n" + "\n".join(link_lines) + "\n\n"
        f"<b>Crime Probability:</b> {prob}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# EXCHANGE SCAN
# ─────────────────────────────────────────────────────────────────────────────
def scan_exchange(ex_futures, ex_spot, name: str) -> list[dict]:
    """
    Full scan of one exchange in 5 stages:
      1. Bulk futures tickers  → pre-filter to movers (1 API call)
      2. Bulk spot tickers     → for Futures/Spot ratio + vol baseline
      3. Funding rates for shortlisted symbols
      4. Candles + OI + order book for extreme-funding candidates only
      5. Score everything, return records
    """
    # Load futures markets
    try:
        markets  = ex_futures.load_markets()
        all_syms = [s for s, m in markets.items()
                    if m.get("swap") and m.get("active") and "USDT" in s]
        print(f"  [{name}] {len(all_syms)} USDT perp markets.")
    except Exception as e:
        print(f"  [{name}] Market load error: {e}")
        return []

    # Stage 1 — bulk futures tickers
    try:
        ftickers = ex_futures.fetch_tickers()
        print(f"  [{name}] {len(ftickers)} futures tickers.")
    except Exception as e:
        print(f"  [{name}] Futures ticker error: {e}")
        ftickers = {}

    shortlist = [s for s in all_syms
                 if ftickers.get(s) and abs(ftickers[s].get("percentage") or 0) >= PREFILTER_CHANGE_PCT]
    if len(shortlist) < 10:
        shortlist = sorted(
            [s for s in all_syms if ftickers.get(s) and ftickers[s].get("percentage") is not None],
            key=lambda s: abs(ftickers[s].get("percentage") or 0), reverse=True
        )[:MAX_INVESTIGATE]
    shortlist = shortlist[:MAX_INVESTIGATE]
    print(f"  [{name}] Investigating {len(shortlist)} symbols.")

    # Stage 2 — bulk spot tickers
    stickers: dict = {}
    try:
        spot_syms = list({s.split(":")[0] for s in shortlist})
        stickers  = ex_spot.fetch_tickers(spot_syms)
    except Exception:
        try:
            stickers = ex_spot.fetch_tickers()
        except Exception:
            stickers = {}
    print(f"  [{name}] {len(stickers)} spot tickers.")

    # Stage 3 — funding rates
    fr_map: dict[str, float | None] = {}
    try:
        bulk = ex_futures.fetch_funding_rates(shortlist)
        for sym, fr in bulk.items():
            fr_map[sym] = fr.get("fundingRate")
        print(f"  [{name}] Funding rates: bulk OK.")
    except Exception:
        print(f"  [{name}] Fetching {len(shortlist)} funding rates individually…")
        for i, sym in enumerate(shortlist):
            try:
                fr_map[sym] = ex_futures.fetch_funding_rate(sym).get("fundingRate")
            except Exception:
                fr_map[sym] = None
            if (i + 1) % 10 == 0:
                print(f"  [{name}]   {i+1}/{len(shortlist)}…", end="\r")
        print(f"  [{name}]   Done.                          ")

    # Stage 4 — candles + OI + order book (extreme funding only)
    extreme    = [s for s in shortlist if fr_map.get(s) is not None and abs(fr_map[s]) > FUNDING_THRESHOLD]
    candle_map: dict[str, list]          = {}
    oi_map:     dict[str, float | None]  = {}
    ob_map:     dict[str, tuple]         = {}

    for i, sym in enumerate(extreme):
        try:
            candle_map[sym] = ex_futures.fetch_ohlcv(sym, timeframe=CANDLE_TIMEFRAME, limit=NUM_CANDLES)
        except Exception:
            candle_map[sym] = []
        try:
            oi_data = ex_futures.fetch_open_interest(sym)
            oi_map[sym] = oi_data.get("openInterestValue") or oi_data.get("openInterest")
        except Exception:
            oi_map[sym] = None
        ob_map[sym] = calc_ob_pressure(ex_futures, sym)
        if (i + 1) % 5 == 0:
            print(f"  [{name}]   detail {i+1}/{len(extreme)}…", end="\r")
    if extreme:
        print(f"  [{name}]   detail fetch done.               ")

    # Stage 5 — score and build records
    records = []
    for sym in shortlist:
        ft          = ftickers.get(sym, {})
        st          = stickers.get(sym.split(":")[0], {})
        fr          = fr_map.get(sym)
        candles     = candle_map.get(sym, [])
        current_oi  = oi_map.get(sym)
        ob_hit, ob_pct = ob_map.get(sym, (False, None))

        change_24h  = ft.get("percentage") or 0.0
        last_price  = ft.get("last")
        futures_vol = ft.get("quoteVolume")
        spot_vol    = st.get("quoteVolume")

        fs_ratio = round(futures_vol / spot_vol, 2) if futures_vol and spot_vol and spot_vol > 0 else None
        spot_vs_base = calc_spot_vol_vs_baseline(name, sym, spot_vol)
        oi_chg       = calc_oi_change(name, sym, current_oi)
        vol_spiked,  vol_ratio = candle_vol_spike(candles)
        price_moved, move_pct  = candle_price_move(candles)

        score, signals = crime_score_and_signals(
            fr, price_moved, vol_spiked, oi_chg,
            fs_ratio, spot_vs_base, futures_vol, ob_hit,
        )

        records.append({
            "exchange": name, "symbol": sym, "score": score, "signals": signals,
            "fr": fr, "change_24h": change_24h, "move_pct": move_pct,
            "vol_ratio": vol_ratio, "vol_spiked": vol_spiked, "price_moved": price_moved,
            "oi_chg": oi_chg, "oi_value": current_oi, "fs_ratio": fs_ratio,
            "spot_vol": spot_vol, "futures_vol": futures_vol,
            "spot_vs_base": spot_vs_base, "ob_pressure": ob_hit, "ob_pct": ob_pct,
            "last_price": last_price,
        })

    crimes = sum(1 for r in records if r["score"] >= ALERT_MIN_SCORE)
    print(f"  [{name}] Done → {crimes} alert(s) (score ≥ {ALERT_MIN_SCORE}).\n")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# CONSOLE OUTPUT
# ─────────────────────────────────────────────────────────────────────────────
def print_alerts(candidates: list[dict]):
    alerts = sorted([c for c in candidates if c["score"] >= ALERT_MIN_SCORE],
                    key=lambda x: x["score"], reverse=True)
    if not alerts:
        print(f"\n  ✅ No crime signals ≥ {ALERT_MIN_SCORE} this round.\n")
        return

    print(f"\n{'='*72}")
    print(f"  🚨  {len(alerts)} CRIME ALERT(S)  —  Telegram: {'yes' if TG_ENABLED else 'no'}")
    print(f"{'='*72}")

    for c in alerts:
        pair = c["symbol"].split(":")[0].replace("/", "-")
        sign = "+" if c["change_24h"] >= 0 else ""
        sigs = "\n     ".join(c["signals"]) or "—"
        print(
            f"\n  🚨 CRIME ALERT (Score: {c['score']}/100)\n"
            f"  🔥 {pair} on {c['exchange']}\n\n"
            f"     Price:         ${c['last_price']:.6g}\n"
            f"     Futures Vol:   {fmt_vol(c['futures_vol'])}\n"
            f"     Spot Vol:      {fmt_vol(c['spot_vol'])} (vs baseline: {fmt_svb(c['spot_vs_base'])})\n"
            f"     Futures/Spot:  {fmt_ratio(c['fs_ratio'])}\n"
            f"     Open Interest: {fmt_vol(c['oi_value'])} ({fmt_oi(c['oi_chg'])} 1min)\n"
            f"     Funding:       {fmt_fr(c['fr'])}\n"
            f"     15m move:      {sign}{c['move_pct']:.2f}%\n"
            f"     Vol spike:     {c['vol_ratio']:.1f}x {'🔥' if c['vol_spiked'] else ''}\n"
            f"     Order book:    {'Buy pressure +' + str(c['ob_pct']) + '%' if c['ob_pressure'] else 'Neutral'}\n\n"
            f"     Signals:  {sigs}\n"
            f"     Prob:     {crime_probability_label(c['score'])}\n"
            f"     Link:     {futures_link(c['exchange'], c['symbol'])}"
        )
        tg_alert(c)   # send to Telegram (deduped internally)
    print()


def print_top_table(candidates: list[dict]):
    ranked = sorted(candidates, key=lambda x: x["score"], reverse=True)[:TOP_N]
    if not ranked:
        print("  (no data)\n")
        return
    rows = []
    for c in ranked:
        pair = c["symbol"].split(":")[0].replace("/", "-")
        sign = "+" if c["change_24h"] >= 0 else ""
        rows.append([
            c["score"], pair, c["exchange"], fmt_fr(c["fr"]),
            f"{sign}{c['change_24h']:.1f}%", f"{c['move_pct']:.2f}%",
            f"{c['vol_ratio']:.1f}x", fmt_ratio(c["fs_ratio"]),
            fmt_oi(c["oi_chg"]), "🚨" if c["score"] >= ALERT_MIN_SCORE else "·",
        ])
    headers = ["Score", "Pair", "Exchange", "Funding", "24h", "15m", "Vol", "F/S", "OI Δ", "!"]
    print(f"\n{'─'*80}")
    print(f"  📊 TOP {TOP_N} BY CRIME SCORE")
    print(f"{'─'*80}")
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# BOT MAIN LOOP  — runs inside a background thread
# ─────────────────────────────────────────────────────────────────────────────
def bot_loop():
    """Main scanning loop. Runs forever in a daemon thread."""
    print("🤖 Crypto Crime Bot v5 — TOSSICO+ Edition")
    print(f"   Telegram: {'✅ enabled' if TG_ENABLED else '⚠️  disabled'}")
    print(f"   Memory file: {MEMORY_FILE} (saved every {SAVE_EVERY_N_RUNS} runs)")
    print(f"   Score weights: Funding={W_FUNDING} | Price={W_PRICE_MOVE} | Vol={W_VOL_SPIKE}"
          f" | OI={W_OI_GROWTH} | F/S={W_FS_RATIO} | SpotBase={W_SPOT_VOL_BASELINE}"
          f" | LowCap={W_LOW_MCAP} | OB={W_ORDERBOOK}")
    print(f"   Alert threshold: score ≥ {ALERT_MIN_SCORE}/100")
    print(f"   Candles: {NUM_CANDLES} × {CANDLE_TIMEFRAME} | Loop: every {LOOP_SECONDS}s\n")

    # Load persisted memory before first scan
    load_memory()

    if TG_ENABLED:
        tg_startup()

    futures_exchanges = make_exchanges()
    spot_exchanges    = make_spot_exchanges()
    run_number        = 0

    while True:
        run_number += 1
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n{'#'*80}")
        print(f"  🔍 RUN #{run_number} — {now}")
        if run_number == 1:
            print("  ℹ️  First run: OI + vol baseline recording (OI Δ active from run #2)")
        print(f"{'#'*80}\n")

        all_records: list[dict] = []
        for name in futures_exchanges:
            print(f"⛏️  {name}…")
            try:
                results = scan_exchange(futures_exchanges[name], spot_exchanges[name], name)
                all_records.extend(results)
            except Exception as e:
                print(f"  [{name}] Unhandled error: {e}\n")

        print_alerts(all_records)
        print_top_table(all_records)

        # Save state every SAVE_EVERY_N_RUNS runs (~10 minutes)
        if run_number % SAVE_EVERY_N_RUNS == 0:
            save_memory()

        next_run = datetime.datetime.utcnow() + datetime.timedelta(seconds=LOOP_SECONDS)
        print(f"💤 Next scan at {next_run.strftime('%H:%M:%S UTC')} (in {LOOP_SECONDS}s)…\n")
        time.sleep(LOOP_SECONDS)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Start the bot scan loop in a background thread so Flask can bind
    # to port 8080 immediately — the workflow health check needs this fast.
    bot_thread = threading.Thread(target=bot_loop, daemon=True, name="bot-loop")
    bot_thread.start()
    print("🌐 Starting web server on port 8099 (UptimeRobot health check)…")

    # Flask runs in the main thread — binds port 8080 right away.
    # daemon bot_thread dies automatically when this process exits.
    run_web()
