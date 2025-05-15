import os
import asyncio
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from threading import Thread  

# === CONFIG ===
SOURCE_API_ID = 26697231
SOURCE_API_HASH = "35f2769c773534c6ebf24c9d0731703a"
SOURCE_PHONE_NUMBER = "+919598293175"
SOURCE_CHAT_ID = -1002256615512

DESTINATION_API_ID = 14135677
DESTINATION_API_HASH = "edbecdc187df07fddb10bcff89964a8e"
DESTINATION_PHONE_NUMBER = "+917897293175"
DESTINATION_BOT_USERNAME = "@gpt3_unlim_chatbot"

SOURCE_SESSION_FILE = "source_session.session"
DESTINATION_SESSION_FILE = "destination_session.session"

otp_data = {'source': None, 'destination': None}

# === TELEGRAM CLIENTS ===
source_client = TelegramClient(SOURCE_SESSION_FILE, SOURCE_API_ID, SOURCE_API_HASH)
destination_client = TelegramClient(DESTINATION_SESSION_FILE, DESTINATION_API_ID, DESTINATION_API_HASH)

# === FLASK APP ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running. Use /receive_otp to send OTPs."

@app.route('/receive_otp', methods=['POST'])
def receive_otp():
    data = request.json
    account_type = data.get('account_type')
    otp = data.get('otp')

    if account_type in otp_data:
        otp_data[account_type] = otp
        return jsonify({"status": "OTP received", "account": account_type}), 200
    else:
        return jsonify({"error": "Invalid account type"}), 400

# === LOGIN HANDLER ===
async def login_with_phone(client, phone_number, account_type):
    await client.connect()
    if not await client.is_user_authorized():
        print(f"Sending code to {account_type} account...")
        await client.send_code_request(phone_number)

        while otp_data[account_type] is None:
            print(f"Waiting for OTP for {account_type}...")
            await asyncio.sleep(1)

        try:
            await client.sign_in(phone_number, otp_data[account_type])
            print(f"{account_type.capitalize()} login successful.")
        except Exception as e:
            print(f"Failed to login {account_type}: {e}")

# === TELEGRAM EVENT HANDLER ===
@source_client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
async def forward_message(event):
    message = event.raw_text
    custom_message = f"""
"{message}"
 
If the text inside double quotes is not a trading signal or says to short/sell, reply with:
👉 "No it's not your call"

If it's a buy/long signal, extract the details and fill the form like this:

Symbol: Use the coin name with 'USDT' (without '/').

Price: Take the highest entry price.

If it says 'buy at cmp', take the CMP given and add 10% as the price in the form.

Stop Loss (SL): If given, use that.
If not given, calculate 1.88% below the entry price.

Take Profit (TP): If given, use the lowest TP price.
If not given, calculate 2% above the entry price.

🔹 Output only the filled form, no extra text.

💡 Notes: 'cmp' = current market price
           'sl' = stop loss
           'tp' = take profit

If the text says 'buy at cmp', use CMP for SL and TP as per message (or calculate if not given). But 
always show the price in the form as 10% higher than CMP.
"""
    try:
        await destination_client.send_message(DESTINATION_BOT_USERNAME, custom_message)
        print("Message forwarded to destination bot.")
    except Exception as e:
        print(f"Error forwarding message: {e}")

# === MAIN FUNCTION ===
async def start_bot():
    await login_with_phone(source_client, SOURCE_PHONE_NUMBER, 'source')
    await login_with_phone(destination_client, DESTINATION_PHONE_NUMBER, 'destination')

    await source_client.start()
    await destination_client.start()

    print("Both clients running...")

    await source_client.run_until_disconnected()

# === THREAD FOR FLASK ===
def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)

# === RUN EVERYTHING ===
if __name__ == "__main__":
    Thread(target=run_flask).start()

    asyncio.run(start_bot())
