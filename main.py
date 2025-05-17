import os
import asyncio
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from threading import Thread
import b2sdk.v2 as b2
from b2sdk.v2.exception import B2Error

# === CONFIG ===
SOURCE_API_ID = os.environ.get('SOURCE_API_ID')
SOURCE_API_HASH = os.environ.get('SOURCE_API_HASH')
SOURCE_PHONE_NUMBER = os.environ.get('SOURCE_PHONE_NUMBER')
SOURCE_CHAT_ID = os.environ.get('SOURCE_CHAT_ID')

DESTINATION_API_ID = os.environ.get('DESTINATION_API_ID')
DESTINATION_API_HASH = os.environ.get('DESTINATION_API_HASH')
DESTINATION_PHONE_NUMBER = os.environ.get('DESTINATION_PHONE_NUMBER')
DESTINATION_BOT_USERNAME = os.environ.get('DESTINATION_BOT_USERNAME')

B2_KEY_ID = os.environ.get('B2_KEY_ID')
B2_APPLICATION_KEY = os.environ.get('B2_APPLICATION_KEY')
B2_BUCKET_NAME = os.environ.get('B2_BUCKET_NAME')

SESSION_DIR = "/opt/render/project/src"
SOURCE_SESSION_FILE = os.path.join(SESSION_DIR, "source_session.session")
DESTINATION_SESSION_FILE = os.path.join(SESSION_DIR, "destination_session.session")

otp_data = {'source': None, 'destination': None}
otp_request_sent = {'source': False, 'destination': False}

# Validate environment variables
required_vars = {
    'SOURCE_API_ID': SOURCE_API_ID,
    'SOURCE_API_HASH': SOURCE_API_HASH,
    'SOURCE_PHONE_NUMBER': SOURCE_PHONE_NUMBER,
    'SOURCE_CHAT_ID': SOURCE_CHAT_ID,
    'DESTINATION_API_ID': DESTINATION_API_ID,
    'DESTINATION_API_HASH': DESTINATION_API_HASH,
    'DESTINATION_PHONE_NUMBER': DESTINATION_PHONE_NUMBER,
    'DESTINATION_BOT_USERNAME': DESTINATION_BOT_USERNAME,
    'B2_KEY_ID': B2_KEY_ID,
    'B2_APPLICATION_KEY': B2_APPLICATION_KEY,
    'B2_BUCKET_NAME': B2_BUCKET_NAME
}
missing_vars = [key for key, value in required_vars.items() if not value]
if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Convert to appropriate types
try:
    SOURCE_API_ID = int(SOURCE_API_ID)
    SOURCE_CHAT_ID = int(SOURCE_CHAT_ID)
    DESTINATION_API_ID = int(DESTINATION_API_ID)
except ValueError as e:
    raise ValueError("Environment variables SOURCE_API_ID, SOURCE_CHAT_ID, and DESTINATION_API_ID must be valid integers") from e

# === BACKBLAZE B2 SETUP ===
def init_b2_client():
    info = b2.InMemoryAccountInfo()
    b2_api = b2.B2Api(info)
    b2_api.authorize_account("production", B2_KEY_ID, B2_APPLICATION_KEY)
    bucket = b2_api.get_bucket_by_name(B2_BUCKET_NAME)
    return b2_api, bucket

def download_session_file(b2_api, bucket, session_file, remote_path):
    try:
        if os.path.exists(session_file):
            print(f"Local session file {session_file} already exists, skipping download.")
            return True
        bucket.download_file_by_name(remote_path, b2.File(session_file))
        print(f"Downloaded {remote_path} to {session_file}")
        return True
    except B2Error as e:
        print(f"Error downloading {remote_path}: {str(e)}")
        return False

def upload_session_file(b2_api, bucket, session_file, remote_path):
    try:
        if not os.path.exists(session_file):
            print(f"Session file {session_file} does not exist, cannot upload.")
            return False
        bucket.upload_local_file(
            local_file=session_file,
            file_name=remote_path,
            file_infos={"uploaded_by": "telegram-bot"}
        )
        print(f"Uploaded {session_file} to {remote_path}")
        return True
    except B2Error as e:
        print(f"Error uploading {remote_path}: {str(e)}")
        return False

# === TELEGRAM CLIENTS ===
source_client = TelegramClient(SOURCE_SESSION_FILE, SOURCE_API_ID, SOURCE_API_HASH)
destination_client = TelegramClient(DESTINATION_SESSION_FILE, DESTINATION_API_ID, DESTINATION_API_HASH)

# === FLASK APP ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Bot is running. Use /receive_otp to send OTPs."

@app.route('/receive_otp', methods=['POST'])
def receive_otp():
    data = request.json
    account_type = data.get('account_type')
    otp = data.get('otp')
    
    if account_type not in otp_data:
        print(f"Error: Invalid account type '{account_type}' received.")
        return jsonify({"error": "Invalid account type. Must be 'source' or 'destination'."}), 400
    
    print(f"OTP received for {account_type} account: {otp}")
    otp_data[account_type] = otp
    return jsonify({"status": "OTP received successfully", "account": account_type}), 200

# === LOGIN HANDLER ===
async def login_with_phone(client, phone_number, account_type, b2_api, bucket, session_file, remote_path):
    try:
        await client.connect()
        if not await client.is_user_authorized():
            if not otp_request_sent[account_type]:
                print(f"Initiating login for {account_type} account...")
                try:
                    await client.send_code_request(phone_number)
                    otp_request_sent[account_type] = True
                    print(f"OTP request sent successfully to {phone_number} for {account_type} account.")
                except FloodWaitError as e:
                    print(f"Error: Too many requests for {account_type} account. Please wait {e.seconds} seconds before trying again.")
                    return False
                except Exception as e:
                    print(f"Error sending OTP request for {account_type} account: {str(e)}")
                    return False
            
            print(f"Waiting for OTP for {account_type} account...")
            while otp_data[account_type] is None:
                await asyncio.sleep(1)
            
            try:
                await client.sign_in(phone_number, otp_data[account_type])
                print(f"Login successful for {account_type} account.")
                # Upload session file after successful login
                upload_session_file(b2_api, bucket, session_file, remote_path)
                return True
            except SessionPasswordNeededError:
                print(f"Error: Two-factor authentication is enabled for {account_type} account. Password login is not supported.")
                return False
            except Exception as e:
                print(f"Error: Invalid OTP for {account_type} account. Please check the code and try again. Details: {str(e)}")
                otp_data[account_type] = None  # Reset OTP to allow retry
                return False
        else:
            print(f"{account_type.capitalize()} account is already authorized.")
            return True
    except Exception as e:
        print(f"Unexpected error during login for {account_type} account: {str(e)}")
        return False

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

Stop Loss: If given, use that.
If not given, calculate 1.88% below the entry price.

Take Profit: If given, use the lowest TP price.
If not given, calculate 2% above the entry price.

🔹 Output only the filled form, no extra text.

💡 Notes: 'cmp' = current market price
           'sl' = stop loss
           'tp' = take profit
"""
    try:
        await destination_client.send_message(DESTINATION_BOT_USERNAME, custom_message)
        print("Message forwarded successfully to destination bot.")
    except Exception as e:
        print(f"Error forwarding message to destination bot: {str(e)}")

# === MAIN FUNCTION ===
async def start_bot():
    print("Starting Telegram bot...")
    
    # Initialize Backblaze B2
    try:
        b2_api, bucket = init_b2_client()
    except B2Error as e:
        print(f"Failed to initialize Backblaze B2: {str(e)}")
        return

    # Download session files if they exist
    source_session_downloaded = download_session_file(b2_api, bucket, SOURCE_SESSION_FILE, "source_session.session")
    destination_session_downloaded = download_session_file(b2_api, bucket, DESTINATION_SESSION_FILE, "destination_session.session")

    # Proceed with login
    source_success = await login_with_phone(
        source_client, SOURCE_PHONE_NUMBER, 'source', b2_api, bucket, 
        SOURCE_SESSION_FILE, "source_session.session"
    )
    if not source_success:
        print("Failed to log in to source account. Bot cannot proceed.")
        return
    
    destination_success = await login_with_phone(
        destination_client, DESTINATION_PHONE_NUMBER, 'destination', b2_api, bucket, 
        DESTINATION_SESSION_FILE, "destination_session.session"
    )
    if not destination_success:
        print("Failed to log in to destination account. Bot cannot proceed.")
        return
    
    await source_client.start()
    await destination_client.start()
    print("Both Telegram clients are running successfully.")
    
    await source_client.run_until_disconnected()

# === THREAD FOR FLASK ===
def run_flask():
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Flask server on port {port}...")
    app.run(host="0.0.0.0", port=port)  # Replace with Gunicorn in production

# === RUN EVERYTHING ===
if __name__ == "__main__":
    Thread(target=run_flask).start()
    asyncio.run(start_bot())
