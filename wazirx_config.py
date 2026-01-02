import os
from dotenv import load_dotenv

# Load .env file if present (useful for local development)
load_dotenv()

# ============= API CREDENTIALS =============
# These should be set as environment variables on your hosting platform
WAZIRX_API_KEY = os.getenv("WAZIRX_API_KEY", "")
WAZIRX_SECRET_KEY = os.getenv("WAZIRX_SECRET_KEY", "")

# Check if credentials are present
if not WAZIRX_API_KEY or not WAZIRX_SECRET_KEY:
    print("WARNING: WazirX API key or secret not found in environment variables!")

# ============= SYMBOL MAPPING =============
# TradingView symbol → WazirX/ccxt symbol
SYMBOL_MAP = {
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT",
    "BNBUSD": "BNBUSDT",
    "SOLUSD": "SOLUSDT",
    "MATICUSD": "MATICUSDT",
    "ADAUSD": "ADAUSDT",
    "DOGEUSD": "DOGEUSDT",
    "XRPUSD": "XRPUSDT",
}

ALLOWED_SYMBOLS = list(SYMBOL_MAP.values())

# ============= RISK MANAGEMENT =============
RISK_PER_TRADE_PERCENT = 1.0      # Risk max 1% of account per trade
MAX_POSITION_SIZE_USDT = 200      # Hard limit per position
MIN_BALANCE_USDT = 20             # Don't trade if balance drops below this
MAX_DAILY_LOSS_USDT = 50          # Stop trading if daily loss exceeds this

# ============= ORDER SETTINGS =============
ORDER_TYPE = "limit"
SLIPPAGE_PERCENT = 0.3            # Allow some price movement when placing limit orders
ORDER_TIMEOUT_SECONDS = 60

# ============= STOP LOSS & TAKE PROFIT =============
USE_PERCENTAGE_SL_TP = True
DEFAULT_SL_PERCENT = 2.0
DEFAULT_TP_PERCENT = 4.0          # 1:2 risk-reward as starting point
EMERGENCY_STOP_LOSS_PERCENT = 7.0
AUTO_MOVE_TO_BREAKEVEN = True
BREAKEVEN_TRIGGER_RR = 1.5

# ============= TRADING CONTROL =============
TRADING_ENABLED = True
DRY_RUN = True                    # Set to False for live trading
TRADING_24_7 = True
RESTRICTED_HOURS = []             # e.g. [0,1,2,3,4,5] for night hours IST

# ============= TELEGRAM NOTIFICATIONS =============
TELEGRAM_ENABLED = False          # Change to True if you want notifications
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============= LOGGING =============
LOG_TRADES_TO_FILE = True
LOG_FILE_PATH = "trades_log.txt"
