"""Sports-only market selection for the updater.

Restricts the discovered universe to today/live sports **game** markets — soccer,
singles tennis, NHL, NBA, MLB, and CS2+LoL esports. Ported and adapted from the
sibling project ``fucked-up-bot`` (``src/fucked_up_bot/{polymarket,time_filters,
sports/*}``).

How it fits in: poly-maker discovers markets from the CLOB ``get_sampling_markets()``
feed, which carries reward data but no sport metadata. This module queries the
Polymarket **Gamma API** (which has the sport tags) and returns a
``{condition_id -> sport}`` membership map. ``update_markets.py`` intersects that map
with the reward feed on ``condition_id``, so reward computation and the reward floor
stay exactly as before — this module only decides *which* markets are in-universe.

Two deliberate changes vs. fucked-up-bot:

* The event-level gate is a **dated slug** (``YYYY-MM-DD``) rather than its live-only
  ``is_live_or_recent_start``. A dated slug is what separates an individual game from
  season-long futures (``world-cup-winner``, ``2026-nba-champion``,
  ``mlb-world-series-champion-2026``) — verified across all six sports against the
  live API — so it also admits pre-game markets, which fucked-up-bot's live filter did
  not.
* **Today + live** window: an event is kept only if it is live now OR resolves within
  ``SPORTS_HORIZON_HOURS`` (default 36). Already-ended games are dropped automatically
  because they leave the CLOB reward feed we intersect against.

The per-sport market-type allow-lists and the soccer "winner"/esports ``cs2-``/``lol-``
refinements are kept verbatim from the source; tennis additionally excludes "doubles"
to honour the singles-only requirement.
"""
import logging
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
# Gamma silently caps /events at 100 per page regardless of `limit`, so a short
# page is the signal that we've reached the last one.
PAGE_SIZE = 100
HTTP_TIMEOUT = 30.0

SPORTS_FILTER_ENV = "SPORTS_TAG_SLUGS"
SPORTS_HORIZON_ENV = "SPORTS_HORIZON_HOURS"
DEFAULT_HORIZON_HOURS = 36.0

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WINNER_RE = re.compile(r"\bwinner\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Gamma API client (port of fucked-up-bot/polymarket.py)
# --------------------------------------------------------------------------- #
def fetch_events_page(tag_slug, *, offset=0, limit=PAGE_SIZE, client=None):
    """One page of active, open Gamma events for a sport tag."""
    params = {
        "tag_slug": tag_slug,
        "active": "true",
        "closed": "false",
        "limit": limit,
        "offset": offset,
    }
    getter = client.get if client is not None else httpx.get
    response = getter(GAMMA_EVENTS_URL, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def iter_events(tag_slug, *, client=None) -> Iterator[dict]:
    """Yield every active, open Gamma event for ``tag_slug``, paginating to the end."""
    offset = 0
    while True:
        batch = fetch_events_page(tag_slug, offset=offset, client=client)
        if not batch:
            return
        yield from batch
        if len(batch) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


# --------------------------------------------------------------------------- #
# Event / market predicates
# --------------------------------------------------------------------------- #
def has_dated_slug(event: dict) -> bool:
    """True if the event slug contains a YYYY-MM-DD date (a specific game, not a future)."""
    slug = event.get("slug")
    return isinstance(slug, str) and _DATE_RE.search(slug) is not None


def ends_within(event: dict, hours: float, *, now: Optional[datetime] = None) -> bool:
    """True if the event's endDate is between now and now+hours."""
    raw = event.get("endDate")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        end = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    ref = now if now is not None else datetime.now(timezone.utc)
    return ref <= end <= ref + timedelta(hours=hours)


def is_today_or_live(event: dict, horizon_hours: float, *, now: Optional[datetime] = None) -> bool:
    """Keep a game if it is live now, or resolves within the horizon. Never if ended."""
    if event.get("ended") is True:
        return False
    if event.get("live") is True:
        return True
    return ends_within(event, horizon_hours, now=now)


def _no_winner_title(event: dict) -> bool:
    """Soccer: drop futures whose title says 'winner' (e.g. 'World Cup Winner')."""
    title = event.get("title")
    if not isinstance(title, str) or not title:
        return True
    return _WINNER_RE.search(title) is None


def _singles_slug(event: dict) -> bool:
    """Tennis: keep singles only — drop any event whose slug mentions 'doubles'."""
    slug = event.get("slug")
    return isinstance(slug, str) and "doubles" not in slug.lower()


def _cs_or_lol_slug(event: dict) -> bool:
    """Esports: keep only CS2 and League of Legends matches by slug prefix."""
    slug = event.get("slug")
    return isinstance(slug, str) and slug.startswith(("cs2-", "lol-"))


# --------------------------------------------------------------------------- #
# Per-sport specs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SportSpec:
    tag: str
    allowed_types: frozenset
    keep_missing_type: bool
    event_extra: Optional[Callable[[dict], bool]] = None


SPORTS = {
    "soccer": SportSpec(
        "soccer", frozenset({"moneyline", "totals", "both_teams_to_score"}), True, _no_winner_title
    ),
    "tennis": SportSpec("tennis", frozenset({"moneyline"}), False, _singles_slug),
    "nba": SportSpec("nba", frozenset({"moneyline", "totals"}), True),
    "nhl": SportSpec("nhl", frozenset({"moneyline", "totals"}), True),
    "mlb": SportSpec("mlb", frozenset({"moneyline", "totals"}), True),
    "esports": SportSpec("esports", frozenset({"moneyline"}), False, _cs_or_lol_slug),
}


def keep_event(spec: SportSpec, event: dict, horizon_hours: float, *, now=None) -> bool:
    """Dated-slug game + today/live window + the sport's extra event refinement."""
    if not has_dated_slug(event):
        return False
    if not is_today_or_live(event, horizon_hours, now=now):
        return False
    if spec.event_extra is not None and not spec.event_extra(event):
        return False
    return True


def keep_market(spec: SportSpec, market: dict) -> bool:
    """Allow the sport's market types; a missing type is kept only where the source did."""
    market_type = market.get("sportsMarketType")
    if not market_type:
        return spec.keep_missing_type
    return market_type in spec.allowed_types


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #
def enabled_sports():
    """The SportSpecs to run, filtered by the SPORTS_TAG_SLUGS env (default: all six)."""
    raw = os.environ.get(SPORTS_FILTER_ENV, "").strip()
    if not raw:
        return list(SPORTS.values())
    wanted = {s.strip() for s in raw.split(",") if s.strip()}
    return [spec for tag, spec in SPORTS.items() if tag in wanted]


def horizon_hours() -> float:
    """The today/live horizon in hours from SPORTS_HORIZON_HOURS (default 36)."""
    raw = os.environ.get(SPORTS_HORIZON_ENV, "").strip()
    if not raw:
        return DEFAULT_HORIZON_HOURS
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r, using %s", SPORTS_HORIZON_ENV, raw, DEFAULT_HORIZON_HOURS)
        return DEFAULT_HORIZON_HOURS


# --------------------------------------------------------------------------- #
# The membership oracle
# --------------------------------------------------------------------------- #
def build_sport_index(specs=None, *, horizon=None, now=None, client=None, fetcher=iter_events):
    """Return ``{condition_id_lower: sport_tag}`` for every in-universe sport market.

    ``fetcher(tag, *, client=...)`` is injectable for testing; it defaults to the live
    Gamma ``iter_events``. A failure for one sport is logged and skipped so it can't
    take down the others.
    """
    if specs is None:
        specs = enabled_sports()
    if horizon is None:
        horizon = horizon_hours()

    index: dict = {}
    for spec in specs:
        kept = 0
        try:
            for event in fetcher(spec.tag, client=client):
                if not keep_event(spec, event, horizon, now=now):
                    continue
                for market in event.get("markets") or []:
                    if not keep_market(spec, market):
                        continue
                    cid = market.get("conditionId")
                    if isinstance(cid, str) and cid:
                        index[cid.lower()] = spec.tag
                        kept += 1
        except Exception:
            logger.exception("sport index build failed for %s", spec.tag)
            continue
        logger.info("sport %s: %d market(s) in universe", spec.tag, kept)
    return index
