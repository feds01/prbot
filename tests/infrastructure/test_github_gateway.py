import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response
from time_machine import TimeMachineFixture

from prbot.domain.tracking.ports import SourceRateLimitError
from prbot.domain.tracking.value_objects import PRUrl, ReviewState
from prbot.infrastructure.github_gateway import GitHubGateway, NoInstallationError

_test_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PRIVATE_KEY = _test_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


@pytest.fixture
def pr_url() -> PRUrl:
    return PRUrl(owner="octocat", repo="hello", number=1)


@pytest.fixture
def gateway() -> GitHubGateway:
    return GitHubGateway(app_id="12345", private_key=TEST_PRIVATE_KEY)


def _mock_installation_and_token() -> respx.Route:
    """Set up mocks for the GitHub App auth flow, returning the token route."""
    respx.get("https://api.github.com/orgs/octocat/installation").mock(
        return_value=Response(200, json={"id": 99})
    )
    return respx.post("https://api.github.com/app/installations/99/access_tokens").mock(
        return_value=Response(
            201,
            json={
                "token": "ghs_fake_token",
                "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
            },
        )
    )


class TestExtractPrReferences:
    def test_extract_from_valid_url(self, gateway: GitHubGateway) -> None:
        refs = gateway.extract_pr_references("https://github.com/octocat/hello-world/pull/42")
        assert len(refs) == 1
        assert refs[0].owner == "octocat"
        assert refs[0].repo == "hello-world"
        assert refs[0].number == 42

    def test_extract_without_scheme(self, gateway: GitHubGateway) -> None:
        refs = gateway.extract_pr_references("github.com/octocat/repo/pull/1")
        assert len(refs) == 1
        assert refs[0].number == 1

    def test_no_match_returns_empty(self, gateway: GitHubGateway) -> None:
        assert gateway.extract_pr_references("https://example.com/not-a-pr") == []
        assert gateway.extract_pr_references("https://github.com/octocat/repo/issues/1") == []
        assert gateway.extract_pr_references("just some text") == []

    def test_extract_embedded_in_text(self, gateway: GitHubGateway) -> None:
        text = "Check out https://github.com/org/repo/pull/99 please"
        refs = gateway.extract_pr_references(text)
        assert len(refs) == 1
        assert refs[0].owner == "org"
        assert refs[0].number == 99

    def test_extract_deduplicates(self, gateway: GitHubGateway) -> None:
        text = "github.com/o/r/pull/1 and github.com/o/r/pull/1 again"
        refs = gateway.extract_pr_references(text)
        assert len(refs) == 1

    def test_extract_multiple(self, gateway: GitHubGateway) -> None:
        text = "github.com/o/r/pull/1 and github.com/o/r/pull/2"
        refs = gateway.extract_pr_references(text)
        assert len(refs) == 2


class TestGitHubGateway:
    @respx.mock
    async def test_fetch_open_pr_no_reviews(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(200, json={"state": "open", "merged": False})
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(200, json=[])
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.state == "open"
        assert info.merged is False
        assert info.reviews == ()

    @respx.mock
    async def test_fetch_merged_pr(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(200, json={"state": "closed", "merged": True})
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(200, json=[])
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.state == "closed"
        assert info.merged is True

    @respx.mock
    async def test_fetch_pr_with_reviews(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(200, json={"state": "open", "merged": False})
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(
                200,
                json=[
                    {"user": {"login": "alice"}, "state": "APPROVED"},
                    {"user": {"login": "bob"}, "state": "CHANGES_REQUESTED"},
                ],
            )
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert len(info.reviews) == 2
        assert info.reviews[0].user_login == "alice"
        assert info.reviews[0].state == ReviewState.APPROVED
        assert info.reviews[1].user_login == "bob"
        assert info.reviews[1].state == ReviewState.CHANGES_REQUESTED

    @respx.mock
    async def test_fetch_pr_with_requested_reviewers(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(
                200,
                json={
                    "state": "open",
                    "merged": False,
                    "requested_reviewers": [{"login": "alice"}, {"login": "bob"}],
                    "requested_teams": [{"slug": "core"}],
                },
            )
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(200, json=[])
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.requested_reviewers == ("alice", "bob")

    @respx.mock
    async def test_handles_api_error(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(httpx.HTTPStatusError, match="404"):
            await gateway.fetch_pr_info(pr_url)


def _mock_no_installation_for(owner: str) -> None:
    respx.get(f"https://api.github.com/orgs/{owner}/installation").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )
    respx.get(f"https://api.github.com/users/{owner}/installation").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )


def _mock_token(installation_id: int, token: str) -> respx.Route:
    return respx.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    ).mock(
        return_value=Response(
            201,
            json={
                "token": token,
                "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
            },
        )
    )


def _mock_linux_pr() -> respx.Route:
    respx.get("https://api.github.com/repos/torvalds/linux/pulls/1/reviews").mock(
        return_value=Response(200, json=[])
    )
    return respx.get("https://api.github.com/repos/torvalds/linux/pulls/1").mock(
        return_value=Response(200, json={"state": "open", "merged": False})
    )


_LINUX_PR = PRUrl(owner="torvalds", repo="linux", number=1)


class TestPublicPRWithoutInstallation:
    @respx.mock
    async def test_borrows_another_installation(self, gateway: GitHubGateway) -> None:
        _mock_no_installation_for("torvalds")
        installations_route = respx.get("https://api.github.com/app/installations").mock(
            return_value=Response(200, json=[{"id": 7}])
        )
        token_route = _mock_token(7, "ghs_borrowed")
        pr_route = _mock_linux_pr()

        info = await gateway.fetch_pr_info(_LINUX_PR)

        assert info.state == "open"
        assert token_route.call_count == 1
        assert pr_route.calls[0].request.headers["Authorization"] == "Bearer ghs_borrowed"

        # A second uninstalled owner reuses the same borrowed installation.
        _mock_no_installation_for("rust-lang")
        respx.get("https://api.github.com/repos/rust-lang/rust/pulls/2").mock(
            return_value=Response(200, json={"state": "closed", "merged": True})
        )
        respx.get("https://api.github.com/repos/rust-lang/rust/pulls/2/reviews").mock(
            return_value=Response(200, json=[])
        )

        await gateway.fetch_pr_info(PRUrl(owner="rust-lang", repo="rust", number=2))

        assert installations_route.call_count == 1
        assert token_route.call_count == 1

    @respx.mock
    async def test_app_with_no_installations_raises(self, gateway: GitHubGateway) -> None:
        _mock_no_installation_for("torvalds")
        respx.get("https://api.github.com/app/installations").mock(
            return_value=Response(200, json=[])
        )

        with pytest.raises(NoInstallationError):
            await gateway.fetch_pr_info(_LINUX_PR)

    @respx.mock
    async def test_skips_suspended_installations(self, gateway: GitHubGateway) -> None:
        _mock_no_installation_for("torvalds")
        respx.get("https://api.github.com/app/installations").mock(
            return_value=Response(
                200, json=[{"id": 6, "suspended_at": "2026-01-01T00:00:00Z"}, {"id": 7}]
            )
        )
        token_route = _mock_token(7, "ghs_borrowed")
        _mock_linux_pr()

        await gateway.fetch_pr_info(_LINUX_PR)

        assert token_route.call_count == 1

    @respx.mock
    async def test_private_repo_still_404s(self, gateway: GitHubGateway) -> None:
        _mock_no_installation_for("torvalds")
        respx.get("https://api.github.com/app/installations").mock(
            return_value=Response(200, json=[{"id": 7}])
        )
        _mock_token(7, "ghs_borrowed")
        respx.get("https://api.github.com/repos/torvalds/linux/pulls/1").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(httpx.HTTPStatusError, match="404"):
            await gateway.fetch_pr_info(_LINUX_PR)

    @respx.mock
    async def test_owner_who_installs_later_switches_over(
        self, gateway: GitHubGateway, time_machine: TimeMachineFixture
    ) -> None:
        time_machine.move_to(datetime.now(UTC))
        respx.get("https://api.github.com/orgs/torvalds/installation").mock(
            side_effect=[
                Response(404, json={"message": "Not Found"}),
                Response(200, json={"id": 99}),
            ]
        )
        respx.get("https://api.github.com/users/torvalds/installation").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        respx.get("https://api.github.com/app/installations").mock(
            return_value=Response(200, json=[{"id": 7}])
        )
        _mock_token(7, "ghs_borrowed")
        _mock_token(99, "ghs_own")
        pr_route = _mock_linux_pr()

        await gateway.fetch_pr_info(_LINUX_PR)
        time_machine.move_to(datetime.now(UTC) + timedelta(minutes=10))
        await gateway.fetch_pr_info(_LINUX_PR)

        assert pr_route.calls[0].request.headers["Authorization"] == "Bearer ghs_borrowed"
        assert pr_route.calls[-1].request.headers["Authorization"] == "Bearer ghs_own"


def _mock_pr_with_head(sha: str = "abc123") -> None:
    """Mock the PR + reviews endpoints, including a head SHA for CI lookups."""
    respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
        return_value=Response(200, json={"state": "open", "merged": False, "head": {"sha": sha}})
    )
    respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
        return_value=Response(200, json=[])
    )


def _mock_check_runs(sha: str, runs: list[dict[str, object]], status_code: int = 200) -> None:
    respx.get(f"https://api.github.com/repos/octocat/hello/commits/{sha}/check-runs").mock(
        return_value=Response(status_code, json={"check_runs": runs})
    )


class TestFetchCiFailing:
    @respx.mock
    async def test_no_head_sha_skips_ci_and_returns_none(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        # PR payload without a head — no check-runs request should be made.
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(200, json={"state": "open", "merged": False})
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(200, json=[])
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.ci_failing is None

    @respx.mock
    async def test_failing_run_returns_true(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
        _mock_pr_with_head()
        _mock_check_runs(
            "abc123",
            [
                {"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "failure"},
            ],
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.ci_failing is True

    @respx.mock
    async def test_all_passing_returns_false(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
        _mock_pr_with_head()
        _mock_check_runs(
            "abc123",
            [
                {"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "neutral"},
            ],
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.ci_failing is False

    @respx.mock
    async def test_in_progress_run_returns_none(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        _mock_installation_and_token()
        _mock_pr_with_head()
        _mock_check_runs(
            "abc123",
            [
                {"status": "completed", "conclusion": "success"},
                {"status": "in_progress", "conclusion": None},
            ],
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.ci_failing is None

    @respx.mock
    async def test_no_runs_returns_false(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
        _mock_pr_with_head()
        _mock_check_runs("abc123", [])

        info = await gateway.fetch_pr_info(pr_url)

        assert info.ci_failing is False

    @respx.mock
    async def test_permission_error_returns_none(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        # App lacks the Checks:read permission — must degrade gracefully, not raise.
        _mock_installation_and_token()
        _mock_pr_with_head()
        _mock_check_runs("abc123", [], status_code=403)

        info = await gateway.fetch_pr_info(pr_url)

        assert info.ci_failing is None

    @respx.mock
    async def test_failing_run_still_failing_even_if_others_pending(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        # A failure is definitive even while other checks are still running.
        _mock_installation_and_token()
        _mock_pr_with_head()
        _mock_check_runs(
            "abc123",
            [
                {"status": "completed", "conclusion": "failure"},
                {"status": "in_progress", "conclusion": None},
            ],
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.ci_failing is True


class TestRateLimitHandling:
    @respx.mock
    async def test_exhausted_budget_raises_rather_than_returning(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1789725110"},
                json={"message": "API rate limit exceeded for installation ID 1"},
            )
        )

        with pytest.raises(SourceRateLimitError) as excinfo:
            await gateway.fetch_pr_info(pr_url)

        assert excinfo.value.reset_at == 1789725110.0

    @respx.mock
    async def test_exhausted_budget_does_not_mint_a_second_token(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        token_route = _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(
                403,
                headers={"x-ratelimit-remaining": "0"},
                json={"message": "API rate limit exceeded for installation ID 1"},
            )
        )

        with pytest.raises(SourceRateLimitError):
            await gateway.fetch_pr_info(pr_url)

        # The budget belongs to the installation, so a fresh token cannot help.
        assert token_route.call_count == 1

    @respx.mock
    async def test_rejected_token_is_reminted_once(
        self, gateway: GitHubGateway, pr_url: PRUrl
    ) -> None:
        token_route = _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            side_effect=[
                Response(401, json={"message": "Bad credentials"}),
                Response(200, json={"state": "open", "merged": False, "user": {"login": "o"}}),
            ]
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(200, json=[])
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.state == "open"
        assert token_route.call_count == 2
