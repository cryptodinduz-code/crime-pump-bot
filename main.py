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
MAX_PAIRS = 150
MIN_ALERT = 60

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "CRIME ENGINE V14 STABLE"

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
# SAFE NUMBERS (CRITICAL FIX)
# =========================================================

def safe_float(x):
    try:
        if x is None:
            return 0.0
        if isinstance(x, complex):
            return float(x.real)
        return float(x)
    except:
        return 0.0

# =========================================================
# SAFE CANDLES
# =========================================================

def clean_candles(c):
    out = []
    for x in c:
        try:
            o = safe_float(x[1])
            h = safe_float(x[2])
            l = safe_float(x[3])
            cl = safe_float(x[4])
            v = safe_float(x[5])

            if any(val != val for val in [o, h, l, cl, v]):  # NaN check
                continue

            out.append([x[0], o, h, l, cl, v])
        except:
            continue
    return out

# =========================================================
# EXCHANGES (BINANCE REMOVED)
# =========================================================

def exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True})
    }

# =========================================================
# CANDLES
# =========================================================

def candles(ex, s, tf="5m", n=40):
    try:
        c = ex.fetch_ohlcv(s, tf, limit=n)
        if not c:
            return None

        c = clean_candles(c)

        return c if len(c) > 20 else None

    except:
        return None

# =========================================================
# FEATURES
# =========================================================

def compression(c):
    highs = [x[2] for x in c]
    lows = [x[3] for x in c]

    old = max(highs[:15]) - min(lows[:15])
    new = max(highs[-15:]) - min(lows[-15:])

    if old <= 0 or new <= 0:
        return 0

    return min((old - new) / old, 1)

# -------------------------

def breakout(c):
    closes = [x[4] for x in c]
    highs = [x[2] for x in c]

    resistance = max(highs[-12:-2])

    if closes[-1] > resistance and closes[-1] > closes[-2]:
        return 0.8

    return 0

# -------------------------

def volume(c):
    vols = [x[5] for x in c]

    short = sum(vols[-5:]) / 5
    long = sum(vols[-20:-5]) / 15

    if long <= 0:
        return 0

    return min((short / long - 1) * 2, 1)

# -------------------------

def sweep(c):
    highs = [x[2] for x in c[:-1]]
    lows = [x[3] for x in c[:-1]]

    last = c[-1]
    h, l = last[2], last[3]

    score = 0

    if h > max(highs):
        score += 0.5
    if l < min(lows):
        score += 0.5

    return min(score, 1)

# -------------------------

def regime(c):
    closes = [x[4] for x in c]

    return 0.65 if closes[-1] > closes[-5] > closes[-10] else 0.35

# =========================================================
# FINAL SCORE (CLEAN + SAFE)
# =========================================================

def score(reg, comp, brk, vol, swp):

    raw = (
        comp * 0.25 +
        brk * 0.30 +
        vol * 0.20 +
        swp * 0.15 +
        reg * 0.10
    )

    final = raw ** 0.75 * 100

    return max(1, min(final, 100))

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    tg("🚀 V14 STABLE ENGINE ONLINE")

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

                    vol = safe_float(t.get("quoteVolume") or t.get("baseVolume"))
                    if vol <= 0:
                        continue

                    universe.append((s, vol))

                universe.sort(key=lambda x: x[1], reverse=True)
                universe = universe[:MAX_PAIRS]

                for s, vol in universe:

                    c = candles(ex, s)
                    if not c:
                        continue

                    comp = compression(c)
                    brk = breakout(c)
                    volx = volume(c)
                    swp = sweep(c)
                    reg = regime(c)

                    sc = score(reg, comp, brk, volx, swp)

                    print(f"{s} | crime={sc:.1f}")

                    if sc < MIN_ALERT:
                        continue

                    tg(f"""
🚨 CRIME V14 SIGNAL

{s}
Score: {sc:.1f}/100

Compression: {comp:.2f}
Breakout: {brk:.2f}
Volume: {volx:.2f}
Sweep: {swp:.2f}
Regime: {reg:.2f}
""")

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
