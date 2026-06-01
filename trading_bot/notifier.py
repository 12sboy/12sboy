from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


@dataclass
class ConsoleNotifier:
    messages: list[str] = field(default_factory=list)

    def send(self, text: str) -> None:
        self.messages.append(text)
        print(text)


@dataclass(frozen=True)
class TelegramNotifier:
    token: str
    chat_id: str
    parse_mode: str | None = None

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if self.parse_mode:
            payload["parse_mode"] = self.parse_mode
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Telegram send failed: {result}")

    def get_updates(self, offset: int | None = None, timeout_sec: int = 0) -> list[dict]:
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        payload: dict[str, int] = {"timeout": timeout_sec}
        if offset is not None:
            payload["offset"] = offset
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=max(15, timeout_sec + 5)) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {result}")
        return result.get("result", [])

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        url = f"https://api.telegram.org/bot{self.token}/setMyCommands"
        payload = {"commands": commands}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Telegram setMyCommands failed: {result}")


def make_notifier(token: str | None = None, chat_id: str | None = None) -> Notifier:
    if token and chat_id:
        return TelegramNotifier(token, chat_id)
    return ConsoleNotifier()
