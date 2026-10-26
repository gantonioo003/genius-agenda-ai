"""Sincroniza apenas o estado local dos avisos; sem timers ou transporte."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from app.plan_reminders import load_recipient_binding
from app.reminder_store import ReminderStore
from app.services.reminder_sync import ReminderSynchronizationService, ReminderSynchronizationSummary


class _LiveLookup:
    """Abrir o cliente Google somente se houver eventos a conferir."""

    def lookup_snapshot(self, event_ids, expected_calendar_id):
        from app.google_calendar import calendar_client
        from app.reminder_calendar import GoogleReminderCalendarGateway
        with calendar_client() as client:
            return GoogleReminderCalendarGateway(client).lookup_snapshot(
                event_ids, expected_calendar_id=expected_calendar_id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Sincronizar avisos locais com os eventos atuais, sem enviar mensagens.')
    parser.add_argument('--database', type=Path, default=Path('data/reminders.sqlite3'),
                        help='Banco privado relativo à raiz do projeto ou absoluto.')
    args = parser.parse_args(argv)
    try:
        from app.settings import PROJECT_ROOT
        path = args.database if args.database.is_absolute() else PROJECT_ROOT / args.database
        store = ReminderStore(path)
        config = store.config()
        if config is None or not config.enabled or not config.permission_granted:
            status = ('not_configured' if config is None else
                      'disabled' if not config.enabled else 'permission_required')
            result = ReminderSynchronizationSummary(status)
        else:
            result = ReminderSynchronizationService(store, _LiveLookup(),
                expected_recipient_key=load_recipient_binding(PROJECT_ROOT)).sync()
        output = asdict(result)
        output['state_counts'] = dict(result.state_counts)
        output['messages_sent'] = 0
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({'status': 'error', 'message': 'Não foi possível sincronizar os lembretes com segurança.',
                          'messages_sent': 0}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
