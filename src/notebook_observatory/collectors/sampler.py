"""Diverse repository sampling strategy.

Goals
-----
The observatory must observe a *different, diverse* slice of the ecosystem each
day so that (a) the same notebooks are not re-collected every run and (b) the
accumulated longitudinal record is not systematically biased toward, say, only
the most-starred repositories.

Why sampling is necessary
--------------------------
There is no API that enumerates "all public notebooks". The GitHub Search API
caps any single query at 1000 returnable results and imposes a small per-minute
budget. We therefore treat each day's collection as a *stratified sample* drawn
from several complementary strata (search queries), and record which strata were
used so downstream analysis can weight or segment accordingly.

Strategy design
---------------
Each :class:`SamplingStrategy` is one GitHub search query template targeting a
different stratum of the ecosystem:

* **recent_push** — repositories pushed to very recently (activity frontier).
* **star_bucket** — repositories within a star range (popularity strata, from
  long-tail 1–10 stars up to 1000+), which counters the head-heavy bias of a
  naive "most starred" query.
* **created_window** — repositories created in a historical window (age strata).
* **size_bucket** — repositories filtered by repo size (proxy for project
  scale).
* **topic** — repositories carrying a rotating ecosystem topic
  (``data-science``, ``machine-learning``, ``jupyter-notebook`` ...).

All strategies are scoped to ``language:"Jupyter Notebook"``. The set of
strategies *active on a given day* is chosen deterministically from the run
date, so the schedule is reproducible and auditable, yet rotates over time. A
per-day random seed (date + configurable offset) shuffles page offsets and star
sub-ranges so repeated runs of the same strategy still reach different repos.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..logging_utils import get_logger

logger = get_logger(__name__)

LANG_FILTER = 'language:"Jupyter Notebook"'

# Star buckets spanning the full popularity distribution. The long tail
# (1..10 stars) is by far the largest stratum in reality, so it is included
# on most days; the head is sampled less frequently to avoid over-representing
# a handful of famous tutorials.
STAR_BUCKETS: list[tuple[str, str]] = [
    ("stars:1..5", "long_tail"),
    ("stars:6..25", "small"),
    ("stars:26..100", "medium"),
    ("stars:101..500", "popular"),
    ("stars:501..2000", "very_popular"),
    ("stars:>2000", "head"),
]

ROTATING_TOPICS: list[str] = [
    "data-science",
    "machine-learning",
    "deep-learning",
    "data-analysis",
    "data-visualization",
    "tutorial",
    "research",
    "physics",
    "bioinformatics",
    "finance",
    "nlp",
    "computer-vision",
    "statistics",
    "education",
]


@dataclass(frozen=True)
class SamplingStrategy:
    """One search-based stratum.

    Attributes:
        name: Machine-readable strategy id (recorded on every observation).
        stratum: Human-readable stratum label for analysis grouping.
        query: The full GitHub search query string.
        sort: Optional sort field (``updated``/``stars``/None for best-match).
        order: Sort order when ``sort`` is set.
    """

    name: str
    stratum: str
    query: str
    sort: str | None = None
    order: str = "desc"


@dataclass
class SamplingPlan:
    """The concrete set of strategies to execute for one run date."""

    run_date: dt.date
    seed: int
    strategies: list[SamplingStrategy] = field(default_factory=list)

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": s.name, "stratum": s.stratum, "query": s.query, "sort": s.sort or "best_match"}
            for s in self.strategies
        ]


def _date_seed(run_date: dt.date, offset: int) -> int:
    """Deterministic integer seed derived from the run date and an offset."""
    digest = hashlib.sha256(f"{run_date.isoformat()}:{offset}".encode()).hexdigest()
    return int(digest[:8], 16)


def _rotating_index(run_date: dt.date, modulus: int) -> int:
    """A stable rotating index in ``[0, modulus)`` derived from the ordinal date."""
    return run_date.toordinal() % modulus


def build_plan(run_date: dt.date | None = None, seed_offset: int = 0) -> SamplingPlan:
    """Construct the day's sampling plan.

    The plan always includes:

    * three star buckets (rotated so all six are covered over two days),
    * one recent-push stratum,
    * one created-window stratum (age stratification),
    * one rotating-topic stratum,
    * one size-bucket stratum.

    Args:
        run_date: The date to plan for; defaults to today (UTC).
        seed_offset: Extra entropy so multiple runs on the same date differ.

    Returns:
        A :class:`SamplingPlan`.
    """
    run_date = run_date or dt.datetime.now(dt.UTC).date()
    seed = _date_seed(run_date, seed_offset)
    rng = random.Random(seed)

    strategies: list[SamplingStrategy] = []

    # --- Star buckets: cover all 6 buckets across every 2 consecutive days. ---
    # Take a disjoint block of 3 buckets per day: even days -> first half,
    # odd days -> second half. Consecutive days therefore union to all 6.
    half = len(STAR_BUCKETS) // 2  # 3
    block = run_date.toordinal() % 2  # 0 or 1
    chosen_buckets = STAR_BUCKETS[block * half : block * half + half]
    for star_q, label in chosen_buckets:
        strategies.append(
            SamplingStrategy(
                name=f"star_bucket_{label}",
                stratum=f"stars:{label}",
                query=f"{LANG_FILTER} {star_q}",
                sort="stars",
                order="desc",
            )
        )

    # --- Recent push frontier: repos pushed in the last ~2 days. ---
    since = (run_date - dt.timedelta(days=2)).isoformat()
    strategies.append(
        SamplingStrategy(
            name="recent_push",
            stratum="activity:recent_push",
            query=f"{LANG_FILTER} pushed:>{since} stars:>0",
            sort="updated",
            order="desc",
        )
    )

    # --- Age stratification: a historical creation window that walks backward. ---
    # Pick a year in the notebook era, rotating across the project's history.
    years = list(range(2015, run_date.year + 1))
    year = years[_rotating_index(run_date, len(years))]
    strategies.append(
        SamplingStrategy(
            name="created_window",
            stratum=f"age:created_{year}",
            query=f"{LANG_FILTER} created:{year}-01-01..{year}-12-31 stars:>2",
            sort=None,
        )
    )

    # --- Rotating topic. ---
    topic = ROTATING_TOPICS[_rotating_index(run_date, len(ROTATING_TOPICS))]
    strategies.append(
        SamplingStrategy(
            name="topic",
            stratum=f"topic:{topic}",
            query=f"{LANG_FILTER} topic:{topic} stars:>1",
            sort=None,
        )
    )

    # --- Size bucket (KB): a rotating repo-size stratum. ---
    size_ranges = ["size:<500", "size:500..5000", "size:5000..50000", "size:>50000"]
    size_q = size_ranges[_rotating_index(run_date, len(size_ranges))]
    strategies.append(
        SamplingStrategy(
            name="size_bucket",
            stratum=f"repo_size:{size_q}",
            query=f"{LANG_FILTER} {size_q} stars:>0",
            sort=None,
        )
    )

    # Shuffle so no stratum is systematically served first (which matters when
    # the request budget runs out mid-run).
    rng.shuffle(strategies)

    plan = SamplingPlan(run_date=run_date, seed=seed, strategies=strategies)
    logger.info(
        "Sampling plan for %s (seed=%d): %s",
        run_date.isoformat(),
        seed,
        [s.name for s in strategies],
    )
    return plan


# Earliest creation year with enough public notebooks to sample meaningfully.
# GitHub's Jupyter-Notebook population is negligible before 2013.
EARLIEST_COHORT_YEAR = 2013

# Star buckets used within a single creation-year cohort. Kept coarse (three
# bands) so each band still returns results even for sparse early years.
COHORT_STAR_BUCKETS: list[tuple[str, str]] = [
    ("stars:>50", "notable"),
    ("stars:5..50", "mid"),
    ("stars:0..4", "long_tail"),
]


def build_cohort_plan(year: int, seed_offset: int = 0) -> SamplingPlan:
    """Build a sampling plan for a single **creation-year cohort**.

    Unlike :func:`build_plan` (which samples the *current* ecosystem for a daily
    census), this plan samples repositories **created in a specific year**,
    stratified by popularity and topic within that year. It powers the
    historical backfill: running it for 2013..present yields one cohort per year.

    Important: GitHub serves the *current* content of each notebook, so a cohort
    measures "notebooks created in year *Y* as they exist today", not a
    historical snapshot of that year. See ``docs/METHODOLOGY.md``.

    Args:
        year: Repository creation year to sample.
        seed_offset: Extra entropy so repeated cohort runs differ.

    Returns:
        A :class:`SamplingPlan` whose ``run_date`` is ``year-01-01`` (used as the
        cohort's key in the longitudinal store).
    """
    cohort_date = dt.date(year, 1, 1)
    seed = _date_seed(cohort_date, seed_offset)
    rng = random.Random(seed)
    created = f"created:{year}-01-01..{year}-12-31"

    strategies: list[SamplingStrategy] = []

    # Popularity strata within the year.
    for star_q, label in COHORT_STAR_BUCKETS:
        strategies.append(
            SamplingStrategy(
                name=f"cohort_star_{label}",
                stratum=f"stars:{label}",
                query=f"{LANG_FILTER} {created} {star_q}",
                sort="stars",
                order="desc",
            )
        )

    # Two rotating topics for within-year diversity (rotation keyed on the year
    # so different cohorts probe different topics but each is reproducible).
    for k in range(2):
        topic = ROTATING_TOPICS[(year * 2 + k) % len(ROTATING_TOPICS)]
        strategies.append(
            SamplingStrategy(
                name=f"cohort_topic_{k}",
                stratum=f"topic:{topic}",
                query=f"{LANG_FILTER} {created} topic:{topic}",
                sort=None,
            )
        )

    rng.shuffle(strategies)
    plan = SamplingPlan(run_date=cohort_date, seed=seed, strategies=strategies)
    logger.info(
        "Cohort plan for %d (seed=%d): %s",
        year,
        seed,
        [s.name for s in strategies],
    )
    return plan


def iter_search_pages(
    strategy: SamplingStrategy, *, pages: int, per_page: int, seed: int
) -> Iterator[tuple[int, int]]:
    """Yield ``(page, per_page)`` tuples for a strategy.

    Pages are offset by a per-day pseudo-random start so repeated runs of the
    same strategy reach different result windows within GitHub's 1000-result
    cap (``max_page = 1000 // per_page``).

    Args:
        strategy: The strategy being paged.
        pages: How many pages to request.
        per_page: Results per page (GitHub max 100).
        seed: Per-day seed.
    """
    max_page = max(1, 1000 // per_page)
    rng = random.Random(seed ^ hash(strategy.name) & 0xFFFFFFFF)
    start_page = rng.randint(1, max(1, max_page - pages)) if max_page > pages else 1
    for i in range(pages):
        page = min(max_page, start_page + i)
        yield page, per_page
