import os
import asyncio
import socket
import traceback
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from threading import Thread
import b2sdk.v2 as b2
from b2sdk.v2.exception import B2Error, FileNotPresent

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
        # Check if file exists in bucket
        file_info = bucket.get_file_info_by_name(remote_path)
        if not file_info:
            print(f"No session file found in B2 at {remote_path}")
            return False
            
        if os.path.exists(session_file):
            print(f"Local session file {session_file} already exists, skipping download.")
            return True
            
        bucket.download_file_by_name(remote_path, b2.File(session_file))
        print(f"Downloaded {remote_path} to {session_file}")
        if os.path.exists(session_file):
            print(f"Confirmed {session_file} exists locally after download.")
            return True
        else:
            print(f"Error: {session_file} not found locally after download attempt.")
            return False
    except FileNotPresent:
        print(f"Session file {remote_path} does not exist in B2 bucket.")
        return False
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
        # Check if session file exists and is valid
        if os.path.exists(session_file):
            print(f"Session file {session_file} found, checking validity...")
            try:
                if await client.is_user_authorized():
                    print(f"{account_type.capitalize()} account is already authorized.")
                    return True
                else:
                    print(f"Session file {session_file} is invalid or expired.")
                    os.remove(session_file)  # Remove invalid session file
            except Exception as e:
                print(f"Error validating session file {session_file}: {str(e)}\n{traceback.format_exc()}")
                os.remove(session_file)  # Remove invalid session file
        else:
            print(f"No session file found locally at {session_file}")

        # Proceed with OTP login if not authorized
        if not otp_request_sent[account_type]:
            print(f"Initiating login for {account_type} account...")
            try:
                await client.send_code_request(phone_number)
                otp_request_sent[account_type] = True
                print(f"OTP request sent successfully to {phone_number} for {account_type} account.")
            except FloodWaitError as e:
                print(f"Error: Too many requests for {account_type} account. Please wait {e.seconds} seconds.")
                return False
            except Exception as e:
                print(f"Error sending OTP request for {account_type} account: {str(e)}\n{traceback.format_exc()}")
                return False
        
        print(f"Waiting for OTP for {account_type} account...")
        timeout = 60  # Wait up to 60 seconds for OTP
        start_time = asyncio.get_event_loop().time()
        while otp_data[account_type] is None:
            if asyncio.get_event_loop().time() - start_time > timeout:
                print(f"Timeout waiting for OTP for {account_type} account.")
                return False
            await asyncio.sleep(1)
        
        try:
            await client.sign_in(phone_number, otp_data[account_type])
            print(f"Login successful for {account_type} account.")
            # Upload session file after successful login
            if upload_session_file(b2_api, bucket, session_file, remote_path):
                print(f"Session file uploaded to B2 for {account_type} account.")
            else:
                print(f"Failed to upload session file for {account_type} account.")
            return True
        except SessionPasswordNeededError:
            print(f"Error: Two-factor authentication enabled for {account_type} account.")
            return False
        except Exception as e:
            print(f"Error: Invalid OTP for {account_type} account: {str(e)}\n{traceback.format_exc()}")
            otp_data[account_type] = None
            return False
    except Exception as e:
        print(f"Unexpected error during login for {account_type} account: {str(e)}\n{traceback.format_exc()}")
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
        print("Message forwarded successfully to destination bot.")
    except Exception as e:
        print(f"Error forwarding message to destination bot: {str(e)}\n{traceback.format_exc()}")

# === MAIN FUNCTION ===
async def start_bot():
    print(f"Starting Telegram bot at {socket.gethostbyname(socket.gethostname())}...")
    
    try:
        b2_api, bucket = init_b2_client()
        print("Backblaze B2 initialized successfully.")
    except B2Error as e:
        print(f"Failed to initialize Backblaze B2: {str(e)}\n{traceback.format_exc()}")
        return

    source_session_downloaded = download_session_file(b2_api, bucket, SOURCE_SESSION_FILE, "source_session.session")
    destination_session_downloaded = download_session_file(b2_api, bucket, DESTINATION_SESSION_FILE, "destination_session.session")

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
    
    # Verify both clients are fully authenticated
    await source_client.start()
    if not await source_client.is_user_authorized():
        print("Source client failed post-start authorization check.")
        return
    await destination_client.start()
    if not await destination_client.is_user_authorized():
        print("Destination client failed post-start authorization check.")
        return
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
