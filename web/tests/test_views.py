from urllib.parse import urlencode

from django.test import TestCase
from django.urls import NoReverseMatch
from django.urls import reverse

from crawler.models import CollectionRequest
from crawler.models import FuelType

from web import codes
from web.tests.factories import add_quote
from web.tests.factories import build_vehicle


class SearchScreenTests(TestCase):
    def setUp(self):
        self.uno = build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        self.uno.vehicle_model.name = "Uno Mille Fire"
        self.uno.vehicle_model.save()
        add_quote(self.uno, 2026, 8, "40000.00")

    def test_lists_every_model_without_a_term(self):
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "Uno Mille Fire")
        self.assertContains(response, "R$ 40.000,00")

    def test_finds_by_term(self):
        self.assertContains(self.client.get(reverse("web:home"), {"q": "mille"}), "Uno Mille")

    def test_term_without_matches_shows_the_empty_state(self):
        response = self.client.get(reverse("web:home"), {"q": "lamborghini"})
        self.assertContains(response, "Nenhum modelo encontrado")

    def test_filters_survive_in_the_links(self):
        response = self.client.get(reverse("web:home"), {"q": "mille", "fuel": "5"})
        self.assertEqual(response.context["filters"].fuels, (5,))
        self.assertContains(response, 'value="mille"')

    def test_htmx_request_returns_only_the_results(self):
        response = self.client.get(
            reverse("web:home"), {"q": "mille"}, headers={"hx-request": "true"}
        )
        self.assertContains(response, "Uno Mille")
        self.assertNotContains(response, "<html")

    def test_version_count_is_spelled_in_portuguese(self):
        # "versã" + "es" would come out as "versães"; the stem is "vers".
        build_vehicle(model_code=1, year=2016, fuel=FuelType.GASOLINE)
        add_quote(self.uno.vehicle_model.model_years.get(year=2016), 2026, 8, "39000.00")
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "2 versões")
        self.assertNotContains(response, "versães")

    def test_cascade_endpoints_are_gone(self):
        for name in ["web:model_options", "web:year_options"]:
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name)

    def test_the_sidebar_offers_the_new_controls(self):
        response = self.client.get(reverse("web:home"))
        for field in ['name="brand"', 'name="price_op"', 'name="price"', 'name="sort"']:
            with self.subTest(field=field):
                self.assertContains(response, field)

    def test_the_engine_option_carries_a_value_the_filter_can_read_back(self):
        # Em pt-BR o Decimal sai "1,0" no template, e o filtro voltava vazio: a
        # opção precisa ir sem localização, porque o value é dado e não texto.
        mille = build_vehicle(model_code=2, year=2015, name="UNO MILLE 1.0 Fire")
        add_quote(mille, 2026, 8, "20000.00")
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, 'value="1.0"')

        filtered = self.client.get(reverse("web:home"), {"engine": "1.0"})
        self.assertContains(filtered, "UNO MILLE 1.0 Fire")
        self.assertNotContains(filtered, "Uno Mille Fire")

    def test_a_price_outside_the_steps_is_offered_back(self):
        # Sem isso a URL compartilhada mostraria um filtro diferente do que pede.
        response = self.client.get(reverse("web:home"), {"price": "43500"})
        self.assertContains(response, "R$ 43.500")

    def test_the_round_steps_are_labelled_short(self):
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "R$ 50 mil")


class DetailTests(TestCase):
    def setUp(self):
        self.model_year = build_vehicle()
        add_quote(self.model_year, 2026, 8, "99000.00")

    def test_shows_the_current_price(self):
        response = self.client.get(reverse("web:detail"), {"v": codes.encode(self.model_year)})
        self.assertContains(response, "R$ 99.000,00")

    def test_single_month_hides_the_chart(self):
        response = self.client.get(reverse("web:detail"), {"v": codes.encode(self.model_year)})
        self.assertNotContains(response, 'id="price-chart"')

    def test_second_month_shows_the_chart(self):
        add_quote(self.model_year, 2026, 7, "98000.00")
        response = self.client.get(reverse("web:detail"), {"v": codes.encode(self.model_year)})
        self.assertContains(response, 'id="price-chart"')

    def test_unknown_vehicle_returns_to_the_search(self):
        response = self.client.get(reverse("web:detail"), {"v": "1-21-4712-1900-1"})
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Veículo não encontrado", status_code=404)


class CompareTests(TestCase):
    def setUp(self):
        self.vehicles = []
        for index in range(5):
            model_year = build_vehicle(model_code=4712 + index, year=2017)
            add_quote(model_year, 2026, 8, f"{90000 + index}.00")
            self.vehicles.append(model_year)
        self.codes = [codes.encode(model_year) for model_year in self.vehicles]

    def test_add_appends_to_the_selection(self):
        response = self.client.get(
            reverse("web:compare"), {"v": self.codes[0], "add": self.codes[1]}
        )
        self.assertEqual(len(response.context["summaries"]), 2)
        self.assertEqual(response.context["selection"], ",".join(self.codes[:2]))

    def test_selection_is_capped(self):
        response = self.client.get(
            reverse("web:compare"), {"v": ",".join(self.codes[:4]), "add": self.codes[4]}
        )
        self.assertEqual(len(response.context["summaries"]), 4)
        self.assertContains(response, "no máximo 4 versões")

    def test_duplicates_are_ignored(self):
        response = self.client.get(
            reverse("web:compare"), {"v": self.codes[0], "add": self.codes[0]}
        )
        self.assertEqual(len(response.context["summaries"]), 1)

    def test_unknown_codes_are_dropped_from_the_selection(self):
        response = self.client.get(
            reverse("web:compare"), {"v": f"{self.codes[0]},1-99-99-1900-1"}
        )
        self.assertEqual(response.context["selection"], self.codes[0])

    def test_removal_link_excludes_only_that_vehicle(self):
        response = self.client.get(reverse("web:compare"), {"v": ",".join(self.codes[:3])})
        summaries = response.context["summaries"]
        self.assertEqual(summaries[1]["without_me"], f"{self.codes[0]},{self.codes[2]}")

    def test_empty_selection_renders(self):
        response = self.client.get(reverse("web:compare"))
        self.assertContains(response, "Nenhuma versão selecionada")

    def test_no_longer_carries_a_picker_of_its_own(self):
        response = self.client.get(reverse("web:compare"))
        self.assertNotContains(response, 'name="add"')
        self.assertContains(response, "Buscar veículos")


class SelectionTrayTests(TestCase):
    """The tray travels in `c=` so a second pick adds instead of replacing.

    The bug this guards against left every "+ comparar" pointing at
    `compare?v=<one code>`: each click threw the previous picks away, so the
    comparison never held more than one version.
    """

    def setUp(self):
        self.years = [build_vehicle(model_code=4712, year=2017 + index) for index in range(5)]
        for index, model_year in enumerate(self.years):
            add_quote(model_year, 2026, 8, f"{90000 + index}.00")
        self.codes = [codes.encode(model_year) for model_year in self.years]
        self.model_code = codes.encode_model(self.years[0].vehicle_model)

    def _model_page(self, tray=()):
        query = {"m": self.model_code}
        if tray:
            query["c"] = ",".join(tray)
        return self.client.get(reverse("web:model"), query)

    def _toggle_url(self, response, code):
        versions = {version.code: version for version in response.context["versions"]}
        return versions[code].toggle_url

    def test_a_second_pick_is_added_not_substituted(self):
        first = self._model_page()
        response = self.client.get(self._toggle_url(first, self.codes[0]))
        self.assertEqual(response.context["selection"], self.codes[0])

        response = self.client.get(self._toggle_url(response, self.codes[1]))
        self.assertEqual(response.context["selection"], ",".join(self.codes[:2]))
        self.assertEqual(response.context["selection_count"], 2)

    def test_picking_keeps_the_reader_on_the_model_page(self):
        response = self.client.get(self._toggle_url(self._model_page(), self.codes[0]))
        self.assertTemplateUsed(response, "web/model.html")

    def test_picking_the_same_version_again_removes_it(self):
        response = self.client.get(self._toggle_url(self._model_page(), self.codes[0]))
        response = self.client.get(self._toggle_url(response, self.codes[0]))
        self.assertEqual(response.context["selection"], "")

    def test_a_full_tray_offers_no_link_for_the_fifth(self):
        response = self._model_page(self.codes[:4])
        self.assertIsNone(self._toggle_url(response, self.codes[4]))
        # Already picked ones stay clickable, or nothing could be removed.
        self.assertIsNotNone(self._toggle_url(response, self.codes[0]))

    def test_the_tray_is_cashed_in_as_the_comparison_querystring(self):
        response = self._model_page(self.codes[:2])
        expected = urlencode({"v": ",".join(self.codes[:2])})
        self.assertContains(response, f"{reverse('web:compare')}?{expected}")

    def test_the_search_carries_the_tray_into_the_model_link(self):
        response = self.client.get(reverse("web:home"), {"c": self.codes[0]})
        self.assertContains(response, f"c={self.codes[0]}")
        self.assertContains(response, 'name="c"')

    def test_the_detail_screen_keeps_its_own_v_and_the_tray_apart(self):
        response = self.client.get(
            reverse("web:detail"), {"v": self.codes[0], "c": self.codes[1]}
        )
        self.assertEqual(response.context["summary"]["model_year"], self.years[0])
        self.assertEqual(response.context["selection"], self.codes[1])
        self.assertFalse(response.context["in_tray"])

        response = self.client.get(response.context["toggle_url"])
        self.assertTemplateUsed(response, "web/detail.html")
        self.assertEqual(response.context["selection"], ",".join([self.codes[1], self.codes[0]]))
        self.assertTrue(response.context["in_tray"])


class CollectionSchedulingTests(TestCase):
    def setUp(self):
        self.uno = build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        self.uno.vehicle_model.name = "Uno Mille Fire"
        self.uno.vehicle_model.save()
        add_quote(self.uno, 2026, 8, "40000.00")

    def test_a_search_with_a_term_schedules_a_collection(self):
        self.client.get(reverse("web:home"), {"q": "mille"})

        request = CollectionRequest.objects.get()
        self.assertEqual(request.term, "mille")
        self.assertEqual(
            list(request.items.values_list("vehicle_model_id", flat=True)),
            [self.uno.vehicle_model.pk],
        )

    def test_a_search_without_a_term_schedules_nothing(self):
        self.client.get(reverse("web:home"))
        self.client.get(reverse("web:home"), {"fuel": "5"})

        self.assertEqual(CollectionRequest.objects.count(), 0)

    def test_a_repeated_search_does_not_schedule_again(self):
        self.client.get(reverse("web:home"), {"q": "mille"})
        self.client.get(reverse("web:home"), {"q": "mille"})

        self.assertEqual(CollectionRequest.objects.count(), 1)

    def test_the_banner_says_scheduled_not_collecting(self):
        # The web app cannot know whether the worker is running; claiming it is
        # would be a lie every time it is not.
        response = self.client.get(reverse("web:home"), {"q": "mille"})

        self.assertContains(response, "agendada")
        self.assertNotContains(response, "Coletando agora")

    def test_no_banner_without_a_collection(self):
        # Not the bare word "histórico": the page tagline already carries it.
        response = self.client.get(reverse("web:home"))
        self.assertNotContains(response, "Coleta de histórico")


class HostileQuerystringTests(TestCase):
    """The screens hold up against a code nobody would type by accident.

    These are the same payloads as web/tests/test_codes.py, one level up: what
    matters here is the status, since an uncaught ValueError in `decode` came
    out as a 500 rather than the "não encontrado" the screens are built to show.
    """

    def setUp(self):
        model_year = build_vehicle()
        add_quote(model_year, 2026, 8, "40000.00")

    def test_a_hostile_version_code_is_not_found_rather_than_a_crash(self):
        for part in ["²", "9" * 5000]:
            with self.subTest(part=part[:8]):
                response = self.client.get(reverse("web:detail"), {"v": f"1-21-{part}-2017-5"})
                self.assertEqual(response.status_code, 404)

    def test_a_hostile_model_code_is_not_found_rather_than_a_crash(self):
        for part in ["²", "9" * 5000]:
            with self.subTest(part=part[:8]):
                response = self.client.get(reverse("web:model"), {"m": f"1-21-{part}"})
                self.assertEqual(response.status_code, 404)

    def test_a_hostile_brand_filter_is_ignored_rather_than_a_crash(self):
        # The brand filter drops a code it cannot read instead of narrowing to
        # nothing, so the cards are still there.
        for part in ["²", "9" * 5000]:
            with self.subTest(part=part[:8]):
                response = self.client.get(reverse("web:home"), {"brand": f"1-{part}"})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "500 Cult")

    def test_a_hostile_code_in_the_comparison_tray_is_skipped(self):
        response = self.client.get(reverse("web:compare"), {"v": f"1-21-{'9' * 5000}-2017-5"})
        self.assertEqual(response.status_code, 200)


class ContentSecurityPolicyTests(TestCase):
    """The policy is only worth anything if the inline scripts carry its nonce.

    A missing nonce is invisible server-side — the page renders, the browser
    refuses to run the script, and the theme silently stops switching. So the
    test is that the header and the markup agree.
    """

    def setUp(self):
        self.model_year = build_vehicle()
        add_quote(self.model_year, 2026, 8, "40000.00")
        add_quote(self.model_year, 2026, 7, "39000.00")

    def policy(self, response):
        return response.headers["Content-Security-Policy"]

    def test_the_header_is_sent(self):
        policy = self.policy(self.client.get(reverse("web:home")))

        self.assertIn("default-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_scripts_are_never_unsafe_inline(self):
        # The one directive where 'unsafe-inline' would give the whole policy
        # away. Style keeps it on purpose; script must not.
        policy = self.policy(self.client.get(reverse("web:home")))
        script = [part for part in policy.split("; ") if part.startswith("script-src")][0]

        self.assertNotIn("unsafe-inline", script)
        self.assertNotIn("unsafe-eval", script)

    def test_every_inline_script_carries_the_nonce_from_the_header(self):
        for url, params in [
            (reverse("web:home"), {}),
            (reverse("web:detail"), {"v": codes.encode(self.model_year)}),
        ]:
            with self.subTest(url=url):
                response = self.client.get(url, params)
                html = response.content.decode()
                nonce = self.policy(response).split("'nonce-")[1].split("'")[0]

                # Every <script> without a src is inline and needs the nonce.
                inline = [
                    tag
                    for tag in html.split("<script")[1:]
                    if "src=" not in tag.split(">")[0]
                    and 'type="application/json"' not in tag.split(">")[0]
                ]
                self.assertTrue(inline)
                for tag in inline:
                    self.assertIn(f'nonce="{nonce}"', tag.split(">")[0])

    def test_the_nonce_changes_between_requests(self):
        first = self.policy(self.client.get(reverse("web:home")))
        second = self.policy(self.client.get(reverse("web:home")))

        self.assertNotEqual(first, second)
