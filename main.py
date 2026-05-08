import asyncio
import ccxt.async_support as ccxt
import os
import time
import requests
import threading
from flask import Flask
from collections import deque

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

SCAN_COUNT = 150
CHUNK_SIZE = 12
LOOP_SECONDS = 60
ALERT_MIN_SCORE = 65

# Persistent memory for /recap (Stores last 15 alerts)
call_history = deque(maxlen=15)

app = Flask(__name__)

@app.route('/')
def home(): return "🚀 Crime Bot v14 + /recap is ACTIVE"

# ================== RECAP COMMAND HANDLER ==================
def check_for_recap():
    """Independent thread to listen for the /recap command."""
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=10"
            resp = requests.get(url).json()
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = message.get("text", "")
                chat_id = message.get("chat", {}).get("id")

                if text == "/recap":
                    if not call_history:
                        send_telegram("📭 No recent calls in history.", chat_id)
                    else:
                        report = "📊 <b>LAST 15 CRIME CALLS</b>\n\n"
                        for c in reversed(list(call_history)):
                            report += f"• <b>{c['symbol']}</b> ({c['ex']}) - Score: <b>{c['score']}</b>\n"
                        send_telegram(report, chat_id)
        except Exception as e:
            print(f"Recap Listener Error: {e}")
        time.sleep(2)

# ================== CORE SCANNER ==================
async def get_stats(exchange, symbol, ticker):
    score = 0
    signals = []
    try:
        # 1. Funding Logic
        fr_data = await exchange.fetch_funding_rate(symbol)
        funding = fr_data.get('fundingRate', 0)
        if funding < -0.0005: 
            score += 40
            signals.append(f"Squeeze {funding*100:.3f}%")

        # 2. Open Interest Logic
        oi_data = await exchange.fetch_open_interest(symbol)
        oi_val = oi_data.get('openInterestAmount', 0)
        # Score based on OI spikes here...

        # (Existing logic from previous versions)
        score += 30 # Placeholder for OI logic match

        if score >= ALERT_MIN_SCORE:
            alert = {"symbol": symbol, "ex": exchange.id.upper(), "score": score, "time": datetime.datetime.now()}
            call_history.append(alert) # Save to history for /recap
            
            msg = f"🚨 <b>CRIME ALERT: {symbol}</b>\nScore: {score}/100 | {exchange.id.upper()}"
            send_telegram(msg)
    except: pass

# ... (Insert scan_exchange and main_loop from v13 here) ...

def send_telegram(text, target_id=None):
    if not TG_ENABLED: return
    chat = target_id or TELEGRAM_CHAT_ID
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                       json={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=5)
    except: pass

if __name__ == "__main__":
    # Start the /recap listener thread
    threading.Thread(target=check_for_recap, daemon=True).start()
    # Start the web server
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    # Run bot
    asyncio.run(main_loop())
