"""Configura um unico telefone autorizado, sem expor o valor em argumentos ou saida."""

import argparse
import getpass
import json
from pathlib import Path
import re
import sys
import warnings


POLICY_FILE = Path('secrets/whatsapp-sandbox-security.json')
PHONE = re.compile(r'[1-9][0-9]{6,14}\Z', re.ASCII)
ERROR = 'Configuracao do remetente invalida; confira o arquivo privado sem compartilhar seu conteudo.'


def canonical_phone(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'\+?[0-9 ()-]{7,40}', value, re.ASCII):
        raise ValueError(ERROR)
    phone = re.sub(r'[ ()-]', '', value)
    if phone.startswith('+'):
        phone = phone[1:]
    if not PHONE.fullmatch(phone):
        raise ValueError(ERROR)
    return phone


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(ERROR)
        value[key] = item
    return value


def configure_sender(root: Path, raw_phone: str) -> str:
    """Cria a politica privada; repetir so confirma o mesmo numero, sem substituir."""
    phone = canonical_phone(raw_phone)
    path = root / POLICY_FILE
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
        except (OSError, UnicodeError, ValueError):
            raise ValueError(ERROR) from None
        if (not isinstance(current, dict) or set(current) != {'allowed_phone'}
                or not isinstance(current['allowed_phone'], str)
                or not PHONE.fullmatch(current['allowed_phone'])):
            raise ValueError(ERROR)
        if current['allowed_phone'] != phone:
            raise ValueError('Ja existe outro remetente configurado; preserve a politica e revise a troca explicitamente.')
        return 'unchanged'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusivo: nao sobrescreve uma politica preexistente ou criada por outra instancia.
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump({'allowed_phone': phone}, stream)
        stream.write('\n')
    return 'created'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if not args.apply:
        parser.error('Use --apply para configurar o remetente privado.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            raw = getpass.getpass('WhatsApp autorizado, com pais e DDD (entrada oculta): ')
        except getpass.GetPassWarning:
            raise ValueError('Use um terminal com entrada oculta disponivel.') from None
    result = configure_sender(args.project_root.resolve(), raw)
    print('Politica privada do remetente ' + ('criada.' if result == 'created' else 'conferida, sem alteracao.'))
    print('Nenhum telefone exibido. Reinicie somente o receptor para aplicar a politica.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, EOFError):
        print('Configuracao interrompida; confira a entrada e o arquivo privado. Nenhum telefone foi exibido.', file=sys.stderr)
        raise SystemExit(1)
