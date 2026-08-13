"""HTTP client for the FIPE JSON API.

Standard library only, so the project stays dependency-free for now. Every
request funnels through ``_post``: switching to ``requests`` later means
rewriting that single method and nothing else.
"""

import collections
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

BASE_URL = "https://veiculos.fipe.org.br/api/veiculos/"
# FIPE's WAF answers 403 to non-browser User-Agents, so a descriptive one is not
# an option. Politeness lives in the request rate instead — see FipeClient.delay.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Endpoint names, as exposed by the FIPE site.
REFERENCE_TABLES = "ConsultarTabelaDeReferencia"
BRANDS = "ConsultarMarcas"
MODELS = "ConsultarModelos"
MODEL_YEARS = "ConsultarAnoModelo"
PRICE = "ConsultarValorComTodosParametros"

# The API tolerates very little volume — it answers 429 well before any
# documented limit — so quotes are capped over a sliding minute.
DEFAULT_QUOTES_PER_MINUTE = 20
QUOTA_WINDOW = 60.0
DEFAULT_RATE_LIMIT_RETRIES = 5
# Once the quota saturates, every quote waits a couple of seconds. Reporting
# each one would bury the log, so waits are announced at most this often, and
# only when they are long enough to be worth a line.
WAIT_REPORT_INTERVAL = 30.0
MIN_REPORTED_WAIT = 1.0


class FipeError(Exception):
    """Base class for every FIPE client failure."""


class FipeNotFound(FipeError):
    """FIPE answered with its "nenhum veículo encontrado" payload."""


class FipeUnavailable(FipeError):
    """Network or server failure that survived every retry."""


class FipeRateLimited(FipeError):
    """HTTP 429 that kept coming back after waiting out the quota window."""


class FipeClient:
    """Thin, polite wrapper around the FIPE endpoints.

    Returns raw decoded JSON — turning it into domain objects is
    ``crawler.fipe.parsers``' job.
    """

    def __init__(
        self,
        base_url=BASE_URL,
        delay=0.5,
        timeout=20,
        max_retries=3,
        backoff=2.0,
        user_agent=DEFAULT_USER_AGENT,
        quotes_per_minute=DEFAULT_QUOTES_PER_MINUTE,
        max_rate_limit_retries=DEFAULT_RATE_LIMIT_RETRIES,
        on_wait=None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.user_agent = user_agent
        self.quotes_per_minute = quotes_per_minute
        self.max_rate_limit_retries = max_rate_limit_retries
        self.on_wait = on_wait
        # Injectable so the quota can be tested without real time passing.
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at = 0.0
        # Timestamp of each quote in the last minute; the oldest frees a slot
        # exactly QUOTA_WINDOW seconds after it was spent.
        self._quote_times = collections.deque()
        self._last_wait_report_at = None
        # Counters, so a 429 can be pinned to an exact request.
        self.requests_made = 0
        self.quotes_requested = 0
        self.rate_limited_at = []

    # -- endpoints ---------------------------------------------------------

    def reference_tables(self):
        return self._post(REFERENCE_TABLES, {})

    def brands(self, reference_code, vehicle_type):
        return self._post(
            BRANDS,
            {
                "codigoTabelaReferencia": reference_code,
                "codigoTipoVeiculo": vehicle_type,
            },
        )

    def models(self, reference_code, vehicle_type, brand_code):
        payload = self._post(
            MODELS,
            {
                "codigoTabelaReferencia": reference_code,
                "codigoTipoVeiculo": vehicle_type,
                "codigoMarca": brand_code,
            },
        )
        # This endpoint wraps the list: {"Modelos": [...], "Anos": [...]}.
        return payload.get("Modelos", []) if isinstance(payload, dict) else payload

    def model_years(self, reference_code, vehicle_type, brand_code, model_code):
        return self._post(
            MODEL_YEARS,
            {
                "codigoTabelaReferencia": reference_code,
                "codigoTipoVeiculo": vehicle_type,
                "codigoMarca": brand_code,
                "codigoModelo": model_code,
            },
        )

    def price(self, reference_code, vehicle_type, brand_code, model_code, fipe_year_code):
        """Fetch one quote. ``fipe_year_code`` looks like ``"2014-3"`` (year-fuel).

        Subject to the per-minute quota: the call blocks until the current
        minute has room.
        """
        self._await_quota()
        self.quotes_requested += 1
        year, _, fuel = fipe_year_code.partition("-")
        return self._post(
            PRICE,
            {
                "codigoTabelaReferencia": reference_code,
                "codigoTipoVeiculo": vehicle_type,
                "codigoMarca": brand_code,
                "codigoModelo": model_code,
                "ano": fipe_year_code,
                "anoModelo": year,
                "codigoTipoCombustivel": fuel,
                "tipoConsulta": "tradicional",
            },
        )

    # -- transport ---------------------------------------------------------

    def _post(self, endpoint, payload):
        """POST a form-encoded body and return decoded JSON.

        The only place that touches the network. Retries transport and 5xx
        errors with exponential backoff, and HTTP 429 by waiting out the quota
        window. Other 4xx and malformed JSON fail fast.
        """
        url = self.base_url + endpoint
        body = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "User-Agent": self.user_agent,
                "Referer": "https://veiculos.fipe.org.br/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        attempt = 0
        throttled = 0
        last_error = None
        while True:
            self._throttle()
            # Counted per network attempt, retries included, so the number in a
            # 429 message matches what the server actually saw.
            self.requests_made += 1
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    # Being rate limited is expected here, not a failure: wait
                    # for the next window and repeat the same request.
                    throttled += 1
                    self.rate_limited_at.append(self.requests_made)
                    if throttled > self.max_rate_limit_retries:
                        raise FipeRateLimited(
                            f"{endpoint}: HTTP 429 na requisição #{self.requests_made} "
                            f"mesmo após {throttled - 1} esperas"
                        ) from exc
                    self._pause_for_rate_limit(endpoint)
                    continue
                if exc.code < 500:
                    raise FipeError(f"{endpoint} respondeu HTTP {exc.code}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc

            attempt += 1
            if attempt > self.max_retries:
                raise FipeUnavailable(
                    f"{endpoint} falhou após {attempt} tentativas: {last_error}"
                ) from last_error
            wait = self.backoff ** (attempt - 1)
            logger.warning("%s falhou (%s); nova tentativa em %.1fs", endpoint, last_error, wait)
            self._sleep(wait)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FipeError(f"{endpoint} devolveu conteúdo não-JSON") from exc

        if isinstance(data, dict) and "erro" in data:
            raise FipeNotFound(f"{endpoint}: {data['erro']}")
        return data

    def _throttle(self):
        """Keep at least ``delay`` seconds between two requests."""
        if not self.delay:
            return
        elapsed = self._monotonic() - self._last_request_at
        if elapsed < self.delay:
            self._sleep(self.delay - elapsed)
        self._last_request_at = self._monotonic()

    # -- per-minute quota --------------------------------------------------

    def _await_quota(self):
        """Allow ``quotes_per_minute`` quotes per *sliding* minute.

        Each quote holds a slot for exactly ``QUOTA_WINDOW`` seconds and frees
        it the instant that minute is up, so the crawler settles into a steady
        rate instead of stalling in bursts at a window boundary.
        """
        if not self.quotes_per_minute:
            return

        now = self._monotonic()
        self._release_expired_slots(now)
        if len(self._quote_times) >= self.quotes_per_minute:
            wait = self._quote_times[0] + QUOTA_WINDOW - now
            if wait > 0:
                if wait >= MIN_REPORTED_WAIT:
                    self._report_quota_wait(
                        f"Cota de {self.quotes_per_minute} cotações/min atingida na "
                        f"requisição #{self.requests_made} (cotação #{self.quotes_requested}); "
                        f"aguardando {wait:.1f}s pelo próximo slot."
                    )
                self._sleep(wait)
                now = self._monotonic()
                self._release_expired_slots(now)
        self._quote_times.append(now)

    def _release_expired_slots(self, now):
        """Drop the quotes that completed a full minute — their slots are free."""
        cutoff = now - QUOTA_WINDOW
        while self._quote_times and self._quote_times[0] <= cutoff:
            self._quote_times.popleft()

    def _pause_for_rate_limit(self, endpoint):
        """Back off for exactly one minute after a 429, then start clean."""
        where = (
            f"requisição #{self.requests_made}, cotação #{self.quotes_requested}, "
            f"{len(self._quote_times)} no último minuto"
        )
        self._report_wait(
            f"HTTP 429 em {endpoint} ({where}); aguardando {QUOTA_WINDOW:.0f}s."
        )
        logger.warning(
            "%s respondeu 429 (%s); aguardando %.0fs — 429 até agora: %s",
            endpoint,
            where,
            QUOTA_WINDOW,
            self.rate_limited_at,
        )
        self._sleep(QUOTA_WINDOW)
        # A full minute passed, so every slot is free again.
        self._quote_times.clear()

    def _report_quota_wait(self, message):
        """Announce a quota wait, but no more than once per WAIT_REPORT_INTERVAL."""
        now = self._monotonic()
        if (
            self._last_wait_report_at is not None
            and now - self._last_wait_report_at < WAIT_REPORT_INTERVAL
        ):
            return
        self._last_wait_report_at = now
        self._report_wait(message)

    def _report_wait(self, message):
        if self.on_wait:
            self.on_wait(message)
