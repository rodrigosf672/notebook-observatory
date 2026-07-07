"""Orchestrates a daily collection run.

Pipeline for one run:

1. Build the day's :class:`~notebook_observatory.collectors.sampler.SamplingPlan`.
2. For each strategy, page through repository search results (respecting the
   request budget) to build a candidate repository pool, de-duplicated by
   ``full_name``.
3. For each sampled repository, fetch its git tree and locate ``.ipynb`` files.
4. Sample up to ``max_notebooks_per_repo`` notebooks per repository (diversity
   guard) and download their raw content.
5. Emit :class:`CollectedNotebook` records carrying the raw notebook JSON plus
   the provenance (which repo, which stratum, stars, etc.).

The collector never parses notebook *content* — that is the parser's job. It is
strictly concerned with sampling and retrieval, and with staying inside the
per-run request budget.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import COLLECTION, CollectionConfig
from ..github_client import GitHubClient, GitHubError
from ..logging_utils import get_logger
from .sampler import SamplingPlan, build_plan, iter_search_pages

logger = get_logger(__name__)


@dataclass
class RepoRef:
    """A sampled repository with the provenance needed for weighting."""

    full_name: str
    default_branch: str
    stars: int
    size_kb: int
    created_at: str
    pushed_at: str
    strategy: str
    stratum: str
    topics: list[str] = field(default_factory=list)


@dataclass
class CollectedNotebook:
    """A downloaded notebook plus provenance. ``raw`` is the undecoded JSON text."""

    repo_full_name: str
    path: str
    ref: str
    size_bytes: int
    raw: str
    repo_stars: int
    repo_size_kb: int
    repo_created_at: str
    repo_pushed_at: str
    strategy: str
    stratum: str

    def provenance(self) -> dict[str, Any]:
        """Provenance dict (everything except the raw payload)."""
        d = asdict(self)
        d.pop("raw")
        return d


@dataclass
class CollectionResult:
    """Outcome of a run: the notebooks plus run-level accounting."""

    run_date: str
    notebooks: list[CollectedNotebook]
    repos_considered: int
    repos_with_notebooks: int
    plan: list[dict[str, str]]
    client_stats: dict[str, int]
    errors: int


class NotebookCollector:
    """Runs the sample → walk → download pipeline within a request budget."""

    def __init__(
        self,
        client: GitHubClient | None = None,
        config: CollectionConfig | None = None,
    ) -> None:
        self.client = client or GitHubClient()
        self.config = config or COLLECTION

    # ------------------------------------------------------------------ #
    def _budget_left(self) -> bool:
        stats = self.client.stats
        if stats.core_requests >= self.config.max_core_requests:
            logger.warning("Core request budget reached (%d).", stats.core_requests)
            return False
        if stats.search_requests >= self.config.max_search_requests:
            logger.warning("Search request budget reached (%d).", stats.search_requests)
            return False
        return True

    def _sample_repositories(self, plan: SamplingPlan) -> list[RepoRef]:
        """Page through each strategy's search results into a deduped repo pool."""
        pool: dict[str, RepoRef] = {}
        # Distribute the repo budget roughly evenly across strategies.
        per_strategy = max(10, self.config.repo_sample_size // max(1, len(plan.strategies)))
        per_page = min(50, per_strategy)
        pages = max(1, per_strategy // per_page)

        for strat in plan.strategies:
            if not self._budget_left():
                break
            for page, pp in iter_search_pages(
                strat, pages=pages, per_page=per_page, seed=plan.seed
            ):
                if not self._budget_left():
                    break
                try:
                    resp = self.client.search_repositories(
                        strat.query, sort=strat.sort, order=strat.order, per_page=pp, page=page
                    )
                except GitHubError as exc:
                    logger.warning("Search failed for %s p%d: %s", strat.name, page, exc)
                    break
                items = resp.get("items", [])
                if not items:
                    break
                for it in items:
                    fn = it["full_name"]
                    if fn in pool:
                        continue
                    pool[fn] = RepoRef(
                        full_name=fn,
                        default_branch=it.get("default_branch", "main"),
                        stars=int(it.get("stargazers_count", 0)),
                        size_kb=int(it.get("size", 0)),
                        created_at=it.get("created_at", ""),
                        pushed_at=it.get("pushed_at", ""),
                        strategy=strat.name,
                        stratum=strat.stratum,
                        topics=it.get("topics", []) or [],
                    )
        logger.info(
            "Sampled %d unique repositories across %d strategies.", len(pool), len(plan.strategies)
        )
        return list(pool.values())

    def _notebooks_in_repo(self, repo: RepoRef, rng: random.Random) -> list[dict[str, Any]]:
        """Return sampled ``.ipynb`` tree entries for one repo (best-effort)."""
        try:
            tree = self.client.get_tree(repo.full_name, repo.default_branch)
        except GitHubError as exc:
            logger.debug("Tree fetch failed for %s: %s", repo.full_name, exc)
            return []
        entries = [
            t
            for t in tree.get("tree", [])
            if t.get("type") == "blob"
            and t.get("path", "").endswith(".ipynb")
            and ".ipynb_checkpoints/" not in t.get("path", "")
            and int(t.get("size", 0)) <= self.config.max_notebook_bytes
        ]
        if not entries:
            return []
        rng.shuffle(entries)
        return entries[: self.config.max_notebooks_per_repo]

    def collect(self, run_date: dt.date | None = None) -> CollectionResult:
        """Execute a full collection run and return the result."""
        run_date = run_date or dt.datetime.now(dt.UTC).date()
        plan = build_plan(run_date=run_date, seed_offset=self.config.seed)
        rng = random.Random(plan.seed)

        repos = self._sample_repositories(plan)
        rng.shuffle(repos)

        collected: list[CollectedNotebook] = []
        repos_with_nb = 0
        errors = 0

        for repo in repos:
            if len(collected) >= self.config.target_notebooks or not self._budget_left():
                break
            entries = self._notebooks_in_repo(repo, rng)
            if entries:
                repos_with_nb += 1
            for entry in entries:
                if len(collected) >= self.config.target_notebooks or not self._budget_left():
                    break
                path = entry["path"]
                try:
                    raw = self.client.get_raw_file(repo.full_name, path, repo.default_branch)
                except GitHubError as exc:
                    logger.debug("Raw download failed %s:%s: %s", repo.full_name, path, exc)
                    errors += 1
                    continue
                collected.append(
                    CollectedNotebook(
                        repo_full_name=repo.full_name,
                        path=path,
                        ref=repo.default_branch,
                        size_bytes=int(entry.get("size", len(raw))),
                        raw=raw,
                        repo_stars=repo.stars,
                        repo_size_kb=repo.size_kb,
                        repo_created_at=repo.created_at,
                        repo_pushed_at=repo.pushed_at,
                        strategy=repo.strategy,
                        stratum=repo.stratum,
                    )
                )

        logger.info(
            "Collection complete: %d notebooks from %d/%d repos (errors=%d).",
            len(collected),
            repos_with_nb,
            len(repos),
            errors,
        )
        return CollectionResult(
            run_date=run_date.isoformat(),
            notebooks=collected,
            repos_considered=len(repos),
            repos_with_notebooks=repos_with_nb,
            plan=plan.describe(),
            client_stats=self.client.stats.as_dict(),
            errors=errors,
        )
