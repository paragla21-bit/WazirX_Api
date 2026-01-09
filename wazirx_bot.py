from flask import Flask, request, jsonify
import ccxt
from wazirx_config import *
from datetime import datetime, timedelta
import json
import time
import requests
import threading
from functools import wraps
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ============= DEFAULT VALUES IF NOT IN CONFIG =============
RATE_LIMIT_ENABLED = True
REQUEST_TIMEOUT_SECONDS = 10
ORDER_CHECK_INTERVAL_SECONDS = 5
ORDER_TIMEOUT_MINUTES = 30
MAX_OPEN_POSITIONS = 3
DEFAULT_SL_PERCENT = 2.0
DEFAULT_TP_PERCENT = 4.0

# WazirX Exchange Setup
exchange = ccxt.wazirx({
    'apiKey': os.getenv('WAZIRX_API_KEY'),
    'secret': os.getenv('WAZIRX_SECRET_KEY'),
    'enableRateLimit': True,
    'sandbox': False,
    'options': {'defaultType': 'spot'}
})

# ============= THREAD-SAFE DATA STRUCTURES =============
data_lock = threading.Lock()

# Daily tracking
daily_pnl_usdt = 0
daily_pnl_inr = 0
last_reset_date = datetime.now().date()
total_trades_today = 0
winning_trades_today = 0
losing_trades_today = 0

# Active orders
active_orders = {}

# ============= RETRY DECORATOR =============
def retry_on_failure(max_retries=3, delay=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    log_message(f"⚠️ Retry {attempt + 1}/{max_retries} for {func.__name__}: {e}")
                    time.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator

# ============= LOGGING =============
log_lock = threading.Lock()

def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"

    with log_lock:
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
            log_message(f"⚠️ Telegram API error: {response.status_code}")
    except Exception as e:
        log_message(f"❌ Telegram error: {e}")

# ============= GET CURRENT BALANCE =============
@retry_on_failure(max_retries=3, delay=2)
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
@retry_on_failure(max_retries=3, delay=1)
def get_current_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except Exception as e:
        log_message(f"❌ Price fetch error for {symbol}: {e}")
        return None

# ============= RESET DAILY TRACKER =============
def reset_daily_tracker():
    global last_reset_date, daily_pnl_usdt, daily_pnl_inr, total_trades_today, winning_trades_today, losing_trades_today
    if datetime.now().date() > last_reset_date:
        daily_pnl_usdt = 0
        daily_pnl_inr = 0
        total_trades_today = 0
        winning_trades_today = 0
        losing_trades_today = 0
        last_reset_date = datetime.now().date()

# ============= SAFETY CHECKS =============
def check_safety_limits(data):
    reset_daily_tracker()

    if not TRADING_ENABLED:
        return False, "❌ Trading is disabled in config"

    with data_lock:
        if abs(daily_pnl_usdt) >= MAX_DAILY_LOSS_USDT:
            return False, f"❌ Daily loss limit reached: ${abs(daily_pnl_usdt):.2f}"

    with data_lock:
        if len(active_orders) >= MAX_OPEN_POSITIONS:
            return False, f"❌ Maximum positions reached: {len(active_orders)}/{MAX_OPEN_POSITIONS}"

    symbol = data.get('symbol', '')
    mapped_symbol = SYMBOL_MAP.get(symbol, symbol)
    if '/' not in mapped_symbol:
        mapped_symbol += '/USDT'

    if mapped_symbol not in ALLOWED_SYMBOLS:
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
        usdt_free = float(balance.get('usdt_free', 0))

        min_balance_to_keep = 0
        
        if usdt_free <= min_balance_to_keep:
            return 0, "Insufficient balance"

        available_capital = usdt_free - min_balance_to_keep
        risk_amount = available_capital * (RISK_PER_TRADE_PERCENT / 100)
        
        sl_distance_percent = abs(entry_price - stop_loss_price) / entry_price

        if sl_distance_percent <= 0:
            return 0, "Invalid SL distance"

        position_size_usdt = risk_amount / sl_distance_percent
        position_size_usdt = min(position_size_usdt, available_capital)
        
        quantity = position_size_usdt / entry_price

        markets = exchange.load_markets()
        market = markets.get(symbol)

        if market:
            precision = market.get('precision', {}).get('amount', 4)
            quantity = round(quantity, precision)
            
            min_notional = market.get('limits', {}).get('cost', {}).get('min', 1.0)
            if (quantity * entry_price) < min_notional:
                quantity = available_capital / entry_price
                quantity = round(quantity, precision)

        if (quantity * entry_price) < 1.0:
            return 0, f"Order too small (${round(quantity * entry_price, 2)})"

        return quantity, "OK"

    except Exception as e:
        log_message(f"❌ Position size calculation error: {e}")
        return 0, str(e)

# ============= PLACE ORDER =============
@retry_on_failure(max_retries=2, delay=3)
def place_order(symbol, side, quantity, entry_price, sl_price, tp_price):
    try:
        if DRY_RUN:
            order_id = f'DRY_RUN_{int(time.time())}'
            log_message(f"🔍 DRY RUN: Would place {side.upper()} {quantity} {symbol} @ ${entry_price}")

            with data_lock:
                active_orders[order_id] = {
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'timestamp': datetime.now(),
                    'status': 'dry_run',
                    'filled_quantity': quantity
                }

            return {
                'id': order_id,
                'status': 'dry_run',
                'symbol': symbol,
                'side': side,
                'price': entry_price,
                'amount': quantity
            }

        if side == 'buy':
            limit_price = entry_price * (1 + SLIPPAGE_PERCENT / 100)
        else:
            limit_price = entry_price * (1 - SLIPPAGE_PERCENT / 100)

        markets = exchange.load_markets()
        market = markets.get(symbol)
        if market:
            price_precision = market.get('precision', {}).get('price')
            if price_precision is not None:
                limit_price = round(limit_price, price_precision)

        order = exchange.create_limit_order(
            symbol=symbol,
            side=side,
            amount=quantity,
            price=limit_price
        )

        log_message(f"✅ Order placed: {order['id']} | {side.upper()} {quantity} {symbol} @ ${limit_price}")

        with data_lock:
            active_orders[order['id']] = {
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'entry_price': limit_price,
                'sl_price': sl_price,
                'tp_price': tp_price,
                'timestamp': datetime.now(),
                'status': 'open',
                'filled_quantity': 0
            }

        msg = f"🚀 <b>Order Placed</b>\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Side: {side.upper()}\n"
        msg += f"Quantity: {quantity}\n"
        msg += f"Price: ${limit_price:.4f}\n"
        msg += f"SL: ${sl_price:.4f}\n"
        msg += f"TP: ${tp_price:.4f}"
        send_telegram(msg)

        return order

    except Exception as e:
        log_message(f"❌ Order placement error: {e}")
        send_telegram(f"❌ Order Failed: {str(e)}")
        raise

# ============= CLOSE POSITION =============
@retry_on_failure(max_retries=3, delay=2)
def close_position(order_id, order_info, reason):
    try:
        symbol = order_info['symbol']
        side = order_info['side']
        quantity = order_info.get('filled_quantity', order_info['quantity'])
        entry_price = order_info['entry_price']

        current_price = get_current_price(symbol)
        if not current_price:
            log_message(f"⚠️ Could not get price for {symbol}, skipping close")
            return False

        close_side = 'sell' if side == 'buy' else 'buy'

        if DRY_RUN:
            log_message(f"🔍 DRY RUN: Would close {close_side.upper()} {quantity} {symbol} @ ${current_price}")
        else:
            close_order = exchange.create_market_order(
                symbol=symbol,
                side=close_side,
                amount=quantity
            )
            log_message(f"✅ Position closed: {close_order['id']}")

        if side == 'buy':
            pnl = (current_price - entry_price) * quantity
        else:
            pnl = (entry_price - current_price) * quantity

        global daily_pnl_usdt, winning_trades_today, losing_trades_today
        with data_lock:
            daily_pnl_usdt += pnl
            if pnl > 0:
                winning_trades_today += 1
            else:
                losing_trades_today += 1

        log_message(f"🔔 Position closed: {reason} | P&L: ${pnl:.2f}")

        emoji = "✅" if pnl > 0 else "❌"
        msg = f"{emoji} <b>Position Closed</b>\n"
        msg += f"Reason: {reason}\n"
        msg += f"P&L: ${pnl:.2f}\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Entry: ${entry_price:.4f}\n"
        msg += f"Exit: ${current_price:.4f}"
        send_telegram(msg)

        return True

    except Exception as e:
        log_message(f"❌ Position close error: {e}")
        raise

# ============= CHECK ORDER TIMEOUT =============
def check_order_timeout(order_id, order_info):
    try:
        if DRY_RUN:
            return False

        order_time = order_info['timestamp']
        time_elapsed = datetime.now() - order_time

        if time_elapsed > timedelta(minutes=ORDER_TIMEOUT_MINUTES):
            try:
                order_status = exchange.fetch_order(order_id, order_info['symbol'])
                if order_status['status'] == 'open':
                    exchange.cancel_order(order_id, order_info['symbol'])
                    log_message(f"⏱️ Order timeout cancelled: {order_id}")
                    return True
            except Exception as e:
                log_message(f"⚠️ Timeout check error for {order_id}: {e}")

        return False

    except Exception as e:
        log_message(f"❌ Timeout check error: {e}")
        return False

# ============= MONITOR ORDERS =============
def monitor_active_orders():
    try:
        with data_lock:
            orders_to_monitor = list(active_orders.items())

        for order_id, order_info in orders_to_monitor:
            try:
                symbol = order_info['symbol']

                if check_order_timeout(order_id, order_info):
                    with data_lock:
                        if order_id in active_orders:
                            del active_orders[order_id]
                    continue

                current_price = get_current_price(symbol)
                if not current_price:
                    continue

                if not DRY_RUN and order_info.get('status') != 'filled':
                    try:
                        order_status = exchange.fetch_order(order_id, symbol)
                        if order_status['status'] in ['closed', 'filled']:
                            with data_lock:
                                active_orders[order_id]['status'] = 'filled'
                                active_orders[order_id]['filled_quantity'] = float(order_status.get('filled', order_info['quantity']))
                        else:
                            continue
                    except Exception as e:
                        log_message(f"⚠️ Order status check failed for {order_id}: {e}")
                        continue

                entry_price = order_info['entry_price']
                sl_price = order_info['sl_price']
                tp_price = order_info['tp_price']
                side = order_info['side']

                should_close = False
                close_reason = ""

                if side == 'buy':
                    if current_price <= sl_price:
                        should_close = True
                        close_reason = "Stop Loss Hit"
                    elif current_price >= tp_price:
                        should_close = True
                        close_reason = "Take Profit Hit"
                else:
                    if current_price >= sl_price:
                        should_close = True
                        close_reason = "Stop Loss Hit"
                    elif current_price <= tp_price:
                        should_close = True
                        close_reason = "Take Profit Hit"

                if should_close:
                    if close_position(order_id, order_info, close_reason):
                        with data_lock:
                            if order_id in active_orders:
                                del active_orders[order_id]

            except Exception as e:
                log_message(f"❌ Error monitoring order {order_id}: {e}")

    except Exception as e:
        log_message(f"❌ Order monitoring error: {e}")

# ============= ROOT ENDPOINT (404 FIX) =============
@app.route('/', methods=['GET'])
def index():
    return "<h1>🚀 WazirX Trading Bot is Live</h1><p>Check <a href='/health'>/health</a> for status.</p>", 200

# ============= WEBHOOK ENDPOINT =============
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        log_message("\n" + "="*80)
        log_message(f"📨 ALERT RECEIVED | {datetime.now()}")
        log_message(json.dumps(data, indent=2))
        log_message("="*80)

        is_safe, msg = check_safety_limits(data)
        if not is_safe:
            log_message(msg)
            return jsonify({"status": "rejected", "reason": msg}), 400

        action = data.get('action', '').upper()
        tv_symbol = data.get('symbol', '')
        price = float(data.get('price', 0))
        sl = float(data.get('sl', 0))
        tp = float(data.get('tp', 0))

        # ✅ FIXED SYMBOL HANDLING
        if tv_symbol in SYMBOL_MAP:
            symbol = SYMBOL_MAP[tv_symbol]
        elif '/' in tv_symbol:
            symbol = tv_symbol  # Already formatted
        else:
            symbol = f"{tv_symbol}/USDT"  # Add /USDT only if missing

        # Validation
        if action not in ['BUY', 'SELL']:
            return jsonify({"status": "error", "reason": "Invalid action"}), 400

        if price <= 0:
            return jsonify({"status": "error", "reason": "Invalid price"}), 400

        if symbol not in ALLOWED_SYMBOLS:
            return jsonify({"status": "error", "reason": f"Symbol not allowed: {symbol}"}), 400

        # Auto SL/TP calculation
        if sl <= 0:
            sl = price * (1 - DEFAULT_SL_PERCENT / 100) if action == 'BUY' else price * (1 + DEFAULT_SL_PERCENT / 100)

        if tp <= 0:
            tp = price * (1 + DEFAULT_TP_PERCENT / 100) if action == 'BUY' else price * (1 - DEFAULT_TP_PERCENT / 100)

        side = 'buy' if action == 'BUY' else 'sell'
        quantity, qty_msg = calculate_position_size(symbol, price, sl)

        if quantity <= 0:
            return jsonify({"status": "error", "reason": f"Position size error: {qty_msg}"}), 400

        order = place_order(symbol, side, quantity, price, sl, tp)

        if order:
            global total_trades_today
            with data_lock:
                total_trades_today += 1

            return jsonify({
                "status": "success",
                "order_id": order.get('id'),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": price,
                "sl": sl,
                "tp": tp,
                "trades_today": total_trades_today
            }), 200
        else:
            return jsonify({"status": "error", "reason": "Order placement failed"}), 500

    except Exception as e:
        log_message(f"❌ Webhook error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
        
# ============= HEALTH CHECK =============
@app.route('/health', methods=['GET'])
def health():
    try:
        balance = get_balance()

        with data_lock:
            response_data = {
                "status": "running",
                "exchange": "WazirX",  # ✅ FIXED
                "balance_usdt": balance['usdt_free'],
                "daily_pnl_usdt": round(daily_pnl_usdt, 2),
                "trades_today": total_trades_today,
                "winning_trades": winning_trades_today,
                "losing_trades": losing_trades_today,
                "active_orders": len(active_orders),
                "max_positions": MAX_OPEN_POSITIONS,
                "trading_enabled": TRADING_ENABLED,
                "dry_run": DRY_RUN,
                "time": str(datetime.now())
            }

        return jsonify(response_data), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============= GET POSITIONS =============
@app.route('/positions', methods=['GET'])
def get_positions():
    with data_lock:
        positions_data = {
            "active_orders": len(active_orders),
            "max_positions": MAX_OPEN_POSITIONS,
            "orders": []
        }

        for order_id, order_info in active_orders.items():
            positions_data["orders"].append({
                "order_id": order_id,
                "symbol": order_info['symbol'],
                "side": order_info['side'],
                "quantity": order_info['quantity'],
                "entry_price": order_info['entry_price'],
                "sl_price": order_info['sl_price'],
                "tp_price": order_info['tp_price'],
                "status": order_info.get('status', 'unknown'),
                "timestamp": str(order_info['timestamp'])
            })

    return jsonify(positions_data), 200

# ============= CLOSE ALL POSITIONS =============
@app.route('/close_all', methods=['POST'])
def close_all_positions():
    try:
        with data_lock:
            orders_to_close = list(active_orders.items())

        closed_count = 0
        for order_id, order_info in orders_to_close:
            try:
                if close_position(order_id, order_info, "Manual Close All"):
                    closed_count += 1
                    with data_lock:
                        if order_id in active_orders:
                            del active_orders[order_id]
            except Exception as e:
                log_message(f"❌ Failed to close {order_id}: {e}")

        return jsonify({
            "status": "success",
            "closed_positions": closed_count,
            "message": f"Closed {closed_count} positions"
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============= BACKGROUND ORDER MONITOR =============
def start_order_monitor():
    def monitor_loop():
        while True:
            try:
                monitor_active_orders()
                time.sleep(ORDER_CHECK_INTERVAL_SECONDS)
            except Exception as e:
                log_message(f"❌ Monitor loop error: {e}")
                time.sleep(10)

    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    log_message("✅ Order monitor thread started")

# ============= MAIN =============
if __name__ == '__main__':
    log_message("\n" + "="*80)
    log_message("🚀 WAZIRX TRADING BOT STARTING...")
    log_message(f"Trading Enabled: {TRADING_ENABLED}")
    log_message(f"Dry Run: {DRY_RUN}")
    log_message(f"Max Positions: {MAX_OPEN_POSITIONS}")
    log_message(f"Risk Per Trade: {RISK_PER_TRADE_PERCENT}%")
    log_message(f"Max Daily Loss: ${MAX_DAILY_LOSS_USDT}")
    log_message(f"Allowed Symbols: {len(ALLOWED_SYMBOLS)}")
    log_message("="*80 + "\n")

    try:
        balance = get_balance()
        log_message(f"✅ Exchange connected | Balance: ${balance['usdt_free']:.2f} USDT")
    except Exception as e:
        log_message(f"❌ Exchange connection failed: {e}")

    start_order_monitor()
    send_telegram("🚀 <b>WazirX Trading Bot Started</b>\n\nBot is now monitoring for signals.")

