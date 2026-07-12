import os
import requests
from src.logging_service import get_logger

LOGGER = get_logger()


class TelegramService:
    def __init__(self, token=None, chat_id=None):
        self.token = token or os.getenv("TELEGRAM_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    def send(self, message):
        return send_telegram(message, self.token, self.chat_id)

    def edit(self, message_id, new_text):
        return edit_telegram(message_id, new_text, self.token, self.chat_id)


def send_telegram(message, token=None, chat_id=None):
    token = token or os.getenv("TELEGRAM_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"})
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
    except Exception:
        LOGGER.exception("Falha ao enviar mensagem ao Telegram.")
    return None


def edit_telegram(message_id, new_text, token=None, chat_id=None):
    token = token or os.getenv("TELEGRAM_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            data={"chat_id": chat_id, "message_id": message_id, "text": new_text, "parse_mode": "Markdown"},
        )
    except Exception:
        LOGGER.exception("Falha ao editar mensagem no Telegram: %s", message_id)
