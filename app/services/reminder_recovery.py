"""Recuperação local de abandono comprovado pelo lock de SO, sem transporte."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from app.reminder_store import ReminderRecoveryResult
from app.services.reminder_tick import reminder_tick_lock
from app.services.reminders import _instant


MAX_RECOVERY_RECORDS = 200
_COUNTERS = ("reservations_recovered", "expired_count", "uncertain_count", "protected_count")
_SKIP_REASONS = {"not_configured", "disabled", "permission_required", "busy"}


class ReminderRecoveryError(RuntimeError):
    def __init__(self):
        super().__init__("Não foi possível confirmar a recuperação local dos lembretes.")


@dataclass(frozen=True)
class ReminderRecoverySummary:
    recovery_status: str
    skip_reason: str | None = None
    reservations_recovered: int = 0
    expired_count: int = 0
    uncertain_count: int = 0
    protected_count: int = 0
    messages_sent: int = 0
    external_mutations: int = 0

    def public_data(self):
        if (self.recovery_status not in {"completed", "skipped", "failed"}
                or any(type(getattr(self, name)) is not int or getattr(self, name) < 0
                       for name in _COUNTERS)
                or type(self.messages_sent) is not int or self.messages_sent != 0
                or type(self.external_mutations) is not int or self.external_mutations != 0):
            raise ReminderRecoveryError()
        if self.recovery_status == "skipped":
            if self.skip_reason not in _SKIP_REASONS or any(getattr(self, name) for name in _COUNTERS):
                raise ReminderRecoveryError()
        elif self.skip_reason is not None:
            raise ReminderRecoveryError()
        return {"recovery_status": self.recovery_status, "skip_reason": self.skip_reason,
                **{name: getattr(self, name) for name in _COUNTERS},
                "messages_sent": 0, "external_mutations": 0}


class ReminderRecoveryService:
    def __init__(self, store, clock=None):
        self._store = store
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def recover(self):
        committed = None
        try:
            # Posse positiva, nunca TTL/idade, demonstra que o worker anterior
            # já não conserva o lock. A posse dura até o commit real terminar.
            with reminder_tick_lock(self._store.path) as acquired:
                if not acquired:
                    return ReminderRecoverySummary("skipped", "busy")
                config = self._store.config()
                if config is None or not config.enabled or not config.permission_granted:
                    reason = ("not_configured" if config is None else "disabled" if not config.enabled
                              else "permission_required")
                    return ReminderRecoverySummary("skipped", reason)
                before = _instant(self._clock())
                snapshot = self._store.recovery_snapshot(limit=MAX_RECOVERY_RECORDS)
                after = _instant(self._clock())
                if snapshot.config != config or after < before or after < snapshot.captured_at:
                    raise ReminderRecoveryError()
                result = self._store.apply_recovery(snapshot, not_before=after)
                if type(result) is not ReminderRecoveryResult:
                    raise ReminderRecoveryError()
                summary = ReminderRecoverySummary("completed",
                    **{name: getattr(result, name) for name in _COUNTERS})
                summary.public_data()
                committed = summary
            return committed
        except Exception:
            if committed is not None:
                # Resultado confirmado antes de falha ao fechar o lock: conservar
                # contagens conhecidas em vez de fingir que o lote não aconteceu.
                return replace(committed, recovery_status="failed")
            # apply_recovery pode ter confirmado e falhado na fronteira depois.
            # Sem resultado observado não inventar contagens ou rollback.
            raise ReminderRecoveryError() from None
