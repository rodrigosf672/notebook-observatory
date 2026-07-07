"""Tests for the sampler plan and the GitHub client's rate-limit bookkeeping."""

from __future__ import annotations

import datetime as dt

from notebook_observatory.collectors.sampler import (
    STAR_BUCKETS,
    build_plan,
    iter_search_pages,
)
from notebook_observatory.github_client import RateBucket


def test_plan_is_deterministic() -> None:
    d = dt.date(2026, 7, 7)
    a = [s.name for s in build_plan(d).strategies]
    b = [s.name for s in build_plan(d).strategies]
    assert a == b


def test_plan_covers_all_star_buckets_over_two_days() -> None:
    all_labels = {label for _, label in STAR_BUCKETS}
    base = dt.date(2026, 7, 7)
    for offset in range(6):
        d0 = base + dt.timedelta(days=offset)
        d1 = d0 + dt.timedelta(days=1)
        covered = set()
        for d in (d0, d1):
            for s in build_plan(d).strategies:
                if s.name.startswith("star_bucket"):
                    covered.add(s.stratum.split(":", 1)[1])
        assert covered == all_labels, f"pair {d0}/{d1} missed {all_labels - covered}"


def test_plan_rotates_topics_and_years() -> None:
    d0 = build_plan(dt.date(2026, 7, 7))
    d1 = build_plan(dt.date(2026, 7, 8))
    topic0 = next(s.stratum for s in d0.strategies if s.name == "topic")
    topic1 = next(s.stratum for s in d1.strategies if s.name == "topic")
    assert topic0 != topic1


def test_plan_always_has_core_strategies() -> None:
    plan = build_plan(dt.date(2026, 7, 7))
    names = {s.name for s in plan.strategies}
    assert "recent_push" in names
    assert "created_window" in names
    assert "size_bucket" in names
    assert "topic" in names
    assert sum(1 for n in names if n.startswith("star_bucket")) == 3


def test_iter_search_pages_within_result_cap() -> None:
    plan = build_plan(dt.date(2026, 7, 7))
    strat = plan.strategies[0]
    pages = list(iter_search_pages(strat, pages=3, per_page=50, seed=plan.seed))
    assert len(pages) == 3
    for page, per_page in pages:
        assert 1 <= page <= 1000 // per_page
        assert per_page == 50


def test_rate_bucket_updates_from_headers() -> None:
    b = RateBucket()
    b.update_from_headers(
        {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Reset": "9999999999",
        }
    )
    assert b.limit == 5000
    assert b.remaining == 4999
    assert b.seconds_until_reset() >= 0


def test_rate_bucket_ignores_bad_headers() -> None:
    b = RateBucket(limit=10, remaining=5)
    b.update_from_headers({"X-RateLimit-Remaining": "not-a-number"})
    # Value preserved on parse failure.
    assert b.remaining == 5
