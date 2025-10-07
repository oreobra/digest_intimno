import os
import re
import html
import hashlib
import logging
import sys
import asyncio
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from rapidfuzz import fuzz
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

try:
    import yaml
except Exception:
    yaml = None

# ---------- Настройка ----------
load_dotenv()
TZ = os.environ.get("TZ", "Europe/Amsterdam")
WEEKDAY = int(os.environ.get("WEEKDAY", 0))  # 0 — воскресенье
POST_HOUR = int(os.environ.get("POST_HOUR", 9))
POST_MINUTE = int(os.environ.get("POST_MINUTE", 0))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", 7))
ITEMS_MIN = int(os.environ.get("ITEMS_MIN", 4))
ITEMS_MAX = int(os.environ.get("ITEMS_MAX", 8))
LANG_PREF = os.environ.get("LANG_PREF", "ru")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")

# LLM провайдеры
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_SITE_URL = os.environ.get(
    "OPENROUTER_SITE_URL")  # опционально для реферала
OPENROUTER_APP_NAME = os.environ.get(
    "OPENROUTER_APP_NAME")  # опционально для реферала

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------- Telegra.ph API (без внешней библиотеки) ----------
TELEGRAPH_API = "https://api.telegra.ph"
TELEGRAPH_TOKEN = os.environ.get("TELEGRAPH_ACCESS_TOKEN")
TELEGRAPH_AUTHOR_NAME = os.environ.get(
    "TELEGRAPH_AUTHOR_NAME", "Fashion Digest")
TELEGRAPH_AUTHOR_URL = os.environ.get("TELEGRAPH_AUTHOR_URL")


def tgraph_create_account(short_name: str, author_name: str = "", author_url: str = "") -> dict:
    try:
        r = requests.post(f"{TELEGRAPH_API}/createAccount", data={
            "short_name": short_name[:32] or "digest",
            "author_name": author_name or "",
            "author_url": author_url or "",
        }, timeout=20)
        return r.json()
    except Exception as e:
        logger.warning(f"Telegraph createAccount failed: {e}")
        return {"ok": False, "error": str(e)}


def tgraph_text_to_nodes(text: str) -> list:
    nodes = []
    for para in [p.strip() for p in (text or "").split("\n") if p.strip()]:
        nodes.append({"tag": "p", "children": [para]})
    return nodes


def tgraph_create_page(access_token: str, title: str, nodes: list, author_name: str = "", author_url: str = "") -> dict:
    try:
        import json
        payload = {
            "access_token": access_token,
            "title": (title or "")[:256],
            "author_name": author_name or "",
            "author_url": author_url or "",
            "content": json.dumps(nodes, ensure_ascii=False),  # строка JSON
            "return_content": False,
        }
        r = requests.post(f"{TELEGRAPH_API}/createPage",
                          data=payload, timeout=30)
        return r.json()
    except Exception as e:
        logger.warning(f"Telegraph createPage failed: {e}")
        return {"ok": False, "error": str(e)}


# ---------- Источники (RSS) ----------
GOOGLE_NEWS_RU = "https://news.google.com/rss/search?hl=ru&gl=RU&ceid=RU:ru&q="
GOOGLE_NEWS_EN = "https://news.google.com/rss/search?hl=en&gl=US&ceid=US:en&q="

# Расширяем запросы на общую моду и одежду, сохраняя акцент на бельё
QUERY_TERMS = [
    # русские запросы — общая мода и одежда
    "мода коллекция OR лукбук OR показ",
    "streetwear OR стритвир",
    "капсульная коллекция OR капсула мода",
    "бренд одежды новинки OR релиз",
    "платье OR юбка OR брюки OR джинсы OR пиджак OR пальто",
    "обувь OR кроссовки OR ботинки OR туфли",
    "аксессуары сумка OR украшения OR ремень",
    # бельё и swim
    "женское белье OR бельё",
    "бра OR бюстгальтер OR бралетт",
    "трусы OR трусики",
    "корсет OR боди",
    "купальник fashion",
    "нижнее белье бренд",
    # английские запросы — общая мода и одежда
    "fashion collection OR lookbook OR runway",
    "streetwear drop OR capsule collection",
    "ready-to-wear OR RTW",
    "dress OR skirt OR jeans OR blazer OR coat",
    "footwear sneakers OR boots OR heels",
    "accessories bag OR jewelry OR belt",
    # бельё и swim (EN)
    "lingerie",
    "panties OR briefs",
    "bra OR bralette",
    "corset OR bodysuit",
    "swimwear fashion",
    "intimates market",
]

STATIC_FEEDS = [
    # добавляйте прямые RSS здесь, если нужно
]

# Ключевые слова (можно дополнять через keywords.yaml)
KEYWORDS = {
    "include": [
        # общая мода/одежда
        "мода", "fashion", "лукбук", "lookbook", "runway", "runway show", "collection", "capsule",
        "бренд", "релиз", "дроп", "drop", "показ", "кампания", "campaign",
        "retail", "магазин", "витрина", "мерчандайзинг", "seasons", "resort", "pre-fall", "SS25", "FW25",
        "streetwear", "ready-to-wear", "RTW", "couture", "athleisure",
        # категории одежды
        "платье", "юбка", "брюки", "джинсы", "пиджак", "жакет", "пальто", "трикотаж", "свитер",
        "обувь", "кроссовки", "ботинки", "туфли", "сандалии",
        "аксессуары", "сумка", "рюкзак", "украшения", "ремень", "очки",
        # материалы/качества
        "материалы", "кружево", "сетчатый", "sustainability", "eco", "upcycle", "denim", "silk", "wool",
        # бельё
        "lingerie", "нижнее белье", "bra", "bralette", "корсет", "bodysuit", "swimwear",
    ],
    "exclude": [
        "18+", "эротика", "порно", "adult", "NSFW", "onlyfans", "интим-услуги"
    ]
}

if yaml is not None:
    try:
        kw_path = os.path.join(os.getcwd(), "keywords.yaml")
        if os.path.isfile(kw_path):
            with open(kw_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    KEYWORDS["include"] = list(
                        set(KEYWORDS["include"] + (loaded.get("include") or [])))
                    KEYWORDS["exclude"] = list(
                        set(KEYWORDS["exclude"] + (loaded.get("exclude") or [])))
    except Exception as e:
        logger.warning(f"keywords.yaml load failed: {e}")

# ---------- Утилиты ----------


def normalize_url(u: str) -> str:
    try:
        p = urlparse(u)
        query = "&".join([kv for kv in p.query.split(
            "&") if not kv.lower().startswith("utm_") and kv])
        return urlunparse((p.scheme, p.netloc, p.path, "", query, ""))
    except Exception:
        return u


def fetch_rss(url: str):
    try:
        return feedparser.parse(url)
    except Exception as e:
        logger.warning(f"RSS error {url}: {e}")
        return {"entries": []}


def extract_main_text(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if not downloaded:
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            with_metadata=False,
            output="txt",
        )
        return (text or "").strip()
    except Exception as e:
        logger.warning(f"Extract error {url}: {e}")
        return ""


def relevance_score(title: str, summary: str = "") -> int:
    """0 = не брать, >=1 = подходит; +баллы за бельё."""
    blob = f"{title}\n{summary}".lower()
    if any(bad in blob for bad in [w.lower() for w in KEYWORDS["exclude"]]):
        return 0
    # распознаём общую моду/одежду по ключевикам
    has_fashion = any(w.lower() in blob for w in [
        "fashion", "мода", "runway", "lookbook", "лукбук", "коллекц", "показ", "бренд", "кампания",
        "ритейл", "магазин", "designer", "дизайнер", "collab", "коллаб", "fashion week",
        "streetwear", "ready-to-wear", "rtw", "couture", "athleisure",
        "платье", "юбка", "брюки", "джинсы", "пиджак", "жакет", "пальто", "свитер", "трикотаж",
        "обувь", "кроссовки", "ботинки", "туфли", "сандалии", "сумка", "аксессуары",
    ]) or any(w.lower() in blob for w in [w.lower() for w in KEYWORDS["include"]])
    if not has_fashion:
        return 0
    score = 1
    # бельёвые термины дают дополнительный балл, но не обязательны
    if any(w in blob for w in [
            "lingerie", "белье", "бельё", "нижнее белье", "bra", "bralette", "бюстгальтер",
            "трусы", "трусики", "panties", "briefs", "корсет", "bodysuit", "swimwear", "чулк"
    ]):
        score += 1
    return score


def dedupe(items):
    """Удаляем дубликаты по нормализованному URL и схожести заголовков."""
    seen = set()
    out = []
    for it in items:
        k = normalize_url(it["link"]) or it["link"]
        title = it.get("title", "")
        key = hashlib.md5(k.encode()).hexdigest()
        if key in seen:
            continue
        dup_title = False
        for jt in out:
            if fuzz.partial_ratio(title, jt.get("title", "")) > 90:
                dup_title = True
                break
        if dup_title:
            continue
        seen.add(key)
        out.append(it)
    return out

# ---------- Саммари ----------


OPENAI_MODEL = "gpt-4o-mini"


def _postprocess_summary(title: str, text: str) -> str:
    # Удаляем повторы заголовка и лишнюю длину
    s = (text or "").strip()
    title_low = (title or "").strip().lower()
    if s.lower().startswith(title_low):
        s = s[len(title):].lstrip(" .:-")
    # убираем повтор заголовка внутри первых 60 символов
    if title_low and title_low in s[:120].lower():
        s = re.sub(re.escape(title), "", s, flags=re.IGNORECASE).lstrip(" .:-")
    # ограничение длины
    if len(s) > 220:
        s = s[:217].rstrip() + "…"
    return s


def summarize(title: str, text: str, lang_pref: str = "ru") -> str:
    # 1) OpenRouter
    if OPENROUTER_API_KEY and len(text) > 200:
        try:
            import json
            import urllib.request

            sys_prompt = (
                "Ты — редактор модного медиа. Кратко перескажи новость по моде/одежде/аксессуарам. "
                "Не повторяй заголовок. 1 короткое предложение + 2 очень коротких пункта. "
                "Только факты (бренд/коллекция/даты/цены, если есть). Язык: " +
                ("русский" if lang_pref != "en" else "English") + "."
            )
            user_prompt = f"Заголовок: {title}\nТекст: {text[:6000]}"
            body = json.dumps({
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1,
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            }
            if OPENROUTER_SITE_URL:
                headers["HTTP-Referer"] = OPENROUTER_SITE_URL
            if OPENROUTER_APP_NAME:
                headers["X-Title"] = OPENROUTER_APP_NAME
            req = urllib.request.Request(
                url="https://openrouter.ai/api/v1/chat/completions",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                import json as _json
                j = _json.loads(resp.read())
                return _postprocess_summary(title, j["choices"][0]["message"]["content"].strip())
        except Exception as e:
            logger.warning(f"OpenRouter summary failed: {e}")
    # 2) OpenAI (если есть ключ)
    if OPENAI_API_KEY and len(text) > 200:
        try:
            import json
            import urllib.request

            sys_prompt = (
                "Ты — редактор модного медиа. Кратко перескажи новость по моде/одежде/аксессуарам. "
                "Не повторяй заголовок. 1 короткое предложение + 2 очень коротких пункта. "
                "Только факты (бренд/коллекция/даты/цены, если есть). Язык: " +
                ("русский" if lang_pref != "en" else "English") + "."
            )
            user_prompt = f"Заголовок: {title}\nТекст: {text[:6000]}"
            body = json.dumps({
                "model": OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1,
            }).encode("utf-8")
            req = urllib.request.Request(
                url="https://api.openai.com/v1/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                import json as _json
                j = _json.loads(resp.read())
                return _postprocess_summary(title, j["choices"][0]["message"]["content"].strip())
        except Exception as e:
            logger.warning(f"OpenAI summary failed: {e}")
    # 3) Fallback: экстрактивно коротко
    sentences = re.split(r"(?<=[.!?])\s+", text)
    core_sentences = [s for s in sentences if len(s.split()) > 6][:2]
    core = " ".join(core_sentences).strip()
    return _postprocess_summary(title, core or "")

# ---------- Сбор дайджеста ----------


def collect_sources():
    feeds = list(STATIC_FEEDS)
    for q in QUERY_TERMS:
        if LANG_PREF in ("ru", "both"):
            feeds.append(GOOGLE_NEWS_RU + requests.utils.quote(q))
        if LANG_PREF in ("en", "both"):
            feeds.append(GOOGLE_NEWS_EN + requests.utils.quote(q))
    return feeds


async def gather_candidates():
    items = []
    since = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
    feeds = collect_sources()
    for f in feeds:
        d = fetch_rss(f)
        for e in d.get("entries", []):
            link = e.get("link")
            title = html.unescape(e.get("title", "").strip())
            summary_html = e.get("summary", "") or e.get("description", "")
            summary = BeautifulSoup(
                summary_html, "html.parser").get_text(" ")[:300]
            if not link or not title:
                continue
            published = None
            if e.get("published_parsed"):
                published = datetime(*e.published_parsed[:6])
            elif e.get("updated_parsed"):
                published = datetime(*e.updated_parsed[:6])
            else:
                published = datetime.utcnow()
            if published < since:
                continue
            if relevance_score(title, summary) == 0:
                continue
            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "published": published,
                "source": "rss",
            })
    items.sort(key=lambda x: x["published"], reverse=True)
    return dedupe(items)


async def build_digest(max_items=None):
    # жёсткий лимит 5
    hard_cap = 5
    max_items = min(ITEMS_MAX or hard_cap, hard_cap) if max_items is None else min(
        max_items, hard_cap)

    cand = await gather_candidates()
    out = []
    for it in cand:
        if len(out) >= max_items:
            break
        full_text = extract_main_text(it["link"]) or it.get("summary", "")
        # Telegra.ph: при отсутствии токена пробуем создать аккаунт
        t_url = None
        if full_text and len(full_text) > 200:
            token = os.environ.get("TELEGRAPH_ACCESS_TOKEN")
            if not token:
                acc = tgraph_create_account(
                    short_name=(TELEGRAPH_AUTHOR_NAME or "digest")[:32],
                    author_name=TELEGRAPH_AUTHOR_NAME or "",
                    author_url=TELEGRAPH_AUTHOR_URL or "",
                )
                if acc.get("ok") and acc.get("result"):
                    token = acc["result"].get("access_token")
                    if token:
                        os.environ["TELEGRAPH_ACCESS_TOKEN"] = token
            if token:
                nodes = tgraph_text_to_nodes(full_text)
                resp = tgraph_create_page(
                    access_token=token,
                    title=it["title"],
                    nodes=nodes,
                    author_name=TELEGRAPH_AUTHOR_NAME,
                    author_url=TELEGRAPH_AUTHOR_URL,
                )
                if resp.get("ok") and resp.get("result"):
                    t_url = resp["result"].get("url")
        s = summarize(it["title"], full_text, LANG_PREF)
        out.append({**it, "summary2": s, "tgraph": t_url})
    return out


def pick_emoji(title: str, text: str) -> str:
    blob = f"{title}\n{text}".lower()
    if any(k in blob for k in ["runway", "показ", "fashion week", "подиум", "неделя моды"]):
        return "👗"
    if any(k in blob for k in ["коллекц", "collection", "капсул", "капсула", "lookbook", "лукбук", "drop", "дроп"]):
        return "🧵"
    if any(k in blob for k in ["ретейл", "магазин", "retail", "маркет", "продаж"]):
        return "🛍️"
    if any(k in blob for k in ["видео", "video", "кампания", "campaign", "креатив", "съёмк", "съемк"]):
        return "🎬"
    if any(k in blob for k in ["джинс", "брюки", "пальто", "пиджак", "сумка", "аксессуар", "обувь", "кроссов", "ботинк", "heels"]):
        return "🧥"
    if any(k in blob for k in ["белье", "бельё", "lingerie", "bra", "bralette", "корсет", "bodysuit", "swimwear"]):
        return "🩱"
    return "✨"


def create_digest_page(title: str, items: list) -> str | None:
    try:
        # Заготовка большого материала c оглавлением и разделами
        nodes = []
        # H2 заголовок
        nodes.append({"tag": "h2", "children": [title]})
        # Подзаголовок‑анонс
        lead = (
            "Неделя — про стиль, силуэты и практичные тренды. Ниже — краткие выжимки и ссылки для погружения."
        )
        nodes.append({"tag": "p", "children": [lead]})
        nodes.append({"tag": "p", "children": [""]})
        # Навигация
        nodes.append({"tag": "h3", "children": ["🧭 Навигация"]})
        toc_list = []
        for idx, it in enumerate(items, 1):
            sec_id = f"sec-{idx}"
            toc_list.append({
                "tag": "li",
                "children": [
                    {"tag": "a", "attrs": {"href": f"#{sec_id}"},
                        "children": [it["title"]]}
                ],
            })
        nodes.append({"tag": "ul", "children": toc_list})
        nodes.append({"tag": "p", "children": [""]})
        # Разделы
        for idx, it in enumerate(items, 1):
            sec_id = f"sec-{idx}"
            emoji = pick_emoji(it.get("title", ""), it.get("summary2", ""))
            # Якорь (Telegraph может игнорировать id; оставим ссылку-якорь перед заголовком)
            nodes.append({"tag": "p", "children": [
                {"tag": "a", "attrs": {"name": sec_id}, "children": [""]}
            ]})
            # Заголовок раздела как ссылка на оригинал (умная ссылка в тексте)
            nodes.append({
                "tag": "h3",
                "children": [
                    f"{emoji} ",
                    {"tag": "a", "attrs": {
                        "href": it["link"]}, "children": [it["title"]]},
                ],
            })
            # Короткая выжимка
            if it.get("summary2"):
                nodes.append({"tag": "p", "children": [it["summary2"]]})
            # Дата (мелким) и скрытая умная ссылка
            date_str = it["published"].strftime(
                "%d %b %Y") if it.get("published") else ""
            nodes.append({
                "tag": "p",
                "children": [
                    {"tag": "em", "children": [f"{date_str}"]}, " — ",
                    {"tag": "a", "attrs": {"href": it["link"]}, "children": [
                        "перейти к материалу"]},
                ],
            })
            nodes.append({"tag": "p", "children": [""]})
        # Итоги
        nodes.append({"tag": "h3", "children": ["Итоги недели"]})
        nodes.append({
            "tag": "p",
            "children": [
                "Главное: выбираем носибельные силуэты, фиксируем капсулы и готовим контент с запасом образов. "
                "Переходим к примеркам и тестам: что действительно зайдёт аудитории — оставляем в планах."
            ],
        })

        token = os.environ.get("TELEGRAPH_ACCESS_TOKEN")
        if not token:
            acc = tgraph_create_account(
                short_name=(TELEGRAPH_AUTHOR_NAME or "digest")[:32],
                author_name=TELEGRAPH_AUTHOR_NAME or "",
                author_url=TELEGRAPH_AUTHOR_URL or "",
            )
            if acc.get("ok") and acc.get("result"):
                token = acc["result"].get("access_token")
                if token:
                    os.environ["TELEGRAPH_ACCESS_TOKEN"] = token
        if not token:
            return None
        resp = tgraph_create_page(
            access_token=token,
            title=title,
            nodes=nodes,
            author_name=TELEGRAPH_AUTHOR_NAME,
            author_url=TELEGRAPH_AUTHOR_URL,
        )
        if resp.get("ok") and resp.get("result"):
            return resp["result"].get("url")
    except Exception as e:
        logger.warning(f"Digest page failed: {e}")
    return None


# ---------- Telegram ----------


async def send_typing(bot, chat_id):
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass


def render_message(items, digest_url: str | None = None):
    if not items:
        return "🙈 За неделю ничего достойного. Предложите источники?"
    # Без вступительной фразы — сразу пункты
    bullets = []
    for it in items:
        emoji = pick_emoji(it.get("title", ""), it.get("summary2", ""))
        line = f"{emoji} {it['title']}: {it['summary2']}"
        bullets.append(line)
    cta = "\nДействие: отбираем образы и планируем контент на неделю.\n"
    link = f"\nЧитать полный разбор: {digest_url}" if digest_url else ""
    return "\n\n".join(bullets) + "\n\n" + cta + link


def build_keyboard(items, digest_url: str | None):
    # Одна кнопка на общий разбор; если нет — кнопки по пунктам
    if digest_url:
        return InlineKeyboardMarkup([[InlineKeyboardButton(text="⚡ ПОСМОТРЕТЬ", url=digest_url)]])
    buttons = []
    for i, it in enumerate(items, 1):
        if it.get("tgraph"):
            buttons.append([InlineKeyboardButton(
                text=f"Читать {i}", url=it["tgraph"])])
    return InlineKeyboardMarkup(buttons) if buttons else None


async def post_digest(bot):
    await send_typing(bot, CHANNEL_ID)
    try:
        items = await build_digest()
        digest_url = create_digest_page("Еженедельный модный дайджест", items)
        text = render_message(items, digest_url)
        keyboard = build_keyboard(items, digest_url)
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=not digest_url,
            reply_markup=keyboard,
        )
        logger.info(f"Опубликовано {len(items)} материалов")
    except Exception as e:
        logger.exception(e)
        await bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ Ошибка дайджеста: {e}")


# ---------- Команды ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я собираю еженедельный дайджест по моде и одежде (включая бельё). Используйте /preview для мгновенного черновика."
    )


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(context.bot, update.effective_chat.id)
    try:
        items = await build_digest()
        text = render_message(items)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"⚠️ Ошибка превью: {e}")


# ---------- Планировщик ----------

async def on_start(app: Application):
    scheduler = AsyncIOScheduler(timezone=TZ)
    trigger = CronTrigger(day_of_week=WEEKDAY,
                          hour=POST_HOUR, minute=POST_MINUTE)
    scheduler.add_job(post_digest, trigger, args=[app.bot], id="weekly_digest")
    scheduler.start()
    logger.info(
        f"Планировщик запущен: WEEKDAY={WEEKDAY} {POST_HOUR}:{POST_MINUTE} ({TZ})")


async def preview_console():
    items = await build_digest()
    digest_url = create_digest_page("Еженедельный модный дайджест", items)
    text = render_message(items, digest_url)
    print(text)


async def post_once():
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    await app.initialize()
    try:
        await post_digest(app.bot)
    finally:
        try:
            await app.shutdown()
        except Exception:
            pass


def main():
    # Консольный превью-режим без Telegram и без планировщика
    if os.environ.get("PREVIEW_CONSOLE") == "1" or (len(sys.argv) > 1 and sys.argv[1].lower() == "preview"):
        asyncio.run(preview_console())
        return

    # Одноразовая публикация в канал без расписания
    if os.environ.get("POST_ONCE") == "1" or (len(sys.argv) > 1 and sys.argv[1].lower() == "post"):
        if not BOT_TOKEN or not CHANNEL_ID:
            raise RuntimeError(
                "Для публикации нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID")
        asyncio.run(post_once())
        return

    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в окружении")
    if not CHANNEL_ID:
        raise RuntimeError("TELEGRAM_CHANNEL_ID не задан в окружении")
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.post_init = on_start
    logger.info("Бот запускается…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
