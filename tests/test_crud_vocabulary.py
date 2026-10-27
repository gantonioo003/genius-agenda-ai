"""Sinônimos reais de agenda preservam seleção, prévia e consentimento locais."""
import re
import unittest

from app.action_language import (ACTION_VERBS, normalize_action_text,
    starts_new_mutation, has_multiple_mutations)
from app.services.whatsapp_agent import explicit_action, _new_intent
import test_whatsapp_agent as fixtures
from test_agent_updates import OWNER


class CrudVocabularyTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.WhatsAppAgentTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_common_create_infinitives_conjugations_and_polite_forms(self):
        for verb in ('agende', 'agenda', 'agendar', 'marque', 'marca', 'marcar',
                     'crie', 'cria', 'criar', 'cadastre', 'cadastra', 'cadastrar',
                     'adicione', 'adiciona', 'adicionar', 'inclua', 'inclui', 'incluir',
                     'insira', 'insere', 'inserir', 'coloque', 'coloca', 'colocar',
                     'anote', 'anota', 'anotar', 'reserve', 'registre', 'programe',
                     'bota', 'ponha', 'põe', 'adcionar'):
            for prefix in ('', 'Por favor, ', 'Quero ', 'Gostaria de ', 'Preciso ', 'pfv ',
                           'Me ', 'Quero, por favor, '):
                with self.subTest(verb=verb, prefix=prefix):
                    message = prefix + verb + ' Entrevista fictícia'
                    self.assertTrue(explicit_action(message, 'create'))
                    self.assertTrue(_new_intent(message))

    def test_common_edit_forms_and_contextual_change(self):
        for verb in ('edite', 'edita', 'editar', 'altere', 'altera', 'alterar',
                     'atualize', 'atualiza', 'atualizar', 'modifique', 'modifica',
                     'modificar', 'corrija', 'corrige', 'corrigir', 'renomeie',
                     'renomeia', 'renomear', 'mude', 'muda', 'mudar', 'troque',
                     'troca', 'trocar', 'ajuste', 'ajusta', 'ajustar', 'atualisar'):
            with self.subTest(verb=verb):
                message = 'Quero ' + verb + ' o título dessa entrevista para Novo nome'
                self.assertTrue(explicit_action(message, 'edit'))
                self.assertFalse(explicit_action(message, 'delete'))
                self.assertTrue(_new_intent(message))

    def test_common_reschedule_forms_are_recognized(self):
        for verb in ('reagende', 'reagenda', 'reagendar', 'remarque', 'remarca',
                     'remarcar', 'mova', 'move', 'mover', 'mude', 'muda', 'mudar',
                     'troque', 'troca', 'trocar', 'transfira', 'transfere',
                     'transferir', 'adie', 'adia', 'adiar', 'antecipe', 'antecipa',
                     'antecipar', 'postergue', 'posterga', 'postergar', 'reajendar'):
            with self.subTest(verb=verb):
                message = verb + ' essa entrevista para amanhã às 15h'
                self.assertTrue(explicit_action(message, 'reschedule'))
                self.assertTrue(_new_intent(message))

    def test_deletion_forms_start_a_new_proposal_without_autoauthorization(self):
        for verb in ('exclua', 'exclui', 'excluir', 'exclue', 'escluir', 'apague',
                     'apaga', 'apagar', 'remova', 'remove', 'remover', 'cancele',
                     'cancela', 'cancelar', 'delete', 'deleta', 'deletar', 'retire',
                     'retira', 'retirar', 'elimine', 'elimina', 'eliminar',
                     'desmarque', 'desmarca', 'desmarcar', 'tire', 'tira', 'tirar'):
            with self.subTest(verb=verb):
                message = 'Por favor, ' + verb + ' essa entrevista'
                self.assertTrue(_new_intent(message))
                self.assertFalse(explicit_action(message, 'delete'))
                self.assertFalse(explicit_action(message, 'create'))
                self.assertFalse(explicit_action(message, 'edit'))

    def test_nominal_actions_are_planning_without_direct_execution(self):
        for text in ('Criação de entrevista', 'Quero o agendamento de entrevista',
                     'Faça a inclusão da entrevista', 'Edição da entrevista',
                     'Quero a alteração da entrevista', 'Troca da entrevista',
                     'Ajuste do título da entrevista', 'Mudança do horário',
                     'Reagendamento da entrevista', 'Quero a remarcação da entrevista',
                     'Exclusão da entrevista', 'Quero a exclusão da entrevista',
                     'Preciso da remoção da entrevista', 'Remoção da entrevista',
                     'Cancelamento da entrevista'):
            with self.subTest(text=text):
                self.assertTrue(_new_intent(text))
                for action in ('create', 'edit', 'reschedule', 'delete'):
                    self.assertFalse(explicit_action(text, action))

    def test_questions_negations_and_hypotheses_never_authorize_synonyms(self):
        for message in ('Pode adicionar uma entrevista', 'Você consegue criar uma entrevista',
                        'Por favor, poderia cadastrar uma entrevista',
                        'Como posso incluir uma entrevista?', 'Não adiciona uma entrevista',
                        'Nunca coloca uma entrevista', 'E se incluir uma entrevista',
                        'Caso adicione uma entrevista', 'Talvez criar uma entrevista',
                        '"Adicione uma entrevista"', 'Adicione uma entrevista, mas não execute',
                        'Adicione uma entrevista apenas como exemplo',
                        'Adicione uma entrevista, aguarde minha confirmação',
                        'Adicione uma entrevista, n grave', 'Adicione uma entrevista, naum execute',
                        'Adicione uma entrevista, será que daria certo'):
            with self.subTest(message=message):
                self.assertFalse(explicit_action(message, 'create'))
        for message in ('Como posso excluir a entrevista?', 'Não mudar a entrevista',
                        'E se apagar a entrevista', 'Pode remover a entrevista',
                        'Quero a exclusão da entrevista', 'Poderia, por favor, excluir a entrevista',
                        'Me exclui a entrevista', 'n apagar a entrevista'):
            with self.subTest(fresh=message):
                self.assertTrue(_new_intent(message))

    def test_vocabulary_is_explicit_and_preserves_accent_folding(self):
        self.assertEqual(normalize_action_text('  EXCLUSÃO  da Reunião '), 'exclusao da reuniao')
        for word in ('agendinha', 'adicionaria', 'cadastrei', 'agendei',
                     'apagável', 'removível', 'mudarzinho', 'excluímos'):
            with self.subTest(word=word):
                self.assertFalse(starts_new_mutation(word + ' entrevista'))
                for action, verbs in ACTION_VERBS.items():
                    self.assertIsNone(re.fullmatch(verbs, normalize_action_text(word)))
                    self.assertFalse(explicit_action(word + ' entrevista', action))

    def test_multi_intent_covers_synonyms_and_nominal_requests(self):
        for message in ('Adiciona uma entrevista e apaga a outra',
                        'Mudar o título e excluir a outra entrevista',
                        'Quero a criação de entrevista e a exclusão da outra',
                        'Remoção da entrevista e reagendamento da reunião',
                        'Inclui uma entrevista; depois desmarca a reunião'):
            with self.subTest(message=message):
                self.assertTrue(has_multiple_mutations(message))
                case = self.fixture()
                case.model.plan = case.create_plan()
                result = case.turn(message)
                self.assertEqual(result.status, 'needs_clarification')
                self.assertIsNone(result.authorization_action)
                self.assertFalse(case.model.calls)
                self.assertFalse(case.writes())

    def test_complete_colloquial_create_uses_grounded_executor(self):
        case = self.fixture()
        case.model.plan = case.create_plan()
        result = case.turn('Adiciona Entrevista fictícia em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.mutation_state, 'confirmed')
        self.assertEqual(len(case.writes()), 1)

    def test_nouns_inside_a_single_request_are_not_extra_mutations(self):
        for message in ('Crie uma reserva na agenda', 'Adicione uma entrevista na minha agenda',
                        'Agende Troca de óleo', 'Quero criar um novo cadastro',
                        'Crie uma entrevista, sem gravar na agenda'):
            with self.subTest(message=message):
                self.assertFalse(has_multiple_mutations(message))

    def test_nominal_create_and_polite_question_require_preview(self):
        for prefix in ('Criação de ', 'Quero o agendamento de ', 'Pode adicionar '):
            with self.subTest(prefix=prefix):
                case = self.fixture()
                case.model.plan = case.create_plan()
                result = case.turn(prefix + 'Entrevista fictícia em 17/06/2030 das 15h às 15h30')
                self.assertEqual(result.status, 'needs_confirmation')
                self.assertIsNone(result.authorization_action)
                self.assertFalse(case.writes())

    def test_ambiguous_change_cannot_silently_become_a_title_edit(self):
        case = self.fixture()
        case.model.plan = fixtures.plan(action='edit', event_id=fixtures.EID, title='18h')
        result = case.turn('Mude ' + fixtures.EID + ' para 18h')
        self.assertEqual(result.status, 'needs_confirmation')
        self.assertIsNone(result.authorization_action)
        self.assertFalse(case.writes())

    def test_synonym_delete_and_reschedule_always_preview_even_with_full_fields(self):
        for action, prefix in (('delete', 'Apaga '), ('delete', 'Excluir '),
                               ('delete', 'Exclusão de '), ('delete', 'Remoção de '),
                               ('reschedule', 'Mudar '), ('reschedule', 'Adiar ')):
            with self.subTest(action=action, prefix=prefix):
                case = self.fixture()
                extra = {} if action == 'delete' else {'start': fixtures.START, 'end': fixtures.END}
                case.model.plan = fixtures.plan(action=action, event_id=fixtures.EID, **extra)
                message = prefix + fixtures.EID
                if action == 'reschedule':
                    message += ' para 17/06/2030 das 15h às 15h30'
                result = case.turn(message)
                self.assertEqual(result.status, 'needs_confirmation')
                self.assertFalse(case.writes())

    def test_new_nominal_or_synonym_cannot_complete_an_old_authorized_create(self):
        for message in ('Exclusão da entrevista às 15h30', 'Remoção da entrevista às 15h30',
                        'Quero apagar a entrevista às 15h30', 'Mudar a entrevista às 15h30'):
            with self.subTest(message=message):
                case = self.fixture()
                case.model.plan = fixtures.plan(action='create', title=fixtures.TITLE, start=fixtures.START)
                first = case.turn('Adiciona Entrevista fictícia em 17/06/2030 às 15h')
                self.assertEqual(first.authorization_action, 'create')
                case.model.plan = fixtures.plan(mode='continue', action='create', end=fixtures.END)
                result = case.turn(message, first)
                self.assertIsNone(result.authorization_action)
                self.assertNotEqual(result.mutation_state, 'confirmed')
                memory = case.context.load(OWNER, result.conversation_id).memory
                if memory.pending_request is not None:
                    self.assertNotIn('title', memory.pending_request.fields)
                self.assertFalse(case.writes())

    def test_informal_negation_in_a_continuation_revokes_write_authorization(self):
        for negation in ('n grave', 'naum execute', 'ñ confirme'):
            with self.subTest(negation=negation):
                case = self.fixture()
                case.model.plan = fixtures.plan(action='create', title=fixtures.TITLE, start=fixtures.START)
                first = case.turn('Adiciona Entrevista fictícia em 17/06/2030 às 15h')
                self.assertEqual(first.authorization_action, 'create')
                case.model.plan = fixtures.plan(mode='continue', action='create', end=fixtures.END)
                result = case.turn('Termina às 15h30, mas ' + negation, first)
                self.assertEqual(result.status, 'needs_confirmation')
                self.assertIsNone(result.authorization_action)
                self.assertFalse(case.writes())


if __name__ == '__main__':
    unittest.main()
