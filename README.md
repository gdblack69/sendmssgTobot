import subprocess
import threading
from telethon import TelegramClient, events
import os
import asyncio
import traceback
from flask import Flask
from threading import Thread

# Keep-alive function (run the script in the background)
def run_keep_alive():
    subprocess.run(["python", "keep_alive.py"])

# Start the keep-alive script in a separate thread
keep_alive_thread = threading.Thread(target=run_keep_alive)
keep_alive_thread.daemon = True
keep_alive_thread.start()

# Initialize Telegram clients (existing code continues here...)
source_api_id = 26697231
source_api_hash = '35f2769c773534c6ebf24c9d0731703a'
source_chat_id = -4564401074

destination_api_id = 14135677
destination_api_hash = 'edbecdc187df07fddb10bcff89964a8e'
destination_bot_username = '@gpt3_unlim_chatbot'

source_session_file = "new10_source_session.session"
destination_session_file = "new10_destination_session.session"

if not os.path.exists(source_session_file):
    print("Source session file not found. Creating a new session...")
if not os.path.exists(destination_session_file):
    print("Destination session file not found. Creating a new session...")

source_client = TelegramClient(source_session_file, source_api_id, source_api_hash)
destination_client = TelegramClient(destination_session_file, destination_api_id, destination_api_hash)

async def handle_disconnection():
    while True:
        try:
            await source_client.run_until_disconnected()
        except Exception as e:
            print(f"Error: {e}. Reconnecting...")
            await asyncio.sleep(5)
            await source_client.start()

@source_client.on(events.NewMessage(chats=source_chat_id))
async def forward_message(event):
    source_id_message = event.raw_text

    custom_message = f"""
    "{source_id_message}"
    """

    async with destination_client:
        try:
            await destination_client.send_message(destination_bot_username, custom_message)
            print("Custom message forwarded to destination bot.")
        except Exception as e:
            print(f"Error while forwarding the message: {e}")

async def main():
    print("Starting both clients...")
    await source_client.start()
    await destination_client.start()
    print("Bot is running... Waiting for messages...")
    await handle_disconnection()

if __name__ == "__main__":
    async def run_bot():
        while True:
            try:
                await main()
            except Exception as e:
                print(f"Error occurred: {e}. Restarting the script...")
                await asyncio.sleep(5)

    asyncio.run(run_bot())
