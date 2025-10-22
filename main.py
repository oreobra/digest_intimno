# main.py — TG-каналы (Telethon) + OpenRouter gpt-4o-mini
# компактные новости в ТГ и длинная статья в Telegraph (inline-ссылки)
# ИЗМЕНЕНИЯ: ссылки теперь внутри строк в Telegram, в Telegram-посте НЕТ вступлений/лидов

import os
import re
import html
import hashlib
import logging
import sys
import asyncio
import random
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
# Планировщик убран: запуск по CRON снаружи (Beget)
from rapidfuzz import fuzz
from dotenv import load_dotenv

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# --- Telethon (чтение постов каналов)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError

load_dotenv()

# ---------- Базовая настройка ----------
TZ = os.environ.get("TZ", "Europe/Amsterdam")
WEEKDAY = int(os.environ.get("WEEKDAY", 0))       # 0 — вс
POST_HOUR = int(os.environ.get("POST_HOUR", 9))
POST_MINUTE = int(os.environ.get("POST_MINUTE", 0))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", 7))
ITEMS_MAX = int(os.environ.get("ITEMS_MAX", 6))
LANG_PREF = os.environ.get("LANG_PREF", "ru")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")

# OpenRouter
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL")
OPENROUTER_APP_NAME = os.environ.get("OPENROUTER_APP_NAME")

# Telethon учётка
TG_API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
TG_API_HASH = os.environ.get("TELEGRAM_API_HASH")
TG_STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION")

# Telegraph
TELEGRAPH_API = "https://api.telegra.ph"
TELEGRAPH_AUTHOR_NAME = os.environ.get(
    "TELEGRAPH_AUTHOR_NAME", "Fashion Digest")
TELEGRAPH_AUTHOR_URL = os.environ.get("TELEGRAPH_AUTHOR_URL")
TELEGRAPH_ACCESS_TOKEN = os.environ.get("TELEGRAPH_ACCESS_TOKEN")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------- Каналы Telegram ----------
TG_CHANNELS = [
    "wearfeelings",
    "devilwearshandm",
    "theblueprintnews",
    "goldchihuahua",
    "fashion_mur",
]

# ---------- Утилиты текста ----------
SITE_TAIL_RE = re.compile(r"\s*[-–—•]\s*[^\n]{2,50}$")
WS_RE = re.compile(r"\s+")


def clean_title(title: str) -> str:
    t = (title or "").strip()
    t = html.unescape(t)
    t = SITE_TAIL_RE.sub("", t).strip(" .–—-·•")
    parts = re.split(r"\s*[:–—-]\s*", t)
    if len(parts) == 2 and fuzz.ratio(parts[0], parts[1]) > 92:
        t = parts[0]
    lines = [x.strip() for x in re.split(r"\s{2,}|\n+", t) if x.strip()]
    if len(lines) >= 2 and fuzz.ratio(lines[0], lines[1]) > 92:
        t = lines[0]
    return WS_RE.sub(" ", t).strip()


def title_key_for_dedupe(t: str) -> str:
    t = clean_title(t).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = WS_RE.sub(" ", t).strip()
    return t


def dedupe(items):
    seen = set()
    out = []
    for it in items:
        tclean = clean_title(it.get("title", ""))
        if not tclean:
            continue
        host = urlparse(it.get("link", "")).netloc.lower(
        ) or it.get("channel", "tg")
        k = hashlib.md5(
            f"{title_key_for_dedupe(tclean)}|{host}".encode()).hexdigest()
        k2 = hashlib.md5(title_key_for_dedupe(tclean).encode()).hexdigest()
        is_dup = any(fuzz.partial_ratio(tclean, jt.get(
            "title_clean", "")) > 92 for jt in out)
        if is_dup or k in seen or k2 in seen:
            continue
        it["title"] = tclean
        it["title_clean"] = tclean
        seen.add(k)
        seen.add(k2)
        out.append(it)
    return out


# ---------- Фильтры ----------
BAD_NEWS_RE = re.compile(
    r"\b(прокуратур|суд|штраф|уголов|расследован|обвин|подозрев|эксплуатац|нарушен[иья])\w*\b",
    flags=re.IGNORECASE
)
FASHION_PASS_RE = re.compile(
    r"\b(коллекц|кампан|лукбук|показ|runway|lookbook|editorial|капсул|capsule|лин[гг]ер[ие]|бель[её]|бренд|коллаб|drop|дроп)\w*\b",
    flags=re.IGNORECASE
)
AD_RE = re.compile(
    r"\b(реклама|спонсорств|партн[её]рск|promocod|промокод|erid|инн\b|сертификат|розыгрыш|разыгрыва|скидк|купон|подписывайтесь|подписка)\b",
    flags=re.IGNORECASE
)
FASHION_CORE_RE = re.compile(
    r"\b(мод[аи]|fashion|couture|дизайн|дизайнер|лук[ -]?бук|lookbook|кампан|коллекц|runway|показ|editorial|капсул|capsule|кро[йя]|силуэт|трикотаж|деним|джинс|пальто|пиджак|сумк|аксессуар|обув|ботинк|кроссов|плать|юбк|брюк|кардиган|тренч|пуховик|креативн\w+ директор|бренд)\b",
    flags=re.IGNORECASE
)


def is_soft_relevant(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if AD_RE.search(t):
        return False
    if BAD_NEWS_RE.search(t) and not (FASHION_PASS_RE.search(t) or FASHION_CORE_RE.search(t)):
        return False
    if not (FASHION_PASS_RE.search(t) or FASHION_CORE_RE.search(t)):
        return False
    if len(re.sub(r"\s+", "", t)) < 25:
        return False
    return True


# ---------- Тон / зачистка ----------
POLICY_TRIGGERS = [
    r"\bэрот\w*", r"\bпорно\w*", r"\bгол(ый|ая|ые)\b", r"\bоголен\w*",
    r"\bинтим\w*", r"\bNSFW\b", r"\bню\b", r"\bобнаж\w*",
]
TRIGGER_RE = re.compile("|".join(POLICY_TRIGGERS), flags=re.IGNORECASE)


def scrub_for_policy(text: str) -> str:
    t = (text or "")
    t = re.sub(r"https?://\S+|t\.me/\S+|www\.\S+", "", t)
    t = re.sub(r"@[A-Za-z0-9_]+", "", t)
    t = TRIGGER_RE.sub(lambda m: m.group(0)[0] + "…", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def fashion_tone_prompt(lang_pref: str = "ru") -> str:
    base = (
        "Пиши как журнальный редактор: уверенно, лаконично, по делу. "
        "Инсайдерский тон, лёгкая ирония без сарказма. "
        "Короткие живые фразы, конкретика: материалы, силуэты, контекст бренда. "
        "Без восклицаний, эмодзи и клише. Настоящее время, активный залог."
    )
    if lang_pref == "en":
        base = (
            "Magazine-editor tone: assured, concise, insider. "
            "Light wit, no sarcasm. Concrete details: materials, silhouettes, brand context. "
            "No exclamation marks or emojis. Present tense, active voice."
        )
    return base


def safer_tone_prompt(lang_pref: str = "ru") -> str:
    return (
        fashion_tone_prompt(lang_pref)
        + " Избегай сексуализированных описаний и упоминаний обнажённого тела; "
        + "говори нейтрально о моде, силуэтах, материалах и контексте бренда."
    )


# ---------- Ротация глаголов ----------
ALT_VERBS = [
    "показывает", "выводит", "открывает", "держит фокус",
    "собирает", "делает ставку", "играет", "подсвечивает",
    "сдвигает акцент", "переосмысляет", "укрупняет силуэт", "работает с фактурой"
]


def vary_verbs(s: str) -> str:
    if not s:
        return s
    if re.search(r"\b(представляет|демонстрирует)\b", s, flags=re.IGNORECASE) and random.random() < 0.5:
        alt = random.choice(ALT_VERBS)
        return re.sub(r"\b(представляет|демонстрирует)\b", alt, s, count=1, flags=re.IGNORECASE)
    return s


# ---------- Вспомогалки ----------
def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return (s[: n-1].rstrip() + "…") if len(s) > n else s


def _postprocess_line(title: str, line: str, max_len: int) -> str:
    s = (line or "").strip()
    if title and title.lower() in s.lower():
        s = re.sub(re.escape(title), "", s,
                   flags=re.IGNORECASE).strip(" .:–—-")
    s = WS_RE.sub(" ", s).strip()
    return _clip(s, max_len)


# ---------- OpenRouter ----------
def llm_call(messages: list[dict], temperature: float = 0.3, timeout: int = 45) -> str:
    if not OPENROUTER_API_KEY:
        return ""
    body = {"model": OPENROUTER_MODEL,
            "messages": messages, "temperature": temperature}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    }
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_APP_NAME:
        headers["X-Title"] = OPENROUTER_APP_NAME

    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                          headers=headers, json=body, timeout=timeout)
        j = r.json()
        if r.status_code == 200 and isinstance(j, dict) and j.get("choices"):
            return j["choices"][0]["message"]["content"] or ""
        if r.status_code == 400 and "filtered" in json.dumps(j).lower():
            safe_msgs = []
            for m in messages:
                if m["role"] == "system":
                    safe_msgs.append(
                        {"role": "system", "content": safer_tone_prompt(LANG_PREF)})
                elif m["role"] == "user":
                    content = m["content"]
                    if isinstance(content, str):
                        content = scrub_for_policy(content)[:2000]
                    safe_msgs.append({"role": "user", "content": content})
                else:
                    safe_msgs.append(m)
            r2 = requests.post("https://openrouter.ai/api/v1/chat/completions",
                               headers=headers,
                               json={"model": OPENROUTER_MODEL,
                                     "messages": safe_msgs,
                                     "temperature": max(0.1, temperature - 0.1)},
                               timeout=timeout)
            j2 = r2.json()
            if r2.status_code == 200 and isinstance(j2, dict) and j2.get("choices"):
                return j2["choices"][0]["message"]["content"] or ""
        logger.warning(f"OpenRouter HTTP {r.status_code}: {j}")
    except Exception as e:
        logger.warning(f"OpenRouter call failed: {e}")
    return ""


# ---------- Вступительная фраза (больше НЕ используем в Telegram) ----------
def generate_intro_line(items: list, lang_pref: str = "ru") -> str:
    topics = []
    for it in items[:8]:
        t = it.get("title") or ""
        if t:
            topics.append(t.strip())
    topics_blob = "\n".join(f"• {t}" for t in topics) if topics else ""
    raw = llm_call([
        {"role": "system", "content": safer_tone_prompt(
            lang_pref) + " Одна короткая вступительная реплика (8–14 слов)."},
        {"role": "user", "content": scrub_for_policy(
            topics_blob) if topics_blob else "Контекст недели: мода, показы, кампании, розница."},
    ], temperature=0.5).strip()
    if not raw:
        return "Стиль — это выбор на каждый день, а не календарь."
    raw = raw.strip(" \n\"'“”‘’")
    raw = re.sub(r"\s+", " ", raw)
    raw = re.sub(r":\s*$", "", raw)
    return _clip(raw, 120)


# ---------- Одна строка для Telegram ----------
def summarize_line(title: str, text: str, lang_pref: str = "ru") -> str:
    blob = (text or "").strip()
    if OPENROUTER_API_KEY and len(blob) > 60:
        raw = llm_call([
            {"role": "system", "content":
             safer_tone_prompt(lang_pref) +
             " Верни ОДНУ живую строку-новость (≈12–20 слов), как в модном журнале. "
             "Без буллетов, кавычек, эмодзи и источников. "
             "Не повторяй заголовок. Настоящее время."},
            {"role": "user",
                "content": f"Заголовок: {clean_title(title)}\nТекст: {scrub_for_policy(blob)[:1200]}"},
        ], temperature=0.5).strip()

        if raw:
            s = raw.strip(" \n\"'“”‘’•-—–")
            s = re.sub(r"\s+", " ", s)
            tclean = clean_title(title)
            if tclean and tclean.lower() in s.lower():
                s = re.sub(re.escape(tclean), "", s,
                           flags=re.IGNORECASE).strip(" .,:;—–-")
            s = s.replace(":", " ").replace(" — ", " ").replace("–", " ")
            s = vary_verbs(s)
            words = s.split()
            if len(words) > 20:
                s = " ".join(words[:20]) + "…"
            return s

    sents = [x.strip()
             for x in re.split(r"(?<=[.!?])\s+", blob) if len(x.split()) > 6]
    s = sents[0] if sents else clean_title(title)
    s = re.sub(r"[:—–-]\s*$", "", s)
    s = vary_verbs(s)
    words = s.split()
    if len(words) > 20:
        s = " ".join(words[:20]) + "…"
    return s


# ---------- Пара для Telegraph ----------
def summarize_pair(title: str, text: str, lang_pref: str = "ru") -> tuple[str, str]:
    hook, desc = "", ""
    blob = scrub_for_policy(text or "")
    tclean = clean_title(title)

    if OPENROUTER_API_KEY and len(blob) > 120:
        raw = llm_call([
            {"role": "system", "content":
             safer_tone_prompt(lang_pref) +
             " РОВНО две строки: 1) хук (образно, но предметно); 2) отдельный факт/деталь. "
             "Без имён персон (если это не бренд), без двоеточий и эмодзи. "
             "Не повторяй заголовок ни в одной строке."},
            {"role": "user",
                "content": f"Заголовок: {tclean}\nТекст: {blob[:6000]}"},
        ], temperature=0.5).strip()

        if raw:
            parts = [p.strip(" •-–—\"'“”‘’")
                     for p in re.split(r"\n+", raw) if p.strip()]
            hook = parts[0] if parts else ""
            desc = parts[1] if len(parts) > 1 else ""

    def _sanitize(s: str) -> str:
        s = re.sub(r"\s+", " ", (s or "").strip())
        if tclean and tclean.lower() in s.lower():
            s = re.sub(re.escape(tclean), "", s,
                       flags=re.IGNORECASE).strip(" .,:;—–-")
        return s.replace(":", " ")

    sents = [s.strip()
             for s in re.split(r"(?<=[.!?])\s+", blob) if len(s.split()) > 6]
    hook = _sanitize(hook) or (sents[0] if sents else "")
    desc = _sanitize(desc)
    if not desc or desc.lower() == hook.lower():
        desc = next((s for s in sents[1:] if s and s.lower() not in (
            hook.lower(), tclean.lower())), "")
    if desc and (desc.lower() == hook.lower() or fuzz.partial_ratio(hook, desc) > 90):
        desc = _clip(desc + " Подробности — в материале.", 160)

    hook = _postprocess_line(title, vary_verbs(hook), max_len=120)
    desc = _postprocess_line(title, vary_verbs(desc), max_len=160)
    return hook, desc


# ---------- Чтение из TG-каналов ----------
def extract_button_url_and_text(msg):
    try:
        if msg and msg.reply_markup and msg.reply_markup.rows:
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    url = getattr(btn, "url", None)
                    text = getattr(btn, "text", None)
                    if url and text:
                        return text.strip(), url
    except Exception:
        pass
    return None, None


async def fetch_tg_items(since: datetime):
    if not (TG_API_ID and TG_API_HASH and TG_STRING_SESSION):
        logger.warning("Telethon creds missing; skip TG.")
        return []
    items = []
    async with TelegramClient(StringSession(TG_STRING_SESSION), TG_API_ID, TG_API_HASH) as client:
        for uname in TG_CHANNELS:
            try:
                entity = await client.get_entity(uname)
                raw_msgs = []
                async for m in client.iter_messages(entity, limit=600):
                    if not m:
                        continue
                    raw_msgs.append(m)

                # Склейка альбомов
                groups, singles = {}, []
                for m in raw_msgs:
                    if m.grouped_id:
                        groups.setdefault(m.grouped_id, []).append(m)
                    else:
                        singles.append(m)
                merged = []
                for gid, msgs in groups.items():
                    msgs.sort(key=lambda x: len(
                        (x.message or "").strip()), reverse=True)
                    merged.append(msgs[0])
                merged.extend(singles)

                count_for_chan = 0
                for msg in merged:
                    d = (msg.date.replace(tzinfo=None)
                         if msg.date else datetime.now()).replace(microsecond=0)
                    if d < since:
                        continue

                    text_full = (msg.message or "").strip()
                    if not is_soft_relevant(text_full):
                        continue

                    btn_text, btn_url = extract_button_url_and_text(msg)
                    title_candidate = (text_full.split("\n", 1)[
                                       0] if text_full else "").strip()
                    if not title_candidate and btn_text:
                        title_candidate = btn_text.strip()
                    if not title_candidate and text_full:
                        title_candidate = text_full[:80].strip()
                    if not title_candidate and not btn_url:
                        continue

                    title = clean_title(title_candidate or "")
                    if not title:
                        title = clean_title(
                            (text_full[:80] if text_full else btn_text or "") or "")
                    if not title:
                        continue

                    permalink = btn_url or f"https://t.me/{uname}/{msg.id}"

                    items.append({
                        "title": title,
                        "summary": (text_full or title)[:400],
                        "link": permalink,
                        "published": d,
                        "source": "tg",
                        "channel": uname,
                        "full_text": text_full or title,
                    })
                    count_for_chan += 1

                logger.info(f"TG @{uname}: собрано {count_for_chan} постов")
            except ChannelPrivateError:
                logger.warning(
                    f"TG fetch @{uname} failed: private channel (нужна подписка этим аккаунтом).")
            except ChatAdminRequiredError:
                logger.warning(
                    f"TG fetch @{uname} failed: нет прав читать историю.")
            except Exception as e:
                logger.warning(f"TG fetch @{uname} failed: {e}")
    return items


# ---------- Сбор и подготовка ----------
async def gather_candidates():
    since = (datetime.now(timezone.utc) -
             timedelta(days=LOOKBACK_DAYS)).replace(tzinfo=None)
    tg_items = await fetch_tg_items(since)
    items = tg_items
    items.sort(key=lambda x: x["published"], reverse=True)
    return dedupe(items)


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


async def build_digest(max_items=None):
    hard_cap = 6
    max_items = min(ITEMS_MAX or hard_cap, hard_cap) if max_items is None else min(
        max_items, hard_cap)
    cand = await gather_candidates()
    out = []
    for it in cand:
        if len(out) >= max_items:
            break
        text_for_sum = it.get("full_text") or it.get("summary", "")
        hook, desc = summarize_pair(
            it["title"], text_for_sum, LANG_PREF)   # для Telegraph
        line = summarize_line(it["title"], text_for_sum,
                              LANG_PREF)         # для Telegram
        out.append({**it, "hook": hook, "desc": desc, "line": line})
    return out


# ---------- Умные ссылки для TELEGRAM (Markdown) ----------
CAPITAL_PHRASE_RE = re.compile(
    r"\b([A-ZА-ЯЁ][\w\-]{2,}(?:\s+[A-ZА-ЯЁ][\w\-]{2,}){0,2})\b")


def _mk_link(text: str, url: str) -> str:
    """
    Внутри одной «строки-новости» аккуратно превращаем первую капитальную фразу
    (или первые 3–5 слов) в [кликабельную ссылку](url) для Telegram Markdown.
    """
    s = (text or "").strip()
    if not s or not url:
        return s

    # избегаем Already-linked
    if "](" in s:
        return s

    # выбираем фразу
    m = CAPITAL_PHRASE_RE.search(s)
    if m:
        a, b = m.span()
        before = s[:a]
        mid = m.group(0)
        after = s[b:]
    else:
        words = s.split()
        n = min(5, max(3, len(words)//4 or 3))
        mid = " ".join(words[:n])
        before = ""
        after = " ".join(words[n:])
        if after:
            after = " " + after  # сохранить пробел

    # минимальная защита от спецсимволов в Markdown
    mid = mid.replace("[", "(").replace("]", ")")
    linked = f"{before}[{mid}]({url}){after}"
    return linked


# ---------- Telegraph (без «умных ссылок», просто текст) ----------
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


def tgraph_create_page(access_token: str, title: str, nodes: list, author_name: str = "", author_url: str = "") -> dict:
    try:
        payload = {
            "access_token": access_token,
            "title": (title or "")[:256],
            "author_name": author_name or "",
            "author_url": author_url or "",
            "content": json.dumps(nodes, ensure_ascii=False),
            "return_content": False,
        }
        r = requests.post(f"{TELEGRAPH_API}/createPage",
                          data=payload, timeout=30)
        return r.json()
    except Exception as e:
        logger.warning(f"Telegraph createPage failed: {e}")
        return {"ok": False, "error": str(e)}


def create_digest_page(title: str, items: list) -> str | None:
    try:
        token = os.environ.get("TELEGRAPH_ACCESS_TOKEN")
        if not token:
            acc = tgraph_create_account(short_name=(TELEGRAPH_AUTHOR_NAME or "digest")[:32],
                                        author_name=TELEGRAPH_AUTHOR_NAME or "",
                                        author_url=TELEGRAPH_AUTHOR_URL or "")
            if acc.get("ok") and acc.get("result"):
                token = acc["result"].get("access_token")
                if token:
                    os.environ["TELEGRAPH_ACCESS_TOKEN"] = token
        if not token:
            return None

        nodes = []
        def add(n): nodes.append(n)

        add({"tag": "h2", "children": [title]})
        # spacer, чтобы превью не склеивало заголовок со следующим абзацем
        add({"tag": "p", "children": [" "]})
        add({"tag": "p", "children": [
            "Неделя в моде: коллекции, показы, кампании и розница. Ниже — выжимки и максимум деталей."
        ]})
        add({"tag": "h3", "children": ["🧭 Оглавление"]})
        toc = []
        for i, it in enumerate(items, 1):
            toc.append({"tag": "li", "children": [
                {"tag": "a", "attrs": {"href": it["link"]}, "children": [
                    f"{i}. {it['title']}"]}
            ]})
        add({"tag": "ul", "children": toc})

        used = 0
        SAFE = 61000

        def ok(s: str) -> bool:
            nonlocal used
            inc = len(s)
            if used + inc > SAFE:
                return False
            used += inc
            return True

        for it in items:
            emoji = pick_emoji(it.get("title", ""), it.get("hook", ""))
            add({"tag": "h3", "children": [
                emoji + " ",
                {"tag": "a", "attrs": {
                    "href": it["link"]}, "children": [it["title"]]}
            ]})
            if it.get("hook"):
                add({"tag": "p", "children": [it["hook"]]})
                ok(it["hook"])
            if it.get("desc"):
                add({"tag": "p", "children": [it["desc"]]})
                ok(it["desc"])

            full_text = (it.get("full_text") or "").strip()
            if full_text:
                for p in [p.strip() for p in full_text.split("\n") if p.strip()]:
                    if not ok(p):
                        break
                    add({"tag": "p", "children": [p]})

        resp = tgraph_create_page(
            access_token=token, title=title, nodes=nodes,
            author_name=TELEGRAPH_AUTHOR_NAME, author_url=TELEGRAPH_AUTHOR_URL
        )
        if resp.get("ok") and resp.get("result"):
            return resp["result"].get("url")
    except Exception as e:
        logger.warning(f"Digest page failed: {e}")
    return None


# ---------- Telegram вывод ----------
IRONIC_FALLBACKS = [
    "Похоже, индустрия взяла выходной. Мода молчит.",
    "Редкий случай: неделя тише, чем библиотека ночью.",
    "Новостей нет — можно спокойно обновить капсулу.",
    "Ленты пусты, зато витрины — нет. Прогуляемся?"
]


async def send_typing(bot, chat_id):
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass


def render_message(items, digest_url: str | None = None):
    if not items:
        return random.choice(IRONIC_FALLBACKS)

    # ТОЛЬКО заголовок + пункты. Никаких вступлений/«лидов».
    title = "Еженедельный модный дайджест"
    lines = [f"*{title}*"]

    # Список новостей с умными ссылками
    seen_lines = []
    for it in items:
        emoji = pick_emoji(it.get("title", ""), it.get(
            "line", "") or it.get("hook", ""))
        raw_line = (it.get("line") or "").replace(":", " ").strip()
        # анти-дубли
        if any(fuzz.partial_ratio(raw_line, prev) > 92 for prev in seen_lines):
            continue
        seen_lines.append(raw_line)
        linked_line = _mk_link(raw_line, it.get("link", ""))
        lines.append(f"{emoji} {linked_line}")

    if digest_url:
        lines.append(f"[Читать полностью]({digest_url})")

    return "\n\n".join(lines).strip()


async def post_digest(bot):
    await send_typing(bot, CHANNEL_ID)
    try:
        items = await build_digest()
        digest_url = create_digest_page(
            "Еженедельный модный дайджест", items) if items else None
        text = render_message(items, digest_url)
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
        logger.info(
            f"Опубликовано {len(items)} материалов; telegraph={bool(digest_url)}")
    except Exception as e:
        logger.exception(e)
        await bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ Ошибка дайджеста: {e}")


# ---------- Команды ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! /preview — черновик модного дайджеста.")


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_typing(context.bot, update.effective_chat.id)
    try:
        items = await build_digest()
        digest_url = create_digest_page(
            "Еженедельный модный дайджест (черновик)", items) if items else None
        text = render_message(items, digest_url)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"⚠️ Ошибка превью: {e}")


# ---------- Планировщик отключён (CRON снаружи) ----------
# Запуск выполняется внешним CRON: POST_ONCE=1 python3 main.py


# ---------- Режимы запуска ----------
async def preview_console():
    items = await build_digest()
    digest_url = create_digest_page(
        "Еженедельный модный дайджест (консоль)", items) if items else None
    print(render_message(items, digest_url))


async def post_once():
    request = HTTPXRequest(
        connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
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
    if os.environ.get("PREVIEW_CONSOLE") == "1" or (len(sys.argv) > 1 and sys.argv[1].lower() == "preview"):
        asyncio.run(preview_console())
        return
    if os.environ.get("POST_ONCE") == "1" or (len(sys.argv) > 1 and sys.argv[1].lower() == "post"):
        if not BOT_TOKEN or not CHANNEL_ID:
            raise RuntimeError(
                "Нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID")
        asyncio.run(post_once())
        return

    # Режим постоянного polling отключён. Используйте CRON с POST_ONCE=1.
    print("CRON-режим: установите POST_ONCE=1 для публикации, PREVIEW_CONSOLE=1 для предпросмотра.")


if __name__ == "__main__":
    main()
