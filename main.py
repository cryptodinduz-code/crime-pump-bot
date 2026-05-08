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
TG = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP = 60
MIN_SCORE = 60

MAX_PAIRS = 300

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "BOT RUNNING"

def run_web():
    app.run("0.0.0.0", 8080, use_reloader=False)

# =========================================================
# TELEGRAM
# =========================================================

def tg(msg):
    if not TG:
        print(msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4000]},
            timeout=10
        )
    except:
        pass

# =========================================================
# SAFE NUMBER PARSER
# =========================================================

def f(x):
    try:
        return float(x) if x is not None else 0.0
    except:
        return 0.0

# =========================================================
# EXCHANGES
# =========================================================

def get_exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True, "timeout": 30000}),
        "Binance": ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"}
        })
    }

# =========================================================
# METRICS
# =========================================================

def vol_spike(ex, s):
    try:
        c = ex.fetch_ohlcv(s, "5m", limit=10)
        v = [f(x[5]) for x in c]
        if len(v) < 5:
            return 0
        avg = sum(v[:-1]) / max(len(v[:-1]), 1)
        return v[-1] / avg if avg > 0 else 0
    except:
        return 0

def accel(ex, s):
    try:
        c = ex.fetch_ohlcv(s, "5m", limit=6)
        return ((f(c[-1][4]) - f(c[0][4])) / max(f(c[0][4]), 1)) * 100
    except:
        return 0

def ob_ratio(ex, s):
    try:
        ob = ex.fetch_order_book(s, 20)
        b = sum(f(x[1]) for x in ob.get("bids", []))
        a = sum(f(x[1]) for x in ob.get("asks", []))
        return b / a if a > 0 else 0
    except:
        return 0

def oi_change(ex, s, state):
    try:
        data = ex.fetch_open_interest(s)
        val = f(data.get("openInterest") or data.get("openInterestAmount"))

        key = f"{ex.id}:{s}"
        old = state.get(key, 0)
        state[key] = val

        if old == 0:
            return 0

        return ((val - old) / old) * 100
    except:
        return 0

# =========================================================
# MTF
# =========================================================

def mtf_score(ex, s):
    try:
        c = ex.fetch_ohlcv(s, "15m", limit=20)
        closes = [f(x[4]) for x in c]
        if len(closes) < 10:
            return 0

        score = 0
        if closes[-1] > max(closes[-10:]):
            score += 10
        if closes[-1] > closes[-5]:
            score += 10

        return score
    except:
        return 0

# =========================================================
# SCORE
# =========================================================

def score(vs, ac, ob, oi, mtf):

    s = 0

    if vs > 3:
        s += 20
    if ac > 5:
        s += 20
    if ob > 1.5:
        s += 15
    if oi > 10:
        s += 20

    s += mtf

    # HARD CAP (IMPORTANT)
    return min(s, 100)

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    state = {}

    tg("BOT STARTED FIXED VERSION")

    while True:

        exs = get_exchanges()

        for name, ex in exs.items():

            try:

                print(f"\nScanning {name}")

                # =================================================
                # FIX 1: SAFE LOAD MARKETS
                # =================================================

                try:
                    ex.load_markets()
                except:
                    pass

                tickers = ex.fetch_tickers()

                print(f"{name} raw tickers: {len(tickers)}")

                if not tickers:
                    print(f"{name} EMPTY")
                    continue

                # =================================================
                # FIX 2: BUILD FULL UNIVERSE FIRST (IMPORTANT)
                # =================================================

                universe = []

                for s, t in tickers.items():

                    vol = f(
                        t.get("quoteVolume")
                        or t.get("baseVolume")
                    )

                    if vol <= 0:
                        continue

                    universe.append((s, t, vol))

                # =================================================
                # FIX 3: SORT BEFORE FILTERING
                # =================================================

                universe.sort(
                    key=lambda x: x[2],
                    reverse=True
                )

                universe = universe[:MAX_PAIRS]

                print(f"{name} universe: {len(universe)}")

                scanned = 0
                skipped = 0

                for s, t, vol in universe:

                    if scanned > MAX_PAIRS:
                        break

                    # =================================================
                    # FIX 4: SYMBOL FILTER (SAFE)
                    # =================================================

                    if not any(x in s for x in ["/USDT", "-USDT"]):
                        skipped += 1
                        continue

                    base = s.split("/")[0]
                    if base in MAJORS:
                        continue

                    scanned += 1

                    # =================================================
                    # METRICS
                    # =================================================

                    vs = vol_spike(ex, s)
                    ac = accel(ex, s)
                    ob = ob_ratio(ex, s)
                    oi = oi_change(ex, s, state)
                    mtf = mtf_score(ex, s)

                    # DEBUG
                    print(f"{s} | vs={vs:.2f} ac={ac:.1f} oi={oi:.1f} mtf={mtf}")

                    sc = score(vs, ac, ob, oi, mtf)

                    if sc < MIN_SCORE:
                        continue

                    msg = f"""
🚨 {name} SIGNAL

{s}
Score: {sc}

VolSpike: {vs:.2f}x
Accel: {ac:.1f}%
OB: {ob:.2f}
OI: {oi:.1f}%
MTF: {mtf}
"""

                    print(msg)
                    tg(msg)

                    time.sleep(0.3)

                print(f"{name} scanned={scanned} skipped={skipped}")

            except Exception as e:
                print(f"{name} error:", e)

        print("SCAN COMPLETE")
        time.sleep(LOOP)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
