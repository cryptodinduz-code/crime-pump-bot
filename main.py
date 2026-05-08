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

# HARD ALERT THRESHOLD
ALERT_MIN_SCORE = 80

# MINIMUM CONFLUENCE REQUIRED
MIN_CONFLUENCE = 4

MIN_24H_VOLUME = 5_000_000
MAX_24H_VOLUME = 300_000_000

MAX_PAIRS_PER_EXCHANGE = 250

COOLDOWN_SECONDS = 3600

MAJOR_COINS = [
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "BNB",
    "DOGE",
    "ADA",
    "TRX",
    "LTC",
]

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🚀 Crime Scanner Elite ONLINE"

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

        print(f"Telegram status: {r.status_code}")

    except Exception as e:
        print(f"Telegram error: {e}")

# =========================================================
# HELPERS
# =========================================================

def get_volume_spike(exchange, symbol):

    try:

        candles = exchange.fetch_ohlcv(symbol, "5m", limit=12)

        volumes = [c[5] for c in candles]

        current = volumes[-1]

        avg = sum(volumes[:-1]) / len(volumes[:-1])

        if avg == 0:
            return 0

        return current / avg

    except Exception as e:
        print(f"Volume error {symbol}: {e}")
        return 0

def get_open_interest_change(exchange, symbol, prev_oi):

    try:

        oi = exchange.fetch_open_interest(symbol)

        oi_value = (
            oi.get("openInterestAmount")
            or oi.get("openInterest")
            or oi.get("openInterestValue")
        )

        if oi_value is None:
            return 0

        oi_value = float(oi_value)

        key = f"{exchange.id}:{symbol}"

        change = 0

        if key in prev_oi:

            old = prev_oi[key]

            if old > 0:

                change = (
                    (oi_value - old) / old
                ) * 100

        prev_oi[key] = oi_value

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

            r = ((high - low) / close) * 100

            ranges.append(r)

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
        print(f"Accel error {symbol}: {e}")
        return 0

def analyze_orderbook(exchange, symbol):

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
        print(f"OB error {symbol}: {e}")
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

def analyze_timeframe(exchange, symbol, tf):

    try:

        candles = exchange.fetch_ohlcv(symbol, tf, limit=40)

        closes = [c[4] for c in candles]
        highs = [c[2] for c in candles]
        volumes = [c[5] for c in candles]

        current_close = closes[-1]

        recent_high = max(highs[-10:-1])

        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])

        current_volume = volumes[-1]

        ema9 = sum(closes[-9:]) / 9

        breakout = current_close > recent_high

        above_ema = current_close > ema9

        bullish_structure = (
            closes[-1] > closes[-2] > closes[-3]
        )

        strong_volume = (
            current_volume > avg_volume * 1.5
        )

        score = 0

        if breakout:
            score += 1

        if above_ema:
            score += 1

        if bullish_structure:
            score += 1

        if strong_volume:
            score += 1

        return score

    except Exception as e:
        print(f"TF error {symbol} {tf}: {e}")
        return 0

def get_mtf_alignment(exchange, symbol):

    tf_data = {
        "5m": analyze_timeframe(exchange, symbol, "5m"),
        "15m": analyze_timeframe(exchange, symbol, "15m"),
        "1h": analyze_timeframe(exchange, symbol, "1h"),
        "4h": analyze_timeframe(exchange, symbol, "4h"),
    }

    score = 0

    if tf_data["5m"] >= 3:
        score += 10

    if tf_data["15m"] >= 3:
        score += 20

    if tf_data["1h"] >= 3:
        score += 30

    if tf_data["4h"] >= 3:
        score += 40

    return score, tf_data

# =========================================================
# SCORE ENGINE
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

    # =====================================================
    # EXTREME VOLUME
    # =====================================================

    if vol_ratio >= 5:

        score += 35
        reasons.append(f"Extreme Volume {vol_ratio:.1f}x")

    elif vol_ratio >= 4:

        score += 25
        reasons.append(f"Strong Volume {vol_ratio:.1f}x")

    # =====================================================
    # OPEN INTEREST
    # =====================================================

    if oi_change >= 20:

        score += 35
        reasons.append(f"OI Explosion +{oi_change:.1f}%")

    elif oi_change >= 12:

        score += 25
        reasons.append(f"Strong OI +{oi_change:.1f}%")

    # =====================================================
    # VOLATILITY
    # =====================================================

    if vol_expansion >= 2:

        score += 20
        reasons.append("Volatility Expansion")

    # =====================================================
    # ORDERBOOK
    # =====================================================

    if ob_ratio >= 2.5:

        score += 20
        reasons.append(f"Heavy Buy Wall {ob_ratio:.2f}")

    elif ob_ratio >= 1.8:

        score += 10
        reasons.append(f"Buy Pressure {ob_ratio:.2f}")

    # =====================================================
    # ACCELERATION
    # =====================================================

    if price_accel >= 10:

        score += 35
        reasons.append(f"Explosive Acceleration +{price_accel:.1f}%")

    elif price_accel >= 6:

        score += 20
        reasons.append(f"Strong Momentum +{price_accel:.1f}%")

    # =====================================================
    # FUNDING
    # =====================================================

    if funding < -0.0001:

        score += 10
        reasons.append("Crowded Shorts")

    # =====================================================
    # MIDCAP BONUS
    # =====================================================

    if 5_000_000 <= volume <= 80_000_000:

        score += 10
        reasons.append("Midcap Profile")

    # =====================================================
    # MULTI TF
    # =====================================================

    score += mtf_score

    if mtf_score >= 70:

        reasons.append("Elite Multi-TF Alignment")

    elif mtf_score >= 40:

        reasons.append("Strong Multi-TF Alignment")

    return score, reasons

# =========================================================
# MAIN LOOP
# =========================================================

def bot_loop():

    prev_oi = {}

    last_alerts = {}

    send_telegram(
        f"🚀 <b>Crime Scanner Elite Started</b>\n"
        f"Threshold = {ALERT_MIN_SCORE}"
    )

    while True:

        print(
            f"\n==============================\n"
            f"SCAN STARTED {datetime.datetime.utcnow()}\n"
            f"=============================="
        )

        alerts = 0

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

                        cooldown_key = f"{name}:{symbol}"

                        now = time.time()

                        if cooldown_key in last_alerts:

                            if (
                                now - last_alerts[cooldown_key]
                                < COOLDOWN_SECONDS
                            ):
                                continue

                        # =================================================
                        # SIGNALS
                        # =================================================

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

                        ob_ratio = analyze_orderbook(
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

                        mtf_score, tf_data = get_mtf_alignment(
                            exchange,
                            symbol
                        )

                        # =================================================
                        # CONFLUENCE
                        # =================================================

                        confluence = 0

                        if vol_ratio >= 4:
                            confluence += 1

                        if oi_change >= 10:
                            confluence += 1

                        if vol_expansion >= 1.5:
                            confluence += 1

                        if ob_ratio >= 1.8:
                            confluence += 1

                        if price_accel >= 5:
                            confluence += 1

                        if mtf_score >= 40:
                            confluence += 1

                        if confluence < MIN_CONFLUENCE:
                            continue

                        # =================================================
                        # SCORE
                        # =================================================

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

                        # =================================================
                        # HARD THRESHOLD
                        # =================================================

                        if score < ALERT_MIN_SCORE:
                            continue

                        # =================================================
                        # ALERT
                        # =================================================

                        alerts += 1

                        last_alerts[cooldown_key] = now

                        tf_text = (
                            f"5m={tf_data['5m']} | "
                            f"15m={tf_data['15m']} | "
                            f"1h={tf_data['1h']} | "
                            f"4h={tf_data['4h']}"
                        )

                        reason_text = "\n".join(
                            [f"• {r}" for r in reasons]
                        )

                        msg = f"""
🚨 <b>CRIME SETUP DETECTED</b>

🔥 <b>{symbol}</b>
🏦 {name}

📊 24h Volume: ${volume/1e6:.1f}M

⚡ Volume Spike: {vol_ratio:.2f}x
📦 OI Change: {oi_change:.2f}%
📈 Volatility Expansion: {vol_expansion:.2f}
📚 Orderbook Ratio: {ob_ratio:.2f}
🚀 Price Acceleration: {price_accel:.2f}%
💸 Funding: {funding*100:.4f}%

🧠 Multi-Timeframe:
{tf_text}

🎯 <b>SCORE: {score}/100</b>

{reason_text}
"""

                        print(msg)

                        send_telegram(msg)

                        time.sleep(1)

                    except Exception as e:
                        print(f"Pair error {symbol}: {e}")

            except Exception as e:
                print(f"{name} error: {e}")

        print(
            f"\n✅ Scan completed | Alerts: {alerts}"
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
