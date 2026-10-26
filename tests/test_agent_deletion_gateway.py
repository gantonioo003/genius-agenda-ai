"""SDK Google com transporte fictício: exclusão só da agenda e versão vinculadas."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app import google_calendar
from app.database import Database
from app.google_transport import SingleAttemptHttp
from app.services.event_deletion import (
    DeletionBindingError, DeletionVersionError, deletion_confirmation,
)
from app.services.interviews import CalendarOperationError
from calendar_http_fake import CalendarHttpFake

ZONE='America/Recife'
EVENT_ID='OpaqueOccurrence_20300617T130000Z'
OTHER_ID='DifferentApprovedEvent'
CALENDAR='fixture-deletion-calendar@example.invalid'
VERSION='"v1"'


class AgentDeletionGatewayTests(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database=Database(Path(temporary.name)/'fixture.sqlite3')
        database.initialize()
        self.backend=CalendarHttpFake()
        self.backend.calendar['id']=CALENDAR
        self.backend.saved[EVENT_ID]={
            'id':EVENT_ID,'summary':'Entrevista aprovada','status':'confirmed','etag':VERSION,
            'description':'Descrição privada','location':'Local privado',
            'start':{'dateTime':'2030-06-17T10:00:00-03:00','timeZone':ZONE},
            'end':{'dateTime':'2030-06-17T10:30:00-03:00','timeZone':ZONE},
            'htmlLink':'https://example.invalid/approved','transparency':'opaque',
        }
        self.original=deepcopy(self.backend.saved[EVENT_ID])
        self.backend.saved[OTHER_ID]={**deepcopy(self.original),'id':OTHER_ID,'summary':'Outra entrevista'}
        self.other=deepcopy(self.backend.saved[OTHER_ID])
        self.sessions=[]
        def session():
            value=self.backend.session()
            self.sessions.append(value)
            return value
        self.addCleanup(patch.stopall)
        patch('app.google_transport.requests.Session',side_effect=session).start()
        patch('app.google_calendar.database',database).start()
        self.resolver=patch('app.google_calendar.resolve_calendar',return_value=self.backend.calendar).start()
        patch('app.google_calendar.get_credentials',side_effect=AssertionError('No OAuth allowed')).start()
        patch('googleapiclient.http.time.sleep').start()
        self.sdk=build('calendar','v3',http=AuthorizedHttp(AnonymousCredentials(),
            http=SingleAttemptHttp(),max_refresh_attempts=0),cache_discovery=False,static_discovery=True)
        self.gateway=google_calendar.GoogleCalendarGateway(self.sdk)
        self.addCleanup(self.assert_closed_and_other_preserved)
        self.addCleanup(self.sdk.close)
    def assert_closed_and_other_preserved(self):
        self.assertTrue(all(session.closed for session in self.sessions))
        self.assertEqual(self.backend.saved[OTHER_ID],self.other)
    def writes(self):
        return [call for call in self.backend.calls if call['method']!='GET']
    def reads(self):
        return [call for call in self.backend.calls if call['method']=='GET']
    def execute(self,**guards):
        return self.gateway.delete_event(EVENT_ID,time_zone=ZONE,execute=True,
            confirmation_token=guards.get('token',deletion_confirmation(CALENDAR,EVENT_ID,VERSION)),
            expected_calendar_id=guards.get('calendar',CALENDAR),expected_version=guards.get('version',VERSION))

    def test_exact_bound_target_deleted_once_if_match_and_absence_checked(self):
        result=self.execute()
        self.assertEqual(result.status,'deleted')
        self.assertEqual(result.event.event_id,EVENT_ID)
        self.assertIsNone(result.confirmation_token)
        self.assertIsNone(result.question)
        self.assertFalse(result.recovered)
        self.assertNotIn(EVENT_ID,self.backend.saved)
        self.assertEqual(len(self.writes()),1)
        write=self.writes()[0]
        self.assertEqual(write['method'],'DELETE')
        self.assertEqual(write['event_id'],EVENT_ID)
        self.assertEqual(write['headers']['If-Match'],VERSION)
        self.assertEqual(write['query']['sendUpdates'],'none')
        self.assertEqual([call['method'] for call in self.backend.calls],['GET','DELETE','GET'])
        self.assertEqual(self.reads()[-1]['status'],404)

    def test_wrong_calendar_guard_precedes_even_event_read(self):
        with self.assertRaises(DeletionBindingError):
            self.execute(calendar='other@example.invalid')
        self.assertEqual(self.backend.calls,[])
        self.assertEqual(self.backend.saved[EVENT_ID],self.original)

    def test_stale_version_guard_does_not_delete_or_generate_another_preview(self):
        with self.assertRaises(DeletionVersionError):
            self.execute(version='"old"')
        self.assertEqual(self.writes(),[])
        self.assertEqual(len(self.reads()),1)
        self.assertEqual(self.backend.saved[EVENT_ID],self.original)

    def test_invalid_guards_precede_resolution_and_provider_calls(self):
        self.resolver.reset_mock()
        for field,values in (('calendar',('', ' ', 'two words', True, 'x'*1025,'private\n','\ud800')),
                             ('version',('', ' ', True, 'x'*2049,'private\r\n','\ud800'))):
            for value in values:
                with self.subTest(field=field,kind=type(value).__name__),self.assertRaises(ValueError):
                    self.execute(**{field:value})
        self.resolver.assert_not_called()
        self.assertEqual(self.backend.calls,[])

    def test_opaque_weak_etag_is_forwarded_without_normalization(self):
        self.backend.saved[EVENT_ID]['etag']='W/"opaque version 1"'
        version=self.backend.saved[EVENT_ID]['etag']
        result=self.execute(version=version,token=deletion_confirmation(CALENDAR,EVENT_ID,version))
        self.assertEqual(result.status,'deleted')
        self.assertEqual(self.writes()[0]['headers']['If-Match'],version)

    def test_already_absent_or_cancelled_does_not_delete_with_bound_version(self):
        for cancelled in (False,True):
            if cancelled:
                self.backend.saved[EVENT_ID]={'id':EVENT_ID,'status':'cancelled'}
            else:
                self.backend.saved.pop(EVENT_ID)
            self.backend.calls.clear()
            result=self.execute()
            self.assertEqual(result.status,'already_absent')
            self.assertIsNone(result.event)
            self.assertFalse(result.recovered)
            self.assertEqual(self.writes(),[])
            self.assertEqual(len(self.reads()),1)

    def test_confirmation_read_confirms_absence_without_any_delete(self):
        self.backend.saved.pop(EVENT_ID)
        snapshot=self.gateway.get_confirmation_snapshot(EVENT_ID,time_zone=ZONE)
        self.assertIsNone(snapshot.event)
        self.assertIsNone(snapshot.version)
        self.assertEqual(snapshot.calendar_id,CALENDAR)
        self.assertEqual(self.writes(),[])

    def test_preview_and_confirmation_normalize_empty_or_whitespace_titles_only_for_display(self):
        for title in ('','   '):
            self.backend.saved[EVENT_ID]={**deepcopy(self.original),'summary':title}
            self.backend.calls.clear()
            snapshot=self.gateway.get_confirmation_snapshot(EVENT_ID,time_zone=ZONE)
            preview=self.gateway.delete_event(EVENT_ID,time_zone=ZONE)
            self.assertEqual(snapshot.event.title,'(sem título)')
            self.assertEqual(preview.event.title,'(sem título)')
            self.assertIn('(sem título)',preview.question)
            self.assertEqual(self.backend.saved[EVENT_ID]['summary'],title)
            self.assertEqual(self.writes(),[])

    def test_if_match_race_bound_412_is_known_version_failure_without_retry(self):
        def change(_method,eid,body):
            self.backend.saved[eid]['etag']='"v99"'
            self.backend.saved[eid]['summary']='Mudança externa'
        self.backend.before_write=change
        with self.assertRaises(DeletionVersionError):
            self.execute()
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.writes()[0]['status'],412)
        self.assertEqual(len(self.reads()),1)
        self.assertEqual(self.backend.saved[EVENT_ID]['summary'],'Mudança externa')

    def test_unbound_412_keeps_original_calendar_operation_error_contract(self):
        def change(_method,eid,body): self.backend.saved[eid]['etag']='"v99"'
        self.backend.before_write=change
        with self.assertRaises(CalendarOperationError) as error:
            self.gateway.delete_event(EVENT_ID,time_zone=ZONE,execute=True,
                confirmation_token=deletion_confirmation(CALENDAR,EVENT_ID,VERSION))
        self.assertIs(type(error.exception),CalendarOperationError)
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.writes()[0]['status'],412)

    def test_lost_response_is_confirmed_by_get_without_second_delete(self):
        self.backend.lose_response_once.add('DELETE')
        result=self.execute()
        self.assertEqual(result.status,'deleted')
        self.assertTrue(result.recovered)
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.reads()[-1]['status'],404)
        self.assertNotIn(EVENT_ID,self.backend.saved)

    def test_failed_postread_remains_uncertain_and_readonly_recovery_observes_absence(self):
        self.backend.fail_reads_after_write=3
        with self.assertRaises(CalendarOperationError):
            self.execute()
        self.assertEqual(len(self.writes()),1)
        self.assertNotIn(EVENT_ID,self.backend.saved)
        snapshot=self.gateway.get_confirmation_snapshot(EVENT_ID,time_zone=ZONE)
        self.assertIsNone(snapshot.event)
        self.assertEqual(len(self.writes()),1)

    def test_postdelete_readback_412_is_uncertain_not_known_version_refusal(self):
        request=self.backend.request
        def readback_failure(method,url,**kwargs):
            response=request(method,url,**kwargs)
            if method.upper()=='GET' and self.backend._has_written:
                response.status_code=412
                response.reason='Precondition Failed'
                response.content=b'{"error":{"code":412,"message":"Fixture readback refused"}}'
                self.backend.calls[-1]['status']=412
            return response
        with patch.object(self.backend,'request',side_effect=readback_failure):
            with self.assertRaises(CalendarOperationError) as error:
                self.execute()
        self.assertNotIsInstance(error.exception,DeletionVersionError)
        self.assertNotIn(EVENT_ID,self.backend.saved)
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.writes()[0]['status'],204)
        self.assertEqual(self.reads()[-1]['status'],412)

    def test_active_postread_prevents_false_success_and_never_redeletes(self):
        self.backend.read_override=lambda eid: deepcopy(self.original) if self.backend._has_written and eid==EVENT_ID else None
        with self.assertRaises(CalendarOperationError):
            self.execute()
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.reads()[-1]['status'],200)

    def test_delete_404_race_requires_matching_absence_and_preserves_other_event(self):
        self.backend.before_write=lambda _method,eid,body:self.backend.saved.pop(eid)
        result=self.execute()
        self.assertEqual(result.status,'already_absent')
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.writes()[0]['status'],404)
        self.assertEqual(self.reads()[-1]['status'],404)

    def test_invalid_provider_snapshot_and_cancelled_wrong_id_never_delete(self):
        for changes in ({'id':'WrongID'},{'id':'WrongID','status':'cancelled'},{'etag':None},
            {'etag':'private\n'},{'status':'unknown'},{'recurrence':['RRULE:FREQ=DAILY']},
            {'eventType':'birthday'},{'start':{'dateTime':'not-a-date'}}):
            self.backend.saved[EVENT_ID]={**deepcopy(self.original),**changes}
            self.backend.calls.clear()
            with self.subTest(changes=tuple(changes)),self.assertRaises(CalendarOperationError):
                self.execute()
            self.assertEqual(self.writes(),[])

    def test_preflight_transport_failure_is_not_absence_and_hides_diagnostic(self):
        with patch.object(self.sdk,'events',side_effect=OSError('PRIVATE_DELETION_DIAGNOSTIC')):
            with self.assertRaises(CalendarOperationError) as error:
                self.execute()
        self.assertNotIn('PRIVATE_DELETION_DIAGNOSTIC',str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertEqual(self.writes(),[])

    def test_invalid_or_other_target_confirmation_never_deletes(self):
        for value in ('', 'A'*64, '0'*64, deletion_confirmation(CALENDAR,OTHER_ID,VERSION)):
            self.backend.calls.clear()
            with self.subTest(kind=len(value)),self.assertRaises((ValueError,CalendarOperationError)):
                self.execute(token=value)
            self.assertEqual(self.writes(),[])

    def test_all_day_specific_event_can_be_deleted_with_exact_guards(self):
        self.backend.saved[EVENT_ID]['start']={'date':'2030-06-17'}
        self.backend.saved[EVENT_ID]['end']={'date':'2030-06-18'}
        result=self.execute()
        self.assertEqual(result.status,'deleted')
        self.assertTrue(result.event.all_day)
        self.assertEqual(result.event.start,'2030-06-17')
        self.assertEqual(len(self.writes()),1)

    def test_legacy_unbound_calls_and_repeat_remain_compatible(self):
        result=self.gateway.delete_event(EVENT_ID,time_zone=ZONE,execute=True,
            confirmation_token=deletion_confirmation(CALENDAR,EVENT_ID,VERSION))
        self.assertEqual(result.status,'deleted')
        again=self.gateway.delete_event(EVENT_ID,time_zone=ZONE,execute=True,
            confirmation_token=deletion_confirmation(CALENDAR,EVENT_ID,VERSION))
        self.assertEqual(again.status,'already_absent')
        self.assertEqual(len(self.writes()),1)


if __name__=='__main__': unittest.main()
