# tests/test_career_watch_targets.py
import pytest

from modules.career_watch.lib.scrapers._targets import parse_url_label_list


def test_empty_input():
    assert parse_url_label_list([]) == []
    assert parse_url_label_list(None) == []
    assert parse_url_label_list("not-a-list") == []


def test_pair_list():
    result = parse_url_label_list([["https://example.com/jobs", "lever:acme"]])
    assert result == [("https://example.com/jobs", "lever:acme")]


def test_pair_strips_whitespace():
    result = parse_url_label_list([[" https://x.com ", " lever:x "]])
    assert result == [("https://x.com", "lever:x")]


def test_dict_with_default_url_key():
    result = parse_url_label_list([{"url": "https://a.com", "source": "lever:a"}])
    assert result == [("https://a.com", "lever:a")]


def test_dict_with_fallback_url_keys():
    result = parse_url_label_list([{"list_url": "https://b.com", "source_label": "boeing:b"}])
    assert result == [("https://b.com", "boeing:b")]

    result = parse_url_label_list([{"search_url": "https://c.com", "source": "icims:c"}])
    assert result == [("https://c.com", "icims:c")]


def test_custom_url_keys():
    result = parse_url_label_list(
        [{"api_url": "https://d.com/api", "source": "bae:d"}],
        url_keys=("api_url", "url"),
    )
    assert result == [("https://d.com/api", "bae:d")]


def test_skips_items_with_missing_url():
    result = parse_url_label_list([{"source": "lever:x"}])
    assert result == []


def test_skips_items_with_missing_label():
    result = parse_url_label_list([{"url": "https://x.com"}])
    assert result == []


def test_skips_single_element_list():
    result = parse_url_label_list([["https://x.com"]])
    assert result == []


def test_skips_empty_url_or_label():
    result = parse_url_label_list([["", "lever:x"], ["https://x.com", ""]])
    assert result == []


def test_mixed_pairs_and_dicts():
    raw = [
        ["https://a.com", "lever:a"],
        {"url": "https://b.com", "source": "lever:b"},
    ]
    result = parse_url_label_list(raw)
    assert result == [("https://a.com", "lever:a"), ("https://b.com", "lever:b")]


def test_multiple_entries_returned_in_order():
    raw = [
        ["https://first.com", "first"],
        ["https://second.com", "second"],
        ["https://third.com", "third"],
    ]
    result = parse_url_label_list(raw)
    assert [label for _, label in result] == ["first", "second", "third"]
