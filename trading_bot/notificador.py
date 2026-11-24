"""Alertas opcionales por Telegram (no bloquean el bot si fallan)."""
import logging
import threading

import requests

logger = logging.getLogger(__name__)


class Notificador:
    def __init__(self, token: str = "", chat_id: str = "", prefijo: str = ""):
        self.token = token
        self.chat_id = chat_id
        self.prefijo = prefijo

    @property
    def activo(self) -> bool:
        return bool(self.token and self.chat_id)

    def enviar(self, texto: str) -> None:
        if not self.activo:
            return
        threading.Thread(target=self._enviar, args=(f"{self.prefijo}{texto}",), daemon=True).start()

    def _enviar(self, texto: str) -> None:
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": texto},
                timeout=10,
            )
        except requests.RequestException as e:
            logger.warning("No se pudo enviar la alerta de Telegram: %s", e)
