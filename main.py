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

ALERT_MIN_SCORE = 75

MIN_24H_VOLUME = 5_000_000
MAX_24H_VOLUME = 300_000_000

MAX_PAIRS_PER_EXCHANGE = 300

COOLDOWN_SECONDS = 3600

MAJOR_COINS = [
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "BNB",
    "DOGE"
]

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🚀 Institutional Alpha Scanner ONLINE"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):

    if not TG_ENABLED:
        print("Telegram disabled")
        return

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            },
            timeout=10
        )

        print(f"Telegram: {r.status_code}")

    except Exception as e:
        print(f"Telegram error: {e}")

# =========================================================
# HELPERS
# =========================================================

def get_volume_spike(exchange, symbol):

    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=10)

        vols = [c[5] for c in candles]

        current = vols[-1]

        avg = sum(vols[:-1]) / len(vols[:-1])

        if avg == 0:
            return 0

        return current / avg

    except Exception as e:
        print(f"Volume spike error {symbol}: {e}")
        return 0

def get_open_interest_change(exchange, symbol, prev_oi):

    try:
        oi = exchange.fetch_open_interest(symbol)

        value = (
            oi.get("openInterestAmount")
            or oi.get("openInterest")
            or oi.get("openInterestValue")
        )

        if value is None:
            return 0

        value = float(value)

        key = f"{exchange.id}:{symbol}"

        change = 0

        if key in prev_oi and prev_oi[key] > 0:

            change = (
                (value - prev_oi[key]) / prev_oi[key]
            ) * 100

        prev_oi[key] = value

        return change

    except Exception as e:
        print(f"OI error {symbol}: {e}")
        return 0

def get_volatility_expansion(exchange, symbol):

    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=20)

        ranges = []

        for c in candles:

            high = c[2]
            low = c[3]
            close = c[4]

            if close == 0:
                continue

            ranges.append(
                ((high - low) / close) * 100
            )

        if len(ranges) < 10:
            return 0

        recent = sum(ranges[-5:]) / 5

        old = sum(ranges[:-5]) / len(ranges[:-5])

        if old == 0:
            return 0

        return recent / old

    except Exception as e:
        print(f"Vol expansion error {symbol}: {e}")
        return 0

def get_price_acceleration(exchange, symbol):

    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=6)

        first = candles[0][4]
        last = candles[-1][4]

        if first == 0:
            return 0

        move = ((last - first) / first) * 100

        return move

    except Exception as e:
        print(f"Acceleration error {symbol}: {e}")
        return 0

def analyze_order_book(exchange, symbol):

    try:
        ob = exchange.fetch_order_book(symbol, limit=20)

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)

        if ask_vol == 0:
            return 0

        return bid_vol / ask_vol

    except Exception as e:
        print(f"Orderbook error {symbol}: {e}")
        return 0

def get_funding(exchange, symbol):

    try:
        fr = exchange.fetch_funding_rate(symbol)

        return float(fr.get("fundingRate", 0))

    except Exception as e:
        print(f"Funding error {symbol}: {e}")
        return 0

# =========================================================
# MULTI TIMEFRAME BREAKOUT
# =========================================================

def timeframe_breakout(exchange, symbol, tf):

    try:
        candles = exchange.fetch_ohlcv(symbol, tf, limit=30)

        closes = [c[4] for c in candles]
        highs = [c[2] for c in candles]
        volumes = [c[5] for c in candles]

        current_close = closes[-1]

        recent_high = max(highs[-10:-1])

        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])

        current_volume = volumes[-1]

        # EMA 9
        ema = sum(closes[-9:]) / 9

        breakout = current_close > recent_high

        above_ema = current_close > ema

        strong_volume = current_volume > avg_volume * 1.5

        bullish_structure = (
            closes[-1] > closes[-2] > closes[-3]
        )

        score = 0

        if breakout:
            score += 1

        if above_ema:
            score += 1

        if strong_volume:
            score += 1

        if bullish_structure:
            score += 1

        return score

    except Exception as e:
        print(f"MTF error {symbol} {tf}: {e}")
        return 0

def get_mtf_alignment(exchange, symbol):

    tf_scores = {
        "5m": timeframe_breakout(exchange, symbol, "5m"),
        "15m": timeframe_breakout(exchange, symbol, "15m"),
        "1h": timeframe_breakout(exchange, symbol, "1h"),
        "4h": timeframe_breakout(exchange, symbol, "4h")
    }

    alignment_score = 0

    if tf_scores["5m"] >= 3:
        alignment_score += 10

    if tf_scores["15m"] >= 3:
        alignment_score += 15

    if tf_scores["1h"] >= 3:
        alignment_score += 25

    if tf_scores["4h"] >= 3:
        alignment_score += 35

    return alignment_score, tf_scores

# =========================================================
# SCORING ENGINE
# =========================================================

def calculate_score(
    volume,
    vol_ratio,
    oi_change,
    vol_expansion,
    ob_ratio,
    price_accel,
    funding,
    mtf_score
):

    score = 0

    reasons = []

    # =========================================
    # VOLUME EXPLOSION
    # =========================================

    if vol_ratio > 4:
        score += 30
        reasons.append(f"Extreme Volume {vol_ratio:.1f}x")

    elif vol_ratio > 3:
        score += 22
        reasons.append(f"Strong Volume {vol_ratio:.1f}x")

    elif vol_ratio > 2:
        score += 12
        reasons.append(f"Moderate Volume {vol_ratio:.1f}x")

    # =========================================
    # OPEN INTEREST
    # =========================================

    if oi_change > 20:
        score += 30
        reasons.append(f"OI Expansion +{oi_change:.1f}%")

    elif oi_change > 10:
        score += 20
        reasons.append(f"OI Rising +{oi_change:.1f}%")

    elif oi_change > 5:
        score += 10
        reasons.append(f"OI Build +{oi_change:.1f}%")

    # =========================================
    # VOLATILITY EXPANSION
    # =========================================

    if vol_expansion > 2:
        score += 20
        reasons.append("Volatility Expansion")

    elif vol_expansion > 1.5:
        score += 10
        reasons.append("Volatility Rising")

    # =========================================
    # ORDERBOOK
    # =========================================

    if ob_ratio > 2.5:
        score += 20
        reasons.append(f"Aggressive Buy Wall {ob_ratio:.2f}")

    elif ob_ratio > 1.8:
        score += 15
        reasons.append(f"Strong Buy Pressure {ob_ratio:.2f}")

    elif ob_ratio > 1.4:
        score += 8
        reasons.append(f"Buy Pressure {ob_ratio:.2f}")

    # =========================================
    # PRICE ACCELERATION
    # =========================================

    if price_accel > 10:
        score += 30
        reasons.append(f"Explosive Move +{price_accel:.1f}%")

    elif price_accel > 6:
        score += 20
        reasons.append(f"Strong Acceleration +{price_accel:.1f}%")

    elif price_accel > 3:
        score += 10
        reasons.append(f"Price Rising +{price_accel:.1f}%")

    # =========================================
    # FUNDING
    # =========================================

    if funding < -0.0001:
        score += 10
        reasons.append("Crowded Shorts")

    # =========================================
    # MIDCAP BONUS
    # =========================================

    if 5_000_000 < volume < 80_000_000:
        score += 10
        reasons.append("Midcap Profile")

    # =========================================
    # MULTI TIMEFRAME
    # =========================================

    score += mtf_score

    if mtf_score >= 70:
        reasons.append("Elite Multi-TF Alignment")

    elif mtf_score >= 45:
        reasons.append("Strong Multi-TF Breakout")

    elif mtf_score >= 25:
        reasons.append("Developing Multi-TF Strength")

    return score, reasons

# =========================================================
# MAIN LOOP
# =========================================================

def bot_loop():

    prev_oi = {}

    last_alerts = {}

    send_telegram(
        "🚀 <b>Institutional Alpha Scanner Started</b>"
    )

    while True:

        print(
            f"\n============================\n"
            f"SCAN STARTED {datetime.datetime.utcnow()}\n"
            f"============================"
        )

        exchanges = {
            "Binance": ccxt.binance({
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future"
                }
            }),

            "MEXC": ccxt.mexc({
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap"
                }
            }),

            "BloFin": ccxt.blofin({
                "enableRateLimit": True
            })
        }

        alerts = 0

        for name, exchange in exchanges.items():

            try:

                print(f"\n🔍 Scanning {name}")

                markets = exchange.load_markets()

                tickers = exchange.fetch_tickers()

                sorted_tickers = sorted(
                    tickers.items(),
                    key=lambda x: x[1].get("quoteVolume", 0),
                    reverse=True
                )

                scanned = 0

                for symbol, ticker in sorted_tickers:

                    try:

                        if scanned >= MAX_PAIRS_PER_EXCHANGE:
                            break

                        if "USDT" not in symbol:
                            continue

                        if symbol not in markets:
                            continue

                        market = markets[symbol]

                        if not (
                            market.get("swap")
                            or market.get("future")
                        ):
                            continue

                        base = symbol.split("/")[0]

                        if base in MAJOR_COINS:
                            continue

                        volume = ticker.get("quoteVolume", 0)

                        if volume is None:
                            continue

                        volume = float(volume)

                        if volume < MIN_24H_VOLUME:
                            continue

                        if volume > MAX_24H_VOLUME:
                            continue

                        scanned += 1

                        # =====================================
                        # COOLDOWN
                        # =====================================

                        now = time.time()

                        cooldown_key = f"{name}:{symbol}"

                        if cooldown_key in last_alerts:

                            if (
                                now - last_alerts[cooldown_key]
                                < COOLDOWN_SECONDS
                            ):
                                continue

                        # =====================================
                        # SIGNALS
                        # =====================================

                        vol_ratio = get_volume_spike(
                            exchange,
                            symbol
                        )

                        oi_change = get_open_interest_change(
                            exchange,
                            symbol,
                            prev_oi
                        )

                        vol_expansion = get_volatility_expansion(
                            exchange,
                            symbol
                        )

                        ob_ratio = analyze_order_book(
                            exchange,
                            symbol
                        )

                        price_accel = get_price_acceleration(
                            exchange,
                            symbol
                        )

                        funding = get_funding(
                            exchange,
                            symbol
                        )

                        mtf_score, tf_scores = get_mtf_alignment(
                            exchange,
                            symbol
                        )

                        # =====================================
                        # CONFLUENCE
                        # =====================================

                        confluence = 0

                        if vol_ratio > 3:
                            confluence += 1

                        if oi_change > 10:
                            confluence += 1

                        if vol_expansion > 1.5:
                            confluence += 1

                        if price_accel > 3:
                            confluence += 1

                        if ob_ratio > 1.5:
                            confluence += 1

                        if mtf_score >= 25:
                            confluence += 1

                        if confluence < 3:
                            continue

                        # =====================================
                        # SCORE
                        # =====================================

                        score, reasons = calculate_score(
                            volume,
                            vol_ratio,
                            oi_change,
                            vol_expansion,
                            ob_ratio,
                            price_accel,
                            funding,
                            mtf_score
                        )

                        # =====================================
                        # DEBUG
                        # =====================================

                        print(
                            f"{symbol} | "
                            f"Score={score} | "
                            f"Vol={vol_ratio:.2f}x | "
                            f"OI={oi_change:.1f}% | "
                            f"VolExp={vol_expansion:.2f} | "
                            f"OB={ob_ratio:.2f} | "
                            f"Accel={price_accel:.2f}% | "
                            f"MTF={mtf_score}"
                        )

                        # =====================================
                        # ALERT
                        # =====================================

                        if score >= ALERT_MIN_SCORE:

                            alerts += 1

                            last_alerts[cooldown_key] = now

                            tf_text = (
                                f"5m={tf_scores['5m']} | "
                                f"15m={tf_scores['15m']} | "
                                f"1h={tf_scores['1h']} | "
                                f"4h={tf_scores['4h']}"
                            )

                            reason_text = "\n".join(
                                [f"• {r}" for r in reasons]
                            )

                            msg = f"""
🚨 <b>INSTITUTIONAL ALPHA ALERT</b>

🔥 <b>{symbol}</b>
🏦 {name}

📊 24h Volume: ${volume/1e6:.1f}M

⚡ Volume Spike: {vol_ratio:.2f}x
📦 OI Change: {oi_change:.2f}%
📈 Vol Expansion: {vol_expansion:.2f}
📚 Orderbook Ratio: {ob_ratio:.2f}
🚀 Price Accel: {price_accel:.2f}%
💸 Funding: {funding*100:.4f}%

🧠 Multi-TF Alignment:
{tf_text}

🎯 Score: <b>{score}/100</b>

{reason_text}
"""

                            print(msg)

                            send_telegram(msg)

                            time.sleep(1)

                    except Exception as e:
                        print(f"Pair error {symbol}: {e}")

            except Exception as e:
                print(f"{name} exchange error: {e}")

        print(
            f"\n✅ Scan complete | Alerts fired: {alerts}"
        )

        time.sleep(LOOP_SECONDS)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    bot_loop()
