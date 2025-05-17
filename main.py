import logging
import math
import asyncio
import traceback
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import os
import time
from b2sdk.v2 import InMemoryAccountInfo, B2Api

# Initialize Flask app
app = Flask(__name__)

# Logging configuration
logging.basicConfig(
    filename='pybit_telegram.log',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s'
)

# Load environment variables from .env file
load_dotenv()

# Configuration variables
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_USERNAME = os.getenv("BOT_USERNAME")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
SESSION_NAME = os.getenv("SESSION_NAME")
SESSION_FILENAME = os.getenv("SESSION_FILENAME", f"{SESSION_NAME}.session")

# B2 config
B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APP_KEY = os.getenv("B2_APP_KEY")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME")

# Initialize Bybit session
session = HTTP(api_key=API_KEY, api_secret=API_SECRET, testnet=False, demo=True)

# Initialize Telegram client
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# OTP control
otp_received = None
login_event = asyncio.Event()
last_otp_request_time = 0
OTP_REQUEST_INTERVAL = 60

# ------------------ B2 Functions ------------------
def init_b2_api():
    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    b2_api.authorize_account("production", B2_KEY_ID, B2_APP_KEY)
    return b2_api

def download_session_file_from_b2():
    try:
        b2_api = init_b2_api()
        bucket = b2_api.get_bucket_by_name(B2_BUCKET_NAME)
        file_info = bucket.get_file_info_by_name(SESSION_FILENAME)
        if file_info:
            with open(SESSION_FILENAME, "wb") as f:
                bucket.download_file_by_name(SESSION_FILENAME).save_to(f)
            logging.info("Downloaded session file from B2.")
    except Exception as e:
        logging.warning("Session download skipped: %s", e)

def upload_session_file_to_b2():
    try:
        if not os.path.exists(SESSION_FILENAME):
            logging.warning("Session file missing locally: %s", SESSION_FILENAME)
            return
        b2_api = init_b2_api()
        bucket = b2_api.get_bucket_by_name(B2_BUCKET_NAME)
        with open(SESSION_FILENAME, "rb") as f:
            bucket.upload_bytes(f.read(), SESSION_FILENAME)
        logging.info("Uploaded session file to B2.")
    except Exception as e:
        logging.error("Session upload failed: %s", traceback.format_exc())

# ------------------ Business Logic ------------------

def get_step_size(symbol):
    try:
        instruments = session.get_instruments_info(category="linear")
        linear_list = instruments["result"]["list"]
        symbol_info = next((x for x in linear_list if x["symbol"] == symbol), None)
        if symbol_info:
            return float(symbol_info["lotSizeFilter"]["qtyStep"])
        else:
            raise ValueError(f"Symbol {symbol} not found")
    except Exception:
        logging.error("Step size error: %s", traceback.format_exc())
        raise

def format_trade_details(symbol, price, stop_loss_price, take_profit_price, qty, order_response, equity, wallet_balance):
    order_id = order_response.get("result", {}).get("orderId", "N/A")
    ret_msg = order_response.get("retMsg", "N/A")
    timestamp = order_response.get("time", "N/A")
    trade_info = "\n===== Trade Details =====\n"
    trade_info += f"{'Symbol':<20}: {symbol}\n"
    trade_info += f"{'Price':<20}: {price:,.2f}\n"
    trade_info += f"{'Stop Loss':<20}: {stop_loss_price:,.2f}\n"
    trade_info += f"{'Take Profit':<20}: {take_profit_price:,.2f}\n"
    trade_info += f"{'Quantity':<20}: {qty:,.8f}\n"
    trade_info += f"{'Order ID':<20}: {order_id}\n"
    trade_info += f"{'Status':<20}: {ret_msg}\n"
    trade_info += f"{'Timestamp':<20}: {timestamp}\n"
    trade_info += f"{'USDT Equity':<20}: {equity:,.2f}\n"
    trade_info += f"{'Wallet Balance':<20}: {wallet_balance:,.2f}\n"
    trade_info += "========================\n"
    return trade_info

async def handle_bot_response(event):
    bot_message = event.raw_text.strip('"').strip()
    try:
        parts = bot_message.split("\n")
        symbol, price, stop_loss_price, take_profit_price = None, None, None, None
        for part in parts:
            if part.startswith("Symbol:"):
                symbol = part.replace("Symbol:", "").strip()
            elif part.startswith("Price:"):
                price = float(part.replace("Price:", "").strip())
            elif part.startswith("Stop Loss:"):
                stop_loss_price = float(part.replace("Stop Loss:", "").strip())
            elif part.startswith("Take Profit:"):
                take_profit_price = float(part.replace("Take Profit:", "").strip())

        if not all([symbol, price, stop_loss_price, take_profit_price]):
            raise ValueError("Missing trading parameters")

        step_size = get_step_size(symbol)
        account_balance = session.get_wallet_balance(accountType="UNIFIED")
        wallet_list = account_balance["result"]["list"]
        usdt_data = next((coin for acc in wallet_list for coin in acc.get("coin", []) if coin.get("coin") == "USDT"), None)
        if not usdt_data:
            raise ValueError("USDT balance not found")

        equity = float(usdt_data.get("equity", 0))
        wallet_balance = float(usdt_data.get("walletBalance", 0))

        max_qty = math.floor((wallet_balance / price) / step_size) * step_size
        if max_qty <= 0:
            raise ValueError("Insufficient balance")

        order = session.place_order(
            category="linear", symbol=symbol, side="Buy", order_type="Limit",
            qty=max_qty, price=price, time_in_force="GTC",
            stopLoss=stop_loss_price, takeProfit=take_profit_price
        )

        if order["retCode"] == 0:
            print(format_trade_details(symbol, price, stop_loss_price, take_profit_price, max_qty, order, equity, wallet_balance))
        else:
            raise ValueError(f"Order failed: {order['retMsg']}")

    except Exception as e:
        logging.error("Trade error: %s", traceback.format_exc())

@client.on(events.NewMessage(from_users=BOT_USERNAME))
async def bot_message_handler(event):
    await handle_bot_response(event)

@app.route('/otp', methods=['POST'])
async def receive_otp():
    global otp_received
    try:
        data = request.get_json()
        otp = data.get('otp')
        if not otp:
            return jsonify({"error": "OTP is required"}), 400
        otp_received = otp
        login_event.set()
        return jsonify({"message": "OTP received", "otp": otp}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

async def telegram_login():
    global otp_received, last_otp_request_time
    await client.connect()
    if not await client.is_user_authorized():
        if time.time() - last_otp_request_time < OTP_REQUEST_INTERVAL:
            await asyncio.sleep(OTP_REQUEST_INTERVAL)
        await client.send_code_request(PHONE_NUMBER)
        last_otp_request_time = time.time()
        await login_event.wait()
        if otp_received:
            try:
                await client.sign_in(phone=PHONE_NUMBER, code=otp_received)
            except PhoneCodeInvalidError:
                raise ValueError("Invalid OTP")
            except SessionPasswordNeededError:
                raise ValueError("2FA not supported")
            finally:
                otp_received = None
                login_event.clear()

async def main():
    from threading import Thread
    flask_thread = Thread(target=run_flask, daemon=False)
    flask_thread.start()
    await asyncio.sleep(2)
    download_session_file_from_b2()
    await telegram_login()
    upload_session_file_to_b2()
    await client.run_until_disconnected()

def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    asyncio.run(main())
