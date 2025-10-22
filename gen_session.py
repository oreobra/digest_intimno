from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import os

api_id = int(os.environ.get("TELEGRAM_API_ID") or input("API_ID: ").strip())
api_hash = os.environ.get("TELEGRAM_API_HASH") or input("API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nTELEGRAM_STRING_SESSION:\n")
    print(client.session.save())
    print("\nСкопируй строку выше в .env как TELEGRAM_STRING_SESSION=...\n")
