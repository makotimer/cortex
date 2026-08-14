# tests/test_vpn_survey_pool.py
"""The survey must flag exits that leave the configured server pool.

Mining 457 surveyed exits found 18 (4.1%) geolocating to countries absent from
SERVER_COUNTRIES entirely -- Sweden, Germany, Norway, Slovenia, Luxembourg,
Spain, Austria -- against a pool of US/Canada/Switzerland/Netherlands. Their
operators name them: DFRI (171.25.193.x) and Zwiebelfreunde (185.220.101.x) are
Tor exit ranges, i.e. ProtonVPN's Tor-over-VPN servers. The Proton server sits
in an allowed country so gluetun's filter passes it, but egress lands on a Tor
exit somewhere else.

That matters because vpn_client.usable() already recorded one such exit holding
a valid IP while unable to reach the target at all. This flags the class at
collection time instead of requiring someone to notice it in a later analysis.
"""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "vpn_survey", Path(__file__).resolve().parent.parent / "scripts" / "vpn_survey.py")
vpn_survey = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vpn_survey)


@pytest.mark.parametrize("country", ["United States", "Canada", "Netherlands"])
def test_pool_countries_are_not_flagged(country):
    assert vpn_survey.outside_pool(country, vpn_survey.DEFAULT_POOL_COUNTRIES) is False


@pytest.mark.parametrize("country", ["Sweden", "Germany", "Norway", "Austria"])
def test_exits_outside_the_pool_are_flagged(country):
    """These are the observed Tor-over-VPN egress countries."""
    assert vpn_survey.outside_pool(country, vpn_survey.DEFAULT_POOL_COUNTRIES) is True


def test_the_netherlands_is_the_same_country_as_netherlands():
    """ipinfo returned both spellings; a naming variant is not an anomaly.

    8 of 457 records said "The Netherlands" and 39 said "Netherlands". Treating
    the article as a different country would have manufactured a false 2%
    out-of-pool rate on top of the real one.
    """
    assert vpn_survey.outside_pool(
        "The Netherlands", vpn_survey.DEFAULT_POOL_COUNTRIES) is False


def test_unknown_country_is_not_flagged():
    """A switch that never came up has no country -- that is a separate failure.

    Conflating "no IP at all" with "egressed outside the pool" would double-count
    the ~4.5% dead-rotation rate into the out-of-pool number.
    """
    assert vpn_survey.outside_pool(None, vpn_survey.DEFAULT_POOL_COUNTRIES) is False
