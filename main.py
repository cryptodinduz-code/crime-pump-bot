import asyncio
import ccxt.async_support as ccxt
import os
import time
import datetime
import requests
import threading
from flask import Flask
from collections import deque

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

SCAN_COUNT = 150      # Top 150 by Volume
CHUNK_SIZE = 12       # Stricter batching for stability
LOOP_SECONDS = 60
ALERT_MIN_SCORE = 65

# Persistent memory for /recap (Stores last 15 alerts)
call_history = deque(maxlen=15)
prev_oi = {}

app = Flask(__name__)

@app.route('/')
def home(): 
    return "🚀 Crime Bot v14.1 FULL is ONLINE"

# ================== UTILS ==================
def send_telegram(text, target_id=None):
    if not TG_ENABLED: return
    chat = target_id or TELEGRAM_CHAT_ID
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=5)
    except:
        pass

# ================== RECAP COMMAND HANDLER ==================
def check_for_recap():
    offset = 0
    while True:
        if not TG_ENABLED: break
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=10"
            resp = requests.get(url).json()
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                if msg.get("text") == "/recap":
                    cid = msg["chat"]["id"]
                    if not call_history:
                        send_telegram("📭 No recent calls yet.", cid)
                    else:
                        report = "📊 <b>LAST 15 CRIME CALLS</b>\n\n"
                        for c in reversed(list(call_history)):
                            report += f"• <b>{c['symbol']}</b> ({c['ex']}) - Score: <b>{c['score']}</b>\n"
                        send_telegram(report, cid)
        except:
            pass
        time.sleep(3)

# ================== CORE SCANNER ==================
async def get_stats(exchange, symbol, ticker):
    score = 0
    signals = []
    try:
        # 1. Funding
        try:
            fr_data = await exchange.fetch_funding_rate(symbol)
            funding = fr_data.get('fundingRate', 0)
            if funding < -0.0005: 
                score += 35
                signals.append(f"Short Squeeze ({funding*100:.3f}%)")
        except: pass

        # 2. Open Interest
        try:
            oi_data = await exchange.fetch_open_interest(symbol)
            oi_val = oi_data.get('openInterestAmount') or oi_data.get('openInterest', 0)
            key = f"{exchange.id}:{symbol}"
            if key in prev_oi:
                oi_chg = ((oi_val - prev_oi[key]) / prev_oi[key] * 100) if prev_oi[key] > 0 else 0
                if oi_chg > 15:
                    score += 30
                    signals.append(f"OI Surge +{oi_chg:.1f}%")
            prev_oi[key] = oi_val
        except: pass

        # 3. Order Book
        try:
            ob = await exchange.fetch_order_book(symbol, limit=20)
            bids = sum(l[1] for l in ob['bids'])
            asks = sum(l[1] for l in ob['asks'])
            ratio = bids / asks if asks > 0 else 99
            if ratio > 2.5:
                score += 15
                signals.append(f"Buy Wall ({ratio:.1f}x)")
        except: pass

        # 4. Filter
        v24h = ticker.get('quoteVolume', 0)
        if 500_000 < v24h < 20_000_000: score += 10

        if score >= ALERT_MIN_SCORE:
            # Add to /recap history
            call_history.append({"symbol": symbol, "ex": exchange.id.upper(), "score": score})
            
            msg = (f"🚨 <b>CRIME ALERT: {symbol}</b>\n"
                   f"🔥 Score: <b>{score}/100</b> | {exchange.id.upper()}\n"
                   f"📊 Signals: {', '.join(signals)}\n"
                   f"💰 Vol: ${v24h/1_000_000:.1f}M")
            send_telegram(msg)
    except: pass

async def scan_exchange(exchange_id, config):
    ex = getattr(ccxt, exchange_id)(config)
    try:
        tickers = await ex.fetch_tickers()
        valid = [(s, t) for s, t in tickers.items() if ':USDT' in s or (s.endswith('/USDT') and exchange_id != 'binance')]
        top_150 = sorted(valid, key=lambda x: x[1].get('quoteVolume', 0), reverse=True)[:SCAN_COUNT]
        
        for i in range(0, len(top_150), CHUNK_SIZE):
            chunk = top_150[i:i+CHUNK_SIZE]
            await asyncio.gather(*[get_stats(ex, s, t) for s, t in chunk])
            await asyncio.sleep(0.5)
    except Exception as e:
        print(f"Error on {exchange_id}: {e}")
    finally:
        await ex.close()

async def main_loop():
    while True:
        configs = {
            'binance': {'enableRateLimit': True, 'options': {'defaultType': 'future'}},
            'bybit': {'enableRateLimit': True, 'options': {'defaultType': 'linear'}},
            'blofin': {'enableRateLimit': True, 'options': {'defaultType': 'swap'}},
            'mexc': {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}
        }
        for eid in configs:
            await scan_exchange(eid, configs[eid])
            await asyncio.sleep(1)
        print(f"Cycle Done at {datetime.datetime.now()}")
        await asyncio.sleep(LOOP_SECONDS)

# ================== EXECUTION ==================
if __name__ == "__main__":
    # Start Web Server
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    
    # Start /recap Listener
    threading.Thread(target=check_for_recap, daemon=True).start()
    
    # Send Online Alert
    send_telegram("🚀 <b>Crime Bot v14.1 Online</b>\nScanning Top 150 pairs on 4 exchanges.")
    
    # Run Async Loop
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        pass
