"""Consulta a agenda e prepara avisos locais; não possui transporte de mensagens."""

import argparse
from dataclasses import asdict
from hashlib import sha256
import hmac
import json
from pathlib import Path

from app.reminder_store import ReminderStore
from app.services.reminders import ReminderGenerationSummary, ReminderPlanningService


def load_recipient_binding(project_root):
    """Mesmo vínculo da política privada usada pelo worker; nunca exibir o telefone."""
    from app.sandbox_webhook import load_allowed_sender, _config_values, _decode_json
    recipient = load_allowed_sender((project_root / 'secrets/whatsapp-sandbox-security.json').read_bytes())
    token, _ = _config_values(_decode_json((project_root / 'secrets/whatsapp-sandbox-local.json').read_bytes()))
    return hmac.new(token.encode('ascii'),
        b'genius-whatsapp-agent-sender:v1\0' + recipient.encode('ascii'), sha256).hexdigest()


def run_planning(store, gateway, recipient_key, *, configure=False, enable=False,
                 allow_reminders=False):
    """Fronteira operacional explícita; flags não são extraídas de mensagens/LLM."""
    config = store.config()
    if configure:
        binding = gateway.binding()
        config = store.configure(binding.calendar_id, recipient_key,
            offsets=None if config is None else dict(config.offsets),
            enabled=enable, permission_granted=allow_reminders,
            expected_revision=None if config is None else config.revision)
    return ReminderPlanningService(store, gateway,
        expected_recipient_key=recipient_key).generate()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Preparar lembretes locais, sem enviar mensagens.')
    parser.add_argument('--database', type=Path, default=Path('data/reminders.sqlite3'),
                        help='Banco privado de lembretes relativo à raiz do projeto ou absoluto.')
    parser.add_argument('--configure', action='store_true', help='Configurar explicitamente agenda e vínculo atuais.')
    parser.add_argument('--enable', action='store_true', help='Habilitar o perfil ao configurar; não inicia agendador.')
    parser.add_argument('--allow-reminders', action='store_true',
                        help='Registrar permissão explícita do operador para os avisos deste perfil.')
    args = parser.parse_args(argv)
    if (args.enable or args.allow_reminders) and not args.configure:
        parser.error('--enable e --allow-reminders exigem --configure.')
    try:
        from app.settings import PROJECT_ROOT
        path = args.database if args.database.is_absolute() else PROJECT_ROOT / args.database
        store = ReminderStore(path)
        config = store.config()
        # A configuração padrão não abre OAuth, arquivos do WhatsApp ou consulta Google.
        if not args.configure and (config is None or not config.enabled or not config.permission_granted):
            status = ('not_configured' if config is None else
                      'disabled' if not config.enabled else 'permission_required')
            result = ReminderGenerationSummary(status)
        else:
            recipient_key = load_recipient_binding(PROJECT_ROOT)
            if not args.configure and not hmac.compare_digest(config.recipient_key, recipient_key):
                raise ValueError()
            from app.google_calendar import calendar_client
            from app.reminder_calendar import GoogleReminderCalendarGateway
            with calendar_client() as client:
                result = run_planning(store, GoogleReminderCalendarGateway(client), recipient_key,
                    configure=args.configure, enable=args.enable, allow_reminders=args.allow_reminders)
        output = asdict(result)
        output['state_counts'] = dict(result.state_counts)
        output['messages_sent'] = 0
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except Exception:
        # Não propagar mensagens HTTP/SQLite/credenciais ou dados de agenda.
        print(json.dumps({'status': 'error', 'message': 'Não foi possível preparar os lembretes com segurança.',
                          'messages_sent': 0}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
