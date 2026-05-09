import ccxt
import time
import threading
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

LOOP = 60
MAX_PAIRS = 120
MIN_SCORE = 60

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# SERVER
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ALPHA ENGINE V18 FULL LIVE"

def run_web():
    app.run("0.0.0.0", 8080, use_reloader=False)

# =========================================================
# SAFE UTILS
# =========================================================

def f(x):
    try:
        if x is None:
            return 0.0
        if isinstance(x, complex):
            return float(x.real)
        return float(x)
    except:
        return 0.0


def safe_div(a, b):
    return a / b if b and b != 0 else 0.0

# =========================================================
# EXCHANGES
# =========================================================

def exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True})
    }


def load(ex):
    try:
        ex.load_markets()
    except:
        pass

# =========================================================
# PAIRS
# =========================================================

def get_pairs(ex):

    pairs = []

    try:
        tickers = ex.fetch_tickers()

        for s, t in tickers.items():

            if not s:
                continue

            s = str(s).upper().replace(":USDT", "")

            if "/USDT" not in s:
                continue

            base = s.split("/")[0]
            if base in MAJORS:
                continue

            vol = f(t.get("quoteVolume") or t.get("baseVolume"))
            if vol <= 0:
                continue

            pairs.append((s, vol))

    except:
        return []

    pairs.sort(key=lambda x: x[1], reverse=True)

    return pairs[:MAX_PAIRS]

# =========================================================
# CANDLES
# =========================================================

def candles(ex, s):
    try:
        c = ex.fetch_ohlcv(s, "5m", limit=40)
        if not c or len(c) < 25:
            return None
        return c
    except:
        return None

# =========================================================
# FUNDING + OI (SAFE OPTIONAL LAYER)
# =========================================================

def funding(ex, symbol):
    try:
        if hasattr(ex, "fetch_funding_rate"):
            fr = ex.fetch_funding_rate(symbol)
            return f(fr.get("fundingRate"))
    except:
        pass
    return 0.0


def open_interest(ex, symbol):
    try:
        if hasattr(ex, "fetch_open_interest"):
            oi = ex.fetch_open_interest(symbol)
            return f(oi.get("openInterest") or oi.get("openInterestAmount"))
    except:
        pass
    return 0.0

# =========================================================
# FEATURES
# =========================================================

def features(c):

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]
    closes = [f(x[4]) for x in c]
    vols = [f(x[5]) for x in c]

    # compression
    old_range = max(highs[:20]) - min(lows[:20])
    new_range = max(highs[-20:]) - min(lows[-20:])
    comp = safe_div(old_range - new_range, old_range)

    # breakout pressure
    resistance = max(highs[-15:-3])
    brk = safe_div(closes[-1] - resistance, resistance)
    brk = max(0, brk)

    # volume acceleration
    short = sum(vols[-5:]) / 5
    long = sum(vols[-20:-5]) / 15 if len(vols) > 20 else short
    vol = safe_div(short - long, long)
    vol = max(0, vol)

    # sweep
    swp = 0
    if highs[-1] > max(highs[:-1]):
        swp += 0.5
    if lows[-1] < min(lows[:-1]):
        swp += 0.5

    return comp, brk, vol, swp

# =========================================================
# SCORE (FULL MULTI-FACTOR MODEL)
# =========================================================

def score(comp, brk, vol, swp, fund, oi):

    raw = (
        comp * 0.28 +
        brk * 0.25 +
        vol * 0.20 +
        swp * 0.12 +
        abs(fund) * 0.10 +
        safe_div(oi, 1_000_000) * 0.05
    )

    return max(1, min((raw ** 0.75) * 100, 100))

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    exs = exchanges()

    while True:

        for name, ex in exs.items():

            print(f"\nScanning {name}")

            load(ex)

            pairs = get_pairs(ex)

            print(f"{name} pairs: {len(pairs)}")

            for s, vol in pairs[:20]:

                c = candles(ex, s)
                if not c:
                    continue

                comp, brk, volx, swp = features(c)

                fund = funding(ex, s)
                oi = open_interest(ex, s)

                sc = score(comp, brk, volx, swp, fund, oi)

                print(
                    f"{s} | score={sc:.1f} | "
                    f"c={comp:.2f} b={brk:.2f} v={volx:.2f} s={swp:.2f} "
                    f"f={fund:.4f} oi={oi:.0f}"
                )

                if sc >= MIN_SCORE:
                    print("🚨 SIGNAL:", s, sc)

        print("SCAN COMPLETE\n")
        time.sleep(LOOP)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
