"""Ciclo real TCP/subprocesso de lembretes com provedores exclusivamente fictícios."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from uuid import uuid4

from reminder_http_worker import FIXTURE_BINDING, FIXTURE_CALENDAR, FIXTURE_PHONE, FIXTURE_TOKEN


PROJECT = Path(__file__).resolve().parents[1]
REPORT_ROOT = PROJECT.parent
OPERATIONS = {"recover": "reminder_recovery", "tick": "reminder_tick", "dispatch": "reminder_dispatch"}


class RemindersSystemHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="system-http-", dir=PROJECT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.process, self.log, self.port = None, None, None
        self.starts, self.cycle_sequence = 0, 0
        self.cycles = []
        self.addCleanup(self.stop)
        self.start()

    @staticmethod
    def unused_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def start(self):
        self.assertIsNone(self.process)
        self.starts += 1
        self.port = self.unused_port()
        self.instance = str(uuid4())
        self.log_path = self.root / f"worker-{self.starts}.log"
        self.log = self.log_path.open("wb")
        # Windows venv python.exe is a redirector and may own a second process.
        # Execute the real interpreter directly with the same installed packages.
        executable = getattr(sys, "_base_executable", sys.executable)
        spec = importlib.util.find_spec("uvicorn")
        self.assertIsNotNone(spec)
        packages = str(Path(spec.origin).resolve().parents[1])
        allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "NUMBER_OF_PROCESSORS",
                   "PROCESSOR_ARCHITECTURE", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        environment.update(TEMP=str(self.root), TMP=str(self.root), PYTHONDONTWRITEBYTECODE="1",
                           PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join((str(PROJECT), packages)))
        self.process = subprocess.Popen([executable, "-B", str(PROJECT / "tests/reminder_http_worker.py"),
            "--root", str(self.root), "--port", str(self.port), "--instance", self.instance],
            cwd=PROJECT, env=environment, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail("Worker de fixture encerrou: " + self.log_path.read_text(encoding="utf-8", errors="replace")[-2500:])
            try:
                code, state = self.http("/__fixture/state", timeout=0.3)
                if code == 200 and state.get("instance") == self.instance:
                    self.assertEqual(state["pid"], self.process.pid,
                                     "O listener deve ser o próprio processo iniciado, sem redirector órfão.")
                    self.assertEqual(state["external_attempts"], 0)
                    return
            except (OSError, http.client.HTTPException, ValueError):
                pass
            time.sleep(0.04)
        self.fail("Worker de fixture não ficou pronto dentro do limite.")

    def stop(self, *, kill=False):
        process, port = self.process, self.port
        if process is not None:
            try:
                if process.poll() is None:
                    (process.kill if kill else process.terminate)()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            finally:
                self.process = None
                if self.log is not None:
                    self.log.close()
                    self.log = None
            # Prove the owned listener is gone before restart/temporary cleanup.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        pass
                except OSError:
                    return
                time.sleep(0.04)
            self.fail("A porta do worker de fixture continuou aberta após encerrar seu PID.")

    def http(self, path, *, method="GET", body=None, timeout=4, authenticated=True, request_id="fixture-control"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        headers = {"X-Request-ID": request_id}
        if authenticated:
            headers["Authorization"] = "Bearer " + FIXTURE_TOKEN
        if body is not None:
            headers["Content-Type"] = "application/json"
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8") if body is not None else None
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def state(self):
        code, state = self.http("/__fixture/state")
        self.assertEqual(code, 200)
        self.assertEqual(state["instance"], self.instance)
        self.assertEqual(state["pid"], self.process.pid)
        self.assertEqual(state["external_attempts"], 0)
        return state

    def control(self, **values):
        code, body = self.http("/__fixture/control", method="POST", body=values)
        self.assertEqual((code, body), (200, {"status": "fixture_control_applied"}))

    def wait_state(self, predicate, *, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = self.state()
            if predicate(state):
                return state
            time.sleep(0.03)
        self.fail("Checkpoint do processo de fixture não foi observado no prazo.")

    def operation(self, name, *, request_id="reminder-tick-interrupted", timeout=4):
        code, body = self.http("/api/v1/reminders/" + name, method="POST", body={},
                               request_id=request_id, timeout=timeout)
        serialized = json.dumps(body)
        for private in (FIXTURE_TOKEN, FIXTURE_PHONE, FIXTURE_BINDING, FIXTURE_CALENDAR,
                        "Fixture appointment", "fixture-event", str(self.root)):
            self.assertNotIn(private, serialized)
        self.assertEqual(body["request_id"], request_id)
        self.assertEqual(body["action"], OPERATIONS[name])
        self.assertEqual(body["api_version"], "1")
        return code, body

    def cycle(self):
        self.cycle_sequence += 1
        execution_id = f"system-{self.cycle_sequence}"
        request_id = "reminder-tick-" + execution_id
        responses = []
        for operation in OPERATIONS:
            code, body = self.operation(operation, request_id=request_id)
            self.assertEqual(code, 200)
            self.assertIn(body["status"], {"succeeded", "skipped"})
            self.assertIsNone(body["error"])
            responses.append({"operation": operation, "http_status": code, "body": body})
        self.cycles.append({"execution_id": execution_id, "request_id": request_id, "responses": responses})
        return {response["operation"]: response["body"]["data"] for response in responses}

    def prepare(self):
        for operation in ("recover", "tick"):
            code, body = self.operation(operation)
            self.assertEqual(code, 200)
            self.assertEqual(body["status"], "succeeded")

    def report(self, *checks):
        state = self.state()
        self.assertEqual(state["external_attempts"], 0)
        target = os.environ.get("REMINDER_SYSTEM_REPORT_PATH")
        if not target:
            return
        path = Path(target).resolve()
        if not path.is_relative_to(REPORT_ROOT) or path == REPORT_ROOT:
            raise AssertionError("O relatório precisa ficar dentro do staging de testes do sistema.")
        document = (json.loads(path.read_text(encoding="utf-8")) if path.exists() else
                    {"format": "reminder-system-http-v1", "protocol": "real-loopback-tcp", "scenarios": []})
        self.assertEqual(document["format"], "reminder-system-http-v1")
        document["scenarios"].append({"scenario": self._testMethodName,
            "checks": list(checks), "process_restarts": self.starts - 1, "listener_pid_verified": True,
            "sender_accepts_count": state["sender_accepts_count"], "external_attempts": state["external_attempts"],
            "ledger_state_counts": dict(Counter(record["state"] for record in state["records"])),
            "calendar_snapshots": state["calendar_snapshots"], "calendar_lookups": state["calendar_lookups"],
            "cycles": self.cycles})
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def crash_at(self, checkpoint, *, accepted_before, recovered=None, uncertain=None):
        self.prepare()
        self.control(checkpoint=checkpoint)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.operation, "dispatch", timeout=10)
            before = self.wait_state(lambda state: state["checkpoint"]["reached"] == checkpoint)
            self.assertEqual(before["sender_accepts_count"], accepted_before)
            self.assertEqual(Counter(record["state"] for record in before["records"]),
                             {"reserved" if checkpoint == "after_reserve" else "sending": 1})
            code, busy = self.operation("recover")
            self.assertEqual(code, 200)
            self.assertEqual(busy["data"]["skip_reason"], "busy")
            self.stop(kill=True)
            with self.assertRaises((OSError, http.client.HTTPException)):
                pending.result(timeout=5)
        self.start()
        result = self.cycle()
        if recovered is not None:
            self.assertEqual(result["recover"]["reservations_recovered"], recovered)
        if uncertain is not None:
            self.assertEqual(result["recover"]["uncertain_count"], uncertain)
        return before, result

    def test_normal_repeat_and_process_restart_keep_one_provider_accept(self):
        # Control/auth parsing is also real HTTP and cannot mutate fixtures unauthenticated.
        self.assertEqual(self.http("/__fixture/control", method="POST", body=b"{invalid", authenticated=False)[0], 401)
        self.assertEqual(self.http("/api/v1/reminders/dispatch", method="POST", body=b"{invalid", authenticated=False)[0], 401)
        first = self.cycle()
        self.assertEqual(first["dispatch"]["accepted_count"], 1)
        self.assertEqual(self.cycle()["dispatch"]["transport_attempts"], 0)
        self.stop()
        self.start()
        self.assertEqual(self.cycle()["dispatch"]["transport_attempts"], 0)
        state = self.state()
        self.assertEqual(state["generation"], 2)
        self.assertEqual(state["sender_accepts_count"], 1)
        self.assertEqual(Counter(record["state"] for record in state["records"]), {"accepted": 1})
        self.report("authenticated_fixture_controls", "real_http_cycle", "repeat_deduplicated", "accepted_survives_process_restart")

    def test_crash_after_reservation_recovers_then_revalidates_before_one_accept(self):
        before, result = self.crash_at("after_reserve", accepted_before=0, recovered=1)
        self.assertEqual(result["dispatch"]["accepted_count"], 1)
        state = self.state()
        self.assertGreater(state["calendar_lookups"], before["calendar_lookups"])
        self.assertEqual(self.cycle()["dispatch"]["transport_attempts"], 0)
        self.assertEqual(self.state()["sender_accepts_count"], 1)
        self.report("crash_reserved_before_transport", "os_lock_released_by_process_death", "safe_reservation_recovered", "event_revalidated_before_accept")

    def test_crash_after_sending_state_is_uncertain_even_before_provider_call(self):
        _before, result = self.crash_at("after_sending", accepted_before=0, uncertain=1)
        self.assertEqual(result["dispatch"]["transport_attempts"], 0)
        self.assertEqual(self.state()["sender_accepts_count"], 0)
        self.assertEqual(Counter(record["state"] for record in self.state()["records"]), {"uncertain": 1})
        self.report("crash_sending_before_provider", "potential_send_conservatively_uncertain", "no_automatic_resend")

    def test_crash_after_provider_accept_before_ledger_accept_does_not_resend(self):
        _before, result = self.crash_at("after_provider_accept", accepted_before=1, uncertain=1)
        self.assertEqual(result["dispatch"]["transport_attempts"], 0)
        self.assertEqual(self.state()["sender_accepts_count"], 1)
        self.assertEqual(Counter(record["state"] for record in self.state()["records"]), {"uncertain": 1})
        self.report("crash_between_provider_accept_and_ledger_commit", "durable_fixture_provider_receipt", "uncertain_preserved_without_resend")

    def test_reschedule_and_cancel_prevent_old_pending_reminders_from_sending(self):
        self.control(events=[{"event_id": "fixture-event-a", "minutes_until_start": 15, "status": "confirmed"},
                             {"event_id": "fixture-event-b", "minutes_until_start": 15, "status": "confirmed"}])
        self.prepare()
        self.control(events=[{"event_id": "fixture-event-a", "minutes_until_start": 30, "status": "confirmed"},
                             {"event_id": "fixture-event-b", "minutes_until_start": 15, "status": "cancelled"}])
        code, tick = self.operation("tick")
        self.assertEqual(code, 200)
        self.assertEqual(tick["data"]["synchronization"]["cancelled_count"], 2)
        self.assertEqual(self.operation("dispatch")[1]["data"]["transport_attempts"], 0)
        self.assertEqual(self.state()["sender_accepts_count"], 0)
        self.control(advance_seconds=15 * 60)
        self.assertEqual(self.cycle()["dispatch"]["accepted_count"], 1)
        state = self.state()
        self.assertEqual(state["sender_accepts_count"], 1)
        self.assertEqual(Counter(record["state"] for record in state["records"]), {"accepted": 1, "cancelled": 2})
        self.assertFalse(any(record["event_id"] == "fixture-event-b" and record["state"] == "accepted" for record in state["records"]))
        self.report("exact_event_sync_after_reschedule_and_cancel", "old_pending_canceled_before_dispatch", "only_rescheduled_current_start_accepted")

    def test_lost_http_response_after_accept_keeps_live_lock_and_replay_is_safe(self):
        self.prepare()
        self.control(checkpoint="after_accepted")
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.operation, "dispatch", timeout=1.5)
            state = self.wait_state(lambda current: current["checkpoint"]["reached"] == "after_accepted")
            self.assertFalse(pending.done(), "O aceite persistente deve anteceder o timeout do cliente.")
            self.assertEqual(state["sender_accepts_count"], 1)
            self.assertEqual(Counter(record["state"] for record in state["records"]), {"accepted": 1})
            with self.assertRaises(TimeoutError):
                pending.result(timeout=4)
            self.assertEqual(self.operation("recover")[1]["data"]["skip_reason"], "busy")
            self.assertEqual(self.operation("tick")[1]["data"]["skip_reason"], "busy")
            self.control(checkpoint="none")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _code, recovery = self.operation("recover")
            if recovery["data"]["recovery_status"] == "completed":
                break
            time.sleep(0.03)
        else:
            self.fail("O worker concluído não liberou seu lock no prazo.")
        self.assertEqual(self.cycle()["dispatch"]["transport_attempts"], 0)
        self.stop()
        self.start()
        self.assertEqual(self.cycle()["dispatch"]["transport_attempts"], 0)
        self.assertEqual(self.state()["sender_accepts_count"], 1)
        self.report("client_timeout_after_durable_accept", "live_worker_still_owns_os_lock", "lost_response_replay_deduplicated", "accept_survives_restart")


if __name__ == "__main__":
    unittest.main()
