import contextlib
import logging
import urllib.error
from email.message import Message
from unittest import mock

from django.test import SimpleTestCase


def setUpModule():
    # The retry paths log warnings on purpose; keep the test output readable.
    logging.disable(logging.WARNING)


def tearDownModule():
    logging.disable(logging.NOTSET)

from crawler.fipe import FipeError, FipeNotFound, FipeRateLimited, FipeUnavailable
from crawler.fipe.client import FipeClient


class FakeClock:
    """Advances only when the client sleeps, so quota tests run instantly."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def tick(self, seconds):
        self.now += seconds


def build_client(clock, responses=None, **kwargs):
    """A client whose only network method is replaced by a scripted stub."""
    client = FipeClient(
        delay=0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **kwargs,
    )
    client.posts = []

    def fake_post(endpoint, payload):
        client.posts.append(endpoint)
        if responses:
            outcome = responses.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        return {}

    client._post = fake_post
    return client


def ok(body):
    """A urlopen context manager yielding ``body``."""
    response = mock.MagicMock()
    response.read.return_value = body
    manager = mock.MagicMock()
    manager.__enter__.return_value = response
    return manager


@contextlib.contextmanager
def patch_urlopen(outcomes):
    """Serve ``outcomes`` in order; exceptions are raised, responses returned."""
    calls = []

    def side_effect(request, timeout=None):
        calls.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with mock.patch("urllib.request.urlopen", side_effect=side_effect):
        yield calls


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("url", code, "erro", headers, None)


class QuotaTests(SimpleTestCase):
    def setUp(self):
        self.clock = FakeClock()

    def fetch(self, client, times):
        # The window governs every request now, so drive it directly: build_client
        # stubs _post, which is where the real quota lives.
        for _ in range(times):
            client._await_slot()

    def test_allows_the_full_quota_without_waiting(self):
        client = build_client(self.clock, requests_per_minute=20)
        self.fetch(client, 20)

        self.assertEqual(self.clock.slept, [])

    def test_request_beyond_the_quota_waits_for_the_next_window(self):
        client = build_client(self.clock, requests_per_minute=20)
        self.fetch(client, 20)
        self.clock.tick(15)
        self.fetch(client, 1)

        self.assertEqual(self.clock.slept, [45.0])

    def test_spreads_forty_five_requests_over_three_minutes(self):
        client = build_client(self.clock, requests_per_minute=20)
        self.fetch(client, 45)

        self.assertEqual(self.clock.slept, [60.0, 60.0])

    def test_each_slot_frees_exactly_one_minute_after_it_was_used(self):
        client = build_client(self.clock, requests_per_minute=2)
        self.fetch(client, 1)  # slot taken at t
        self.clock.tick(10)
        self.fetch(client, 1)  # slot taken at t+10

        self.clock.tick(5)  # t+15: both slots still held
        self.fetch(client, 1)

        # Waits only until the first slot completes its minute, not a whole one.
        self.assertEqual(self.clock.slept, [45.0])

    def test_a_freed_slot_is_reused_without_waiting(self):
        client = build_client(self.clock, requests_per_minute=2)
        self.fetch(client, 2)
        self.clock.tick(61)  # both slots have served their minute

        self.fetch(client, 2)

        self.assertEqual(self.clock.slept, [])

    def test_repeated_wait_reports_are_throttled(self):
        messages = []
        client = build_client(self.clock, requests_per_minute=2, on_wait=messages.append)

        client._report_quota_wait("primeira")
        client._report_quota_wait("logo em seguida")
        self.clock.tick(31)
        client._report_quota_wait("depois do intervalo")

        self.assertEqual(messages, ["primeira", "depois do intervalo"])

    def test_a_slow_window_does_not_carry_debt_forward(self):
        client = build_client(self.clock, requests_per_minute=20)
        self.fetch(client, 5)
        self.clock.tick(90)  # window expired on its own
        self.fetch(client, 20)

        self.assertEqual(self.clock.slept, [])

    def test_quota_can_be_disabled(self):
        client = build_client(self.clock, requests_per_minute=0)
        self.fetch(client, 100)

        self.assertEqual(self.clock.slept, [])

    def test_every_endpoint_is_charged_to_the_quota(self):
        # The whole point of the fix: a catalogue call (here ConsultarMarcas)
        # takes a slot too, so a models-only sweep can no longer outrun the cap.
        client = FipeClient(
            delay=0,
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
            requests_per_minute=2,
        )
        with patch_urlopen([ok(b"{}")] * 3):
            client.brands(322, 1)
            client.brands(322, 1)
            client.brands(322, 1)

        self.assertEqual(self.clock.slept, [60.0])

    def test_reports_the_wait(self):
        messages = []
        client = build_client(self.clock, requests_per_minute=2, on_wait=messages.append)
        self.fetch(client, 3)

        self.assertEqual(len(messages), 1)
        self.assertIn("60.0s", messages[0])

    def test_sub_second_waits_are_not_reported(self):
        messages = []
        client = build_client(self.clock, requests_per_minute=2, on_wait=messages.append)
        self.fetch(client, 2)
        self.clock.tick(59.5)  # the first slot frees in half a second

        self.fetch(client, 1)

        self.assertEqual(self.clock.slept, [0.5])
        self.assertEqual(messages, [])


class RequestCounterTests(SimpleTestCase):
    def setUp(self):
        self.clock = FakeClock()

    def live_client(self, **kwargs):
        return FipeClient(delay=0, sleep=self.clock.sleep, monotonic=self.clock.monotonic, **kwargs)

    def test_counts_every_request(self):
        client = self.live_client()
        with patch_urlopen([ok(b"{}"), ok(b"{}"), ok(b"{}")]):
            client.brands(322, 1)
            client.price(322, 1, 21, 4828, "2013-1")
            client.price(322, 1, 21, 4828, "2012-2")

        self.assertEqual(client.requests_made, 3)
        self.assertEqual(client.quotes_requested, 2)

    def test_counts_retries_as_requests(self):
        client = self.live_client()
        with patch_urlopen([http_error(500), ok(b"{}")]):
            client._post("Endpoint", {})

        self.assertEqual(client.requests_made, 2)

    def test_records_which_request_hit_429(self):
        client = self.live_client()
        with patch_urlopen([ok(b"{}"), ok(b"{}"), http_error(429), ok(b"{}")]):
            client.price(322, 1, 21, 4828, "2013-1")
            client.price(322, 1, 21, 4828, "2012-2")
            client.price(322, 1, 21, 4712, "32000-1")

        self.assertEqual(client.rate_limited_at, [3])
        self.assertEqual(client.requests_made, 4)

    def test_records_every_429_position(self):
        client = self.live_client()
        with patch_urlopen([http_error(429), http_error(429), ok(b"{}")]):
            client._post("Endpoint", {})

        self.assertEqual(client.rate_limited_at, [1, 2])

    def test_the_wait_message_names_the_request(self):
        messages = []
        client = self.live_client(on_wait=messages.append)
        with patch_urlopen([ok(b"{}"), http_error(429), ok(b"{}")]):
            client.brands(322, 1)
            client.price(322, 1, 21, 4828, "2013-1")

        self.assertIn("requisição #2", messages[0])
        self.assertIn("cotação #1", messages[0])

    def test_the_quota_message_names_the_request(self):
        messages = []
        client = self.live_client(requests_per_minute=2, on_wait=messages.append)
        with patch_urlopen([ok(b"{}")] * 3):
            for _ in range(3):
                client.price(322, 1, 21, 4828, "2013-1")

        self.assertIn("requisição #2", messages[0])


class RateLimitTests(SimpleTestCase):
    def setUp(self):
        self.clock = FakeClock()

    def test_retries_after_a_429_and_succeeds(self):
        client = self.live_client()
        with patch_urlopen([http_error(429), ok(b'{"ok": true}')]) as calls:
            self.assertEqual(client._post("Endpoint", {}), {"ok": True})

        self.assertEqual(len(calls), 2)
        self.assertEqual(self.clock.slept, [60.0])

    def test_always_waits_exactly_one_minute_ignoring_retry_after(self):
        client = self.live_client()
        with patch_urlopen([http_error(429, retry_after=12), ok(b"{}")]):
            client._post("Endpoint", {})

        self.assertEqual(self.clock.slept, [60.0])

    def test_gives_up_after_too_many_429s(self):
        client = self.live_client(max_rate_limit_retries=2)
        with patch_urlopen([http_error(429)] * 3):
            with self.assertRaises(FipeRateLimited):
                client._post("Endpoint", {})

    def test_other_client_errors_fail_immediately(self):
        client = self.live_client()
        with patch_urlopen([http_error(403)]) as calls:
            with self.assertRaises(FipeError):
                client._post("Endpoint", {})

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.clock.slept, [])

    def test_server_errors_are_retried_with_backoff(self):
        client = self.live_client()
        with patch_urlopen([http_error(500), http_error(500), ok(b"{}")]):
            client._post("Endpoint", {})

        self.assertEqual(self.clock.slept, [1.0, 2.0])

    def test_server_errors_eventually_give_up(self):
        client = self.live_client(max_retries=1)
        with patch_urlopen([http_error(500)] * 2):
            with self.assertRaises(FipeUnavailable):
                client._post("Endpoint", {})

    def test_fipe_error_payload_becomes_not_found(self):
        client = self.live_client()
        with patch_urlopen([ok('{"codigo": "0", "erro": "nenhum ve\\u00edculo encontrado"}'.encode())]):
            with self.assertRaises(FipeNotFound):
                client._post("Endpoint", {})

    def test_non_json_body_is_reported(self):
        client = self.live_client()
        with patch_urlopen([ok(b"<html>manutencao</html>")]):
            with self.assertRaises(FipeError):
                client._post("Endpoint", {})

    def live_client(self, **kwargs):
        return FipeClient(delay=0, sleep=self.clock.sleep, monotonic=self.clock.monotonic, **kwargs)

    def test_the_wait_is_a_full_minute_regardless_of_elapsed_time(self):
        client = self.live_client()
        client._request_times.append(self.clock.monotonic())
        self.clock.tick(50)

        client._pause_for_rate_limit("Endpoint")

        self.assertEqual(self.clock.slept, [60.0])

    def test_a_429_frees_every_slot(self):
        client = self.live_client(requests_per_minute=20)
        for _ in range(20):
            client._request_times.append(self.clock.monotonic())

        client._pause_for_rate_limit("Endpoint")

        self.assertEqual(len(client._request_times), 0)

    def test_after_a_429_the_next_request_does_not_wait(self):
        client = build_client(self.clock, requests_per_minute=2)
        client._await_slot()
        client._await_slot()  # window full
        client._pause_for_rate_limit("Endpoint")  # clears it
        self.clock.slept.clear()

        client._await_slot()

        self.assertEqual(self.clock.slept, [])
