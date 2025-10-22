# check_tg.py
import os
import asyncio
from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError, UsernameInvalidError, UsernameNotOccupiedError
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH")
STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION")

# ---- настройки из .env ----
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH")
STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION")

# Список каналов по умолчанию (можно менять)
TG_CHANNELS = [
    "wearfeelings",
    "devilwearshandm",
    "theblueprintnews",
    "goldchihuahua",
    "fashion_mur",
]


def short(s: str, n: int = 100) -> str:
    s = (s or "").strip().replace("\n", " ")
    return (s[: n - 1] + "…") if len(s) > n else s


async def check_channel(client: TelegramClient, uname: str):
    print(f"\n— @{uname}")
    try:
        entity = await client.get_entity(uname)
    except UsernameInvalidError:
        print("  ⚠️ Некорректный юзернейм (проверь @username).")
        return
    except UsernameNotOccupiedError:
        print("  ⚠️ Канал по такому @username не найден.")
        return
    except ChannelPrivateError:
        print("  🚫 Канал приватный. Нужна подписка именно этим аккаунтом (STRING_SESSION).")
        return
    except Exception as e:
        print(f"  ⚠️ Ошибка получения канала: {e}")
        return

    # Проверим, подписаны ли
    subscribed = True
    try:
        me = await client.get_me()
        perms = await client.get_permissions(entity, me)
        # если нет прав читать историю, будет исключение; если дошли сюда — подписаны
    except Exception:
        subscribed = False

    print(f"  Подписка: {'✅ да' if subscribed else '❌ нет'}")

    # Печатаем последние 5 сообщений
    msgs = []
    try:
        async for m in client.iter_messages(entity, limit=5):
            msgs.append(m)
    except ChatAdminRequiredError:
        print("  🚫 Нет прав читать историю.")
        return
    except ChannelPrivateError:
        print("  🚫 Приватный канал: подпишитесь сначала.")
        return
    except Exception as e:
        print(f"  ⚠️ Ошибка чтения сообщений: {e}")
        return

    if not msgs:
        print("  ℹ️ Сообщений не получено. Возможные причины:")
        print("     • Вы не подписаны, и история скрыта для гостей.")
        print("     • В API история доступна только для новых постов после подписки.")
        print("     • В канале не было постов в последнее время.")
        return

    for m in reversed(msgs):
        dt = m.date
        # покажем локальное время пользователя
        if dt and dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        link = f"https://t.me/{uname}/{m.id}"
        head = (m.message or "").strip().split("\n", 1)[0]
        print(f"  • {dt:%Y-%m-%d %H:%M} — {short(head, 90)}")
        print(f"    {link}")


async def main():
    if not (API_ID and API_HASH and STRING_SESSION):
        print("❌ В .env должны быть TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_STRING_SESSION")
        return
    async with TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(
            f"Вошли как: {me.first_name or ''} {me.last_name or ''} (@{me.username or '—'})")
        for uname in TG_CHANNELS:
            await check_channel(client, uname)

if __name__ == "__main__":
    asyncio.run(main())
