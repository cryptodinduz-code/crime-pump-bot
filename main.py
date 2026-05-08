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

MIN_VOL = 5_000_000
MAX_VOL = 400_000_000

MAX_PAIRS = 200

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "BOT OK"

def run_web():
    app.run("0.0.0.0", 8080, use_reloader=False)

# =========================================================
# SAFE NUMBERS
# =========================================================

def f(x):
    try:
        return float(x) if x is not None else 0.0
    except:
        return 0.0

def clamp(x, a, b):
    return max(a, min(b, x))

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
# EXCHANGES
# =========================================================

def exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True, "timeout": 30000}),
        # Binance kept but may fail in US
        "Binance": ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    }

# =========================================================
# METRICS (SAFE)
# =========================================================

def vol_spike(ex, s):
    try:
        c = ex.fetch_ohlcv(s, "5m", limit=10)
        v = [f(x[5]) for x in c]
        if len(v) < 6:
            return 0
        avg = sum(v[:-1]) / max(len(v[:-1]), 1)
        return clamp(v[-1] / avg if avg else 0, 0, 10)
    except:
        return 0

def accel(ex, s):
    try:
        c = ex.fetch_ohlcv(s, "5m", limit=6)
        return clamp(((f(c[-1][4]) - f(c[0][4])) / max(f(c[0][4]), 1)) * 100, -50, 50)
    except:
        return 0

def ob_ratio(ex, s):
    try:
        ob = ex.fetch_order_book(s, 20)
        b = sum(f(x[1]) for x in ob.get("bids", []))
        a = sum(f(x[1]) for x in ob.get("asks", []))
        return clamp(b / a if a else 0, 0, 5)
    except:
        return 0

def oi(ex, s, st):
    try:
        data = ex.fetch_open_interest(s)
        val = f(data.get("openInterest") or data.get("openInterestAmount"))
        key = f"{ex.id}:{s}"
        old = st.get(key, 0)
        st[key] = val
        if old == 0:
            return 0
        return clamp(((val - old) / old) * 100, -100, 100)
    except:
        return 0

# =========================================================
# MTF
# =========================================================

def mtf(ex, s):
    try:
        c = ex.fetch_ohlcv(s, "15m", limit=20)
        closes = [f(x[4]) for x in c]
        if len(closes) < 10:
            return 0
        return clamp((closes[-1] > closes[-5]) * 10 + (closes[-1] > closes[-10]) * 10, 0, 20)
    except:
        return 0

# =========================================================
# SCORE (FIXED NORMALIZED)
# =========================================================

def score_all(vs, ac, ob, oi_c, mtf_score):

    score = 0
    reasons = []

    # volume
    if vs > 3:
        score += 20
        reasons.append("Volume spike")

    # momentum
    if ac > 5:
        score += 20
        reasons.append("Momentum")

    # orderbook
    if ob > 1.5:
        score += 15
        reasons.append("Buy pressure")

    # oi
    if oi_c > 10:
        score += 20
        reasons.append("OI increase")

    # mtf
    score += mtf_score

    # HARD CAP (IMPORTANT FIX)
    score = clamp(score, 0, 100)

    return score, reasons

# =========================================================
# MAIN
# =========================================================

def run():

    state = {}

    tg("BOT STARTED")

    while True:

        exs = exchanges()

        for name, ex in exs.items():

            try:

                print(f"\nScanning {name}")

                tickers = ex.fetch_tickers()

                if not tickers:
                    print(f"{name} EMPTY tickers → skip")
                    continue

                print(f"{name} tickers: {len(tickers)}")

                # BLOFIN DEBUG
                if name == "BloFin":
                    print("BloFin sample:", list(tickers.items())[:2])

                valid = []

                for s, t in tickers.items():

                    vol = f(t.get("quoteVolume") or t.get("baseVolume"))

                    if vol <= 0:
                        continue

                    valid.append((s, t, vol))

                valid.sort(key=lambda x: x[2], reverse=True)

                print(f"{name} valid: {len(valid)}")

                scanned = 0

                for s, t, vol in valid:

                    if scanned > MAX_PAIRS:
                        break

                    if "USDT" not in s:
                        continue

                    base = s.split("/")[0]
                    if base in MAJORS:
                        continue

                    if vol < MIN_VOL:
                        continue

                    scanned += 1

                    vs = vol_spike(ex, s)
                    ac = accel(ex, s)
                    ob = ob_ratio(ex, s)
                    oi_c = oi(ex, s, state)
                    mtf_score = mtf(ex, s)

                    score, reasons = score_all(vs, ac, ob, oi_c, mtf_score)

                    print(f"{s} score={score}")

                    # DEBUG FOR BLOFIN SILENCE
                    if name == "BloFin" and scanned < 3:
                        print("DEBUG:", s, vs, ac, ob, oi_c, mtf_score)

                    if score < MIN_SCORE:
                        continue

                    msg = f"""
🚨 {name} SIGNAL

{s}
Score: {score}

Vol: {vs:.2f}x
Accel: {ac:.1f}%
OB: {ob:.2f}
OI: {oi_c:.1f}%
MTF: {mtf_score}

{reasons}
"""

                    tg(msg)

                    time.sleep(0.5)

            except Exception as e:
                print(name, "error:", e)

        print("SCAN DONE")
        time.sleep(LOOP)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
