"""Consumes the on-demand collection queue. Argument parsing and reporting only —
the work lives in ``crawler.services.collecting``.
"""

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crawler.fipe import FipeClient, FipeError
from crawler.fipe.client import DEFAULT_REQUESTS_PER_MINUTE
from crawler.services import collecting

# Settings-driven so the deployed tree can stay read-only and the lock can live
# in /run, where a runtime lock belongs. Defaults to BASE_DIR for development.
LOCK_PATH = Path(settings.CRAWL_QUEUE_LOCK_PATH)


class Command(BaseCommand):
    help = "Executa as coletas agendadas pelas buscas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--forever",
            action="store_true",
            help="Fica em laço, dormindo --interval entre as passadas.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=60.0,
            help="Segundos entre passadas com --forever. Padrão: 60.",
        )
        parser.add_argument(
            "--budget",
            type=int,
            default=collecting.DEFAULT_BUDGET,
            help=(
                "Máximo de requisições gastas em cada pedido por passada; esgotado, "
                f"o worker passa ao próximo. Padrão: {collecting.DEFAULT_BUDGET}."
            ),
        )
        parser.add_argument(
            "--requests-per-minute",
            type=int,
            default=DEFAULT_REQUESTS_PER_MINUTE,
            help=f"Cota por minuto corrido. Padrão: {DEFAULT_REQUESTS_PER_MINUTE}.",
        )

    def handle(self, *args, **options):
        client = FipeClient(
            requests_per_minute=options["requests_per_minute"],
            on_wait=lambda message: self.log(f"  {message}", self.style.WARNING),
        )
        try:
            with collecting.queue_lock(LOCK_PATH):
                self._run(client, options)
        except collecting.QueueBusy as exc:
            raise CommandError(f"{exc}. Só um worker por vez — a cota depende disso.")

    def _run(self, client, options):
        while True:
            reclaimed = collecting.reclaim_stale_requests()
            if reclaimed:
                self.log(f"{reclaimed} pedido(s) retomado(s) de um worker anterior.")
            self._pass(client, options["budget"])
            if not options["forever"]:
                return
            self._sleep(options["interval"])

    def _pass(self, client, budget):
        requests = list(collecting.pending_requests())
        if not requests:
            self.log("Nada na fila.")
            return
        for request in requests:
            self.log(f"{request.term}: {request.items.count()} modelos")
            try:
                spent = collecting.process_request(
                    client, request, budget=budget, progress=self.log
                )
            except FipeError as exc:
                request.last_error = f"{type(exc).__name__}: {exc}"
                request.status = collecting.CollectionStatus.PARTIAL
                request.save(update_fields=["last_error", "status"])
                raise CommandError(str(exc)) from exc
            self.log(
                self.style.SUCCESS(
                    f"  {request.get_status_display()}: {spent} requisições, "
                    f"{request.quotes_created} cotações novas, "
                    f"{request.quotes_updated} atualizadas, "
                    f"{request.quotes_missing} sem preço na FIPE."
                )
            )

    def _sleep(self, seconds):
        time.sleep(seconds)

    def log(self, message, style=None):
        """Every log line carries the moment it was written."""
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{stamp}] {message}"
        self.stdout.write(style(text) if style else text)
