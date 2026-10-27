"""Vocabulário explícito de agenda; reconhecer intenção não concede autorização.

Os padrões públicos são strings regex sem grupos de captura, para texto passado
por ``normalize_action_text``. Verbos flexionados, infinitivos e erros comuns
enumerados compartilham o mesmo léxico. Não há aproximação por distância nem
prefixos abertos que transformem palavras desconhecidas em comandos.

``REQUEST_PREFIX`` inclui pedidos polidos usados apenas para preparar propostas.
``EXPLICIT_REQUEST_PREFIX`` é mais restrito: perguntas de capacidade e nomes de
ações (exclusão, troca, remoção etc.) continuam exigindo prévia e confirmação.
"""
import re
import unicodedata


CREATE_VERBS = (
    r'(?:agende|agenda|agendar|marque|marca|marcar|crie|cria|criar|'
    r'cadastre|cadastra|cadastrar|adicione|adiciona|adicionar|'
    r'adcione|adciona|adcionar|inclua|inclui|incluir|insira|insere|inserir|'
    r'coloque|coloca|colocar|bote|bota|botar|ponha|poe|'
    r'registre|registra|registrar|reserve|reserva|reservar|'
    r'programe|programa|programar|anote|anota|anotar)'
)
EDIT_VERBS = (
    r'(?:edite|edita|editar|altere|altera|alterar|atualize|atualiza|atualizar|'
    r'atualise|atualisa|atualisar|modifique|modifica|modificar|'
    r'mude|muda|mudar|troque|troca|trocar|corrija|corrige|corrigir|'
    r'ajuste|ajusta|ajustar|renomeie|renomeia|renomear|'
    r'retifique|retifica|retificar)'
)
RESCHEDULE_VERBS = (
    r'(?:reagende|reagenda|reagendar|remarque|remarca|remarcar|'
    r'reprograme|reprograma|reprogramar|'
    r'reajende|reajenda|reajendar|mova|move|mover|'
    r'mude|muda|mudar|troque|troca|trocar|'
    r'transfira|transfere|transferir|adie|adia|adiar|'
    r'antecipe|antecipa|antecipar|postergue|posterga|postergar|'
    r'atrase|atrasa|atrasar)'
)
DELETE_VERBS = (
    r'(?:exclua|exclui|excluir|exclue|esclua|escluir|'
    r'apague|apaga|apagar|remova|remove|remover|'
    r'cancele|cancela|cancelar|delete|deleta|deletar|'
    r'retire|retira|retirar|elimine|elimina|eliminar|'
    r'desmarque|desmarca|desmarcar|tire|tira|tirar)'
)
ACTION_VERBS = {
    'create': CREATE_VERBS, 'edit': EDIT_VERBS,
    'reschedule': RESCHEDULE_VERBS, 'delete': DELETE_VERBS,
}
MUTATION_VERBS = r'(?:' + '|'.join(ACTION_VERBS.values()) + r')'

# "Mudar" e "trocar" dependem do campo; nunca significam excluir.
AMBIGUOUS_CHANGE_VERBS = r'(?:mude|muda|mudar|troque|troca|trocar|ajuste|ajusta|ajustar)'
NOMINAL_ACTIONS = {
    'create': r'(?:criacao|agendamento|marcacao|cadastro|inclusao|insercao|adicao|registro|reserva)',
    'edit': r'(?:edicao|alteracao|atualizacao|modificacao|correcao|mudanca|troca|ajuste)',
    'reschedule': r'(?:reagendamento|remarcacao|reprogramacao|adiamento|antecipacao)',
    'delete': r'(?:exclusao|remocao|apagamento|cancelamento|delecao)',
}
MUTATION_NOUNS = r'(?:' + '|'.join(NOMINAL_ACTIONS.values()) + r')'

POLITE_PREFIX = r'(?:(?:por favor|por gentileza|gentileza|favor|pfv|pfvr)[,\s]+)?'
_DESIRE = (
    r'(?:(?:eu\s+)?(?:quero(?:\s+que(?:\s+(?:voce|vc))?)?|'
    r'gostaria(?:\s+(?:de|da|do))?|queria|preciso(?:\s+(?:de|da|do))?|necessito(?:\s+(?:de|da|do))?|'
    r'desejo|vou querer)|vamos|bora)'
)
_ABILITY = r'(?:(?:(?:voce|vc)\s+)?(?:pode|poderia|consegue|conseguiria|podia)(?:\s+(?:voce|vc))?)'
EXPLICIT_REQUEST_PREFIX = POLITE_PREFIX + r'(?:' + _DESIRE + r'[,\s]+)?' + POLITE_PREFIX + r'(?:me\s+)?'
REQUEST_PREFIX = POLITE_PREFIX + r'(?:(?:' + _DESIRE + '|' + _ABILITY + r')[,\s]+)?' + POLITE_PREFIX + r'(?:me\s+)?'
_PLAN_PREFIX = r'(?:(?:faca|faz|fazer|realize|realizar|providencie|providenciar|solicito)\s+)?(?:(?:a|o|um|uma)\s+)?'
_GUARDED_PREFIX = r'(?:(?:nao|naum|n|nunca|nem|sem|como|posso|podemos|se|e se|talvez|suponha|caso|seria possivel|e possivel)\s+){0,3}'
NEW_INTENT_PREFIX = _GUARDED_PREFIX + REQUEST_PREFIX
_INTENT_BODY = NEW_INTENT_PREFIX + r'(?:' + MUTATION_VERBS + r'|' + _PLAN_PREFIX + MUTATION_NOUNS + r')\b'
_NEW_INTENT = re.compile(r'^' + _INTENT_BODY)
_MUTATIONS = re.compile(r'\b(?:' + MUTATION_VERBS + '|' + MUTATION_NOUNS + r')\b')
_NOUN_LIKE = re.compile(r'(?:agenda|marca|programa|' + MUTATION_NOUNS + r')\Z')
_CLAUSE_INTENT = re.compile(r'(?:^|[,;\n]|\b(?:e|ou|depois|tambem|ainda|alem disso)\s+)\s*' + _INTENT_BODY)


def normalize_action_text(message):
    """Dobrar caixa, acentos e espaços; preservar pontuação para as guardas."""
    return ' '.join(''.join(char for char in unicodedata.normalize('NFKD', message.casefold())
                           if not unicodedata.combining(char)).split())


def starts_new_mutation(message):
    """Detectar nova proposta, inclusive nominal, negativa ou interrogativa.

    A função descarta autorização herdada. Não decide ação nem execução, e não
    precisa declarar uma pergunta/negação como um comando para reconhecê-la.
    """
    value = normalize_action_text(message).lstrip('"\'“‘`')
    return _NEW_INTENT.match(value) is not None


def has_multiple_mutations(message):
    """Impedir que um pedido composto seja reduzido a uma escrita pelo modelo.

    Nomes e formas ambíguas como "agenda", "troca" e "reserva" contam apenas
    no início de um pedido ou de outra oração: "crie uma reserva na agenda" e
    "agende Troca de óleo" continuam sendo um único pedido.
    """
    value = normalize_action_text(message).lstrip('"\'“‘`')
    clause_ends = {match.end() for match in _CLAUSE_INTENT.finditer(value)}
    count = 0
    for match in _MUTATIONS.finditer(value):
        if _NOUN_LIKE.fullmatch(match.group()) is None or match.end() in clause_ends:
            count += 1
            if count > 1:
                return True
    return False
