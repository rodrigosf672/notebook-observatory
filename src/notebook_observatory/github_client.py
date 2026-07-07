"""A rate-limited, retrying, caching client for the GitHub REST API.

The client is deliberately small and dependency-light. It focuses on the three
endpoints the observatory needs — repository search, git trees, and raw file
contents — while handling the operational realities of the GitHub API:

* **Authentication** via a personal access token (or anonymous, at much lower
  limits).
* **Rate limiting.** GitHub returns ``X-RateLimit-Remaining`` / ``-Reset`` on
  every response and a separate bucket for the Search API. The client tracks
  both, proactively sleeps when a bucket is exhausted, and honors
  ``Retry-After`` on secondary-limit (403/429) responses.
* **Retries** on transient 5xx / connection errors with exponential backoff
  (via :mod:`tenacity`).
* **Conditional requests.** Responses are cached on disk keyed by URL; stored
  ``ETag`` values are replayed as ``If-None-Match`` so a ``304 Not Modified``
  costs no rate-limit quota and returns the cached body.
* **Budget accounting.** Every call increments per-run counters so a collection
  run can be capped well within the hourly quota.

The client is intentionally synchronous: the collection budget is small enough
that concurrency would mostly serve to trip GitHub's abuse detection.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import CLIENT, PATHS, ClientConfig, github_token
from .logging_utils import get_logger

logger = get_logger(__name__)

API_ROOT = "https://api.github.com"


class GitHubError(RuntimeError):
    """Raised for non-retryable GitHub API failures (4xx other than rate limit)."""

    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"GitHub API {status} for {url}: {message}")
        self.status = status
        self.url = url


class RateLimitExhausted(RuntimeError):
    """Raised when a rate-limit bucket is exhausted and sleeping is disabled."""


class _Transient(RuntimeError):
    """Internal marker for retryable transport/server errors."""


@dataclass
class RateBucket:
    """Tracks one GitHub rate-limit bucket (core or search)."""

    limit: int = 0
    remaining: int = 1
    reset_epoch: float = 0.0

    def update_from_headers(self, headers: Mapping[str, str]) -> None:
        try:
            self.limit = int(headers.get("X-RateLimit-Limit", self.limit))
            self.remaining = int(headers.get("X-RateLimit-Remaining", self.remaining))
            self.reset_epoch = float(headers.get("X-RateLimit-Reset", self.reset_epoch))
        except (TypeError, ValueError):
            pass

    def seconds_until_reset(self) -> float:
        return max(0.0, self.reset_epoch - time.time())


@dataclass
class ClientStats:
    """Per-run accounting of requests spent."""

    core_requests: int = 0
    search_requests: int = 0
    cache_hits: int = 0
    conditional_not_modified: int = 0
    retries: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "core_requests": self.core_requests,
            "search_requests": self.search_requests,
            "cache_hits": self.cache_hits,
            "conditional_not_modified": self.conditional_not_modified,
            "retries": self.retries,
        }


class _DiskCache:
    """A tiny on-disk cache mapping URL -> {etag, body, headers}.

    Bodies are stored as JSON where possible, or base64-free raw text otherwise.
    The cache is best-effort: any read/write error degrades to a cache miss.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.json"

    def get(self, url: str) -> dict[str, Any] | None:
        p = self._path(url)
        if not p.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, url: str, etag: str | None, body: Any) -> None:
        try:
            self._path(url).write_text(json.dumps({"etag": etag, "body": body}), encoding="utf-8")
        except OSError:
            logger.debug("cache write failed for %s", url)


class GitHubClient:
    """Synchronous GitHub REST client with rate limiting, retries, and caching.

    Example:
        >>> client = GitHubClient()
        >>> result = client.search_repositories("language:Jupyter+Notebook", per_page=5)
        >>> len(result["items"])
        5
    """

    def __init__(
        self,
        token: str | None = None,
        config: ClientConfig | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.config = config or CLIENT
        self.token = token if token is not None else github_token()
        self.session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self.config.user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            logger.warning(
                "No GitHub token found; using anonymous access (60 req/hour). "
                "Set GITHUB_TOKEN for production collection."
            )
        self.session.headers.update(headers)

        self.core = RateBucket()
        self.search = RateBucket()
        self.stats = ClientStats()
        self._cache = (
            _DiskCache(cache_dir or (PATHS.cache / "http")) if self.config.use_cache else None
        )

    # ------------------------------------------------------------------ #
    # Rate-limit management
    # ------------------------------------------------------------------ #
    def _bucket_for(self, url: str) -> RateBucket:
        return self.search if "/search/" in url else self.core

    def _wait_if_needed(self, url: str) -> None:
        bucket = self._bucket_for(url)
        if bucket.remaining > 0:
            return
        sleep_for = bucket.seconds_until_reset() + 1.0
        if sleep_for <= 0:
            return
        if not self.config.respect_rate_limit:
            raise RateLimitExhausted(
                f"Rate limit exhausted for {url}; reset in {sleep_for:.0f}s "
                "(respect_rate_limit disabled)."
            )
        logger.info("Rate-limit bucket empty; sleeping %.0fs until reset.", sleep_for)
        time.sleep(sleep_for)

    # ------------------------------------------------------------------ #
    # Core request path
    # ------------------------------------------------------------------ #
    @retry(
        retry=retry_if_exception_type(_Transient),
        stop=stop_after_attempt(CLIENT.max_retries),
        wait=wait_exponential(multiplier=1.5, min=2, max=60),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
        allow_cache: bool = True,
    ) -> tuple[int, Any, dict[str, str]]:
        self._wait_if_needed(url)

        cached = self._cache.get(url) if (self._cache and allow_cache and not params) else None
        req_headers: dict[str, str] = {}
        if accept:
            req_headers["Accept"] = accept
        if cached and cached.get("etag"):
            req_headers["If-None-Match"] = cached["etag"]

        try:
            resp = self.session.request(
                method,
                url,
                params=params,
                headers=req_headers or None,
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            self.stats.retries += 1
            raise _Transient(f"transport error for {url}: {exc}") from exc

        bucket = self._bucket_for(url)
        bucket.update_from_headers(resp.headers)
        if "/search/" in url:
            self.stats.search_requests += 1
        else:
            self.stats.core_requests += 1

        # 304: serve from cache, no quota consumed for content.
        if resp.status_code == 304 and cached is not None:
            self.stats.conditional_not_modified += 1
            return 200, cached["body"], dict(resp.headers)

        # Secondary rate limit / abuse detection.
        if resp.status_code in (403, 429):
            retry_after = resp.headers.get("Retry-After")
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if retry_after or remaining == "0":
                wait_s = float(retry_after) if retry_after else bucket.seconds_until_reset() + 1
                if not self.config.respect_rate_limit:
                    raise RateLimitExhausted(f"secondary limit hit for {url}")
                logger.warning("Secondary/precise rate limit on %s; sleeping %.0fs.", url, wait_s)
                time.sleep(max(1.0, wait_s))
                self.stats.retries += 1
                raise _Transient(f"rate limited, retrying {url}")

        if 500 <= resp.status_code < 600:
            self.stats.retries += 1
            raise _Transient(f"server error {resp.status_code} for {url}")

        if resp.status_code >= 400:
            message = _safe_message(resp)
            raise GitHubError(resp.status_code, message, url)

        body = _parse_body(resp, accept)
        if self._cache and allow_cache and not params and method == "GET":
            self._cache.put(url, resp.headers.get("ETag"), body)
        return resp.status_code, body, dict(resp.headers)

    def get(self, path_or_url: str, **kwargs: Any) -> Any:
        """GET a path (``/repos/...``) or absolute URL; return the parsed body."""
        url = path_or_url if path_or_url.startswith("http") else f"{API_ROOT}{path_or_url}"
        _, body, _ = self._request("GET", url, **kwargs)
        return body

    # ------------------------------------------------------------------ #
    # High-level endpoints
    # ------------------------------------------------------------------ #
    def search_repositories(
        self,
        query: str,
        *,
        sort: str | None = None,
        order: str = "desc",
        per_page: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search repositories. See GitHub's search syntax for ``query``.

        Returns the raw search response (``total_count``, ``items``, ...).
        """
        params: dict[str, Any] = {"q": query, "per_page": per_page, "page": page, "order": order}
        if sort:
            params["sort"] = sort
        _, body, _ = self._request("GET", f"{API_ROOT}/search/repositories", params=params)
        return body  # type: ignore[no-any-return]

    def search_code(self, query: str, *, per_page: int = 50, page: int = 1) -> dict[str, Any]:
        """Search code (used sparingly; the code-search bucket is very small)."""
        params = {"q": query, "per_page": per_page, "page": page}
        _, body, _ = self._request("GET", f"{API_ROOT}/search/code", params=params)
        return body  # type: ignore[no-any-return]

    def get_repo(self, full_name: str) -> dict[str, Any]:
        """Fetch repository metadata for ``owner/name``."""
        return self.get(f"/repos/{full_name}")  # type: ignore[no-any-return]

    def get_tree(self, full_name: str, ref: str, *, recursive: bool = True) -> dict[str, Any]:
        """Fetch the git tree for a ref. With ``recursive`` the full tree is returned.

        The response includes a ``truncated`` flag when the tree is too large;
        callers should treat a truncated tree as a best-effort listing.
        """
        suffix = "?recursive=1" if recursive else ""
        return self.get(f"/repos/{full_name}/git/trees/{ref}{suffix}")  # type: ignore[no-any-return]

    def get_raw_file(self, full_name: str, path: str, ref: str) -> str:
        """Download a file's raw text via the contents API (raw media type)."""
        # Use the raw accept header so we get bytes directly, not base64 JSON.
        url = f"{API_ROOT}/repos/{full_name}/contents/{path}?ref={ref}"
        _, body, _ = self._request(
            "GET", url, accept="application/vnd.github.raw+json", allow_cache=True
        )
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="replace")
        return str(body)

    def rate_limit_snapshot(self) -> dict[str, Any]:
        """Return current known rate-limit state (does not spend quota if cached)."""
        return {
            "core": {"remaining": self.core.remaining, "limit": self.core.limit},
            "search": {"remaining": self.search.remaining, "limit": self.search.limit},
            "stats": self.stats.as_dict(),
        }


def _safe_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return str(data.get("message", resp.text[:200]))
    except (ValueError, AttributeError):
        return resp.text[:200]


def _parse_body(resp: requests.Response, accept: str | None) -> Any:
    if accept and "raw" in accept:
        return resp.content.decode("utf-8", errors="replace")
    ctype = resp.headers.get("Content-Type", "")
    if "application/json" in ctype or "+json" in ctype:
        try:
            return resp.json()
        except ValueError:
            return resp.text
    return resp.text
