import ccxt
import time
import threading
import requests
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

LOOP = 60
MIN_SCORE = 70
MAX_PAIRS = 120

TIMEFRAMES = ["5m", "15m", "1h", "4h"]

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""
TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

# =========================================================
# SERVER
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "V26 ALPHA ENGINE LIVE"

def run_web():
    app.run("0.0.0.0", 8080, use_reloader=False)

# =========================================================
# SAFE MATH
# =========================================================

def f(x):
    try:
        if x is None:
            return 0.0
        if isinstance(x, complex):
            return float(x.real)
        x = float(x)
        if x != x:
            return 0.0
        return x
    except:
        return 0.0


def clamp(x, a=0, b=1):
    x = f(x)
    return max(a, min(b, x))


def div(a, b):
    a, b = f(a), f(b)
    return a / b if b else 0.0

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):
    if not TG_ENABLED:
        print("[TG OFF]", msg)
        return

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{8634708300:AAEph9U53eSKAunguDo9IP914RCqu6kxU}/sendMessage",
            json={"chat_id": -5156355307, "text": msg},
            timeout=10
        )
        if not r.ok:
            print("TG ERROR:", r.text)
    except Exception as e:
        print("TG EXCEPTION:", e)

# =========================================================
# EXCHANGES
# =========================================================

def exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True})
    }

# =========================================================
# MARKET LOAD
# =========================================================

def get_pairs(ex):
    pairs = []
    try:
        markets = ex.load_markets()

        for s in markets:
            if "/USDT" not in s:
                continue

            base = s.split("/")[0]
            if base in MAJORS:
                continue

            try:
                t = ex.fetch_ticker(s)
                v = f(t.get("quoteVolume") or t.get("baseVolume"))
                if v > 0:
                    pairs.append((s, v))
            except:
                continue

    except:
        return []

    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:MAX_PAIRS]

# =========================================================
# CANDLES
# =========================================================

def get_candles(ex, symbol, tf):
    try:
        c = ex.fetch_ohlcv(symbol, tf, limit=60)
        if not c or len(c) < 40:
            return None
        return c
    except:
        return None

# =========================================================
# STRUCTURE ENGINE
# =========================================================

def structure(c):

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]
    closes = [f(x[4]) for x in c]
    vols = [f(x[5]) for x in c]

    old_range = max(highs[:30]) - min(lows[:30])
    new_range = max(highs[-30:]) - min(lows[-30:])

    comp = 0
    if old_range > 0:
        comp = 1 - new_range / old_range

    resistance = max(highs[-25:-5])
    breakout = div(closes[-1] - resistance, resistance)

    return clamp(comp * 2), clamp(breakout * 5)

# =========================================================
# FLOW ENGINE
# =========================================================

def flow(c):

    vols = [f(x[5]) for x in c]

    short = sum(vols[-10:]) / 10
    long = sum(vols[-40:-10]) / 30 if len(vols) > 40 else short

    vol = div(short, long) - 1

    sweep = 0
    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    if highs[-1] >= max(highs[:-1]):
        sweep += 0.5
    if lows[-1] <= min(lows[:-1]):
        sweep += 0.5

    return clamp(vol), sweep

# =========================================================
# DERIVATIVES (SAFE)
# =========================================================

def derivatives(ex, s):

    fund = 0
    oi = 0

    try:
        if hasattr(ex, "fetch_funding_rate"):
            fr = ex.fetch_funding_rate(s)
            fund = f(fr.get("fundingRate"))
    except:
        pass

    try:
        if hasattr(ex, "fetch_open_interest"):
            oi_data = ex.fetch_open_interest(s)
            oi = f(oi_data.get("openInterest") or oi_data.get("openInterestAmount"))
    except:
        pass

    return clamp(abs(fund) * 50), clamp(oi / 1_000_000)

# =========================================================
# MULTI TIMEFRAME
# =========================================================

def mtf_score(ex, s):

    scores = []

    for tf in TIMEFRAMES:
        c = get_candles(ex, s, tf)
        if not c:
            continue

        comp, brk = structure(c)
        vol, swp = flow(c)

        score = (comp + brk + vol + swp) / 4
        scores.append(score)

    if not scores:
        return 0

    return sum(scores) / len(scores)

# =========================================================
# FINAL SCORE ENGINE
# =========================================================

def score(struct, flow, deriv, mtf):

    raw = (
        struct * 0.30 +
        flow * 0.25 +
        deriv * 0.20 +
        mtf * 0.25
    )

    raw = clamp(raw * 1.2)

    return max(1, min(raw * 100, 100))

# =========================================================
# RUN
# =========================================================

def run():

    exs = exchanges()

    while True:

        for name, ex in exs.items():

            print(f"\nScanning {name}")

            pairs = get_pairs(ex)

            print(f"{name} pairs: {len(pairs)}")

            for s, _ in pairs[:25]:

                c = get_candles(ex, s, "5m")
                if not c:
                    continue

                struct = sum(structure(c)) / 2
                flow_score = sum(flow(c)) / 2
                deriv = sum(derivatives(ex, s)) / 2
                mtf = mtf_score(ex, s)

                sc = score(struct, flow_score, deriv, mtf)

                line = f"{s} | score={sc:.1f} | s={struct:.2f} f={flow_score:.2f} d={deriv:.2f} m={mtf:.2f}"

                print(line)

                if sc >= MIN_SCORE:
                    send_telegram("🚨 V26 SIGNAL\n" + line)

        print("SCAN COMPLETE\n")
        time.sleep(LOOP)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
