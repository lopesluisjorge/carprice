from django.test import TestCase

from crawler.models import Brand
from crawler.models import VehicleModel

from web import search


class BuildMatchQueryTests(TestCase):
    def test_quotes_each_token_with_a_prefix_star(self):
        self.assertEqual(search.build_match_query("corsa sedan"), '"corsa"* AND "sedan"*')

    def test_drops_single_char_tokens_when_a_longer_one_exists(self):
        # "gol 1.0" tokenizes as gol/1/0; the digits alone are only noise.
        self.assertEqual(search.build_match_query("gol 1.0"), '"gol"*')

    def test_keeps_a_single_char_term_when_it_is_all_there_is(self):
        self.assertEqual(search.build_match_query("c"), '"c"*')

    def test_input_without_any_word_produces_no_query(self):
        for term in ["", "   ", '"', "*", "((", "...", None]:
            with self.subTest(term=term):
                self.assertEqual(search.build_match_query(term), "")


class BuildTsqueryTests(TestCase):
    """The Postgres half of the same contract, token for token.

    Both dialects are built from the same tokenizer, so the two classes assert
    the same rules twice — on purpose: a change to tokenize() that broke one
    engine and not the other would otherwise only show up on the engine that
    happens to be running the suite.
    """

    def test_each_token_gets_a_prefix_match_joined_by_and(self):
        self.assertEqual(search.build_tsquery("corsa sedan"), "corsa:* & sedan:*")

    def test_drops_single_char_tokens_when_a_longer_one_exists(self):
        self.assertEqual(search.build_tsquery("gol 1.0"), "gol:*")

    def test_keeps_a_single_char_term_when_it_is_all_there_is(self):
        self.assertEqual(search.build_tsquery("c"), "c:*")

    def test_input_without_any_word_produces_no_query(self):
        for term in ["", "   ", '"', "*", "((", "...", None]:
            with self.subTest(term=term):
                self.assertEqual(search.build_tsquery(term), "")

    def test_tsquery_operators_never_survive_the_tokenizer(self):
        # & | ! : ( ) and <-> are to tsquery what AND and * are to FTS5.
        for term in ["a & b", "a | b", "!a", "a <-> b", "a:*", "(a)"]:
            with self.subTest(term=term):
                built = search.build_tsquery(term)
                self.assertNotIn("|", built)
                self.assertNotIn("!", built)
                self.assertNotIn("<", built)
                self.assertNotIn("(", built)


class SearchTests(TestCase):
    def setUp(self):
        citroen = Brand.objects.create(fipe_code=13, name="Citroën")
        fiat = Brand.objects.create(fipe_code=21, name="Fiat")
        self.aircross = VehicleModel.objects.create(
            brand=citroen, fipe_code=1, name="AIRCROSS Exclusive 1.6 Flex 16V 5p Aut."
        )
        self.siena = VehicleModel.objects.create(
            brand=fiat, fipe_code=2, name="Grand Siena TETRAFUEL 1.4 Evo F. Flex 8V"
        )

    def test_ignores_accents_in_both_directions(self):
        self.assertEqual(search.search("citroen"), [self.aircross.pk])

    def test_matches_by_prefix(self):
        self.assertEqual(search.search("aircro"), [self.aircross.pk])

    def test_multiple_tokens_are_combined_with_and(self):
        self.assertEqual(search.search("grand tetrafuel"), [self.siena.pk])
        self.assertEqual(search.search("grand aircross"), [])

    def test_no_term_is_not_the_same_as_no_result(self):
        self.assertIsNone(search.search(""))
        self.assertEqual(search.search("zzzzz"), [])

    def test_hostile_input_never_reaches_the_match_syntax(self):
        for term in ['"', "AND", "corsa AND", "*", "-corsa", "((", 'siena"* OR "', "NEAR(a b)"]:
            with self.subTest(term=term):
                search.search(term)  # must not raise OperationalError


class TriggerTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(fipe_code=21, name="Fiat")
        self.model = VehicleModel.objects.create(
            brand=self.brand, fipe_code=1, name="Zumbi Turbo"
        )

    def test_new_model_becomes_searchable(self):
        self.assertEqual(search.search("zumbi"), [self.model.pk])

    def test_renaming_the_model_reindexes_it(self):
        self.model.name = "Fantasma Turbo"
        self.model.save()
        self.assertEqual(search.search("zumbi"), [])
        self.assertEqual(search.search("fantasma"), [self.model.pk])

    def test_renaming_the_brand_reindexes_its_models(self):
        self.brand.name = "Fiat Automóveis"
        self.brand.save()
        self.assertEqual(search.search("automoveis"), [self.model.pk])

    def test_deleting_the_model_removes_it_from_the_index(self):
        self.model.delete()
        self.assertEqual(search.search("zumbi"), [])
