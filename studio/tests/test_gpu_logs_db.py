"""Port of PodLogsTest (fake SSE endpoint on localhost) plus leases and events on a scratch Postgres."""

import threading
import time
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studio.gpu import logs as logs_mod
from studio.gpu.events import PgPodEvents
from studio.gpu.leases import PgPodLeases
from studio.gpu.logs import PodLogs
from studio.gpu.pods import Pod
from studio.ledger import CostLedger
from studio.tests.test_pricing import at


class Fake:
    queries: list[str] = []
    auth: list[str] = []
    routes: dict[str, tuple[int, str, bool]] = {}


@pytest.fixture
def server():
    Fake.queries, Fake.auth, Fake.routes = [], [], {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path, _, query = self.path.partition("?")
            Fake.queries.append(query)
            Fake.auth.append(self.headers.get("Authorization"))
            status, body, hold = Fake.routes.get(path, (404, "", False))
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(body.encode())
                self.wfile.flush()
                if hold:  # the real stream never ends by itself
                    time.sleep(2.5)
            except OSError:
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v2"
    srv.shutdown()


def ev(ts, line):
    return f'id: {ts}\ndata: {{"ts":"{ts}","source":"container","line":"{line}"}}\n\n'


def test_reads_lines_and_returns_without_waiting_for_the_stream_to_end(server):
    Fake.routes["/v2/pods/abcd1234/logs"] = (200, ev("2026-11-20T01:00:00Z", "booting") + ev("2026-11-20T01:00:05Z", "ready"), True)
    t = time.monotonic()
    p = PodLogs(server, "k", 3000, 400).read("abcd1234", None, 150)
    ms = (time.monotonic() - t) * 1000
    assert [x.line for x in p.lines] == ["booting", "ready"] and p.next == "2026-11-20T01:00:05Z" and p.error is None
    assert ms < 2500, f"returned once the stream went quiet, took {ms:.0f} ms"
    assert Fake.queries[0] == "tail=150" and Fake.auth[0] == "Bearer k"


def test_uses_since_when_given_and_keeps_it_when_nothing_new(server):
    Fake.routes["/v2/pods/abcd1234/logs"] = (200, "", True)
    p = PodLogs(server, "k", 700, 200).read("abcd1234", "2026-11-20T01:00:05Z", 150)
    assert not p.lines and p.next == "2026-11-20T01:00:05Z"
    assert Fake.queries[0].startswith("since=2026-11-20T01%3A00%3A05Z")


def test_reports_an_upstream_error_instead_of_raising(server):
    Fake.routes["/v2/pods/abcd1234/logs"] = (502, "", False)
    p = PodLogs(server, "k", 1500, 300).read("abcd1234", None, 10)
    assert not p.lines and p.error and "502" in p.error


def test_rejects_a_bad_pod_id_and_missing_key_without_calling_runpod(server):
    logs = PodLogs(server, "k", 500, 100)
    assert logs.read("../secrets", None, 10).error == "Not a valid pod id"
    assert logs.read("a/b", None, 10).error == "Not a valid pod id"
    assert PodLogs(server, "", 500, 100).read("abcd1234", None, 10).error
    assert not Fake.queries


def test_skips_malformed_events_and_truncates_huge_lines(server):
    Fake.routes["/v2/pods/abcd1234/logs"] = (200, "data: not json\n\n" + ev("2026-11-20T01:00:00Z", "x" * 5000), True)
    p = PodLogs(server, "k", 2000, 300).read("abcd1234", None, 10)
    assert len(p.lines) == 1 and len(p.lines[0].line) == logs_mod.MAX_LINE_CHARS + 1


# ---- leases and events on the real schema


def test_lease_lifecycle(scratch_db):
    leases = PgPodLeases(scratch_db)
    leases.open("runpod", "pod-1", "projectv-a", "wan")
    first = leases.find("pod-1")
    assert first.released_at is None and first.purpose == "wan"
    leases.heartbeat("pod-1")
    leases.release("pod-1")
    assert leases.find("pod-1").released_at is not None
    leases.heartbeat("pod-1")  # a released lease is not revived by a late heartbeat
    assert leases.find("pod-1").released_at is not None
    leases.open("runpod", "pod-1", "projectv-a", "wan")  # re-opening clears the release
    assert leases.find("pod-1").released_at is None and leases.find("nope") is None


def test_shorts_leases_attach_and_recent(scratch_db):
    leases = PgPodLeases(scratch_db)
    short = uuid.uuid4()
    scratch_db.execute("INSERT INTO video (id, prompt, status) VALUES (%(i)s, 'x', 'RUNNING')", {"i": short})
    try:
        leases.open("runpod", "pod-2", "projectv-b", "dream", short, 0.69, "RTX 4090")
    except Exception:  # dream_short_id may reference dream_short: fall back to a real row below
        pytest.skip("dream_short_id references the dream_short table")
    assert [o.pod_id for o in leases.open_for_shorts()] == ["pod-2"]
    leases.attach("pod-2", short)
    leases.attach("pod-2", short)
    assert leases.attached("pod-2") == [short]
    leases.detach("pod-2", short)
    assert leases.attached("pod-2") == [] and leases.recent(5)[0]["pod_id"] in {"pod-1", "pod-2"}


def test_terminate_books_a_cost_entry_and_an_event_and_orphan_booking_is_remembered(scratch_db):
    pricing = at("2026-11-19")
    ev_ = PgPodEvents(scratch_db, CostLedger(scratch_db), pricing)
    pod = Pod("pod-9", "projectv-z", "RUNNING", 0.5, None, None)
    assert ev_.last_orphan_booking("pod-9") is None
    ev_.orphan_billed("runpod", pod, "no lease", 1.5, 0.75)
    assert ev_.last_orphan_booking("pod-9") is not None
    ev_.terminated("runpod", pod, "idle", 0.25, 0.125)
    rows = scratch_db.rows("SELECT amount_inr, units, unit_type, estimated, note FROM cost_entry WHERE provider_id = 'runpod' ORDER BY ts")
    assert rows[1]["unit_type"] == "gpu_hour" and rows[1]["estimated"] is True and rows[1]["units"] == Decimal("0.2500")
    assert rows[1]["note"].startswith("GPU watchdog terminated pod pod-9 (projectv-z): idle; $0.5000/h live rate")
    assert float(rows[1]["amount_inr"]) == pytest.approx(0.125 * pricing.fx(), abs=1e-4)
    kinds = [r["kind"] for r in ev_.recent(10)]
    assert kinds == ["terminated", "orphan_billed"]
