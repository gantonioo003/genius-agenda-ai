"""Um pulso local de sincronização e geração, sem timer ou transporte.

Cada fase conserva sua transação própria. O lock de SO abrange as duas fases,
mas não transforma commits independentes em uma transação com o Google.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import errno
import hmac
import os
from pathlib import Path
import re

from app.services.reminder_sync import (
    ReminderSynchronizationService, ReminderSynchronizationSummary,
)
from app.services.reminders import ReminderGenerationSummary, ReminderPlanningService


_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_STATES = frozenset({"pending", "blocked", "reserved", "sending", "accepted",
                     "uncertain", "cancelled", "expired"})


class ReminderTickError(RuntimeError):
    def __init__(self):
        super().__init__("Não foi possível concluir a verificação dos lembretes.")


@contextmanager
def reminder_tick_lock(database_path):
    """Exclusão não bloqueante entre processos; nunca apagar o arquivo do lock.

    O fechamento do descritor, inclusive pelo SO após crash, libera o lock.
    Não há prazo que autorize sobrepor uma execução que ainda possa continuar.
    O schema 1 do ledger permanece intacto.
    """
    descriptor = None
    try:
        path = Path(str(Path(database_path).resolve()) + ".tick.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            # Só compartilhar o resultado depois da tentativa real do SO.
            yield False
        else:
            yield True
    except Exception:
        raise ReminderTickError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _state_counts(entries):
    result = {}
    for state, count in entries:
        if (state not in _STATES or state in result
                or type(count) is not int or count < 0):
            raise ReminderTickError()
        result[state] = count
    return result


def _phase(summary, *, generation=False, failed=False):
    fields = (("events_seen", "eligible_events", "skipped_count", "reminders_considered")
              if generation else ("events_checked", "tracked_reminders", "reminders_considered",
                                  "cancelled_count", "replanned_count", "unchanged_count"))
    result = {"status": "failed" if failed else "not_run"}
    for field in fields:
        value = getattr(summary, field) if summary is not None else 0
        if type(value) is not int or value < 0:
            raise ReminderTickError()
        result[field] = value
    if summary is not None:
        expected = "planned" if generation else "synchronized"
        if summary.status != expected:
            raise ReminderTickError()
        result["status"] = expected
    result["state_counts"] = _state_counts(summary.state_counts) if summary is not None else {}
    return result


@dataclass(frozen=True)
class ReminderTickSummary:
    tick_status: str
    skip_reason: str | None = None
    synchronization: ReminderSynchronizationSummary | None = None
    generation: ReminderGenerationSummary | None = None
    failed_phase: str | None = None

    def public_data(self):
        """Projeção estrita; applied confirma fases, inclusive sem novas linhas.

        partial preserva ao menos a sincronização concluída antes de falha
        posterior, inclusive ao encerrar recursos após concluir a geração.
        """
        return {
            "tick_status": self.tick_status,
            "skip_reason": self.skip_reason,
            "local_mutation_state": ("partial" if self.tick_status == "partial" else
                                     "applied" if self.tick_status == "completed" else "not_applied"),
            "synchronization": _phase(self.synchronization,
                failed=self.failed_phase == "synchronization"),
            "generation": _phase(self.generation, generation=True,
                failed=self.failed_phase == "generation"),
            "messages_sent": 0,
            "external_mutations": 0,
        }


class _LazyGateway:
    def __init__(self, stack, factory):
        self._stack = stack
        self._factory = factory
        self._gateway = None

    def _get(self):
        if self._gateway is None:
            self._gateway = self._stack.enter_context(self._factory())
        return self._gateway

    def lookup_snapshot(self, *args, **kwargs):
        return self._get().lookup_snapshot(*args, **kwargs)

    def snapshot(self, *args, **kwargs):
        return self._get().snapshot(*args, **kwargs)


class ReminderTickService:
    """Sincronizar antes de descobrir novos avisos; cada fase falha fechada."""

    def __init__(self, store, gateway_factory, recipient_binding_loader, *, clock=None):
        self._store = store
        self._gateway_factory = gateway_factory
        self._recipient_binding_loader = recipient_binding_loader
        self._clock = clock

    def tick(self) -> ReminderTickSummary:
        synchronization = None
        generation = None
        phase = None
        try:
            with reminder_tick_lock(self._store.path) as acquired:
                if not acquired:
                    return ReminderTickSummary("skipped", skip_reason="busy")
                config = self._store.config()
                if config is None or not config.enabled or not config.permission_granted:
                    reason = ("not_configured" if config is None else
                              "disabled" if not config.enabled else "permission_required")
                    return ReminderTickSummary("skipped", skip_reason=reason)
                recipient = self._recipient_binding_loader()
                if (not isinstance(recipient, str) or _HASH.fullmatch(recipient) is None
                        or not hmac.compare_digest(config.recipient_key, recipient)):
                    raise ReminderTickError()
                with ExitStack() as stack:
                    gateway = _LazyGateway(stack, self._gateway_factory)
                    phase = "synchronization"
                    synchronization = ReminderSynchronizationService(self._store, gateway,
                        expected_recipient_key=recipient, clock=self._clock).sync()
                    if (type(synchronization) is not ReminderSynchronizationSummary
                            or synchronization.status != "synchronized"):
                        synchronization = None
                        raise ReminderTickError()
                    phase = "generation"
                    generation = ReminderPlanningService(self._store, gateway,
                        expected_recipient_key=recipient, clock=self._clock).generate()
                    if (type(generation) is not ReminderGenerationSummary
                            or generation.status != "planned"):
                        generation = None
                        raise ReminderTickError()
                    phase = "cleanup"
                    # Validar a projeção ainda sob o lock e antes de liberar o cliente.
                    result = ReminderTickSummary("completed", synchronization=synchronization,
                                                 generation=generation)
                    result.public_data()
                return result
        except Exception:
            # Se a segunda fase falhar, o commit da primeira continua observado.
            # Não inventar rollback entre fases nem reproduzir exceções privadas.
            return ReminderTickSummary("partial" if synchronization is not None else "failed",
                synchronization=synchronization, generation=generation, failed_phase=phase)
