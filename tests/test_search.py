from __future__ import annotations

import pytest

from shopassist.catalog.search import (
    LocalProductSearch,
    SearchFilters,
    merge_filters,
    parse_query_constraints,
    tokenize,
)

HEADLINE = "waterproof hiking boots under €150 for wide feet"


@pytest.fixture(scope="module")
def engine(catalog):
    return LocalProductSearch(catalog)


def test_parse_headline_query():
    f = parse_query_constraints(HEADLINE)
    assert f.max_price_eur == 150
    assert set(f.attributes) == {"waterproof", "wide_fit"}
    assert f.categories == ("hiking_boots",)


@pytest.mark.parametrize(
    "query, lo, hi",
    [
        ("sneakers between 50 and 100 euros", 50, 100),
        ("boots €80 or less", None, 80),
        ("jacket up to 120 EUR", None, 120),
        ("boots over 200 euro", 200, None),
        ("running shoes for 90", None, None),
    ],
)
def test_parse_prices(query, lo, hi):
    f = parse_query_constraints(query)
    assert (f.min_price_eur, f.max_price_eur) == (lo, hi)


def test_parse_size_and_gender():
    f = parse_query_constraints("women's trail running shoes in size 39")
    assert f.gender == "women" and f.size_eu == "39" and f.categories == ("trail_running_shoes",)


def test_explicit_arguments_win_over_parsed_ones():
    merged = merge_filters(SearchFilters(max_price_eur=120), parse_query_constraints(HEADLINE))
    assert merged.max_price_eur == 120
    assert set(merged.attributes) == {"waterproof", "wide_fit"}


def test_headline_query_returns_only_matching_products(engine):
    filters = parse_query_constraints(HEADLINE)
    result = engine.search(HEADLINE, filters, limit=10)
    ids = {p.id for p in result.products}
    assert ids == {"B0FX000001", "B0FX000005", "B0FX000010", "B0FX000042"}
    for p in result.products:
        assert p.price_eur <= 150 and {"waterproof", "wide_fit"} <= set(p.attributes)
    assert result.relaxed == []


def test_gender_filter_keeps_unisex(engine):
    filters = SearchFilters(max_price_eur=150, categories=("hiking_boots",), attributes=("waterproof", "wide_fit"), gender="women")
    ids = {p.id for p in engine.search("hiking boots", filters, limit=10).products}
    assert "B0FX000001" in ids and "B0FX000005" not in ids and "B0FX000010" in ids


def test_relaxation_is_reported_and_never_touches_price(engine):
    filters = SearchFilters(max_price_eur=100, categories=("hiking_boots",), attributes=("waterproof", "wide_fit"))
    result = engine.search("hiking boots", filters, limit=5)
    assert result.relaxed and "wide fit" in result.relaxed
    assert result.products and all(p.price_eur <= 100 and "waterproof" in p.attributes for p in result.products)


def test_sort_by_price(engine):
    result = engine.search("hiking boots", SearchFilters(categories=("hiking_boots",)), limit=5, sort="price_asc")
    prices = [p.price_eur for p in result.products]
    assert prices == sorted(prices)


def test_tokenize_stems_plurals():
    assert tokenize("Waterproof Hiking Boots") == ["waterproof", "hiking", "boot"]
