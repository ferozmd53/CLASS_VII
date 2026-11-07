"""Notifications: Telegram + optional speech, both non-blocking.

repo 1 had ``SendMessageToTelegram`` and ``Text2Speech`` that were called
inline from the trading loop; a slow Telegram request or a stuck pyttsx3
engine therefore stalled order management. Everything here goes through a
background worker so the trading loop never blocks on I/O.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

from . import settings as S


class AlertManager:
    def __init__(
        self,
        config: S.AlertConfig,
        logger: Optional[logging.Logger] = None,
        timeout: float = 5.0,
    ) -> None:
        self.config = config
        self.log = logger or logging.getLogger("class_vii.alerts")
        self.timeout = timeout
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=500)
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._speech_engine = None

    # -- lifecycle ------------------------------------------------------- #
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(
            target=self._drain, name="class_vii-alerts", daemon=True
        )
        self._worker.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:  # pragma: no cover
            pass

    # -- public ---------------------------------------------------------- #
    def notify(self, message: str) -> None:
        """Queue a message; never raises, never blocks."""
        if not message:
            return
        self.log.info("ALERT %s", message)
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            self.log.warning("alert queue full, dropping: %s", message)

    # -- worker ---------------------------------------------------------- #
    def _drain(self) -> None:
        while self._running:
            try:
                message = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if message is None:
                break
            self._send_telegram(message)
            if self.config.voice:
                self._speak(message)

    def _send_telegram(self, message: str) -> None:
        if not self.config.telegram_enabled:
            return
        try:
            import requests
        except ImportError:
            self.log.warning("requests not installed; Telegram disabled")
            return
        url = f"https://api.telegram.org/bot{self.config.telegram_token}/sendMessage"
        try:
            requests.post(
                url,
                data={"chat_id": self.config.telegram_chat_id, "text": message},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - alerts must never crash trading
            self.log.warning("telegram send failed: %s", exc)

    def _speak(self, message: str) -> None:
        try:
            if self._speech_engine is None:
                import pyttsx3

                self._speech_engine = pyttsx3.init()
            self._speech_engine.say(message)
            self._speech_engine.runAndWait()
        except Exception as exc:  # noqa: BLE001 - headless boxes have no voice
            self.log.debug("speech unavailable: %s", exc)
            self._speech_engine = None
