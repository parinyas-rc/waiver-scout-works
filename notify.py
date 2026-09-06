"""Telegram delivery."""

import html
import os
import time

import requests

LIMIT = 4000  # Telegram hard-caps at 4096


def _chunks(text):
    if len(text) <= LIMIT:
        return [text]
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > LIMIT:
            out.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return out


def esc(s):
    return html.escape(str(s or ""))


def send(text, token=None, chat_id=None, silent=False):
    """Send a message. Returns True if every chunk landed."""
    token = token or os.environ.get("TELEGRAM_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[notify] no telegram creds; printing instead:\n" + text)
        return False

    ok = True
    for chunk in _chunks(text):
        for attempt in range(3):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "disable_notification": silent,
                    },
                    timeout=20,
                )
                if r.status_code == 200:
                    break
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", 3)
                    time.sleep(wait + 1)
                    continue
                print(f"[notify] telegram {r.status_code}: {r.text[:200]}")
                if attempt == 2:
                    ok = False
            except requests.RequestException as exc:
                print(f"[notify] {exc}")
                time.sleep(2 ** attempt)
                if attempt == 2:
                    ok = False
    return ok
