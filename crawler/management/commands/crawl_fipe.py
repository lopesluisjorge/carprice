"""Entry point for the FIPE crawler. Argument parsing and reporting only —
all the logic lives in ``crawler.services.sync``.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crawler.fipe import FipeClient, FipeError
from crawler.fipe.client import DEFAULT_REQUESTS_PER_MINUTE
from crawler.models import VehicleType
from crawler.services import sync

VEHICLE_TYPES = {
    "car": VehicleType.CAR,
    "motorcycle": VehicleType.MOTORCYCLE,
    "truck": VehicleType.TRUCK,
}


class Command(BaseCommand):
    help = "Coleta a tabela FIPE e grava as cotações no banco."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reference",
            help="Tabela de referência no formato AAAA-MM. Padrão: a mais recente.",
        )
        parser.add_argument(
            "--vehicle-type",
            choices=sorted(VEHICLE_TYPES),
            default="car",
            help="Tipo de veículo a coletar. Padrão: car.",
        )
        parser.add_argument(
            "--brand",
            action="append",
            dest="brands",
            help="Código FIPE da marca. Pode repetir. Padrão: todas.",
        )
        parser.add_argument(
            "--brands-only",
            action="store_true",
            help="Grava apenas o catálogo de marcas, sem modelos nem cotações.",
        )
        parser.add_argument(
            "--models-only",
            action="store_true",
            help=(
                "Atualiza modelos e anos/modelo (sem cotações). Aceita --brand; "
                "sem ele, percorre todas as marcas."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Interrompe após N cotações. Útil para testar.",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Retoma a última execução inacabada, pulando marcas já concluídas.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Executa a coleta sem gravar nada.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.5,
            help="Segundos entre requisições. Padrão: 0.5.",
        )
        parser.add_argument(
            "--requests-per-minute",
            type=int,
            default=DEFAULT_REQUESTS_PER_MINUTE,
            help=(
                "Máximo de requisições por minuto corrido (todos os endpoints); cada slot é "
                f"liberado 60s após ser usado. Padrão: {DEFAULT_REQUESTS_PER_MINUTE}. Use 0 "
                "para desligar a cota."
            ),
        )

    def handle(self, *args, **options):
        period = _parse_reference(options["reference"])
        vehicle_type = VEHICLE_TYPES[options["vehicle_type"]]
        # Shared with the client so every pause — quota or HTTP 429 — prints
        # where the sweep stands.
        progress = sync.CrawlProgress()
        client = FipeClient(
            delay=options["delay"],
            requests_per_minute=options["requests_per_minute"],
            on_wait=lambda message: self.log(
                f"  {message} — {progress.summary()}", self.style.WARNING
            ),
        )

        if options["models_only"]:
            conflicting = [
                flag
                for flag, value in (
                    ("--brands-only", options["brands_only"]),
                    ("--limit", options["limit"]),
                    ("--resume", options["resume"]),
                )
                if value
            ]
            if conflicting:
                raise CommandError(
                    f"--models-only não pode ser combinado com {', '.join(conflicting)}"
                )
            return self._handle_models_only(
                client, vehicle_type, period, options["brands"], options["dry_run"], progress
            )

        if options["brands_only"]:
            conflicting = [
                flag
                for flag, value in (
                    ("--brand", options["brands"]),
                    ("--limit", options["limit"]),
                    ("--resume", options["resume"]),
                )
                if value
            ]
            if conflicting:
                raise CommandError(
                    f"--brands-only não pode ser combinado com {', '.join(conflicting)}"
                )
            return self._handle_brands_only(client, vehicle_type, period, options["dry_run"])

        try:
            run = sync.sync(
                client,
                vehicle_type=vehicle_type,
                period=period,
                brand_codes=options["brands"],
                limit=options["limit"],
                resume=options["resume"],
                dry_run=options["dry_run"],
                progress=self.log,
                progress_state=progress,
            )
        except (FipeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.log(
            self.style.SUCCESS(
                f"{run.get_status_display()}: {run.brands_done} marcas, "
                f"{run.models_done} modelos, {run.quotes_created} cotações novas, "
                f"{run.quotes_updated} atualizadas."
            )
        )
        self._report_requests(client)

    def _report_requests(self, client):
        line = f"{client.requests_made} requisições ({client.quotes_requested} cotações)"
        if not client.rate_limited_at:
            self.log(f"{line}, nenhum HTTP 429.")
            return
        positions = ", ".join(f"#{n}" for n in client.rate_limited_at[:10])
        if len(client.rate_limited_at) > 10:
            positions += ", …"
        self.log(
            self.style.WARNING(
                f"{line}, {len(client.rate_limited_at)} HTTP 429 nas requisições {positions}."
            )
        )

    def log(self, message, style=None):
        """Every log line carries the moment it was written."""
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{stamp}] {message}"
        self.stdout.write(style(text) if style else text)

    def _handle_models_only(self, client, vehicle_type, period, brand_codes, dry_run, progress):
        try:
            result = sync.sync_models(
                client,
                vehicle_type=vehicle_type,
                period=period,
                brand_codes=brand_codes,
                dry_run=dry_run,
                progress=self.log,
                progress_state=progress,
            )
        except (FipeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.log(
            self.style.SUCCESS(
                f"{result.models_created} modelos novos, {result.models_updated} atualizados; "
                f"{result.years_created} anos/modelo novos, {result.years_updated} atualizados."
            )
        )
        self._report_requests(client)

    def _handle_brands_only(self, client, vehicle_type, period, dry_run):
        try:
            created, updated = sync.sync_brands(
                client,
                vehicle_type=vehicle_type,
                period=period,
                dry_run=dry_run,
                progress=self.log,
            )
        except (FipeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.log(
            self.style.SUCCESS(f"{created} marcas novas, {updated} já conhecidas.")
        )
        self._report_requests(client)


def _parse_reference(value):
    """``"2024-01"`` -> ``(1, 2024)``; ``None`` -> ``None``."""
    if not value:
        return None
    try:
        year, month = value.split("-")
        month, year = int(month), int(year)
    except ValueError as exc:
        raise CommandError("--reference deve estar no formato AAAA-MM (ex.: 2024-01)") from exc
    if not 1 <= month <= 12:
        raise CommandError("--reference tem um mês inválido")
    return month, year
