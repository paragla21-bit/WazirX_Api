# wazirx_config.py
import os
from dotenv import load_dotenv

load_dotenv()  # .env file se values load karne ke liye (local development)

# ============= API CREDENTIALS =============
# In values ko Render dashboard ke Environment Variables section mein set karo
# Code mein inko direct hard-code mat karna
WAZIRX_API_KEY = os.getenv("WAZIRX_API_KEY", "")
WAZIRX_SECRET_KEY = os.getenv("WAZIRX_SECRET_KEY", "")

# Agar Binance use kar rahe ho (recommended, kyunki CCXT mein WazirX support nahi)
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

# ============= TRADING CONTROLS =============
TRADING_ENABLED = True      # Master switch
DRY_RUN = False              # True = simulation mode, False = real trading

# ============= RISK MANAGEMENT =============
RISK_PER_TRADE_PERCENT = 1.0
MAX_POSITION_SIZE_USDT = 1.0
MIN_BALANCE_USDT = 10
MAX_DAILY_LOSS_USDT = 5.0
MAX_OPEN_POSITIONS = 3

# ============= ORDER SETTINGS =============
SLIPPAGE_PERCENT = 0.5
RATE_LIMIT_ENABLED = True
REQUEST_TIMEOUT_SECONDS = 10
ORDER_CHECK_INTERVAL_SECONDS = 5
ORDER_TIMEOUT_MINUTES = 30

# ============= SYMBOL MAPPING =============
# TradingView se aane wale symbols → exchange format mein convert
# Binance ke liye /USDT style use kar rahe hain
SYMBOL_MAP = {
    'BTCUSD': 'BTC/USDT',
    'ETHUSD': 'ETH/USDT',
    'BNBUSD': 'BNB/USDT',
    'XRPUSD': 'XRP/USDT',
    'ADAUSD': 'ADA/USDT',
    'SOLUSD': 'SOL/USDT',
    'DOGEUSD': 'DOGE/USDT',
    'MATICUSD': 'MATIC/USDT',
    'DOTUSD': 'DOT/USDT',
    'SHIBUSD': 'SHIB/USDT',
}

# ============= ALLOWED SYMBOLS =============
ALLOWED_SYMBOLS = list(SYMBOL_MAP.values())

# ============= TRADING HOURS =============
TRADING_24_7 = True
RESTRICTED_HOURS = []  # example: [0, 1, 2, 3, 4, 5] for night hours IST

# ============= LOGGING =============
LOG_TRADES_TO_FILE = True
LOG_FILE_PATH = "trading_bot.log"

# ============= TELEGRAM NOTIFICATIONS =============
TELEGRAM_ENABLED = False  # True karo agar notifications chahiye
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============= STOP LOSS / TAKE PROFIT =============
DEFAULT_SL_PERCENT = 2.0
DEFAULT_TP_PERCENT = 4.0


