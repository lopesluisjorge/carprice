"""The rule that keeps the apps apart: web writes a request, never runs a crawl."""

import ast
from pathlib import Path

from django.test import SimpleTestCase

WEB = Path(__file__).resolve().parent.parent
FORBIDDEN = {"FipeClient", "crawler.fipe"}


def imported_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield module
            for alias in node.names:
                yield f"{module}.{alias.name}"
                yield alias.name


class BoundaryTests(SimpleTestCase):
    def test_web_never_imports_the_fipe_client(self):
        offenders = []
        for path in WEB.rglob("*.py"):
            if "tests" in path.parts:
                continue
            for name in imported_names(path):
                if any(name.startswith(bad) or name == bad for bad in FORBIDDEN):
                    offenders.append(f"{path.relative_to(WEB.parent)}: {name}")

        self.assertEqual(
            offenders,
            [],
            "web deve agendar coleta, nunca executá-la — use crawler.services.scheduling",
        )
