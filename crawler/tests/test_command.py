"""Argument validation for crawl_fipe. These raise before any network call."""

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class ModelsOnlyValidationTests(SimpleTestCase):
    def test_rejects_resume(self):
        with self.assertRaisesMessage(CommandError, "--resume"):
            call_command("crawl_fipe", "--models-only", "--resume")

    def test_rejects_limit(self):
        with self.assertRaisesMessage(CommandError, "--limit"):
            call_command("crawl_fipe", "--models-only", "--limit", "5")

    def test_rejects_brands_only(self):
        with self.assertRaisesMessage(CommandError, "--brands-only"):
            call_command("crawl_fipe", "--models-only", "--brands-only")
