# wazirx_bot.py
from flask import Flask, request, jsonify
import ccxt
from wazirx_config import *
from datetime import datetime
import json
import time
import requests
import os
from dotenv import load_dotenv

# Load environment variables (Render pe optional, lekin safe hai)
load_dotenv()

app = Flask(__name__)

# ============= INITIALIZE WAZIRX =============
exchange = ccxt.wazirx({
    'apiKey': os.getenv('WAZIRX_API_KEY'),
    'secret': os.getenv('WAZIRX_SECRET_KEY'),
    'enableRateLimit': True,
    'timeout': 10000,
    'options': {
        'defaultType': 'spot',
    }
})

# ============= DAILY TRACKING =============
daily_pnl_usdt = 0
daily_pnl_inr = 0
last_reset_date = datetime.now().date()
total_trades_today = 0
winning_trades_today = 0
losing_trades_today = 0

# ============= ACTIVE ORDERS TRACKING =============
active_orders = {}  # {order_id: {symbol, side, sl, tp, entry_price}}

def reset_daily_tracker():
    global daily_pnl_usdt, daily_pnl_inr, last_reset_date, total_trades_today
    global winning_trades_today, losing_trades_today
    
    today = datetime.now().date()
    if today != last_reset_date:
        daily_pnl_usdt = 0
        daily_pnl_inr = 0
        total_trades_today = 0
        winning_trades_today = 0
        losing_trades_today = 0
        last_reset_date = today
        log_message(f"✅ Daily tracker reset: {today}")

# ============= LOGGING =============
def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)
    
    if LOG_TRADES_TO_FILE:
        try:
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(log_entry + "\n")
        except Exception as e:
            print(f"❌ Logging error: {e}")

# ============= TELEGRAM NOTIFICATIONS =============
def send_telegram(message):
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN:
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data, timeout=5)
        if response.status_code != 200:
            log_message(f"⚠️ Telegram send failed: {response.text}")
    except Exception as e:
        log_message(f"❌ Telegram error: {e}")

# ============= GET CURRENT BALANCE =============
def get_balance():
    try:
        balance = exchange.fetch_balance()
        usdt_free = balance.get('USDT', {}).get('free', 0)
        usdt_total = balance.get('USDT', {}).get('total', 0)
        
        return {
            'usdt_free': float(usdt_free),
            'usdt_total': float(usdt_total)
        }
    except Exception as e:
        log_message(f"❌ Balance fetch error: {e}")
        return {'usdt_free': 0, 'usdt_total': 0}

# ============= GET CURRENT PRICE =============
def get_current_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except Exception as e:
        log_message(f"❌ Price fetch error for {symbol}: {e}")
        return None

# ============= SAFETY CHECKS =============
def check_safety_limits(data):
    global daily_pnl_usdt
    reset_daily_tracker()
    
    if not TRADING_ENABLED:
        return False, "❌ Trading is disabled in config"
    
    if abs(daily_pnl_usdt) >= MAX_DAILY_LOSS_USDT:
        return False, f"❌ Daily loss limit reached: ${abs(daily_pnl_usdt):.2f}"
    
    symbol = data.get('symbol', '')
    mapped_symbol = SYMBOL_MAP.get(symbol, symbol).upper() + 'USDT'
    
    if mapped_symbol not in [s.upper().replace('/', '') + 'USDT' for s in ALLOWED_SYMBOLS]:
        return False, f"❌ Symbol not allowed: {mapped_symbol}"
    
    balance = get_balance()
    if balance['usdt_free'] < MIN_BALANCE_USDT:
        return False, f"❌ Insufficient balance: ${balance['usdt_free']:.2f}"
    
    if not TRADING_24_7:
        current_hour = datetime.now().hour
        if current_hour in RESTRICTED_HOURS:
            return False, f"❌ Trading restricted at {current_hour}:00 IST"
    
    return True, "✅ All safety checks passed"

# ============= CALCULATE POSITION SIZE =============
def calculate_position_size(symbol, entry_price, stop_loss_price):
    try:
        balance = get_balance()
        usdt_free = balance['usdt_free']
        
        if usdt_free <= MIN_BALANCE_USDT:
            return 0, "Insufficient balance"
        
        available_capital = usdt_free - MIN_BALANCE_USDT
        
        risk_amount = available_capital * (RISK_PER_TRADE_PERCENT / 100)
        risk_amount = min(risk_amount, MAX_POSITION_SIZE_USDT * 0.02)
        
        sl_distance_percent = abs(entry_price - stop_loss_price) / entry_price
        
        if sl_distance_percent <= 0:
            return 0, "Invalid SL distance"
        
        position_size_usdt = risk_amount / sl_distance_percent
        position_size_usdt = min(position_size_usdt, MAX_POSITION_SIZE_USDT)
        position_size_usdt = min(position_size_usdt, available_capital * 0.8)
        
        quantity = position_size_usdt / entry_price
        
        markets = exchange.load_markets()
        market = markets.get(symbol)
        
        if market and market['precision']['amount']:
            quantity = round(quantity, market['precision']['amount'])
        
        min_order_usdt = 1.0
        if quantity * entry_price < min_order_usdt:
            return 0, f"Order size too small (min ${min_order_usdt})"
        
        return quantity, "OK"
        
    except Exception as e:
        log_message(f"❌ Position size calculation error: {e}")
        return 0, str(e)

# ============= PLACE ORDER =============
def place_order(symbol, side, quantity, entry_price, sl_price, tp_price):
    try:
        if DRY_RUN:
            log_message(f"🔍 DRY RUN: Would place {side.upper()} {quantity:.6f} {symbol} @ ${entry_price}")
            return {
                'id': f'DRY_RUN_{int(time.time())}',
                'status': 'dry_run'
            }
        
        if side == 'buy':
            limit_price = entry_price * (1 + SLIPPAGE_PERCENT / 100)
        else:
            limit_price = entry_price * (1 - SLIPPAGE_PERCENT / 100)
        
        markets = exchange.load_markets()
        market = markets.get(symbol)
        if market and market['precision']['price']:
            limit_price = round(limit_price, market['precision']['price'])
        
        order = exchange.create_limit_order(
            symbol=symbol,
            side=side,
            amount=quantity,
            price=limit_price
        )
        
        log_message(f"✅ Order placed: {order['id']} | {side.upper()} {quantity:.6f} {symbol} @ ${limit_price}")
        
        active_orders[order['id']] = {
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'entry_price': limit_price,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'timestamp': datetime.now()
        }
        
        msg = f"🚀 <b>New Order</b>\n{symbol} | {side.upper()} | Qty: {quantity:.6f} | Entry: ${limit_price:.2f}\nSL: ${sl_price:.2f} | TP: ${tp_price:.2f}"
        send_telegram(msg)
        
        return order
        
    except Exception as e:
        log_message(f"❌ Order placement error: {e}")
        send_telegram(f"❌ Order Failed: {str(e)}")
        return None

# ============= MONITOR ORDERS =============
def monitor_active_orders():
    try:
        for order_id, info in list(active_orders.items()):
            symbol = info['symbol']
            current_price = get_current_price(symbol)
            if not current_price:
                continue
            
            try:
                order_status = exchange.fetch_order(order_id, symbol)
                if order_status['status'] != 'closed':
                    continue
            except:
                continue
            
            side = info['side']
            entry = info['entry_price']
            sl = info['sl_price']
            tp = info['tp_price']
            qty = info['quantity']
            
            should_close = False
            reason = ""
            
            if side == 'buy':
                if current_price <= sl:
                    should_close = True
                    reason = "SL Hit"
                elif current_price >= tp:
                    should_close = True
                    reason = "TP Hit"
            else:  # sell
                if current_price >= sl:
                    should_close = True
                    reason = "SL Hit"
                elif current_price <= tp:
                    should_close = True
                    reason = "TP Hit"
            
            if should_close:
                close_side = 'sell' if side == 'buy' else 'buy'
                exchange.create_market_order(symbol, close_side, qty)
                
                pnl = (current_price - entry) * qty if side == 'buy' else (entry - current_price) * qty
                
                global daily_pnl_usdt, winning_trades_today, losing_trades_today
                daily_pnl_usdt += pnl
                if pnl > 0:
                    winning_trades_today += 1
                else:
                    losing_trades_today += 1
                
                log_message(f"🔔 Closed: {reason} | P&L: ${pnl:.2f}")
                send_telegram(f"{'✅' if pnl > 0 else '❌'} <b>Closed</b> | {reason} | P&L: ${pnl:.2f} | {symbol}")
                
                del active_orders[order_id]
                
    except Exception as e:
        log_message(f"❌ Monitor error: {e}")

# ============= WEBHOOK =============
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        log_message("\n" + "="*80)
        log_message(f"📨 Webhook Alert | {datetime.now()}")
        log_message(json.dumps(data, indent=2))
        
        is_safe, msg = check_safety_limits(data)
        if not is_safe:
            log_message(msg)
            return jsonify({"status": "rejected", "reason": msg}), 400
        
        action = data.get('action', '').upper()
        tv_symbol = data.get('symbol', '')
        price = float(data.get('price', 0))
        sl = float(data.get('sl', 0))
        tp = float(data.get('tp', 0))
        
        symbol = SYMBOL_MAP.get(tv_symbol, tv_symbol).upper().replace('/', '') + 'USDT'
        
        if action not in ['BUY', 'SELL']:
            return jsonify({"status": "error", "reason": "Invalid action"}), 400
        
        side = 'buy' if action == 'BUY' else 'sell'
        quantity, qty_msg = calculate_position_size(symbol, price, sl)
        
        if quantity <= 0:
            return jsonify({"status": "error", "reason": qty_msg}), 400
        
        order = place_order(symbol, side, quantity, price, sl, tp)
        
        if order:
            global total_trades_today
            total_trades_today += 1
            return jsonify({"status": "success", "order_id": order.get('id'), "symbol": symbol}), 200
        else:
            return jsonify({"status": "error", "reason": "Failed to place order"}), 500
            
    except Exception as e:
        log_message(f"❌ Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ============= HEALTH & POSITIONS =============
@app.route('/health', methods=['GET'])
def health():
    balance = get_balance()
    return jsonify({
        "status": "running",
        "balance_usdt": balance['usdt_free'],
        "daily_pnl": round(daily_pnl_usdt, 2),
        "trades_today": total_trades_today,
        "active_orders": len(active_orders)
    })

@app.route('/positions', methods=['GET'])
def positions():
    return jsonify({"active": len(active_orders), "orders": list(active_orders.values())})

# ============= START MONITOR =============
def start_order_monitor():
    import threading
    def loop():
        while True:
            monitor_active_orders()
            time.sleep(ORDER_CHECK_INTERVAL_SECONDS or 5)
    threading.Thread(target=loop, daemon=True).start()
    log_message("✅ Background monitor started")

# ============= MAIN (Render Ready) =============
if __name__ == '__main__':
    log_message("🚀 WAZIRX TRADING BOT STARTING...")
    log_message(f"Dry Run: {DRY_RUN} | Trading: {TRADING_ENABLED}")
    
    try:
        bal = get_balance()['usdt_free']
        log_message(f"✅ Connected to WazirX | Balance: ${bal:.2f} USDT")
    except:
        log_message("❌ Failed to connect to WazirX")
    
    start_order_monitor()
    send_telegram("🚀 <b>Bot Started Successfully on Render!</b>")
    
    # NO app.run() here → Render uses Gunicorn
