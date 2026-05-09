import ccxt
import os
import time
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
MAX_PAIRS = 200
MIN_ALERT = 68

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "CRIME ENGINE V12.1 FIXED"

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
# SAFE FLOAT
# =========================================================

def f(x):
    try:
        return float(x) if x is not None else 0.0
    except:
        return 0.0

# =========================================================
# EXCHANGES
# =========================================================

def exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True}),
        "Binance": ccxt.binance({"enableRateLimit": True})
    }

# =========================================================
# CANDLES
# =========================================================

def candles(ex, s, tf="5m", n=30):
    try:
        c = ex.fetch_ohlcv(s, tf, limit=n)
        return c if c and len(c) > 15 else None
    except:
        return None

# =========================================================
# REGIME
# =========================================================

def regime(ex, s):
    c = candles(ex, s, "15m", 30)
    if not c:
        return 0.4

    closes = [f(x[4]) for x in c]

    trend = closes[-1] > closes[-5] > closes[-10]

    return 0.65 if trend else 0.35

# =========================================================
# COMPRESSION
# =========================================================

def compression(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    old_range = max(highs[:15]) - min(lows[:15])
    new_range = max(highs[-15:]) - min(lows[-15:])

    if new_range <= 0:
        return 0

    ratio = old_range / new_range

    return min((ratio - 1) / 4, 1)

# =========================================================
# BREAKOUT
# =========================================================

def breakout(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    closes = [f(x[4]) for x in c]
    highs = [f(x[2]) for x in c]

    resistance = max(highs[-12:-2])

    broke = closes[-1] > resistance
    follow = closes[-1] > closes[-2]

    return 0.85 if (broke and follow) else 0

# =========================================================
# VOLUME
# =========================================================

def volume(ex, s, breakout_active):
    c = candles(ex, s)
    if not c:
        return 0

    vols = [f(x[5]) for x in c]

    avg = sum(vols[:-1]) / max(len(vols[:-1]), 1)
    if avg == 0:
        return 0

    spike = vols[-1] / avg

    base = min((spike - 1) / 5, 1)

    if breakout_active:
        base *= 0.6

    return base

# =========================================================
# SWEEP
# =========================================================

def sweep(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    highs = [f(x[2]) for x in c[:-1]]
    lows = [f(x[3]) for x in c[:-1]]

    last = c[-1]
    h, l, cl = f(last[2]), f(last[3]), f(last[4])

    if h > max(highs) or l < min(lows):
        return 1

    return 0

# =========================================================
# SCORE (FIXED SYNTAX)
# =========================================================

def score(reg, comp, brk, vol, swp):

    raw = (
        comp * 0.22 +
        brk * 0.32 +
        vol * 0.20 +
        swp * 0.16 +
        reg * 0.10
    )

    return max(1, min(raw * 100, 100))

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    tg("🚀 CRIME ENGINE V12.1 FIXED LIVE")

    while True:

        exs = exchanges()

        for name, ex in exs.items():

            try:

                print(f"\nScanning {name}")

                try:
                    ex.load_markets()
                except:
                    pass

                tickers = ex.fetch_tickers()
                if not tickers:
                    continue

                universe = []

                for s, t in tickers.items():

                    if not any(x in s for x in ["/USDT", "-USDT"]):
                        continue

                    base = s.split("/")[0]
                    if base in MAJORS:
                        continue

                    vol = f(t.get("quoteVolume") or t.get("baseVolume"))
                    if vol <= 0:
                        continue

                    universe.append((s, vol))

                universe.sort(key=lambda x: x[1], reverse=True)
                universe = universe[:MAX_PAIRS]

                for s, vol in universe:

                    reg = regime(ex, s)
                    comp = compression(ex, s)
                    brk = breakout(ex, s)
                    volx = volume(ex, s, brk > 0)
                    swp = sweep(ex, s)

                    sc = score(reg, comp, brk, volx, swp)

                    print(f"{s} | crime={sc:.1f}")

                    if sc < MIN_ALERT:
                        continue

                    msg = f"""
🚨 CRIME SIGNAL V12.1

{s}
Crime Score: {sc:.1f}/100

Breakdown:
• Regime: {reg:.2f}
• Compression: {comp:.2f}
• Breakout: {brk:.2f}
• Volume: {volx:.2f}
• Sweep: {swp}
"""

                    tg(msg)
                    time.sleep(0.2)

            except Exception as e:
                print(name, "error:", e)

        print("SCAN COMPLETE")
        time.sleep(LOOP)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
