#!/usr/bin/env python3
"""សម្លេង — Telegram text-to-speech bot.

Webhook-ready for Vercel and also runnable locally with polling.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import asyncio
import contextlib
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import edge_tts
import httpx
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

import somleng_stt as stt

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DEFAULT_VOICE = os.getenv("TTS_VOICE", "km-KH-PisethNeural").strip()
DEFAULT_RATE = os.getenv("TTS_RATE", "+0%").strip()
DEFAULT_VOLUME = os.getenv("TTS_VOLUME", "+0%").strip()
DEFAULT_TTS_MODEL = os.getenv("TTS_MODEL", "edge").strip() or "edge"
INWORLD_DEFAULT_VOICE = os.getenv("INWORLD_VOICE", "Ashley").strip() or "Ashley"
INWORLD_LANGUAGE = os.getenv("INWORLD_LANGUAGE", "km-KH").strip() or "km-KH"
INWORLD_DELIVERY_MODE = os.getenv("INWORLD_DELIVERY_MODE", "BALANCED").strip() or "BALANCED"
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY", "").strip()
KIRI_API_KEY = os.getenv("KIRI_API_KEY", "").strip()
KIRI_BASE_URL = os.getenv("KIRI_BASE_URL", "https://api.kiritts.com/v1").strip().rstrip("/")
KIRI_MODEL = os.getenv("KIRI_MODEL", "kiritts").strip() or "kiritts"
KIRI_DEFAULT_VOICE = os.getenv("KIRI_VOICE", "Maly").strip() or "Maly"
DEFAULT_LEADING_SILENCE_SECONDS = os.getenv("TTS_LEADING_SILENCE_SECONDS", "0").strip() or "0"
TTS_MODELS = {
    "edge": "Edge TTS (free)",
    "inworld-tts-2": "Inworld Realtime TTS-2",
    "inworld-tts-1.5-mini": "Inworld TTS 1.5 Mini",
    "inworld-tts-1.5-max": "Inworld TTS 1.5 Max",
    "kiri": "Kiri TTS (Khmer + Voice Clone)",
}
LEADING_SILENCE_OPTIONS = {
    "0": 0.0,
    "0.5": 0.5,
    "1": 1.0,
}
EDGE_VOICE_EXAMPLES = [
    "km-KH-PisethNeural",
    "km-KH-SreymomNeural",
    "ru-RU-DmitryNeural",
    "ru-RU-SvetlanaNeural",
    "uk-UA-OstapNeural",
    "uk-UA-PolinaNeural",
    "en-US-GuyNeural",
    "en-US-JennyNeural",
]
INWORLD_VOICE_EXAMPLES = [
    "Dmitry",
    "Elena",
    "Nikolai",
    "Svetlana",
    "Ashley",
    "Dennis",
    "Alex",
    "Ava",
]
MAX_CHARS = int(os.getenv("MAX_CHARS", "1500"))
TTS_TIMEOUT_SECONDS = int(os.getenv("TTS_TIMEOUT_SECONDS", "35"))
SEND_TIMEOUT_SECONDS = int(os.getenv("SEND_TIMEOUT_SECONDS", "20"))
STT_MAX_MB = int(os.getenv("STT_MAX_MB", "20"))  # Telegram bots can download at most 20 MB
STT_WAIT_SECONDS = int(os.getenv("STT_WAIT_SECONDS", "30"))  # keep below vercel.json maxDuration
STT_CONVERT_MP3 = os.getenv("STT_CONVERT_MP3", "1").strip() not in {"0", "false", "no"}
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", tempfile.gettempdir())).expanduser()
PERSISTENCE_FILE = Path(os.getenv("PERSISTENCE_FILE", "data/bot-state.pickle")).expanduser()
ADMIN_IDS = {int(part) for part in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "").strip()) if part.isdigit()}
SUPPORTED_LANGS = {"km", "en", "ru"}
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "km").strip().lower()
if DEFAULT_LANG not in SUPPORTED_LANGS:
    DEFAULT_LANG = "km"
LANG_BUTTON_TEXTS = {"🌐 ភាសា", "🌐 Language", "🌐 Язык", "ភាសា", "Language", "Язык"}
SETTINGS_BUTTON_TEXTS = {"⚙️ ការកំណត់", "⚙️ Settings", "⚙️ Настройки", "ការកំណត់", "Settings", "Настройки"}
ADMIN_BUTTON_TEXTS = {"👑 អ្នកគ្រប់គ្រង", "👑 Admin", "👑 Админ", "អ្នកគ្រប់គ្រង", "Admin", "Админ"}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)
log = logging.getLogger("telegram-tts-bot")

KM_TEXTS = {
    "start": (
        "ផ្ញើអត្ថបទមកខ្ញុំ ខ្ញុំនឹងបម្លែងវាជាឯកសារសំឡេង MP3។\n\n"
        "ពាក្យបញ្ជា៖\n"
        "/voice <voice> — ប្តូរសំឡេងសម្រាប់ chat នេះ\n"
        "/settings — ជ្រើសរើស TTS model\n"
        "/voices — ឧទាហរណ៍សំឡេង\n"
        "/language — ជ្រើសរើសភាសា\n"
        "/help — ជំនួយ\n\n"
        "🎙 ផ្ញើសារសំឡេង ឬឯកសារសំឡេងមក ខ្ញុំនឹងបម្លែងវាជាអត្ថបទខ្មែរ។"
    ),
    "help": (
        "គ្រាន់តែផ្ញើអត្ថបទមិនលើស {max_chars} តួអក្សរ។\n"
        "សំឡេងលំនាំដើម៖ {default_voice}\n"
        "Model បច្ចុប្បន្ន៖ {model_name}\n\n"
        "ប្តូរ TTS model៖ /settings\n\n"
        "{voice_examples}\n\n"
        "ប្តូរសំឡេង៖ /voice {voice_example}\n"
        "ប្តូរភាសា៖ /language"
    ),
    "voices_edge": "ឧទាហរណ៍សំឡេង Edge TTS៖\n{voices}\n\nប្រើ៖ /voice km-KH-SreymomNeural",
    "voices_inworld": "ឧទាហរណ៍សំឡេង Inworld សម្រាប់ {model_name}៖\n{voices}\n\nប្រើ៖ /voice Ashley\nសំឡេងរុស្ស៊ី៖ Dmitry, Elena, Nikolai, Svetlana។",
    "language_prompt": "ជ្រើសរើសភាសា៖",
    "language_set": "បានកំណត់ភាសាជាខ្មែរ។",
    "settings_prompt": "⚙️ ការកំណត់\nTTS model បច្ចុប្បន្ន៖ {model_name}\nពេលពន្យារមុនចាប់ផ្តើម៖ {silence_label}\n\nជ្រើសរើស model ឬពេលពន្យារ៖",
    "model_set": "បានកំណត់ TTS model៖ {model_name}",
    "silence_set": "បានកំណត់ពេលពន្យារ៖ {silence_label}",
    "settings_button": "⚙️ ការកំណត់",
    "current_voice": "Model បច្ចុប្បន្ន៖ {model_name}\nសំឡេងបច្ចុប្បន្ន៖ {voice}\n{hint}",
    "invalid_voice": "ឈ្មោះសំឡេងនេះមិនត្រឹមត្រូវសម្រាប់ model បច្ចុប្បន្នទេ។ ឧទាហរណ៍៖ /voice {example}",
    "voice_set": "រួចរាល់ សំឡេងសម្រាប់ chat នេះ៖ {voice}",
    "empty_text": "សូមផ្ញើអត្ថបទដែលមិនទទេ។",
    "too_long": "អត្ថបទវែងពេក៖ {length} តួអក្សរ។ កំណត់ត្រឹម {max_chars}។",
    "caption_voice": "Model៖ {model_name}\nសំឡេង៖ {voice}",
    "processing": "⏳ កំពុងបង្កើតសំឡេង… អត្ថបទវែងអាចចំណាយពេលបន្តិច។",
    "sending_audio": "📤 សំឡេងរួចរាល់ កំពុងផ្ញើ…",
    "tts_failed": "មិនអាចបង្កើតសំឡេងបានទេ៖ {error}",
    "main_menu_button": "🌐 ភាសា",
    "admin_menu_button": "👑 អ្នកគ្រប់គ្រង",
    "admin_only": "ពាក្យបញ្ជានេះសម្រាប់អ្នកគ្រប់គ្រងប៉ុណ្ណោះ។",
    "admin_menu": (
        "👑 ម៉ឺនុយអ្នកគ្រប់គ្រង\n\n"
        "/stats — ស្ថិតិ bot\n"
        "/users — អ្នកប្រើថ្មីៗ\n"
        "/admin — ម៉ឺនុយនេះ"
    ),
    "stats": (
        "📊 ស្ថិតិ bot\n"
        "អ្នកប្រើ៖ {users}\n"
        "សំណើ៖ {requests}\n"
        "TTS ជោគជ័យ៖ {tts_success}\n"
        "TTS បរាជ័យ៖ {tts_failed}\n"
        "តាម model៖ {by_model}\n"
        "Admin IDs៖ {admin_ids}"
    ),
    "users_empty": "មិនទាន់មានអ្នកប្រើនៅឡើយទេ។",
    "users_header": "👥 អ្នកប្រើ (សរុប {count} បង្ហាញ {shown})៖\n",
    "new_user_notice": (
        "🆕 អ្នកប្រើថ្មីនៃ TTS bot\n"
        "ID៖ {id}\n"
        "ឈ្មោះ៖ {name}\n"
        "Username៖ {username}\n"
        "ភាសា៖ {language_code}\n"
        "ប្រភព៖ {source}"
    ),
    "placeholder": "វាយអត្ថបទដើម្បីបម្លែងជាសំឡេង",
    "stt_disabled": "bot នេះមិនទាន់បើកមុខងារបម្លែងសំឡេងជាអត្ថបទទេ។",
    "stt_processing": "⏳ កំពុងបម្លែងសំឡេងជាអត្ថបទ…",
    "stt_too_big": "ឯកសារសំឡេងធំពេក៖ {size_mb} MB។ កំណត់ត្រឹម {max_mb} MB។",
    "stt_pending": "⏳ នៅកំពុងដំណើរការ។ សូមចុចប៊ូតុងខាងក្រោមបន្តិចទៀតដើម្បីទទួលលទ្ធផល។",
    "stt_check_button": "🔄 ពិនិត្យលទ្ធផល",
    "stt_not_ready": "មិនទាន់រួចទេ សូមសាកម្តងទៀតបន្តិចទៀត។",
    "stt_failed": "មិនអាចបម្លែងសំឡេងជាអត្ថបទបានទេ៖ {error}",
    "stt_empty": "មិនស្គាល់សំឡេងនិយាយទេ។",
    "stt_caption": "📝 អក្សររត់ (.srt)",
}

TEXTS = {
    "en": {
        "start": (
            "Send me text and I will return an MP3 audio file.\n\n"
            "Commands:\n"
            "/voice <voice> — change the TTS voice for this chat\n"
            "/settings — choose TTS model\n"
            "/voices — voice examples\n"
            "/language — choose interface language\n"
            "/help — help\n\n"
            "🎙 Send a voice message or audio file and I will turn it into text (Khmer)."
        ),
        "help": (
            "Just send text up to {max_chars} characters.\n"
            "Default voice: {default_voice}\n"
            "Current model: {model_name}\n\n"
            "Change TTS model: /settings\n\n"
            "{voice_examples}\n\n"
            "Change voice: /voice {voice_example}\n"
            "Change interface language: /language"
        ),
        "voices_edge": "Edge TTS voice examples:\n{voices}\n\nUse: /voice km-KH-SreymomNeural",
        "voices_inworld": "Inworld voice examples for {model_name}:\n{voices}\n\nUse: /voice Ashley\nRussian voices available: Dmitry, Elena, Nikolai, Svetlana.",
        "language_prompt": "Choose interface language:",
        "language_set": "Interface language set to English.",
        "settings_prompt": "⚙️ Settings\nCurrent TTS model: {model_name}\nStart delay: {silence_label}\n\nChoose model or start delay:",
        "model_set": "TTS model set to: {model_name}",
        "silence_set": "Start delay set to: {silence_label}",
        "settings_button": "⚙️ Settings",
        "current_voice": "Current model: {model_name}\nCurrent voice: {voice}\n{hint}",
        "invalid_voice": "This does not look like a valid voice for the current model. Example: /voice {example}",
        "voice_set": "OK, voice for this chat: {voice}",
        "empty_text": "Send non-empty text.",
        "too_long": "Text is too long: {length} characters. Limit: {max_chars}.",
        "caption_voice": "Model: {model_name}\nVoice: {voice}",
        "processing": "⏳ Generating audio… This can take a bit for long text.",
        "sending_audio": "📤 Audio is ready, uploading…",
        "tts_failed": "Could not generate audio: {error}",
        "main_menu_button": "🌐 Language",
        "admin_menu_button": "👑 Admin",
        "admin_only": "Admin-only command.",
        "admin_menu": (
            "👑 Admin menu\n\n"
            "/stats — bot statistics\n"
            "/users — recent users\n"
            "/admin — this menu"
        ),
        "stats": (
            "📊 Bot statistics\n"
            "Users: {users}\n"
            "Requests: {requests}\n"
            "Successful TTS: {tts_success}\n"
            "Failed TTS: {tts_failed}\n"
            "By model: {by_model}\n"
            "Admin IDs: {admin_ids}"
        ),
        "users_empty": "No users recorded yet.",
        "users_header": "👥 Users ({count} total, showing {shown}):\n",
        "new_user_notice": (
            "🆕 New TTS bot user\n"
            "ID: {id}\n"
            "Name: {name}\n"
            "Username: {username}\n"
            "Language: {language_code}\n"
            "Source: {source}"
        ),
        "placeholder": "Type text for TTS",
        "stt_disabled": "Speech-to-text is not configured on this bot.",
        "stt_processing": "⏳ Transcribing audio…",
        "stt_too_big": "Audio file is too large: {size_mb} MB. Limit: {max_mb} MB.",
        "stt_pending": "⏳ Still processing. Tap the button in a moment to get the result.",
        "stt_check_button": "🔄 Check result",
        "stt_not_ready": "Not ready yet, try again in a few seconds.",
        "stt_failed": "Could not transcribe audio: {error}",
        "stt_empty": "No speech was recognized.",
        "stt_caption": "📝 Subtitles (.srt)",
    },
    "ru": {
        "start": (
            "Пришли мне текст — я верну MP3-аудиофайл.\n\n"
            "Команды:\n"
            "/voice <voice> — сменить голос для этого чата\n"
            "/settings — выбрать TTS модель\n"
            "/voices — подсказка по голосам\n"
            "/language — выбрать язык интерфейса\n"
            "/help — помощь\n\n"
            "🎙 Пришли голосовое или аудиофайл — я переведу его в текст (кхмерский)."
        ),
        "help": (
            "Просто отправь текст до {max_chars} символов.\n"
            "Голос по умолчанию: {default_voice}\n"
            "Текущая модель: {model_name}\n\n"
            "Сменить TTS модель: /settings\n\n"
            "{voice_examples}\n\n"
            "Сменить голос: /voice {voice_example}\n"
            "Сменить язык интерфейса: /language"
        ),
        "voices_edge": "Примеры голосов Edge TTS:\n{voices}\n\nИспользуй: /voice km-KH-SreymomNeural",
        "voices_inworld": "Примеры голосов Inworld для {model_name}:\n{voices}\n\nИспользуй: /voice Ashley\nРусские голоса: Dmitry, Elena, Nikolai, Svetlana.",
        "language_prompt": "Выбери язык интерфейса:",
        "language_set": "Язык интерфейса: русский.",
        "settings_prompt": "⚙️ Настройки\nТекущая TTS модель: {model_name}\nПауза перед стартом: {silence_label}\n\nВыбери модель или паузу:",
        "model_set": "TTS модель: {model_name}",
        "silence_set": "Пауза перед стартом: {silence_label}",
        "settings_button": "⚙️ Настройки",
        "current_voice": "Текущая модель: {model_name}\nТекущий голос: {voice}\n{hint}",
        "invalid_voice": "Похоже на неверный голос для текущей модели. Пример: /voice {example}",
        "voice_set": "Ок, голос для этого чата: {voice}",
        "empty_text": "Пришли непустой текст.",
        "too_long": "Текст слишком длинный: {length} символов. Лимит: {max_chars}.",
        "caption_voice": "Модель: {model_name}\nГолос: {voice}",
        "processing": "⏳ Генерирую аудио… Для длинного текста это может занять немного времени.",
        "sending_audio": "📤 Аудио готово, загружаю…",
        "tts_failed": "Не смог сгенерировать аудио: {error}",
        "main_menu_button": "🌐 Язык",
        "admin_menu_button": "👑 Админ",
        "admin_only": "Команда только для админа.",
        "admin_menu": (
            "👑 Админ-меню\n\n"
            "/stats — статистика бота\n"
            "/users — последние пользователи\n"
            "/admin — это меню"
        ),
        "stats": (
            "📊 Статистика бота\n"
            "Пользователей: {users}\n"
            "Запросов: {requests}\n"
            "Успешных TTS: {tts_success}\n"
            "Ошибок TTS: {tts_failed}\n"
            "По моделям: {by_model}\n"
            "Admin IDs: {admin_ids}"
        ),
        "users_empty": "Пользователей пока нет.",
        "users_header": "👥 Пользователи ({count} всего, показано {shown}):\n",
        "new_user_notice": (
            "🆕 Новый пользователь TTS bot\n"
            "ID: {id}\n"
            "Имя: {name}\n"
            "Username: {username}\n"
            "Язык: {language_code}\n"
            "Источник: {source}"
        ),
        "placeholder": "Введите текст для озвучки",
        "stt_disabled": "Распознавание речи не настроено в этом боте.",
        "stt_processing": "⏳ Распознаю аудио…",
        "stt_too_big": "Аудиофайл слишком большой: {size_mb} МБ. Лимит: {max_mb} МБ.",
        "stt_pending": "⏳ Ещё обрабатывается. Нажми кнопку чуть позже, чтобы получить результат.",
        "stt_check_button": "🔄 Проверить результат",
        "stt_not_ready": "Пока не готово, попробуй через несколько секунд.",
        "stt_failed": "Не удалось распознать аудио: {error}",
        "stt_empty": "Речь не распознана.",
        "stt_caption": "📝 Субтитры (.srt)",
    },
}
TEXTS["km"] = KM_TEXTS
HINTS_EDGE = {
    "km": "ប្តូរ៖ /voice km-KH-SreymomNeural",
    "en": "Change it: /voice km-KH-SreymomNeural",
    "ru": "Сменить: /voice km-KH-SreymomNeural",
}
HINTS_KIRI = {
    "km": "ប្តូរ៖ /voice Maly (ឬឈ្មោះសំឡេង clone របស់អ្នក) · មើលបញ្ជី៖ /voices",
    "en": "Change it: /voice Maly (or your cloned voice name) · list: /voices",
    "ru": "Сменить: /voice Maly (или имя клона) · список: /voices",
}
HINTS_INWORLD = {
    "km": "ប្តូរ៖ /voice Dmitry, Elena, Nikolai, Svetlana, Ashley…",
    "en": "Change it: /voice Dmitry, Elena, Nikolai, Svetlana, Ashley…",
    "ru": "Сменить: /voice Dmitry, Elena, Nikolai, Svetlana, Ashley…",
}


def clean_text(text: str) -> str:
    text = text.strip()
    # Telegram often sends URLs/markdown fine, but normalize excessive whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text


def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    lang = context.user_data.get("lang", DEFAULT_LANG)
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def t(context: ContextTypes.DEFAULT_TYPE, key: str, **kwargs: object) -> str:
    template = TEXTS[get_lang(context)][key]
    return template.format(**kwargs)


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in ADMIN_IDS)


def main_menu(context: ContextTypes.DEFAULT_TYPE, user_id: int | None = None) -> ReplyKeyboardMarkup:
    rows = [[TEXTS[get_lang(context)]["main_menu_button"], TEXTS[get_lang(context)]["settings_button"]]]
    if is_admin(user_id):
        rows.append([TEXTS[get_lang(context)]["admin_menu_button"]])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder=TEXTS[get_lang(context)]["placeholder"],
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ខ្មែរ", callback_data="lang:km"),
                InlineKeyboardButton("English", callback_data="lang:en"),
                InlineKeyboardButton("Русский", callback_data="lang:ru"),
            ]
        ]
    )


def inworld_available() -> bool:
    return bool(INWORLD_API_KEY) and importlib.util.find_spec("inworld_tts") is not None


def enabled_models() -> dict[str, str]:
    """Edge is always on; Inworld/Kiri models appear only when their API key is configured."""
    result = {}
    for key, label in TTS_MODELS.items():
        if key == "edge":
            result[key] = label
        elif key == "kiri":
            if KIRI_API_KEY:
                result[key] = label
        elif inworld_available():
            result[key] = label
    return result


def get_tts_model(context: ContextTypes.DEFAULT_TYPE) -> str:
    models = enabled_models()
    model = context.user_data.get("tts_model", DEFAULT_TTS_MODEL)
    return model if model in models else "edge"


def get_tts_model_name(model: str) -> str:
    return TTS_MODELS.get(model, model)


def get_leading_silence(context: ContextTypes.DEFAULT_TYPE) -> float:
    raw = str(context.user_data.get("leading_silence_seconds", DEFAULT_LEADING_SILENCE_SECONDS))
    return LEADING_SILENCE_OPTIONS.get(raw, LEADING_SILENCE_OPTIONS.get(DEFAULT_LEADING_SILENCE_SECONDS, 0.0))


def silence_key(seconds: float) -> str:
    if seconds == 0:
        return "0"
    return str(seconds).rstrip("0").rstrip(".")


def silence_label(seconds: float) -> str:
    key = silence_key(seconds)
    return f"{key}s"


def settings_keyboard(current_model: str, current_silence: float) -> InlineKeyboardMarkup:
    rows = []
    for model, label in enabled_models().items():
        prefix = "✅ " if model == current_model else ""
        rows.append([InlineKeyboardButton(prefix + label, callback_data=f"model:{model}")])
    rows.append(
        [
            InlineKeyboardButton(("✅ " if current_silence == seconds else "") + f"Delay {key}s", callback_data=f"silence:{key}")
            for key, seconds in LEADING_SILENCE_OPTIONS.items()
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_voice_for_model(context: ContextTypes.DEFAULT_TYPE, model: str) -> str:
    if model == "edge":
        return context.user_data.get("edge_voice") or context.user_data.get("voice", DEFAULT_VOICE)
    if model == "kiri":
        return context.user_data.get("kiri_voice") or KIRI_DEFAULT_VOICE
    return context.user_data.get("inworld_voice") or INWORLD_DEFAULT_VOICE


def voice_example_for_model(model: str) -> str:
    if model == "kiri":
        return "Maly"
    return "km-KH-SreymomNeural" if model == "edge" else "Dmitry"


def voice_hint_for_model(context: ContextTypes.DEFAULT_TYPE, model: str) -> str:
    if model == "edge":
        return HINTS_EDGE[get_lang(context)]
    if model == "kiri":
        return HINTS_KIRI[get_lang(context)]
    return HINTS_INWORLD[get_lang(context)]


def voice_examples_text(context: ContextTypes.DEFAULT_TYPE, model: str) -> str:
    voices = INWORLD_VOICE_EXAMPLES if model != "edge" else EDGE_VOICE_EXAMPLES
    bullet_list = "\n".join(f"• {voice}" for voice in voices)
    if model == "edge":
        return t(context, "voices_edge", voices=bullet_list)
    return t(context, "voices_inworld", voices=bullet_list, model_name=get_tts_model_name(model))


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def display_user(user: object) -> str:
    if not user:
        return "unknown"
    name_parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(part for part in name_parts if part).strip()
    return name or getattr(user, "username", None) or str(getattr(user, "id", "unknown"))


def get_stats(context: ContextTypes.DEFAULT_TYPE) -> dict[str, object]:
    stats = context.bot_data.setdefault("stats", {})
    for key in ("requests", "tts_success", "tts_failed"):
        stats.setdefault(key, 0)
    stats.setdefault("by_model", {})
    return stats


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, skip_user_id: int | None = None) -> None:
    for admin_id in ADMIN_IDS:
        if admin_id == skip_user_id:
            continue
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            log.exception("Could not notify admin_id=%s", admin_id)


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE, source: str) -> None:
    user = update.effective_user
    if not user or user.is_bot:
        return

    users = context.bot_data.setdefault("users", {})
    user_id = str(user.id)
    now = utc_now()
    is_new = user_id not in users
    record = users.setdefault(user_id, {"id": user.id, "first_seen": now})
    record.update(
        {
            "id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "language_code": user.language_code or "",
            "is_admin": is_admin(user.id),
            "last_seen": now,
            "last_source": source,
        }
    )
    if update.effective_chat:
        chats = set(record.get("chat_ids", []))
        chats.add(update.effective_chat.id)
        record["chat_ids"] = sorted(chats)

    if is_new:
        notice = TEXTS["km"]["new_user_notice"].format(
            id=user.id,
            name=display_user(user),
            username=f"@{user.username}" if user.username else "—",
            language_code=user.language_code or "—",
            source=source,
        )
        await notify_admins(context, notice, skip_user_id=user.id if is_admin(user.id) else None)


def require_admin(update: Update) -> bool:
    return is_admin(update.effective_user.id if update.effective_user else None)


def get_ffmpeg_exe() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - surface clear user-facing error later
        raise RuntimeError("ffmpeg is required to add a start delay; install imageio-ffmpeg or ffmpeg") from exc


def prepend_silence_with_ffmpeg(mp3_path: Path, seconds: float) -> Path:
    if seconds <= 0:
        return mp3_path
    ffmpeg = get_ffmpeg_exe()
    delayed = mp3_path.with_name(f"{mp3_path.stem}_delay{silence_key(seconds).replace('.', '_')}{mp3_path.suffix}")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-t",
        silence_key(seconds),
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-i",
        str(mp3_path),
        "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1[a]",
        "-map",
        "[a]",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(delayed),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    mp3_path.unlink(missing_ok=True)
    return delayed


async def add_leading_silence(mp3_path: Path, seconds: float) -> Path:
    return await asyncio.to_thread(prepend_silence_with_ffmpeg, mp3_path, seconds)


async def synthesize_mp3(text: str, model: str = "edge", voice: str = DEFAULT_VOICE, leading_silence: float = 0.0) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"tts_{uuid.uuid4().hex}.mp3"
    if model == "edge":
        last_error: Exception | None = None
        for attempt in (1, 2):  # one retry: the Edge service occasionally drops a connection
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice,
                    rate=DEFAULT_RATE,
                    volume=DEFAULT_VOLUME,
                )
                await communicate.save(str(out))
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("Edge TTS attempt %s failed: %s", attempt, exc)
                out.unlink(missing_ok=True)
        if last_error is not None:
            raise last_error
        return await add_leading_silence(out, leading_silence)

    if model.startswith("inworld-tts"):
        if not INWORLD_API_KEY:
            raise RuntimeError("INWORLD_API_KEY is not configured on the server")

        def generate_inworld() -> None:
            from inworld_tts import InworldTTS  # optional dependency, see requirements-extra.txt

            tts_client = InworldTTS(api_key=INWORLD_API_KEY)
            tts_client.generate(
                text=text,
                voice=voice,
                model=model,
                encoding="MP3",
                language=INWORLD_LANGUAGE,
                delivery_mode=INWORLD_DELIVERY_MODE,
                output_file=str(out),
            )

        await asyncio.to_thread(generate_inworld)
        return await add_leading_silence(out, leading_silence)

    if model == "kiri":
        if not KIRI_API_KEY:
            raise RuntimeError("KIRI_API_KEY is not configured on the server")
        async with httpx.AsyncClient(timeout=httpx.Timeout(TTS_TIMEOUT_SECONDS, connect=10.0)) as client:
            response = await client.post(
                f"{KIRI_BASE_URL}/audio/speech",
                headers={"Authorization": f"Bearer {KIRI_API_KEY}"},
                json={
                    "model": KIRI_MODEL,
                    "input": text,
                    "voice": voice,
                    "response_format": "mp3",
                },
            )
        if response.status_code != 200:
            try:
                detail = str(response.json().get("detail", ""))
            except Exception:  # noqa: BLE001
                detail = response.text[:200]
            raise RuntimeError(f"Kiri TTS error {response.status_code}: {detail}")
        out.write_bytes(response.content)
        return await add_leading_silence(out, leading_silence)

    raise ValueError(f"Unsupported TTS model: {model}")


async def keep_upload_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Keep Telegram's progress indicator alive during slow TTS generation/upload."""
    while True:
        with contextlib.suppress(Exception):
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
        await asyncio.sleep(4)


def log_update(update: Update, event: str) -> None:
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        log.info("%s: non-message update_id=%s", event, update.update_id)
        return
    user = update.effective_user
    log.info(
        "%s: chat_id=%s chat_type=%s user_id=%s username=%s first_name=%s",
        event,
        msg.chat_id,
        msg.chat.type if msg.chat else None,
        user.id if user else None,
        user.username if user else None,
        user.first_name if user else None,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "start")
    await register_user(update, context, "start")
    user_id = update.effective_user.id if update.effective_user else None
    await update.message.reply_text(t(context, "start"), reply_markup=main_menu(context, user_id))
    await update.message.reply_text(t(context, "language_prompt"), reply_markup=language_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context, "help")
    user_id = update.effective_user.id if update.effective_user else None
    model = get_tts_model(context)
    await update.message.reply_text(
        t(
            context,
            "help",
            max_chars=MAX_CHARS,
            default_voice=get_voice_for_model(context, model),
            model_name=get_tts_model_name(model),
            voice_examples=voice_examples_text(context, model),
            voice_example=voice_example_for_model(model),
        ),
        reply_markup=main_menu(context, user_id),
    )


async def kiri_voices_text() -> str:
    """List voices (built-in + cloned) available to the configured Kiri key."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(f"{KIRI_BASE_URL}/voices", headers={"Authorization": f"Bearer {KIRI_API_KEY}"})
        response.raise_for_status()
        data = response.json()
        items = data.get("data", data.get("voices", data)) if isinstance(data, dict) else data
        names = []
        for item in items or []:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("voice") or item.get("id")
                if name:
                    names.append(str(name))
        if not names:
            return "Kiri: no voices returned.\n\nUse: /voice Maly"
        return "Kiri voices:\n" + "\n".join(f"• {n}" for n in names[:40]) + "\n\nUse: /voice <name>"
    except Exception as exc:  # noqa: BLE001
        log.warning("Kiri voices failed: %s", exc)
        return "Kiri voices unavailable right now.\n\nUse: /voice Maly"


async def voices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context, "voices")
    user_id = update.effective_user.id if update.effective_user else None
    model = get_tts_model(context)
    if model == "kiri":
        text = await kiri_voices_text()
    else:
        text = voice_examples_text(context, model)
    await update.message.reply_text(text, reply_markup=main_menu(context, user_id))


async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "language")
    await register_user(update, context, "language")
    await update.message.reply_text(t(context, "language_prompt"), reply_markup=language_keyboard())


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "language_callback")
    await register_user(update, context, "language_callback")
    query = update.callback_query
    await query.answer()
    lang = query.data.split(":", 1)[1]
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    context.user_data["lang"] = lang
    user_id = update.effective_user.id if update.effective_user else None
    await query.edit_message_text(TEXTS[lang]["language_set"])
    await query.message.reply_text(TEXTS[lang]["start"], reply_markup=main_menu(context, user_id))


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "settings")
    await register_user(update, context, "settings")
    model = get_tts_model(context)
    leading_silence = get_leading_silence(context)
    await update.message.reply_text(
        t(
            context,
            "settings_prompt",
            model_name=get_tts_model_name(model),
            silence_label=silence_label(leading_silence),
        ),
        reply_markup=settings_keyboard(model, leading_silence),
    )


async def model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "model_callback")
    await register_user(update, context, "model_callback")
    query = update.callback_query
    await query.answer()
    model = query.data.split(":", 1)[1]
    if model not in enabled_models():
        model = "edge"
    context.user_data["tts_model"] = model
    user_id = update.effective_user.id if update.effective_user else None
    await query.edit_message_text(t(context, "model_set", model_name=get_tts_model_name(model)))
    await query.message.reply_text(
        t(
            context,
            "current_voice",
            model_name=get_tts_model_name(model),
            voice=get_voice_for_model(context, model),
            hint=voice_hint_for_model(context, model),
        ),
        reply_markup=main_menu(context, user_id),
    )


async def silence_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "silence_callback")
    await register_user(update, context, "silence_callback")
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    seconds = LEADING_SILENCE_OPTIONS.get(key, 0.0)
    context.user_data["leading_silence_seconds"] = silence_key(seconds)
    model = get_tts_model(context)
    await query.edit_message_text(t(context, "silence_set", silence_label=silence_label(seconds)))
    await query.message.reply_text(
        t(
            context,
            "settings_prompt",
            model_name=get_tts_model_name(model),
            silence_label=silence_label(seconds),
        ),
        reply_markup=settings_keyboard(model, seconds),
    )


async def voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context, "voice")
    user_id = update.effective_user.id if update.effective_user else None
    model = get_tts_model(context)
    if not context.args:
        current = get_voice_for_model(context, model)
        await update.message.reply_text(
            t(
                context,
                "current_voice",
                model_name=get_tts_model_name(model),
                voice=current,
                hint=voice_hint_for_model(context, model),
            ),
            reply_markup=main_menu(context, user_id),
        )
        return
    voice = " ".join(context.args).strip() if model == "kiri" else context.args[0].strip()
    if model == "edge":
        if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural", voice):
            await update.message.reply_text(t(context, "invalid_voice", example=voice_example_for_model(model)), reply_markup=main_menu(context, user_id))
            return
        context.user_data["edge_voice"] = voice
        context.user_data["voice"] = voice  # backward compatibility with existing saved state
    elif model == "kiri":
        if not re.fullmatch(r"[\w .:-]{1,80}", voice):
            await update.message.reply_text(t(context, "invalid_voice", example=voice_example_for_model(model)), reply_markup=main_menu(context, user_id))
            return
        context.user_data["kiri_voice"] = voice
    else:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{2,80}", voice):
            await update.message.reply_text(t(context, "invalid_voice", example=voice_example_for_model(model)), reply_markup=main_menu(context, user_id))
            return
        context.user_data["inworld_voice"] = voice
    await update.message.reply_text(t(context, "voice_set", voice=voice), reply_markup=main_menu(context, user_id))


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context, "admin")
    if not require_admin(update):
        await update.message.reply_text(t(context, "admin_only"))
        return
    await update.message.reply_text(t(context, "admin_menu"), reply_markup=main_menu(context, update.effective_user.id))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context, "stats")
    if not require_admin(update):
        await update.message.reply_text(t(context, "admin_only"))
        return
    stats = get_stats(context)
    users = context.bot_data.setdefault("users", {})
    by_model = stats.get("by_model", {})
    by_model_text = ", ".join(f"{get_tts_model_name(model)}={count}" for model, count in sorted(by_model.items())) or "—"
    await update.message.reply_text(
        t(
            context,
            "stats",
            users=len(users),
            requests=stats["requests"],
            tts_success=stats["tts_success"],
            tts_failed=stats["tts_failed"],
            by_model=by_model_text,
            admin_ids=", ".join(str(admin_id) for admin_id in sorted(ADMIN_IDS)) or "—",
        ),
        reply_markup=main_menu(context, update.effective_user.id),
    )


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context, "users")
    if not require_admin(update):
        await update.message.reply_text(t(context, "admin_only"))
        return
    users = list(context.bot_data.setdefault("users", {}).values())
    if not users:
        await update.message.reply_text(t(context, "users_empty"), reply_markup=main_menu(context, update.effective_user.id))
        return
    users.sort(key=lambda item: item.get("last_seen", ""), reverse=True)
    shown = users[:20]
    lines = [t(context, "users_header", count=len(users), shown=len(shown))]
    for record in shown:
        username = f"@{record['username']}" if record.get("username") else "—"
        full_name = " ".join(part for part in [record.get("first_name"), record.get("last_name")] if part).strip() or "—"
        marker = " 👑" if record.get("is_admin") else ""
        lines.append(
            f"• {full_name}{marker}\n"
            f"  ID: {record.get('id')} | {username}\n"
            f"  Lang: {record.get('language_code') or '—'} | Last: {record.get('last_seen') or '—'}\n"
            f"  Source: {record.get('last_source') or '—'}"
        )
    await update.message.reply_text("\n".join(lines), reply_markup=main_menu(context, update.effective_user.id))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "text")
    await register_user(update, context, "text")
    msg = update.message
    if not msg or not msg.text:
        return

    user_id = update.effective_user.id if update.effective_user else None
    text = clean_text(msg.text)
    if text in LANG_BUTTON_TEXTS:
        await language_cmd(update, context)
        return
    if text in SETTINGS_BUTTON_TEXTS:
        await settings_cmd(update, context)
        return
    if text in ADMIN_BUTTON_TEXTS:
        await admin_cmd(update, context)
        return
    if not text:
        await msg.reply_text(t(context, "empty_text"), reply_markup=main_menu(context, user_id))
        return
    if len(text) > MAX_CHARS:
        await msg.reply_text(
            t(context, "too_long", length=len(text), max_chars=MAX_CHARS),
            reply_markup=main_menu(context, user_id),
        )
        return

    stats = get_stats(context)
    stats["requests"] += 1
    model = get_tts_model(context)
    voice = get_voice_for_model(context, model)
    leading_silence = get_leading_silence(context)
    out: Path | None = None
    status_message = None
    action_task = None
    try:
        status_message = await msg.reply_text(t(context, "processing"), reply_markup=main_menu(context, user_id))
        action_task = asyncio.create_task(keep_upload_action(context, msg.chat_id))
        log.info(
            "Generating TTS: chat_id=%s user_id=%s chars=%s model=%s voice=%s leading_silence=%s",
            msg.chat_id,
            user_id,
            len(text),
            model,
            voice,
            leading_silence,
        )
        out = await asyncio.wait_for(
            synthesize_mp3(text, model=model, voice=voice, leading_silence=leading_silence),
            timeout=TTS_TIMEOUT_SECONDS,
        )
        log.info("TTS generated: chat_id=%s user_id=%s bytes=%s", msg.chat_id, user_id, out.stat().st_size)
        stats["tts_success"] += 1
        by_model = stats.setdefault("by_model", {})
        by_model[model] = by_model.get(model, 0) + 1
        with contextlib.suppress(Exception):
            await status_message.edit_text(t(context, "sending_audio"))
        title = text[:45].replace("\n", " ")
        if len(text) > 45:
            title += "…"
        with out.open("rb") as audio_file:
            await asyncio.wait_for(
                msg.reply_audio(
                    audio=audio_file,
                    title=title,
                    performer="TTS Bot",
                    caption=t(context, "caption_voice", model_name=get_tts_model_name(model), voice=voice),
                    reply_markup=main_menu(context, user_id),
                ),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        if status_message:
            with contextlib.suppress(Exception):
                await status_message.delete()
        log.info("TTS audio sent: chat_id=%s user_id=%s", msg.chat_id, user_id)
    except Exception as exc:  # noqa: BLE001 - user-facing bot should not crash on one bad request
        stats["tts_failed"] += 1
        log.exception("TTS failed")
        error_text = t(context, "tts_failed", error=exc)
        if status_message:
            with contextlib.suppress(Exception):
                await status_message.edit_text(error_text)
        else:
            await msg.reply_text(error_text, reply_markup=main_menu(context, user_id))
    finally:
        if action_task:
            action_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await action_task
        if out:
            try:
                out.unlink(missing_ok=True)
            except Exception:
                log.warning("Could not remove temp file %s", out)


def _convert_to_mp3_sync(data: bytes, suffix: str) -> bytes:
    ffmpeg = get_ffmpeg_exe()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    token_ = uuid.uuid4().hex
    src = OUTPUT_DIR / f"stt_{token_}{suffix or '.bin'}"
    dst = OUTPUT_DIR / f"stt_{token_}.mp3"
    try:
        src.write_bytes(data)
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
             "-ac", "1", "-ar", "16000", "-codec:a", "libmp3lame", "-b:a", "64k", str(dst)],
            check=True, capture_output=True, timeout=40,
        )
        return dst.read_bytes()
    finally:
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)


async def prepare_audio(data: bytes, filename: str, mime: str) -> tuple[bytes, str, str]:
    """Convert Telegram voice/audio (usually OGG/Opus) to mono 16 kHz MP3, as in the API example."""
    if not STT_CONVERT_MP3 or mime in {"audio/mpeg", "audio/mp3"}:
        return data, filename, mime
    try:
        converted = await asyncio.to_thread(_convert_to_mp3_sync, data, Path(filename).suffix)
    except Exception as exc:  # noqa: BLE001 - fall back to the original file
        log.warning("ffmpeg conversion failed, sending original audio: %s", exc)
        return data, filename, mime
    return converted, f"{Path(filename).stem or 'audio'}.mp3", "audio/mpeg"


async def keep_chat_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int, action: str) -> None:
    while True:
        with contextlib.suppress(Exception):
            await context.bot.send_chat_action(chat_id=chat_id, action=action)
        await asyncio.sleep(4)


def stt_error_text(context: ContextTypes.DEFAULT_TYPE, user_id: int | None, exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    detail = getattr(exc, "detail", "")
    if detail and is_admin(user_id):  # raw API output only for admins
        message = f"{message}\n{detail}"
    return t(context, "stt_failed", error=message)


async def deliver_transcript(context: ContextTypes.DEFAULT_TYPE, target, job_id: str, user_id: int | None) -> None:
    """Fetch the SRT for ``job_id`` and send the plain text + the .srt file to ``target`` (a Message)."""
    srt = await stt.fetch_srt(job_id)
    text = stt.srt_to_text(srt)
    if not text:
        await target.reply_text(t(context, "stt_empty"), reply_markup=main_menu(context, user_id))
        return
    if len(text) <= 3500:
        await target.reply_text(text, reply_markup=main_menu(context, user_id))
    else:
        await target.reply_document(document=text.encode("utf-8"), filename="transcript.txt")
    await target.reply_document(
        document=srt.encode("utf-8"),
        filename="subtitles.srt",
        caption=t(context, "stt_caption"),
    )
    jobs = context.user_data.get("stt_jobs")
    if isinstance(jobs, dict):
        jobs.pop(job_id, None)


def pending_keyboard(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> InlineKeyboardMarkup:
    data = f"stt:{job_id}"
    if len(data.encode()) > 64:  # Telegram callback_data limit
        data = "stt:last"
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(context, "stt_check_button"), callback_data=data)]])


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "audio")
    await register_user(update, context, "audio")
    msg = update.message
    if not msg:
        return
    user_id = update.effective_user.id if update.effective_user else None
    if not stt.enabled():
        await msg.reply_text(t(context, "stt_disabled"), reply_markup=main_menu(context, user_id))
        return

    media = msg.voice or msg.audio or msg.document
    if media is None:
        return
    size = getattr(media, "file_size", None) or 0
    if size > STT_MAX_MB * 1024 * 1024:
        await msg.reply_text(
            t(context, "stt_too_big", size_mb=round(size / 1024 / 1024, 1), max_mb=STT_MAX_MB),
            reply_markup=main_menu(context, user_id),
        )
        return

    if msg.voice:
        filename, mime = "voice.ogg", media.mime_type or "audio/ogg"
    else:
        filename = getattr(media, "file_name", None) or "audio.mp3"
        mime = getattr(media, "mime_type", None) or "audio/mpeg"

    status_message = await msg.reply_text(t(context, "stt_processing"), reply_markup=main_menu(context, user_id))
    action_task = asyncio.create_task(keep_chat_action(context, msg.chat_id, ChatAction.TYPING))
    try:
        tg_file = await context.bot.get_file(media.file_id)
        data = bytes(await tg_file.download_as_bytearray())
        data, filename, mime = await prepare_audio(data, filename, mime)
        job_id = await stt.submit(data, filename, mime)
        log.info("STT job submitted: chat_id=%s user_id=%s job_id=%s bytes=%s", msg.chat_id, user_id, job_id, len(data))
        state = await stt.wait_until_done(job_id, STT_WAIT_SECONDS)
        if state == "done":
            await deliver_transcript(context, msg, job_id, user_id)
            with contextlib.suppress(Exception):
                await status_message.delete()
        elif state == "failed":
            await status_message.edit_text(t(context, "stt_failed", error="job failed"))
        else:
            jobs = context.user_data.setdefault("stt_jobs", {})
            jobs[job_id] = utc_now()
            while len(jobs) > 5:
                jobs.pop(next(iter(jobs)))
            await status_message.edit_text(t(context, "stt_pending"), reply_markup=pending_keyboard(context, job_id))
    except Exception as exc:  # noqa: BLE001
        log.exception("STT failed")
        with contextlib.suppress(Exception):
            await status_message.edit_text(stt_error_text(context, user_id, exc))
    finally:
        action_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await action_task


async def stt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_update(update, "stt_callback")
    await register_user(update, context, "stt_callback")
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    job_id = query.data.split(":", 1)[1]
    jobs = context.user_data.get("stt_jobs") or {}
    if job_id == "last" and jobs:
        job_id = next(reversed(jobs))
    try:
        state = await stt.wait_until_done(job_id, 10)
        if state == "done":
            await query.answer()
            await deliver_transcript(context, query.message, job_id, user_id)
            with contextlib.suppress(Exception):
                await query.message.delete()
        elif state == "failed":
            await query.answer()
            await query.edit_message_text(t(context, "stt_failed", error="job failed"))
        else:
            await query.answer(t(context, "stt_not_ready"), show_alert=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("STT callback failed")
        with contextlib.suppress(Exception):
            await query.answer()
        await query.message.reply_text(stt_error_text(context, user_id, exc))


async def setup_bot_commands(app: Application) -> None:
    default_commands = [
        BotCommand("start", "ចាប់ផ្តើម / ភាសា"),
        BotCommand("help", "ជំនួយ"),
        BotCommand("voices", "ឧទាហរណ៍សំឡេង"),
        BotCommand("voice", "មើល ឬប្តូរសំឡេង"),
        BotCommand("settings", "ការកំណត់ TTS"),
        BotCommand("language", "ជ្រើសរើសភាសា"),
    ]
    admin_commands = [
        *default_commands,
        BotCommand("admin", "ម៉ឺនុយអ្នកគ្រប់គ្រង"),
        BotCommand("stats", "ស្ថិតិ bot"),
        BotCommand("users", "អ្នកប្រើថ្មីៗ"),
    ]
    await app.bot.set_my_commands(default_commands, scope=BotCommandScopeDefault())
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            log.exception("Could not set admin commands for admin_id=%s", admin_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error while processing update", exc_info=context.error)


def build_application(persistence: PicklePersistence | None = None) -> Application:
    """Build the Telegram application without starting anything.

    Used by local polling (with PicklePersistence) and by the Vercel webhook entrypoint
    (state is persisted through ``state_store`` instead, because instances are ephemeral).
    """
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

    builder = Application.builder().token(TOKEN)
    if persistence is not None:
        builder = builder.persistence(persistence)
    app = builder.build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("voices", voices))
    app.add_handler(CommandHandler("voice", voice_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("language", language_cmd))
    app.add_handler(CommandHandler("lang", language_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang:(km|en|ru)$"))
    app.add_handler(CallbackQueryHandler(model_callback, pattern=r"^model:(edge|inworld-tts-2|inworld-tts-1\.5-mini|inworld-tts-1\.5-max|kiri)$"))
    app.add_handler(CallbackQueryHandler(silence_callback, pattern=r"^silence:(0|0\.5|1)$"))
    app.add_handler(CallbackQueryHandler(stt_callback, pattern=r"^stt:"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


async def run_polling() -> None:
    PERSISTENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    app = build_application(PicklePersistence(filepath=PERSISTENCE_FILE))
    log.info("Starting Telegram TTS bot in polling mode")
    await app.initialize()
    await setup_bot_commands(app)
    await app.start()
    if app.updater is None:
        raise RuntimeError("Telegram updater is unavailable")
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    asyncio.run(run_polling())


if __name__ == "__main__":
    main()
