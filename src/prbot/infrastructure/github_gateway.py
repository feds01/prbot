import logging
import re
import time
from datetime import datetime

import httpx
import jwt

from prbot.domain.exclusions.ports import GitHubUserKind, GitHubUserRef
from prbot.domain.tracking.ports import SourceRateLimitError
from prbot.domain.tracking.status_resolver import resolve_ci_failing
from prbot.domain.tracking.value_objects import CheckRun, PRInfo, PRUrl, Review, ReviewState

_BOT_SUFFIX = "[bot]"
_USER_TYPE_TO_KIND: dict[str, GitHubUserKind] = {
    "User": "user",
    "Bot": "bot",
    "Organization": "organization",
}

_GITHUB_PR_PATTERN = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")

# GitHub's permanent answer when the App simply lacks a permission; re-minting
# the token cannot change it, so it is not worth a retry.
_PERMISSION_DENIED = "Resource not accessible by integration"

logger = logging.getLogger(__name__)


def _is_rate_limited(resp: httpx.Response) -> bool:
    """GitHub answers 403 — not 429 — once the hourly budget is spent."""
    return resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"


def _reset_at(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["x-ratelimit-reset"])
    except KeyError, ValueError:
        return None


def _looks_like_bad_credentials(resp: httpx.Response) -> bool:
    """Whether a fresh installation token might succeed where this one failed.

    A rate-limit 403 must not qualify: the budget belongs to the installation,
    not the token, so retrying on a new one only spends the deficit twice.
    """
    if resp.status_code == 401:
        return True
    if resp.status_code != 403:
        return False
    return not _is_rate_limited(resp) and _PERMISSION_DENIED not in resp.text


class GitHubGateway:
    """Fetches PR data from GitHub REST API using GitHub App installation tokens."""

    def __init__(self, app_id: str, private_key: str) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )
        # Cache: installation_id -> (token, expires_at)
        self._token_cache: dict[int, tuple[str, float]] = {}
        # Cache: owner -> installation_id
        self._installation_cache: dict[str, int] = {}

    def _generate_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def _get_installation_id(self, owner: str) -> int:
        if owner in self._installation_cache:
            return self._installation_cache[owner]

        token = self._generate_jwt()
        resp = await self._client.get(
            f"/orgs/{owner}/installation",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 404:
            # Try as a user installation
            resp = await self._client.get(
                f"/users/{owner}/installation",
                headers={"Authorization": f"Bearer {token}"},
            )
        resp.raise_for_status()
        installation_id: int = resp.json()["id"]
        self._installation_cache[owner] = installation_id
        return installation_id

    async def _get_token(self, owner: str) -> str:
        installation_id = await self._get_installation_id(owner)

        cached = self._token_cache.get(installation_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]

        jwt_token = self._generate_jwt()
        resp = await self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        token: str = data["token"]
        expires_at = datetime.fromisoformat(data["expires_at"]).timestamp()
        self._token_cache[installation_id] = (token, expires_at)
        logger.info("Obtained installation token for %s (installation %d)", owner, installation_id)
        return token

    def _invalidate_token(self, owner: str) -> None:
        """Drop the cached token so the next call mints a fresh one."""
        installation_id = self._installation_cache.get(owner)
        if installation_id is not None:
            self._token_cache.pop(installation_id, None)

    def _log_http_error(self, resp: httpx.Response) -> None:
        """Log what GitHub actually said — the reason only ever lives in the body."""
        logger.warning(
            "GitHub %s %s -> %d | body=%s | ratelimit=%s/%s reset=%s | "
            "retry-after=%s | accepted-perms=%s",
            resp.request.method,
            resp.request.url.path,
            resp.status_code,
            resp.text[:500],
            resp.headers.get("x-ratelimit-remaining"),
            resp.headers.get("x-ratelimit-limit"),
            resp.headers.get("x-ratelimit-reset"),
            resp.headers.get("retry-after"),
            resp.headers.get("x-accepted-github-permissions"),
        )

    async def _authed_get(
        self,
        owner: str,
        path: str,
        params: dict[str, int] | None = None,
        *,
        log_errors: bool = True,
    ) -> httpx.Response:
        """GET with an installation token, re-minting it once if GitHub refuses it.

        Tokens are cached for their full hour, so one the API starts rejecting
        would otherwise poison every request until it expired.
        """
        headers = {"Authorization": f"Bearer {await self._get_token(owner)}"}
        resp = await self._client.get(path, params=params, headers=headers)

        if _looks_like_bad_credentials(resp):
            if log_errors:
                self._log_http_error(resp)
            logger.info("Re-minting token for %s after %d on %s", owner, resp.status_code, path)
            self._invalidate_token(owner)
            headers = {"Authorization": f"Bearer {await self._get_token(owner)}"}
            resp = await self._client.get(path, params=params, headers=headers)

        if resp.is_error and log_errors:
            self._log_http_error(resp)
        if _is_rate_limited(resp):
            raise SourceRateLimitError(reset_at=_reset_at(resp))
        return resp

    def extract_pr_references(self, text: str) -> list[PRUrl]:
        """Extract all GitHub PR URLs from the given text."""
        seen: set[tuple[str, str, int]] = set()
        results: list[PRUrl] = []
        for match in _GITHUB_PR_PATTERN.finditer(text):
            key = (match.group(1), match.group(2), int(match.group(3)))
            if key not in seen:
                seen.add(key)
                results.append(PRUrl(owner=key[0], repo=key[1], number=key[2]))
        return results

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        pr_path = f"/repos/{pr_url.owner}/{pr_url.repo}/pulls/{pr_url.number}"

        pr_resp = await self._authed_get(pr_url.owner, pr_path)
        pr_resp.raise_for_status()
        pr_data = pr_resp.json()

        reviews: list[Review] = []
        page = 1
        while True:
            rev_resp = await self._authed_get(
                pr_url.owner,
                f"{pr_path}/reviews",
                {"per_page": 100, "page": page},
            )
            rev_resp.raise_for_status()
            page_data = rev_resp.json()
            if not page_data:
                break
            for r in page_data:
                reviews.append(
                    Review(
                        user_login=r["user"]["login"],
                        state=ReviewState(r["state"]),
                    )
                )
            if len(page_data) < 100:
                break
            page += 1

        head_sha = pr_data.get("head", {}).get("sha")
        check_runs = await self._fetch_check_runs(pr_url, head_sha)

        return PRInfo(
            state=pr_data["state"],
            merged=pr_data.get("merged", False),
            reviews=tuple(reviews),
            author_login=pr_data.get("user", {}).get("login", ""),
            ci_failing=resolve_ci_failing(check_runs),
        )

    async def _fetch_check_runs(
        self,
        pr_url: PRUrl,
        head_sha: str | None,
    ) -> tuple[CheckRun, ...]:
        """Fetch all check-runs for a commit.

        Generic status data only — interpreting what counts as "failing" is the
        caller's concern (see ``resolve_ci_failing``). Returns an empty tuple when
        there is no head SHA or the request fails (e.g. the App lacks the
        *Checks: read* permission); callers treat "no data" as indeterminate.
        """
        if not head_sha:
            return ()

        try:
            runs: list[CheckRun] = []
            page = 1
            while True:
                resp = await self._authed_get(
                    pr_url.owner,
                    f"/repos/{pr_url.owner}/{pr_url.repo}/commits/{head_sha}/check-runs",
                    {"per_page": 100, "page": page},
                    log_errors=False,
                )
                resp.raise_for_status()
                page_runs = resp.json().get("check_runs", [])
                runs.extend(
                    CheckRun(status=r.get("status", ""), conclusion=r.get("conclusion"))
                    for r in page_runs
                )
                if len(page_runs) < 100:
                    break
                page += 1
        except SourceRateLimitError:
            raise
        except Exception:
            logger.warning("Failed to fetch check-runs for %s@%s", pr_url, head_sha[:7])
            return ()

        return tuple(runs)

    async def lookup_user(self, github_username: str) -> GitHubUserRef | None:
        """Resolve a GitHub login via the public API."""
        stripped = github_username.strip()
        if stripped.lower().endswith(_BOT_SUFFIX):
            return await self.lookup_app(stripped[: -len(_BOT_SUFFIX)])
        if not stripped:
            return None

        resp = await self._client.get(f"/users/{stripped}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        kind = _USER_TYPE_TO_KIND.get(data.get("type", ""))
        if kind is None:
            return None
        return GitHubUserRef(login=data["login"], kind=kind)

    async def lookup_app(self, github_app_name: str) -> GitHubUserRef | None:
        """Resolve a GitHub bot via the public apps API."""
        # /apps/{slug} is public and rejects app JWTs scoped to a different app.
        resp = await self._client.get(f"/apps/{github_app_name}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return GitHubUserRef(login=github_app_name, kind="bot")

    async def close(self) -> None:
        await self._client.aclose()
