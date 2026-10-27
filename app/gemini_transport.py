"""Conexões Gemini reutilizadas por thread, sem histórico ou credenciais no pool."""

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, local

import requests
from requests.adapters import HTTPAdapter


def _configured_session(factory):
    session = factory()
    try:
        session.trust_env = False
        session.mount("https://", HTTPAdapter(max_retries=0))
    except Exception:
        _close_session(session)
        raise
    return session


def _close_session(session):
    try:
        session.close()
    except Exception:
        # Uma falha de limpeza não impede fechar as outras conexões nem
        # substitui o erro público da requisição que já terminou.
        pass


@dataclass(eq=False)
class _SessionSlot:
    session: requests.Session
    generation: int
    users: int = 0
    retired: bool = False


class _GeminiSessionPool:
    def __init__(self):
        self._local = local()
        self._lock = Lock()
        self._generation = 0
        self._slots = {}

    @contextmanager
    def lease(self):
        with self._lock:
            slot = getattr(self._local, "slot", None)
            if (slot is None or slot.generation != self._generation
                    or slot.retired or slot.users):
                # Um slot em uso também não é compartilhado por uma chamada
                # reentrante na mesma thread.
                slot = _SessionSlot(_configured_session(requests.Session), self._generation)
                self._slots[id(slot)] = slot
                self._local.slot = slot
            slot.users += 1
        try:
            yield slot.session
        finally:
            discard = False
            try:
                # O Gemini autentica pelo cabeçalho de cada pedido. Cookies
                # recebidos não devem criar estado entre chaves ou conversas.
                slot.session.cookies.clear()
            except Exception:
                discard = True
            with self._lock:
                slot.users -= 1
                # Uma chamada reentrante pode ter substituído o slot local;
                # não reter a conexão anterior depois que deixa de ser usada.
                slot.retired = (slot.retired or discard
                                or getattr(self._local, "slot", None) is not slot)
                should_close = slot.retired and slot.users == 0
                if should_close:
                    self._slots.pop(id(slot), None)
            if should_close:
                _close_session(slot.session)

    def close(self):
        with self._lock:
            # Slots guardados em outras threads ficam obsoletos mesmo quando
            # essas threads sobrevivem ao reinício da aplicação nos testes.
            self._generation += 1
            idle = []
            for slot in tuple(self._slots.values()):
                slot.retired = True
                if slot.users == 0:
                    self._slots.pop(id(slot), None)
                    idle.append(slot.session)
        for session in idle:
            _close_session(session)


_POOL = _GeminiSessionPool()


@contextmanager
def gemini_session(session_factory=None):
    """Reutilizar só o transporte padrão; injeções preservam o ciclo por chamada."""
    if session_factory is None:
        with _POOL.lease() as session:
            yield session
    else:
        # Clientes injetados podem ter seu próprio protocolo de recursos.
        # Conservar o comportamento anterior dos adaptadores e testes.
        with session_factory() as session:
            session.trust_env = False
            session.mount("https://", HTTPAdapter(max_retries=0))
            yield session


def close_gemini_transport():
    """Fechar conexões ociosas e retirar as ativas ao término da chamada atual."""
    _POOL.close()
