"""Turnos locais do agente, sem transporte WhatsApp nem autorização pelo modelo."""
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import re
import unicodedata
from uuid import UUID

from app.agent_query_contract import QueryPlan
from app.api_agent_queries import _validate_result as validate_query_result
from app.confirmation_state import ConfirmationError
from app.conversation_store import ConversationError, validate_memory
from app.llm import ModelError
from app.services.agenda_tools import AgendaReadTools
from app.services.agent_creation import validate_creation_result
from app.services.agent_deletion import validate_deletion_result
from app.services.agent_dialogue import validate_dialogue_result
from app.services.agent_preview import validate_preview_message
from app.services.agent_updates import validate_update_result
from app.services.confirmations import is_confirmation_message, is_rejection_message, validate_confirmation_result
from app.services.time_rules import load_zone
from app.services.preparation_chat import PreparationChatResult, is_preparation_request
from app.chat_formatting import event_interval, visible_zone_label

MAX_REPLY_CHARS = 3500
_ACTIONS = {'create', 'edit', 'reschedule'}
_FIELDS = {'title':'Título', 'description':'Descrição', 'location':'Local'}
_VERBS = {'create': r'(?:agende|agendar|marque|marcar|crie|criar|cadastre|cadastrar)',
          'edit': r'(?:edite|editar|altere|alterar|atualize|atualizar|troque|trocar)',
          'reschedule': r'(?:reagende|reagendar|remarque|remarcar|mova|mover|troque|trocar)'}
_DURATION = re.compile(r'\b(?:duracao(?: de)?|durante|por|com|de)\s+(\d{1,3})\s*(min(?:uto)?s?|h(?:ora)?s?)\b')

@dataclass(frozen=True, repr=False)
class AgentTurn:
    reply: str
    conversation_id: str
    authorization_action: str | None
    status: str
    action: str | None
    mutation_state: str
    def __repr__(self):
        return 'AgentTurn(<private>)'


def validate_agent_turn(turn):
    try:
        if type(turn) is not AgentTurn or not isinstance(turn.reply, str) or not turn.reply.strip() or len(turn.reply)>MAX_REPLY_CHARS:
            raise ValueError()
        turn.reply.encode('utf-8')
        if any(unicodedata.category(c).startswith('C') and c not in '\r\n\t' for c in turn.reply):
            raise ValueError()
        parsed=UUID(turn.conversation_id)
        if parsed.version!=4 or str(parsed)!=turn.conversation_id:
            raise ValueError()
        if turn.authorization_action not in _ACTIONS|{None} or turn.action not in _ACTIONS|{'delete','list','get','summarize_agenda','prepare_briefing','find_free_slots','suggest_reschedule',None}:
            raise ValueError()
        if turn.status not in {'succeeded','failed','needs_clarification','needs_correction','needs_confirmation','cancelled','revoked','expired','invalidated','consumed','not_found','conflict'}:
            raise ValueError()
        if turn.mutation_state not in {'not_applied','not_applicable','confirmed','unknown'}:
            raise ValueError()
        return turn
    except Exception:
        raise ValueError('O turno do agente não contém um resultado válido.') from None


def _normalized(message):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',message.casefold()) if not unicodedata.combining(c)).split())


def _restricted(message):
    # Uma ressalva pode aparecer depois dos dados. Não deixar o começo
    # imperativo anular uma negação, hipótese ou pedido de mera preparação.
    # Falsos positivos em nomes/títulos seguem para prévia, sem escrita.
    value=_normalized(message)
    return bool(re.search(r'\b(?:nao|nem|sem|se|apenas|somente|so|talvez|hipoteticamente|hipotese|simulacao|simular|simule|simulando|exemplo|previa|rascunho|demonstracao|demonstre|aguarde|aguardar|espere|esperar|antes|quando|confirmacao|confirmar|aprovacao|aprovar|revisao|revise|revisar|confira|conferir)\b',value))


def _question(message):
    value=_normalized(message)
    return ('?' in value or _restricted(message)
            or bool(re.match(r'^(?:como|sera|seria|se |talvez|suponha|hipoteticamente|e se|nao\b|nunca\b)',value))
            or message.lstrip().startswith(('"',"'",'“','‘','`')))


def explicit_action(message, action):
    """Sinal conservador do texto atual; o resultado ready sozinho não autoriza."""
    if action not in _ACTIONS or _question(message):
        return False
    value=_normalized(message)
    verb=_VERBS[action]
    prefix=r'(?:(?:por favor|favor)[, ]+)?'
    if re.match(prefix+verb+r'\b\s+\S',value):
        return True
    return bool(re.match(prefix+r'(?:eu )?(?:quero|gostaria de|preciso(?: de)?)\s+'+verb+r'\b\s+\S',value))


def _new_intent(message):
    value=_normalized(message)
    verbs='|'.join(_VERBS.values())+r'|exclua|excluir|apague|apagar|remova|remover'
    return bool(re.match(r'^(?:(?:por favor|favor)[, ]+)?(?:(?:eu )?(?:quero|gostaria de|preciso(?: de)?)\s+)?(?:'+verbs+r')\b',value))


def _multiple_mutations(message):
    # O modelo não pode escolher uma das escritas de um pedido composto.
    verbs='|'.join(_VERBS.values())+r'|exclua|excluir|apague|apagar|remova|remover'
    return len(re.findall(r'\b(?:'+verbs+r')\b',_normalized(message)))>1


def _event_id_literal(event_id, message):
    # IDs são opacos, inclusive sua pontuação e caixa. Não remover partes de um
    # token para transformá-lo silenciosamente em outro identificador válido.
    return event_id in message.split()


def _literal(value, message):
    value=_normalized(value)
    if not value:
        return False
    before=r'(?<!\w)' if value[0].isalnum() else ''
    after=r'(?!\w)' if value[-1].isalnum() else ''
    return re.search(before+re.escape(value)+after,_normalized(message)) is not None


def _date_literal(value, message):
    try:
        day=date.fromisoformat(value)
    except (ValueError,TypeError):
        return False
    # A data ISO pode estar imediatamente antes do T do instante completo.
    return re.search(r'(?<!\d)'+re.escape(day.isoformat())+r'(?!\d)',message) is not None or _literal(day.strftime('%d/%m/%Y'),message)


def _time_literal_count(value, message):
    hour=str(value.hour) if value.hour>=10 else '0?'+str(value.hour)
    minute=str(value.minute) if value.minute>=10 else '0?'+str(value.minute)
    # Não interpretar um número isolado como horário: pode ser dia ou opção.
    suffix=(r'(?:h(?:00)?|:00|\s+horas?)' if value.minute==0 else r'(?:h|:)'+minute)
    return len(re.findall(r'(?<!\d)'+hour+suffix+r'(?!\d)',_normalized(message)))


def _time_literal(value, message):
    return bool(_time_literal_count(value,message))


def _duration_end(value, fields, message):
    if 'start' not in fields:
        return False
    try:
        start=datetime.fromisoformat(fields['start'])
    except (ValueError,TypeError):
        return False
    elapsed=value-start
    for match in _DURATION.finditer(_normalized(message)):
        duration=timedelta(minutes=int(match[1])) if match[2].startswith('min') else timedelta(hours=int(match[1]))
        if duration>timedelta(0) and duration==elapsed:
            return True
    return False


def _unstated_new_end(message, before, pending):
    """Uma continuação não ganha um término estimado apenas porque veio ready."""
    if pending is None or pending.fields.get('action') not in {'create','reschedule'}:
        return False
    fields=pending.fields
    old=before.fields if before is not None and before.fields.get('action')==fields['action'] else {}
    if 'end' not in fields or 'end' in old:
        return False
    try:
        zone=load_zone(fields.get('time_zone','America/Recife'))
        finish=datetime.fromisoformat(fields['end'])
        start=datetime.fromisoformat(fields['start']) if 'start' in fields else None
        if finish.utcoffset() is None or start is not None and start.utcoffset() is None:
            return False
        finish=finish.astimezone(zone)
        start=start.astimezone(zone) if start is not None else None
        if _duration_end(finish,fields,message):
            return False
        mentioned=_time_literal_count(finish,message)
        # Um único horário de início não sustenta também o fim no dia seguinte.
        shared=(start is not None and (start.hour,start.minute)==(finish.hour,finish.minute)
                and mentioned==1
                and not re.search(r'\b(?:termina|termino|fim|ate)\b',_normalized(message)))
        if mentioned and not shared:
            return False
        temporal=bool(before is not None and old or start is not None and _time_literal(start,message)
                      or _DURATION.search(_normalized(message)))
        return temporal
    except (ValueError,TypeError,KeyError,OverflowError):
        # Datas/fusos inválidos seguem pelo caminho de correção já existente.
        return False


def _mentioned_constraints_match(message, fields, event):
    """Não ignorar uma correção literal de horário/data/fuso na continuação."""
    value=_normalized(message)
    zones=re.findall(r'(?<![\w/])([a-z][a-z_+-]*/[a-z][a-z0-9_+-]*(?:/[a-z][a-z0-9_+-]*)?)\b',value)
    if zones and any(zone!=_normalized(fields.get('time_zone','')) for zone in zones):
        return False
    dates=set()
    times=set()
    for name in ('date','start','end'):
        if name in fields:
            dates.add(date.fromisoformat(fields[name][:10]))
            if name!='date':
                instant=datetime.fromisoformat(fields[name])
                times.add((instant.hour,instant.minute))
    if event is not None:
        for name in ('start','end'):
            dates.add(date.fromisoformat(event[name][:10]))
            if not event['all_day']:
                instant=datetime.fromisoformat(event[name])
                times.add((instant.hour,instant.minute))
    for literal in re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',value):
        if date.fromisoformat(literal) not in dates:
            return False
    for literal in re.findall(r'(?<!\d)\d{1,4}/\d{1,4}(?:/\d{1,4})?(?![\d/])',value):
        parts=literal.split('/')
        if len(parts[0])>2 or len(parts[1])>2 or len(parts)==3 and len(parts[2])!=4:
            return False
        day,month=int(parts[0]),int(parts[1])
        year=int(parts[2]) if len(parts)==3 else None
        if not any(item.day==day and item.month==month and (year is None or item.year==year) for item in dates):
            return False
    duration_spans=[match.span() for match in _DURATION.finditer(value)]
    for match in re.finditer(r'(?:(?<![\w:+-])|(?<=\dt))(\d{1,2})(?:h(\d{1,2})?|:(\d{2}))(?!\w)',value):
        if any(start<=match.start()<end for start,end in duration_spans):
            continue
        instant=(int(match[1]),int(match[2] or match[3] or 0))
        if instant not in times:
            return False
    return True


def _grounded_fields(action, message, before, after, event, selected_event_id):
    """Só conservar autorização automática para dados ancorados localmente.

    A interpretação geral continua no LLM. Caso ela acrescente um valor que
    não podemos conferir no texto, apresentar a proposta inteira para aprovação
    em vez de tratar o formato válido como autorização dos seus valores.
    """
    if action not in _ACTIONS or after is None:
        return False
    fields=after.fields
    old=before.fields if before is not None else {}
    if not _mentioned_constraints_match(message,fields,event):
        return False
    value=_normalized(message)
    mentioned={name for name,label in (('title','titulo|nome'),('description','descricao'),('location','local'))
               if re.search(r'\b(?:'+label+r')\b',value)}
    changed_text={name for name in _FIELDS if fields.get(name) and old.get(name)!=fields[name]}
    # Presença de valores não prova qual valor pertence a qual campo. Edições
    # compostas são apresentadas por inteiro, inclusive se o modelo omitir um papel.
    if len(mentioned)>1 or len(changed_text)>1:
        return False
    for name in mentioned:
        item=fields.get(name)
        clearing=(item=='' and name in {'description','location'}
                  and re.search(r'\b(?:limpe|limpar|apague|apagar|remova|remover|retire|retirar|esvazie)\b',value))
        if not clearing and (not item or not _literal(item,message)):
            return False
    relative=bool(re.search(r'\b(?:hoje|amanha|ontem|proxim[oa]|seguinte|segunda|terca|quarta|quinta|sexta|sabado|domingo)\b',value))
    anchors=set()
    for name in ('date','start','end'):
        if name in old:
            anchors.add(old[name][:10])
    for name in ('date','start','end'):
        if name in fields and _date_literal(fields[name][:10],message):
            anchors.add(fields[name][:10])
    for name,item in fields.items():
        if name=='action' or old.get(name)==item:
            continue
        if name=='time_zone':
            if item=='America/Recife' or _literal(item,message):
                continue
            return False
        if name=='event_id':
            if _event_id_literal(item,message):
                continue
            if (item==selected_event_id and re.search(r'\b(?:esse|essa|este|esta|aquele|aquela)\s+(?:evento|entrevista|compromisso)\b',value)):
                continue
            # Uma seleção por nome também exige a data do alvo no texto atual.
            if event is not None and _literal(event['title'],message) and _date_literal(event['start'][:10],message):
                continue
            return False
        if name in {'title','description','location','search_title'}:
            if item and _literal(item,message):
                continue
            if item=='' and name=='description' and action=='create':
                continue
            if item=='' and name in {'description','location'} and re.search(r'\b(?:limpe|limpar|apague|apagar|remova|remover|retire|retirar|esvazie)\b',value) and re.search(r'\b'+('descricao' if name=='description' else 'local')+r'\b',value):
                continue
            return False
        if name=='period':
            if item=='day' or _literal({'week':'semana','month':'mes'}.get(item,item),message):
                continue
            return False
        if name=='date':
            if _date_literal(item,message):
                continue
            return False
        if name in {'start','end'}:
            instant=datetime.fromisoformat(item)
            if name=='end' and _duration_end(instant,fields,message):
                continue
            if not _time_literal(instant,message):
                return False
            if _date_literal(instant.date().isoformat(),message):
                continue
            if not relative and instant.date().isoformat() in anchors:
                continue
            return False
        return False
    return True


def _is_query(message, memory):
    value=_normalized(message)
    if re.match(r'^(?:o que (?:eu )?tenho|qual\b|quais\b|consulte\b|consultar\b|liste\b|listar\b|mostre\b|mostrar\b|minha agenda\b|tenho (?:algo|algum|entrevista|compromisso)\b)',value):
        return True
    return memory.last_list is not None and bool(re.match(r'^(?:e )?(?:hoje|amanha|(?:na|nesta|nessa|esta|essa) (?:proxima )?semana|(?:no|neste|nesse|este|esse) (?:proximo )?mes|na semana seguinte)\b',value))


def _safe_text(value, limit=None):
    if not isinstance(value,str):
        raise ValueError()
    value.encode('utf-8')
    value=''.join(c if not unicodedata.category(c).startswith('C') or c in '\n\t' else ' ' for c in value)
    return value if limit is None or len(value)<=limit else value[:max(0,limit-1)]+'…'


def _when(event, *, with_zone=True, include_date=True):
    value = event_interval(event['start'], event['end'], event['all_day'], include_date=include_date)
    label = visible_zone_label(event['time_zone']) if with_zone else ''
    return value + (' · ' + label if label else '')


def _event_line(event, title_limit=None, *, with_zone=True, include_date=True):
    return _safe_text(event['title'],title_limit)+' — '+_when(event, with_zone=with_zone, include_date=include_date)


def _interval(parameters):
    return _when({'all_day':False,'start':parameters['start'],'end':parameters['end'],'time_zone':parameters['time_zone']})


def _preview_text(data):
    action=data['action']
    params=data['parameters']
    if action=='create':
        lines=['Confira a criação:',_safe_text(params['title'])+' — '+_interval(params)]
        if params.get('description'):
            lines.append('Descrição: '+_safe_text(params['description']))
    else:
        lines=[{'edit':'Confira a edição:', 'reschedule':'Confira o reagendamento:', 'delete':'Confirma excluir este compromisso?'}[action],_event_line(data['event'])]
        if action=='edit':
            for key,label in _FIELDS.items():
                if key in params:
                    lines.append(label+': '+(_safe_text(params[key]) if params[key] else '(limpar campo)'))
        elif action=='reschedule':
            lines.append('Novo horário: '+_interval(params))
    lines.append('Responda "sim" para confirmar ou "não" para cancelar.')
    return '\n'.join(lines)


def _question_text(result):
    data=result.data or {}
    if data.get('candidates'):
        lines=[_safe_text(data.get('question') or 'Qual compromisso deseja usar?')]
        for number,event in enumerate(data['candidates'],1):
            # As referências públicas dos candidatos têm apenas seleção mínima.
            lines.append(str(number)+'. '+_event_line(event,120))
        lines.append('Responda com o número da opção, ou refine o título e a data.')
        text='\n'.join(lines)
        if len(text)>MAX_REPLY_CHARS:
            return 'Há muitas opções para apresentar com segurança. Refine o título e a data do compromisso.'
        return text
    if data.get('question'):
        return _safe_text(data['question'])
    if data.get('reason')=='cancelled':
        return 'Pedido cancelado. Nenhum compromisso foi removido.'
    return 'Pode esclarecer um pedido de agenda por vez?'


def _query_text(result):
    if result.status=='failed':
        return 'Não consegui consultar a agenda agora. Uma falha de leitura não significa que ela está vazia.'
    if result.status=='not_found':
        return 'O evento não foi encontrado na agenda consultada. Informe outro título/data ou identificador.'
    if result.status=='needs_clarification':
        return _safe_text(result.data['question'])
    if result.action=='get':
        return 'Encontrei este compromisso:\n'+_event_line(result.data['event'],500)
    events=result.data['events']
    period=result.data['period']
    start=datetime.fromisoformat(period['start']).strftime('%d/%m/%Y')
    end=(datetime.fromisoformat(period['end'])-timedelta(microseconds=1)).strftime('%d/%m/%Y')
    single_day = start == end
    label = visible_zone_label(period['time_zone'])
    if not events:
        return ('Não encontrei compromissos em '+start if single_day else
                'Não encontrei compromissos de '+start+' a '+end)+'.'+(' '+label if label else '')
    lines=['*Sua agenda*',start if single_day else start+' a '+end,'']
    day = datetime.fromisoformat(period['start']).date().isoformat()
    for number,event in enumerate(events[:10],1):
        include_date = not (single_day and event['start'][:10] == day)
        lines.append(str(number)+'. '+_event_line(event,180,with_zone=False,include_date=include_date))
    if len(events)>10:
        lines.append('Há mais '+str(len(events)-10)+' compromissos. Consulte um período menor para ver os demais.')
    if label:
        lines += ['',label]
    text='\n'.join(lines)
    return text if len(text)<=MAX_REPLY_CHARS else 'A consulta trouxe muitos dados. Consulte um dia ou um período menor para apresentar os compromissos com segurança.'


def _failure_text(result):
    if result.mutation_state=='unknown':
        return 'Não consegui confirmar o resultado na agenda. A mudança pode ter sido aplicada; não vou repetir a gravação automaticamente. Precisamos conferir o estado antes de continuar.'
    if result.mutation_state=='confirmed':
        return 'Essa operação foi confirmada anteriormente, mas a consulta atual não reconfirmou o mesmo estado. Não reapliquei a alteração nem removi o evento novamente.'
    if result.status=='conflict':
        data=result.data or {}
        lines=['Esse intervalo conflita com outro compromisso. Escolha outro horário:']
        for event in data.get('conflicts',())[:5]:
            lines.append('• '+_event_line(event,120))
        return '\n'.join(lines)
    if result.error_code in {'confirmation_expired','confirmation_stale','confirmation_not_approved','confirmation_consumed','delete_snapshot_changed','update_snapshot_changed','delete_claim_conflict'}:
        return 'A aprovação ou o estado do evento não permite essa mudança. Confira os dados e prepare uma nova prévia antes de confirmar.'
    return 'Não consegui concluir a operação com os dados e o estado atuais. Nenhuma nova tentativa de gravação será feita automaticamente.'


class WhatsAppAgentService:
    def __init__(self, context_store, owner_key, *, dialogue_factory, confirmation_factory,
                 creation_factory, update_factory, deletion_factory, query_factory=None, gateway_factory=None,
                 preparation_factory=None):
        if not isinstance(owner_key,str) or re.fullmatch(r'[0-9a-f]{64}',owner_key) is None:
            raise ValueError('Configuração do agente indisponível.')
        if not all(callable(factory) for factory in (dialogue_factory,confirmation_factory,creation_factory,update_factory,deletion_factory)):
            raise ValueError('Configuração do agente indisponível.')
        if query_factory is not None and not callable(query_factory) or gateway_factory is not None and not callable(gateway_factory):
            raise ValueError('Configuração do agente indisponível.')
        if preparation_factory is not None and not callable(preparation_factory):
            raise ValueError('Configuração do preparo indisponível.')
        self._context,self._owner=context_store,owner_key
        self._dialogue,self._confirm=dialogue_factory,confirmation_factory
        self._create,self._update,self._delete=creation_factory,update_factory,deletion_factory
        self._query,self._gateway=query_factory,gateway_factory
        self._preparation=preparation_factory

    def _turn(self, reply, cid, authorization=None, status='succeeded', action=None, mutation='not_applied'):
        return validate_agent_turn(AgentTurn(reply,cid,authorization,status,action,mutation))

    def _load(self, conversation_id):
        if conversation_id is not None:
            try:
                return conversation_id,self._context.load(self._owner,conversation_id),False
            except ConversationError as error:
                if error.code!='conversation_not_found':
                    raise
        cid=self._context.create(self._owner)
        return cid,self._context.load(self._owner,cid),True

    def _save(self,cid,snapshot,memory):
        memory=validate_memory(memory)
        if memory!=snapshot.memory:
            self._context.save(self._owner,cid,snapshot.revision,memory)
        return self._context.load(self._owner,cid)

    def discard_unseen_confirmation(self,conversation_id):
        """Descartar consentimento de uma prévia cujo envio foi recusado."""
        try:
            snapshot=self._context.load(self._owner,conversation_id)
        except ConversationError as error:
            if error.code=='conversation_not_found':
                return None
            raise
        if snapshot.memory.confirmation is not None:
            self._save(conversation_id,snapshot,replace(snapshot.memory,confirmation=None))
        return None

    def _preparation_turn(self, message, cid, snapshot):
        """Isolar uma leitura de qualquer aprovação anterior, usando CAS existente."""
        try:
            memory = snapshot.memory
            revoked = False
            proposal = memory.confirmation
            if proposal is not None and proposal.state in {'pending', 'approved'}:
                result, memory = self._confirm().revoke(memory, proposal.proposal_id)
                validate_confirmation_result(result)
                memory = validate_memory(memory)
                if (memory.confirmation is None or memory.confirmation.proposal_id != proposal.proposal_id
                        or memory.confirmation.state not in {'revoked', 'expired', 'invalidated'}
                        or memory.pending_request != snapshot.memory.pending_request):
                    raise ConfirmationError('confirmation_stale')
                revoked = True
            # Salvar mesmo memória igual confere a revisão antes de abrir a fonte.
            # Aprovações consumidas, rascunhos e ledgers de execução são preservados.
            self._context.save(self._owner, cid, snapshot.revision, memory)
            claimed = self._context.load(self._owner, cid)
            if claimed.revision != snapshot.revision + 1 or claimed.memory != memory:
                raise ConversationError('conversation_conflict')
            result = self._preparation().respond(message, memory=claimed.memory)
            if (type(result) is not PreparationChatResult
                    or result.action not in {'summarize_agenda', 'prepare_briefing', 'find_free_slots','suggest_reschedule'}
                    or result.status not in {'succeeded', 'failed', 'needs_clarification'}):
                raise ValueError()
            latest = self._context.load(self._owner, cid)
            if latest.revision != claimed.revision or latest.memory != claimed.memory:
                raise ConversationError('conversation_conflict')
            if result.action == 'suggest_reschedule' and result.status == 'succeeded':
                memory = validate_memory(replace(latest.memory, selected_event_id=result.selected_event_id,
                                                 selected_time_zone=result.selected_time_zone))
                if memory.selected_event_id is None:
                    raise ValueError()
                self._context.save(self._owner, cid, latest.revision, memory)
            elif result.selected_event_id is not None or result.selected_time_zone is not None:
                raise ValueError()
            prefix = 'A prévia anterior perdeu validade; o rascunho foi preservado.\n\n' if revoked else ''
            if len(prefix + result.text) > MAX_REPLY_CHARS:
                return self._turn(prefix + 'A resposta é longa para uma mensagem. Use uma consulta por dia ou a API para ver o conteúdo completo.',
                                  cid, status='needs_clarification', action=result.action, mutation='not_applicable')
            return self._turn(prefix + result.text, cid, status=result.status, action=result.action, mutation='not_applicable')
        except (ConversationError, ConfirmationError):
            return self._turn('A conversa mudou ou a prévia não pôde ser invalidada com segurança. Confira o estado atual e faça o pedido novamente.',
                              cid, status='failed', mutation='not_applicable')
        except Exception:
            return self._turn('Não consegui preparar essa consulta agora. Confira a conexão e faça o pedido novamente; nenhuma alteração foi executada por esse preparo.',
                              cid, status='failed', mutation='not_applicable')

    def _preview(self,cid,snapshot,action):
        result,memory=self._confirm().preview(snapshot.memory)
        result=validate_confirmation_result(result)
        snapshot=self._save(cid,snapshot,memory)
        if result.status=='needs_confirmation':
            text=_preview_text(result.data)
            if len(text)>MAX_REPLY_CHARS:
                # Não deixar uma aprovação possível para conteúdo que não foi exibido.
                revoked,memory=self._confirm().revoke(snapshot.memory,result.data['proposal_id'])
                validate_confirmation_result(revoked)
                if revoked.status!='revoked':
                    raise RuntimeError('Não foi possível descartar a prévia não apresentada.')
                self._save(cid,snapshot,memory)
                return self._turn('A prévia é longa demais para uma mensagem. Resuma os dados ou use a API para conferir a proposta completa antes de aprovar.',cid,status='needs_correction',action=action)
            return self._turn(text,cid,status='needs_confirmation',action=action)
        return self._turn('Não consegui preparar uma prévia válida desse evento. Confira a identificação e os dados antes de continuar.',cid,status='failed',action=action)

    def _execute(self,cid,action):
        snapshot=self._context.load(self._owner,cid)
        pending=snapshot.memory.pending_request
        params=dict(pending.fields) if pending is not None else {}
        proposal=snapshot.memory.confirmation
        if action=='create':
            result=validate_creation_result(self._create().execute(self._owner,cid))
        elif action in {'edit','reschedule'}:
            result=validate_update_result(self._update().execute(self._owner,cid,action))
        elif action=='delete':
            result=validate_deletion_result(self._delete().execute(self._owner,cid))
        else:
            raise ValueError('A ferramenta de execução não está disponível.')
        if result.status!='succeeded':
            return self._turn(_failure_text(result),cid,status=result.status,action=action,mutation=result.mutation_state)
        data=result.data
        if action=='create':
            text='Compromisso confirmado na agenda:\n'+_safe_text(params['title'],500)+' — '+_interval(params)
            event_id=data['event_id']
        elif action=='delete':
            event=proposal.event if proposal is not None else None
            text=('Esse compromisso já não está na agenda.' if data['result']=='already_absent' else 'Compromisso excluído da agenda.')
            if event is not None:
                text+='\n'+_event_line(event,500)
            event_id=None
        else:
            text=('Os dados já estavam iguais ao pedido.' if data['result']=='unchanged' else 'Alteração confirmada na agenda:')+'\n'+_event_line(data['after'],500)
            if action=='edit':
                for field,label in _FIELDS.items():
                    if field in data['requested']:
                        text+='\n'+label+': '+(_safe_text(data['requested'][field],500) if data['requested'][field] else '(campo limpo)')
            event_id=data['after']['event_id']
        # A intenção já concluída não pode ser completada novamente por acidente.
        current=self._context.load(self._owner,cid)
        memory=replace(current.memory,pending_request=None,
            selected_event_id=event_id,selected_time_zone=params.get('time_zone') if event_id else None)
        self._save(cid,current,memory)
        return self._turn(text,cid,action=action,mutation='confirmed')

    def process(self,message,*,conversation_id=None,authorization_action=None,event_type='message_text'):
        cid,snapshot,fresh=self._load(conversation_id)
        authorization=None if fresh or authorization_action not in _ACTIONS else authorization_action
        if event_type!='message_text':
            return self._turn('Por enquanto, consigo conversar sobre a agenda por texto. Envie seu pedido em uma mensagem de texto.',cid,status='needs_clarification')
        try:
            message=validate_preview_message(message,max_input_chars=4000)
        except (ValueError,TypeError):
            return self._turn('Envie uma mensagem de texto com até 4.000 caracteres e um pedido de agenda por vez.',cid,status='needs_correction')
        try:
            if self._preparation is not None and is_preparation_request(message):
                return self._preparation_turn(message, cid, snapshot)
            if _multiple_mutations(message):
                self._save(cid,snapshot,replace(snapshot.memory,pending_request=None,confirmation=None))
                return self._turn('Vamos preparar um pedido por vez. Qual deseja fazer primeiro?',cid,status='needs_clarification')
            if snapshot.memory.confirmation is not None and (is_confirmation_message(message) or is_rejection_message(message)):
                service=self._confirm()
                pid=snapshot.memory.confirmation.proposal_id
                result,memory=(service.revoke(snapshot.memory,pid) if is_rejection_message(message)
                               else service.approve(snapshot.memory,pid,message))
                result=validate_confirmation_result(result)
                snapshot=self._save(cid,snapshot,memory)
                if result.status=='approved':
                    return self._execute(cid,result.action)
                if result.status=='revoked':
                    return self._turn('Proposta cancelada. Nenhum evento foi removido ou alterado por essa confirmação.',cid,status='revoked',action=result.action)
                return self._turn('Essa proposta não pode ser aprovada novamente. Confira o estado e prepare uma nova prévia se necessário.',cid,status=result.status if result.status in {'expired','invalidated','consumed'} else 'failed',action=result.action)
            if self._query is not None and _is_query(message,snapshot.memory):
                result,memory=self._query().query_with_context(message,snapshot.memory)
                validate_query_result(result)
                event_id=result.data['event']['event_id'] if result.status=='succeeded' and result.action=='get' else None
                if event_id is not None and event_id in message and not _event_id_literal(event_id,message):
                    self._save(cid,snapshot,replace(memory,confirmation=None,selected_event_id=None,selected_time_zone=None))
                    return self._turn('Informe o identificador completo e exato do compromisso, ou refine o título e a data.',cid,status='needs_clarification',action='get',mutation='not_applicable')
                self._save(cid,snapshot,memory)
                return self._turn(_query_text(result),cid,status=result.status,action=result.action,mutation='not_applicable')
            previous=snapshot.memory.pending_request
            new_intent=_new_intent(message)
            input_memory=(replace(snapshot.memory,pending_request=None,confirmation=None)
                          if new_intent else snapshot.memory)
            result,memory=self._dialogue().respond(message,input_memory)
            result=validate_dialogue_result(result)
            memory=validate_memory(memory)
            if result.status=='failed':
                return self._turn('Não consegui preparar esse pedido com segurança. Confira os dados e tente novamente.',cid,status='failed',action=result.action)
            memory=replace(memory,confirmation=None)
            unstated_end=_unstated_new_end(message,input_memory.pending_request,memory.pending_request)
            if unstated_end:
                pending=memory.pending_request
                fields={name:value for name,value in pending.fields.items() if name!='end'}
                waiting=tuple(dict.fromkeys((*pending.awaiting,'end')))
                memory=replace(memory,pending_request=replace(pending,fields=fields,awaiting=waiting))
            event_id=memory.pending_request.fields.get('event_id') if memory.pending_request is not None else None
            if event_id is not None and event_id in message and not _event_id_literal(event_id,message):
                self._save(cid,snapshot,replace(memory,pending_request=None,selected_event_id=None,selected_time_zone=None))
                return self._turn('Informe o identificador completo e exato do compromisso, ou refine o título e a data.',cid,status='needs_clarification',action=result.action)
            snapshot=self._save(cid,snapshot,memory)
            action=result.action
            explicit=explicit_action(message,action)
            continuing=(authorization==action and previous is not None
                and bool(previous.awaiting or previous.candidates) and previous.fields.get('action')==action
                and not new_intent and not _question(message))
            try:
                grounded=_grounded_fields(action,message,
                    previous if continuing else None,memory.pending_request,(result.data or {}).get('event'),
                    snapshot.memory.selected_event_id)
            except (ValueError,TypeError,KeyError,OverflowError):
                grounded=False
            authorization=action if action in _ACTIONS and (explicit or continuing) and grounded else None
            if unstated_end:
                return self._turn('Qual é a data e o horário de término ou a duração do compromisso?',cid,authorization,status='needs_clarification',action=action)
            if result.status!='ready':
                if result.status=='cancelled' or (result.data or {}).get('candidates'):
                    # A escolha será seguida de prévia; rótulos podem ser abreviados.
                    authorization=None
                return self._turn(_question_text(result),cid,authorization,status='cancelled' if (result.data or {}).get('reason')=='cancelled' else result.status,action=action)
            if action=='list':
                params=result.data['parameters']
                if self._gateway is None:
                    return self._turn('A consulta da agenda está indisponível agora.',cid,status='failed',action='list')
                queried=AgendaReadTools(self._gateway).execute(QueryPlan('list_events',dict(params),'whatsapp-agent-local'))
                validate_query_result(queried)
                next_memory=replace(snapshot.memory,last_list=dict(params),
                    selected_event_id=queried.data['events'][0]['event_id'] if queried.status=='succeeded' and len(queried.data['events'])==1 else None,
                    selected_time_zone=params['time_zone'] if queried.status=='succeeded' and len(queried.data['events'])==1 else None)
                if queried.status=='succeeded':
                    self._save(cid,snapshot,next_memory)
                return self._turn(_query_text(queried),cid,status=queried.status,action=queried.action,mutation='not_applicable')
            if action in {'delete','reschedule'} or authorization!=action:
                return self._preview(cid,snapshot,action)
            return self._execute(cid,action)
        except ModelError:
            return self._turn('A IA está indisponível neste momento. Não enviei uma nova alteração à agenda. Tente novamente quando a conexão estiver disponível.',cid,status='failed')
        except ConfirmationError:
            return self._turn('A aprovação não corresponde ao pedido e ao estado atuais. Confira os dados e prepare uma nova prévia antes de confirmar.',cid,status='failed')
