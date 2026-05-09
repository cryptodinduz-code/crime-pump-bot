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
    return "CRIME ENGINE V12"

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
        return c if len(c) > 15 else None
    except:
        return None

# =========================================================
# VOLATILITY NORMALIZER (IMPORTANT FIX)
# =========================================================

def vol_norm(c):
    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    price = f(c[-1][4])
    if price == 0:
        return 1

    range_ = max(highs) - min(lows)
    return range_ / price

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
# COMPRESSION (DE-BIASED)
# =========================================================

def compression(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    old = max(highs[:15]) - min(lows[:15])
    new = max(highs[-15:]) - min(lows[-15:])

    if new <= 0:
        return 0

    ratio = old / new

    return min((ratio - 1) / 4, 1)

# =========================================================
# BREAKOUT (CONFIRMED, NOT ASSUMED)
# =========================================================

def breakout(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    closes = [f(x[4]) for x in c]
    highs = [f(x[2]) for x in c]
    vols = [f(x[5]) for x in c]

    resistance = max(highs[-12:-2])

    broke = closes[-1] > resistance
    follow_through = closes[-1] > closes[-2]
    vol_ok = vols[-1] > (sum(vols[-6:-1]) / 5)

    if broke and follow_through and vol_ok:
        return 0.85

    return 0

# =========================================================
# VOLUME (CONDITIONAL IMPORTANCE)
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

    # reduce importance if breakout already exists (fix double count)
    if breakout_active:
        base *= 0.6

    return base

# =========================================================
# LIQUIDITY SWEEP (CONTEXTUAL)
# =========================================================

def sweep(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    highs = [f(x[2]) for x in c[:-1]]
    lows = [f(x[3]) for x in c[:-1]]

    last = c[-1]
    h, l, cl = f(last[2]), f(last[3]), f(last[4])

    sweep_up = h > max(highs) and cl < h
    sweep_down = l < min(lows) and cl > l

    return 1 if (sweep_up or sweep_down) else 0

# =========================================================
# FINAL SCORE (CALIBRATED MODEL)
# =========================================================

def score(reg, comp, brk, vol, swp, vol_norm_factor):

    # volatility adjustment (IMPORTANT FIX)
    vol_factor = 1 / (1 + vol_norm_factor * 10)

    raw =
        comp * 0.22 +
        brk * 0.32 +
        vol * 0.20 +
        swp * 0.16 +
        reg * 0.10

    adjusted = raw * vol_factor

    return max(1, min(adjusted * 100, 100))

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    tg("🚀 CRIME ENGINE V12 LIVE")

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

                    c = candles(ex, s)
                    if not c:
                        continue

                    vnorm = vol_norm(c)

                    reg = regime(ex, s)
                    comp = compression(ex, s)

                    brk = breakout(ex, s)
                    volx = volume(ex, s, brk > 0)
                    swp = sweep(ex, s)

                    sc = score(reg, comp, brk, volx, swp, vnorm)

                    print(f"{s} | crime={sc:.1f}")

                    if sc < MIN_ALERT:
                        continue

                    msg = f"""
🚨 CRIME SIGNAL V12

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
