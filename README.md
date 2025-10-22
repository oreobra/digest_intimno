# digest_intimno

Модный дайджест для Telegram: сбор свежих постов из источников, краткие выжимки для поста в канал и полная статья в Telegraph.

## Быстрый старт (локально)

1) Python 3.11+ и виртуальное окружение:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt telethon
```

2) Переменные окружения (создайте `.env`):
```env
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHANNEL_ID=@your_channel_or_id
TZ=Europe/Amsterdam
# для чтения каналов (опционально)
TELEGRAM_API_ID=12345
TELEGRAM_API_HASH=abcdef...
TELEGRAM_STRING_SESSION=...  # безопасно храните!
# OpenRouter (опционально)
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_SITE_URL=https://example.com
OPENROUTER_APP_NAME=digest_intimno
# Telegraph (опционально)
TELEGRAPH_AUTHOR_NAME=Fashion Digest
TELEGRAPH_AUTHOR_URL=https://t.me/your_channel
```

3) Разовая публикация «сейчас» в канал:
```bash
TZ=Europe/Amsterdam POST_ONCE=1 python3 main.py
```

4) Предпросмотр в консоль (без отправки):
```bash
PREVIEW_CONSOLE=1 python3 main.py
```

5) Обычный режим бота (с планировщиком раз в неделю):
Переменные `WEEKDAY`, `POST_HOUR`, `POST_MINUTE` задаются через окружение. Пример — каждую среду в 09:00 по `TZ`:
```bash
WEEKDAY=3 POST_HOUR=9 POST_MINUTE=0 python3 main.py
```

## Полезные скрипты
- `check_tg.py` — проверка доступности и подписки на каналы источников.

## Безопасность
- Не коммитьте `.env`, сессионные файлы Telethon и виртуальное окружение. См. `.gitignore`.
- Храните секреты в GitHub Secrets/Actions при деплое.

## Авто-деплой на Railway

1) **Создай аккаунт на Railway:**
   - Зайди на https://railway.app
   - Войди через GitHub

2) **Создай новый проект:**
   - New Project → Deploy from GitHub repo
   - Выбери `oreobra/digest_intimno`
   - Railway автоматически определит Python и запустит

3) **Настрой переменные окружения в Railway:**
   - Variables → Add Variable
   - Добавь все секреты из `.env`:
     ```
     TELEGRAM_BOT_TOKEN=xxx
     TELEGRAM_CHANNEL_ID=@your_channel
     TZ=Europe/Amsterdam
     TELEGRAM_API_ID=12345
     TELEGRAM_API_HASH=abcdef...
     TELEGRAM_STRING_SESSION=...
     OPENROUTER_API_KEY=...
     # и т.д.
     ```

4) **Настрой GitHub Actions (опционально):**
   - В GitHub репозитории: Settings → Secrets and variables → Actions
   - Добавь `RAILWAY_TOKEN` и `RAILWAY_SERVICE`
   - Теперь каждый push в `main` будет автоматически деплоить

5) **Проверь деплой:**
   - Railway покажет URL и логи
   - Бот должен запуститься автоматически

## Альтернативы
- **Heroku:** используй `Procfile` вместо `railway.json`
- **VPS:** настрой SSH-деплой через GitHub Actions

## Лицензия
Добавьте файл LICENSE при необходимости.
