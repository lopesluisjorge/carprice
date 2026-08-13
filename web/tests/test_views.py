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
