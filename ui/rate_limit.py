"""Límite de mensajes por sesión de chat (ventana deslizante).

Protege la cuota compartida del LLM contra ráfagas accidentales (doble
Enter, scripts tontos, recargas repetidas) sin penalizar el uso normal:
10 mensajes/minuto es holgado para una conversación humana.

Determinista y sin dependencias: cada sesión se identifica con una clave
propia que `ui/app.py` guarda en `cl.user_session`. Un valor de
``max_messages`` <= 0 desactiva el límite (útil en tests y entornos
controlados).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Dict


class MessageRateLimiter:
    """Ventana deslizante por clave (una clave = una sesión de chat)."""

    def __init__(
        self,
        max_messages: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_messages = int(max_messages)
        self._window_seconds = float(window_seconds)
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}

    @property
    def enabled(self) -> bool:
        """False cuando la configuración desactiva el límite."""
        return self._max_messages > 0 and self._window_seconds > 0

    def allow(self, key: str) -> bool:
        """Registra un mensaje y devuelve si la ventana permite enviarlo."""
        if not self.enabled:
            return True
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        self._prune(hits, now)
        if len(hits) >= self._max_messages:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str) -> float:
        """Segundos hasta el próximo hueco en la ventana (0 si ya hay hueco)."""
        if not self.enabled:
            return 0.0
        hits = self._hits.get(key)
        if not hits:
            return 0.0
        self._prune(hits, self._clock())
        if len(hits) < self._max_messages:
            return 0.0
        return max(0.0, hits[0] + self._window_seconds - self._clock())

    def _prune(self, hits: Deque[float], now: float) -> None:
        cutoff = now - self._window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
