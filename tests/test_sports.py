"""Tests for sports-only market selection and the single-market override.

All pure: the Gamma fetcher is injected, env is monkeypatched, and a fixed `now`
is passed in -- no network and no database.
"""
from datetime import datetime, timedelta, timezone

import pytest

from data_updater.sports import (
    SPORTS,
    build_sport_index,
    enabled_sports,
    ends_within,
    has_dated_slug,
    horizon_hours,
    is_today_or_live,
    keep_event,
    keep_market,
)
from data_updater.single_market import load_single_market_restriction

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _iso(hours_from_now):
    return (NOW + timedelta(hours=hours_from_now)).isoformat().replace("+00:00", "Z")


def _event(slug, *, title="Team A vs Team B", live=None, ended=None, end_in_hours=2.0, markets=None):
    e = {"slug": slug, "title": title, "endDate": _iso(end_in_hours), "markets": markets or []}
    if live is not None:
        e["live"] = live
    if ended is not None:
        e["ended"] = ended
    return e


def _mkt(cid, mtype="moneyline"):
    return {"conditionId": cid, "sportsMarketType": mtype}


def _fetcher(by_tag):
    def f(tag, *, client=None):
        return iter(by_tag.get(tag, []))

    return f


# --------------------------------------------------------------------------- #
# dated-slug discriminator (the futures excluder)
# --------------------------------------------------------------------------- #
def test_has_dated_slug_true_for_game():
    assert has_dated_slug({"slug": "mlb-tb-nyy-2026-05-23"}) is True
    assert has_dated_slug({"slug": "cs2-aaa-inf1-2026-03-10"}) is True


def test_has_dated_slug_false_for_futures():
    assert has_dated_slug({"slug": "world-cup-winner"}) is False
    assert has_dated_slug({"slug": "2026-nba-champion"}) is False  # year only, no full date
    assert has_dated_slug({"slug": "nhl-2025-26-hart-memorial-trophy"}) is False
    assert has_dated_slug({"slug": "mlb-world-series-champion-2026"}) is False


def test_has_dated_slug_missing():
    assert has_dated_slug({}) is False
    assert has_dated_slug({"slug": None}) is False


# --------------------------------------------------------------------------- #
# today / live window
# --------------------------------------------------------------------------- #
def test_today_or_live_when_live():
    assert is_today_or_live({"live": True, "endDate": _iso(10000)}, 36, now=NOW) is True


def test_today_or_live_excludes_ended():
    assert is_today_or_live({"ended": True, "live": True}, 36, now=NOW) is False


def test_today_or_live_within_horizon():
    assert is_today_or_live(_event("x", end_in_hours=10), 36, now=NOW) is True


def test_today_or_live_beyond_horizon():
    assert is_today_or_live(_event("x", end_in_hours=200), 36, now=NOW) is False


def test_today_or_live_past_enddate_not_live():
    assert is_today_or_live(_event("x", end_in_hours=-5, live=False), 36, now=NOW) is False


def test_ends_within_missing_enddate():
    assert ends_within({}, 36, now=NOW) is False


# --------------------------------------------------------------------------- #
# per-sport market-type allow-lists
# --------------------------------------------------------------------------- #
def test_keep_market_soccer_allows_main_lines_and_missing():
    s = SPORTS["soccer"]
    assert keep_market(s, _mkt("c", "moneyline"))
    assert keep_market(s, _mkt("c", "totals"))
    assert keep_market(s, _mkt("c", "both_teams_to_score"))
    assert keep_market(s, _mkt("c", None))  # missing type kept (matches source)
    assert not keep_market(s, _mkt("c", "spreads"))


def test_keep_market_tennis_moneyline_only():
    s = SPORTS["tennis"]
    assert keep_market(s, _mkt("c", "moneyline"))
    assert not keep_market(s, _mkt("c", None))  # missing dropped
    assert not keep_market(s, _mkt("c", "tennis_match_totals"))


@pytest.mark.parametrize("tag", ["nba", "nhl", "mlb"])
def test_keep_market_ball_sports(tag):
    s = SPORTS[tag]
    assert keep_market(s, _mkt("c", "moneyline"))
    assert keep_market(s, _mkt("c", "totals"))
    assert keep_market(s, _mkt("c", None))
    assert not keep_market(s, _mkt("c", "spreads"))


def test_keep_market_esports_moneyline_only():
    s = SPORTS["esports"]
    assert keep_market(s, _mkt("c", "moneyline"))
    assert not keep_market(s, _mkt("c", None))
    assert not keep_market(s, _mkt("c", "map_handicap"))


# --------------------------------------------------------------------------- #
# event-level refinements
# --------------------------------------------------------------------------- #
def test_keep_event_requires_dated_slug():
    s = SPORTS["nba"]
    assert keep_event(s, _event("nba-nyk-sas-2026-06-01"), 36, now=NOW)
    assert not keep_event(s, _event("2026-nba-champion"), 36, now=NOW)


def test_soccer_excludes_winner_title():
    s = SPORTS["soccer"]
    assert keep_event(s, _event("epl-ars-che-2026-06-01", title="Arsenal vs Chelsea"), 36, now=NOW)
    assert not keep_event(s, _event("epl-ars-che-2026-06-01", title="Premier League Winner"), 36, now=NOW)


def test_tennis_excludes_doubles():
    s = SPORTS["tennis"]
    assert keep_event(s, _event("atp-zhou-kotov-2026-06-01"), 36, now=NOW)
    assert not keep_event(s, _event("atp-doubles-kielpau-bassgen-2026-06-01"), 36, now=NOW)


def test_esports_only_cs2_and_lol():
    s = SPORTS["esports"]
    assert keep_event(s, _event("cs2-a-b-2026-06-01"), 36, now=NOW)
    assert keep_event(s, _event("lol-a-b-2026-06-01"), 36, now=NOW)
    assert not keep_event(s, _event("dota2-a-b-2026-06-01"), 36, now=NOW)
    assert not keep_event(s, _event("val-nrg-sen-2026-06-01"), 36, now=NOW)


def test_keep_event_excludes_far_future_game():
    s = SPORTS["nba"]
    assert not keep_event(s, _event("nba-nyk-sas-2026-09-01", end_in_hours=200), 36, now=NOW)


# --------------------------------------------------------------------------- #
# build_sport_index (the membership oracle)
# --------------------------------------------------------------------------- #
def test_build_sport_index_filters_and_labels():
    by_tag = {
        "soccer": [
            _event(
                "epl-ars-che-2026-06-01",
                title="Arsenal vs Chelsea",
                markets=[_mkt("0xS1", "moneyline"), _mkt("0xS2", "spreads")],
            ),
            _event("world-cup-winner", title="World Cup Winner", markets=[_mkt("0xFUT", "moneyline")]),
        ],
        "tennis": [
            _event("atp-zhou-kotov-2026-06-01", markets=[_mkt("0xT1", "moneyline"), _mkt("0xT2", None)]),
            _event("atp-doubles-x-y-2026-06-01", markets=[_mkt("0xD1", "moneyline")]),
        ],
        "esports": [
            _event("cs2-a-b-2026-06-01", markets=[_mkt("0xE1", "moneyline")]),
            _event("dota2-a-b-2026-06-01", markets=[_mkt("0xE2", "moneyline")]),
        ],
    }
    specs = [SPORTS["soccer"], SPORTS["tennis"], SPORTS["esports"]]
    idx = build_sport_index(specs, horizon=36, now=NOW, fetcher=_fetcher(by_tag))
    # 0xS2 (spreads), 0xFUT (futures), 0xT2 (missing type), doubles, dota2 all excluded.
    assert idx == {"0xs1": "soccer", "0xt1": "tennis", "0xe1": "esports"}


def test_build_sport_index_isolates_one_sport_failure():
    def f(tag, *, client=None):
        if tag == "tennis":
            raise RuntimeError("gamma down")
        data = {"soccer": [_event("epl-a-b-2026-06-01", title="A vs B", markets=[_mkt("0xS1")])]}
        return iter(data.get(tag, []))

    idx = build_sport_index([SPORTS["soccer"], SPORTS["tennis"]], horizon=36, now=NOW, fetcher=f)
    assert idx == {"0xs1": "soccer"}


# --------------------------------------------------------------------------- #
# config helpers
# --------------------------------------------------------------------------- #
def test_enabled_sports_default(monkeypatch):
    monkeypatch.delenv("SPORTS_TAG_SLUGS", raising=False)
    assert {s.tag for s in enabled_sports()} == set(SPORTS)


def test_enabled_sports_subset(monkeypatch):
    monkeypatch.setenv("SPORTS_TAG_SLUGS", "soccer, mlb")
    assert {s.tag for s in enabled_sports()} == {"soccer", "mlb"}


def test_horizon_hours_default(monkeypatch):
    monkeypatch.delenv("SPORTS_HORIZON_HOURS", raising=False)
    assert horizon_hours() == 36.0


def test_horizon_hours_env(monkeypatch):
    monkeypatch.setenv("SPORTS_HORIZON_HOURS", "12")
    assert horizon_hours() == 12.0


# --------------------------------------------------------------------------- #
# single-market override
# --------------------------------------------------------------------------- #
def test_single_market_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SINGLE_MARKET_ENABLED", raising=False)
    assert load_single_market_restriction() is None


def test_single_market_normalizes_and_lowercases(monkeypatch):
    cid = "0x" + "a" * 64
    monkeypatch.setenv("SINGLE_MARKET_ENABLED", "true")
    # Uppercase hex digits (real condition ids keep the lowercase "0x" prefix).
    monkeypatch.setenv("SINGLE_MARKET_CONDITION_IDS", "0x" + "A" * 64)
    assert load_single_market_restriction() == frozenset({cid})


def test_single_market_multiple_ids(monkeypatch):
    a = "0x" + "a" * 64
    b = "0x" + "b" * 64
    monkeypatch.setenv("SINGLE_MARKET_ENABLED", "true")
    monkeypatch.setenv("SINGLE_MARKET_CONDITION_IDS", f"{a}, {b}")
    assert load_single_market_restriction() == frozenset({a, b})


def test_single_market_enabled_without_ids_raises(monkeypatch):
    monkeypatch.setenv("SINGLE_MARKET_ENABLED", "true")
    monkeypatch.delenv("SINGLE_MARKET_CONDITION_IDS", raising=False)
    with pytest.raises(RuntimeError, match="not set"):
        load_single_market_restriction()


def test_single_market_bad_id_raises(monkeypatch):
    monkeypatch.setenv("SINGLE_MARKET_ENABLED", "true")
    monkeypatch.setenv("SINGLE_MARKET_CONDITION_IDS", "0x123")
    with pytest.raises(RuntimeError, match="64-hex"):
        load_single_market_restriction()
