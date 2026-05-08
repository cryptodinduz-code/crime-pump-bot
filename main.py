import ccxt
import os
import time
import datetime
import threading
import requests
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP_SECONDS = 60

ALERT_MIN_SCORE = 60

MIN_VOLUME = 5_000_000
MAX_VOLUME = 500_000_000  # widened to avoid over-filtering

MAX_PAIRS = 250

COOLDOWN_SECONDS = 1800

MAJOR_COINS = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🚀 Scanner LIVE"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):
    if not TG_ENABLED:
        print("TG disabled:", msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:3900]},
            timeout=10
        )
    except Exception as e:
        print("TG error:", e)

# =========================================================
# SAFE HELPERS (CRITICAL FIX)
# =========================================================

def f(x):
    try:
        if x is None:
            return 0.0
        return float(x)
    except:
        return 0.0

# =========================================================
# EXCHANGE INIT (FIXED)
# =========================================================

def make_exchanges():

    return {
        "Binance": ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"}
        }),

        "MEXC": ccxt.mexc({
            "enableRateLimit": True,
            "timeout": 30000,
            "options": {"defaultType": "swap"}
        }),

        "BloFin": ccxt.blofin({
            "enableRateLimit": True,
            "timeout": 30000
        })
    }

# =========================================================
# CORE SIGNALS
# =========================================================

def volume_spike(ex, symbol):
    try:
        c = ex.fetch_ohlcv(symbol, "5m", limit=10)
        vols = [f(x[5]) for x in c]
        if len(vols) < 5:
            return 0
        avg = sum(vols[:-1]) / max(len(vols[:-1]), 1)
        return vols[-1] / avg if avg > 0 else 0
    except:
        return 0

def price_accel(ex, symbol):
    try:
        c = ex.fetch_ohlcv(symbol, "5m", limit=6)
        if len(c) < 6:
            return 0
        return ((f(c[-1][4]) - f(c[0][4])) / max(f(c[0][4]), 1)) * 100
    except:
        return 0

def orderbook_ratio(ex, symbol):
    try:
        ob = ex.fetch_order_book(symbol, limit=20)
        bids = sum(f(x[1]) for x in ob.get("bids", []))
        asks = sum(f(x[1]) for x in ob.get("asks", []))
        return bids / asks if asks > 0 else 0
    except:
        return 0

def oi_change(ex, symbol, state):
    try:
        oi = ex.fetch_open_interest(symbol)
        val = f(
            oi.get("openInterest")
            or oi.get("openInterestAmount")
            or oi.get("openInterestValue")
        )

        key = f"{ex.id}:{symbol}"
        prev = state.get(key, 0)

        state[key] = val

        if prev == 0:
            return 0

        return ((val - prev) / prev) * 100
    except:
        return 0

# =========================================================
# MTF (FIXED SAFE)
# =========================================================

def tf_score(ex, symbol, tf):
    try:
        c = ex.fetch_ohlcv(symbol, tf, limit=20)
        if len(c) < 10:
            return 0

        closes = [f(x[4]) for x in c]
        highs = [f(x[2]) for x in c]

        if len(closes) < 5:
            return 0

        break_high = closes[-1] > max(highs[-10:-1])
        trend = closes[-1] > closes[-3] > closes[-5]

        score = 0
        if break_high:
            score += 1
        if trend:
            score += 1
        return score
    except:
        return 0

def mtf_alignment(ex, symbol):
    tf = {
        "5m": tf_score(ex, symbol, "5m"),
        "15m": tf_score(ex, symbol, "15m"),
        "1h": tf_score(ex, symbol, "1h"),
        "4h": tf_score(ex, symbol, "4h"),
    }

    score = tf["5m"]*5 + tf["15m"]*10 + tf["1h"]*20 + tf["4h"]*30
    return score, tf

# =========================================================
# MAIN LOOP
# =========================================================

def bot():

    prev_oi_state = {}

    send_telegram("🚀 Scanner FIXED version started")

    while True:

        print("\n================ SCAN START ================")

        exchanges = make_exchanges()

        for name, ex in exchanges.items():

            try:

                tickers = ex.fetch_tickers()
                print(f"{name} tickers: {len(tickers)}")

                if not tickers:
                    continue

                valid = []

                for sym, t in tickers.items():

                    vol = f(t.get("quoteVolume"))

                    if vol == 0:
                        continue

                    valid.append((sym, t, vol))

                # FIXED SAFE SORT (NO NoneType CRASH)
                valid.sort(key=lambda x: x[2], reverse=True)

                print(f"{name} valid pairs: {len(valid)}")

                scanned = 0

                for sym, t, vol in valid:

                    if scanned > MAX_PAIRS:
                        break

                    if "USDT" not in sym:
                        continue

                    base = sym.split("/")[0]
                    if base in MAJOR_COINS:
                        continue

                    if vol < MIN_VOLUME:
                        continue

                    scanned += 1

                    # =========================
                    # SIGNALS
                    # =========================

                    vs = volume_spike(ex, sym)
                    pa = price_accel(ex, sym)
                    ob = orderbook_ratio(ex, sym)
                    oi = oi_change(ex, sym, prev_oi_state)
                    mtf, tfs = mtf_alignment(ex, sym)

                    # =========================
                    # DEBUG (IMPORTANT FIX)
                    # =========================

                    print(f"{sym} | vol={vs:.2f}x accel={pa:.1f}% oi={oi:.1f}% mtf={mtf}")

                    # =========================
                    # CONFLUENCE
                    # =========================

                    con = 0
                    if vs > 3: con += 1
                    if pa > 4: con += 1
                    if ob > 1.5: con += 1
                    if oi > 8: con += 1
                    if mtf > 20: con += 1

                    if con < 3:
                        continue

                    # =========================
                    # SCORE (SIMPLE BUT CLEAN)
                    # =========================

                    score = (
                        vs*10 +
                        pa*2 +
                        ob*10 +
                        oi*1.5 +
                        mtf
                    )

                    if score < ALERT_MIN_SCORE:
                        continue

                    msg = f"""
🚨 SIGNAL {name}

{sym}
Score: {score:.1f}

VolSpike: {vs:.2f}x
Accel: {pa:.1f}%
OI: {oi:.1f}%
OB: {ob:.2f}

MTF: {tfs}
"""

                    print(msg)
                    send_telegram(msg)

                    time.sleep(0.5)

            except Exception as e:
                print(f"{name} error:", e)

        print("SCAN COMPLETE")
        time.sleep(LOOP_SECONDS)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot()
