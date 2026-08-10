"""Tests for the Jira connector, with Jira replaced by a mock transport.

No test here needs a real Jira site, a real account or a network call. The fake
records every request it served, which is what lets us assert the properties
that matter most: that the JQL is ours and not the caller's, that only six
fields are ever requested, and that the issue cap shrinks the request rather
than trimming the answer.

Using httpx.MockTransport rather than patching the client out means these tests
run through the real httpx.Client - real base URL joining, a real Basic auth
header, real body serialisation.
"""

import base64
import inspect
import json

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.jira_connector import ISSUE_FIELDS, JiraConnector
from app.core.exceptions import (
    JiraApiError,
    JiraAuthenticationError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitError,
)

SITE = "https://example.atlassian.net"
EMAIL = "ingest@example.com"
FAKE_TOKEN = SecretStr("jira_fake_api_token_for_tests_only")
PROJECT = "TRACK"
CLOUD_ID = "c57406ca-88a7-49e4-8c62-9919bdc039a7"

# What the gateway puts in front of every authenticated REST path.
GATEWAY_PREFIX = f"/ex/jira/{CLOUD_ID}"


def api_path(request: httpx.Request) -> str:
    """The REST path with the gateway prefix removed, if it has one.

    Lets the fake and its assertions talk about "/rest/api/3/myself" while the
    connector really requests
    "https://api.atlassian.com/ex/jira/<cloud id>/rest/api/3/myself".
    """
    path = str(request.url.path)
    if path.startswith(GATEWAY_PREFIX):
        return path[len(GATEWAY_PREFIX) :]
    return path


# ------------------------------------------------------------------- fakes


def make_issue(
    key: str, *, issue_type: str = "Story", parent: str | None = None,
    description: object = None,
) -> dict:
    """One raw issue, shaped as the REST API returns it."""
    fields: dict = {
        "summary": f"Summary for {key}",
        "description": description,
        "issuetype": {"name": issue_type},
        "status": {"name": "To Do"},
        "project": {"key": PROJECT},
    }
    if parent is not None:
        fields["parent"] = {"key": parent}
    return {"id": key, "key": key, "fields": fields}


class FakeJira:
    """A Jira site that serves the three endpoints and records every request.

    Pages are handed out by integer offset, and the page token is that offset as
    a string - so a test can assert exactly which token was echoed back.
    """

    def __init__(
        self,
        *,
        issues: list[dict] | None = None,
        page_size: int = 2,
        tenant_status: int = 200,
        tenant_body: object = None,
        myself_status: int = 200,
        myself_body: object = None,
        myself_text: str | None = None,
        project_status: int = 200,
        search_status: int = 200,
        search_body: object = None,
        search_text: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.issues = issues if issues is not None else []
        self.page_size = page_size
        self.tenant_status = tenant_status
        self.tenant_body = (
            tenant_body if tenant_body is not None else {"cloudId": CLOUD_ID}
        )
        self.myself_status = myself_status
        self.myself_body = (
            myself_body if myself_body is not None else {"accountId": "557058:abc"}
        )
        self.myself_text = myself_text
        self.project_status = project_status
        self.search_status = search_status
        self.search_body = search_body
        self.search_text = search_text
        self.raises = raises

        # Call recorders - the point of the fake.
        self.requests: list[httpx.Request] = []
        self.search_bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises

        path = api_path(request)

        if path == "/_edge/tenant_info":
            return httpx.Response(self.tenant_status, json=self.tenant_body)

        if path == "/rest/api/3/myself":
            if self.myself_text is not None:
                return httpx.Response(self.myself_status, text=self.myself_text)
            return httpx.Response(self.myself_status, json=self.myself_body)

        if path.startswith("/rest/api/3/project/"):
            if self.project_status != 200:
                return httpx.Response(
                    self.project_status, json={"errorMessages": ["nope"]}
                )
            return httpx.Response(200, json={"key": PROJECT, "name": "Tracker"})

        if path == "/rest/api/3/search/jql":
            body = json.loads(request.content)
            self.search_bodies.append(body)

            if self.search_text is not None:
                return httpx.Response(self.search_status, text=self.search_text)
            if self.search_status != 200:
                return httpx.Response(
                    self.search_status, json={"errorMessages": ["nope"]}
                )
            if self.search_body is not None:
                return httpx.Response(200, json=self.search_body)

            token = body.get("nextPageToken")
            start = 0 if token is None else int(token)
            take = min(int(body.get("maxResults", 50)), self.page_size)
            page = self.issues[start : start + take]
            following = start + len(page)
            is_last = following >= len(self.issues)

            payload: dict = {"issues": page, "isLast": is_last}
            if not is_last:
                payload["nextPageToken"] = str(following)
            return httpx.Response(200, json=payload)

        return httpx.Response(404, json={"errorMessages": ["No such endpoint"]})

    @property
    def paths(self) -> list[str]:
        return [api_path(request) for request in self.requests]


def build_connector(fake: FakeJira, site: str = SITE, **kwargs) -> JiraConnector:
    """Construct a connector whose transport is the fake."""
    return JiraConnector(
        site,
        EMAIL,
        FAKE_TOKEN,
        transport=httpx.MockTransport(fake.handler),
        **kwargs,
    )


# -------------------------------------------------------------- happy path


def test_every_issue_is_retrieved() -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(5)])

    snapshot = build_connector(fake).get_issues(PROJECT)

    assert [issue["key"] for issue in snapshot.issues] == [
        f"TRACK-{i}" for i in range(5)
    ]
    assert snapshot.retrieved_issues == 5
    assert snapshot.truncated is False
    assert snapshot.project_key == PROJECT
    assert snapshot.site_url == SITE


def test_an_empty_project_is_not_an_error() -> None:
    snapshot = build_connector(FakeJira(issues=[])).get_issues(PROJECT)

    assert snapshot.issues == []
    assert snapshot.retrieved_issues == 0
    assert snapshot.truncated is False


def test_raw_issue_json_is_handed_back_untouched() -> None:
    """Parsing belongs downstream; the connector does not reshape anything."""
    raw = make_issue("TRACK-1", description={"type": "doc", "content": []})
    fake = FakeJira(issues=[raw])

    assert build_connector(fake).get_issues(PROJECT).issues[0] == raw


# -------------------------------------------------------------- validation


def test_credentials_and_project_are_checked_before_any_search() -> None:
    fake = FakeJira(issues=[make_issue("TRACK-1")])

    build_connector(fake).get_issues(PROJECT)

    assert fake.paths[:4] == [
        "/_edge/tenant_info",
        "/rest/api/3/myself",
        f"/rest/api/3/project/{PROJECT}",
        "/rest/api/3/search/jql",
    ]


def test_a_bad_credential_stops_before_the_project_is_read() -> None:
    fake = FakeJira(myself_status=401)

    with pytest.raises(JiraAuthenticationError):
        build_connector(fake).get_issues(PROJECT)

    assert fake.paths == ["/_edge/tenant_info", "/rest/api/3/myself"]


# ------------------------------------------------------- the cloud gateway


def test_authenticated_calls_go_through_the_gateway() -> None:
    """A scoped token can only authenticate against api.atlassian.com; aimed at
    the site's own hostname it returns 401 however correct it is."""
    fake = FakeJira(issues=[make_issue("TRACK-1")])

    build_connector(fake).get_issues(PROJECT)

    hosts = {request.url.host for request in fake.requests}
    assert hosts == {"example.atlassian.net", "api.atlassian.com"}

    rest_calls = [r for r in fake.requests if api_path(r).startswith("/rest/")]
    assert len(rest_calls) == 3
    for request in rest_calls:
        assert str(request.url).startswith(
            f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/"
        )


def test_the_cloud_id_is_read_from_the_site_before_anything_else() -> None:
    fake = FakeJira(issues=[])

    build_connector(fake).get_issues(PROJECT)

    assert fake.paths[0] == "/_edge/tenant_info"
    assert fake.requests[0].url.host == "example.atlassian.net"


def test_the_tenant_lookup_carries_no_credential() -> None:
    """It is public, and a scoped token cannot authenticate anywhere yet."""
    fake = FakeJira(issues=[])

    build_connector(fake).get_issues(PROJECT)

    assert "authorization" not in fake.requests[0].headers
    assert "authorization" in fake.requests[1].headers


def test_an_unknown_site_is_reported_before_credentials_are_tried() -> None:
    fake = FakeJira(tenant_status=404)

    with pytest.raises(JiraNotFoundError) as caught:
        build_connector(fake).get_issues(PROJECT)

    assert fake.paths == ["/_edge/tenant_info"]
    assert "site" in caught.value.message.lower()


@pytest.mark.parametrize("body", [{}, {"cloudId": ""}, {"cloudId": 123}])
def test_a_tenant_response_without_a_cloud_id_is_an_api_error(body: dict) -> None:
    with pytest.raises(JiraApiError) as caught:
        build_connector(FakeJira(tenant_body=body)).get_issues(PROJECT)

    assert "cloud id" in caught.value.message


def test_the_cloud_id_is_resolved_once_per_run(caplog) -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(5)], page_size=2)

    build_connector(fake).get_issues(PROJECT)

    assert fake.paths.count("/_edge/tenant_info") == 1


def test_a_missing_project_stops_before_a_search_runs() -> None:
    fake = FakeJira(project_status=404)

    with pytest.raises(JiraNotFoundError):
        build_connector(fake).get_issues(PROJECT)

    assert "/rest/api/3/search/jql" not in fake.paths


def test_the_project_key_appears_in_the_not_found_message() -> None:
    fake = FakeJira(project_status=404)

    with pytest.raises(JiraNotFoundError) as caught:
        build_connector(fake).get_issues(PROJECT)

    assert PROJECT in caught.value.message


def test_credentials_are_sent_as_http_basic_auth() -> None:
    fake = FakeJira(issues=[])

    build_connector(fake).get_issues(PROJECT)

    # requests[0] is the public tenant lookup, which carries no credential.
    header = fake.requests[1].headers["authorization"]
    scheme, encoded = header.split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == (
        f"{EMAIL}:{FAKE_TOKEN.get_secret_value()}"
    )


def test_a_myself_response_without_an_account_is_rejected() -> None:
    """An SSO wall can answer 200 with a login page; that is not a valid login."""
    fake = FakeJira(myself_body={"message": "please log in"})

    with pytest.raises(JiraAuthenticationError):
        build_connector(fake).get_issues(PROJECT)

    assert "/rest/api/3/search/jql" not in fake.paths


def test_a_trailing_slash_in_the_site_url_does_not_double_the_path() -> None:
    fake = FakeJira(issues=[])

    build_connector(fake, site=f"{SITE}/").get_issues(PROJECT)

    assert fake.paths[0] == "/_edge/tenant_info"
    assert all(path.startswith("/rest/api/3/") for path in fake.paths[1:])
    assert not any("//" in path for path in fake.paths)


# --------------------------------------------------------- JQL and fields


def test_the_jql_targets_only_the_requested_project_and_types() -> None:
    fake = FakeJira(issues=[])

    build_connector(fake).get_issues(PROJECT)

    assert fake.search_bodies[0]["jql"] == (
        'project = "TRACK" AND issuetype in ("Epic", "Story") ORDER BY created ASC'
    )


def test_the_connector_accepts_no_caller_supplied_jql() -> None:
    """V1 builds the query itself; a caller must not be able to reach other
    projects by supplying their own."""
    parameters = inspect.signature(JiraConnector.get_issues).parameters

    assert "jql" not in parameters
    assert set(parameters) == {"self", "project_key", "max_issues"}


def test_only_the_six_required_fields_are_requested() -> None:
    """An unqualified search returns every custom field a site has defined."""
    fake = FakeJira(issues=[])

    build_connector(fake).get_issues(PROJECT)

    requested = fake.search_bodies[0]["fields"]
    assert len(requested) == 6
    assert set(requested) == {
        "summary",
        "description",
        "status",
        "issuetype",
        "parent",
        "project",
    }
    assert set(requested) == set(ISSUE_FIELDS)


# -------------------------------------------------------------- pagination


def test_every_page_is_followed_until_the_last_one() -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(5)], page_size=2)

    snapshot = build_connector(fake).get_issues(PROJECT)

    assert len(fake.search_bodies) == 3
    assert len(snapshot.issues) == 5


def test_the_first_page_sends_no_page_token() -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(3)], page_size=2)

    build_connector(fake).get_issues(PROJECT)

    assert "nextPageToken" not in fake.search_bodies[0]


def test_the_next_page_token_is_sent_back() -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(5)], page_size=2)

    build_connector(fake).get_issues(PROJECT)

    assert fake.search_bodies[1]["nextPageToken"] == "2"
    assert fake.search_bodies[2]["nextPageToken"] == "4"


def test_pagination_stops_when_no_token_is_offered() -> None:
    """A malformed response with neither isLast nor a token must not spin."""
    fake = FakeJira(search_body={"issues": [make_issue("TRACK-1")]})

    snapshot = build_connector(fake).get_issues(PROJECT)

    assert len(fake.search_bodies) == 1
    assert len(snapshot.issues) == 1
    assert snapshot.truncated is False


def test_a_repeated_page_token_does_not_loop_forever() -> None:
    fake = FakeJira(
        search_body={
            "issues": [make_issue("TRACK-1")],
            "isLast": False,
            "nextPageToken": "always-the-same",
        }
    )

    snapshot = build_connector(fake).get_issues(PROJECT)

    assert len(fake.search_bodies) == 2
    assert snapshot.truncated is True


def test_an_empty_page_that_promises_more_stops_the_run() -> None:
    fake = FakeJira(
        search_body={"issues": [], "isLast": False, "nextPageToken": "next"}
    )

    snapshot = build_connector(fake).get_issues(PROJECT)

    assert len(fake.search_bodies) == 1
    assert snapshot.truncated is True


def test_the_page_ceiling_stops_a_server_that_never_finishes() -> None:
    """Every page offers a fresh token, so only the ceiling can end this."""
    counter = {"n": 0}

    def endless(request: httpx.Request) -> httpx.Response:
        if api_path(request) != "/rest/api/3/search/jql":
            return fake.handler(request)
        counter["n"] += 1
        return httpx.Response(
            200,
            json={
                "issues": [make_issue(f"TRACK-{counter['n']}")],
                "isLast": False,
                "nextPageToken": f"token-{counter['n']}",
            },
        )

    fake = FakeJira()
    connector = JiraConnector(
        SITE, EMAIL, FAKE_TOKEN, max_pages=3, transport=httpx.MockTransport(endless)
    )

    snapshot = connector.get_issues(PROJECT)

    assert counter["n"] == 3
    assert snapshot.truncated is True
    assert (PROJECT, "Ingestion stopped at the page ceiling.") in snapshot.errors


# ------------------------------------------------------------------- the cap


def test_the_issue_cap_limits_what_is_fetched() -> None:
    """The cap must prevent the fetch, not just trim the result.

    The last request asks for exactly what is still wanted, so three issues
    cross the wire and never a fourth.
    """
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(10)], page_size=2)

    snapshot = build_connector(fake, page_size=2).get_issues(PROJECT, max_issues=3)

    assert len(snapshot.issues) == 3
    assert fake.search_bodies[0]["maxResults"] == 2
    assert fake.search_bodies[1]["maxResults"] == 1
    assert len(fake.search_bodies) == 2


def test_the_cap_marks_the_run_truncated() -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(10)], page_size=2)

    snapshot = build_connector(fake).get_issues(PROJECT, max_issues=3)

    assert snapshot.truncated is True
    assert any("cap" in reason for _, reason in snapshot.errors)


def test_the_cap_is_not_truncation_when_it_lands_on_the_last_page() -> None:
    """Fetching exactly what exists is a complete run, not a partial one."""
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(3)], page_size=2)

    snapshot = build_connector(fake).get_issues(PROJECT, max_issues=3)

    assert len(snapshot.issues) == 3
    assert snapshot.truncated is False
    assert snapshot.errors == []


def test_no_cap_fetches_everything() -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(7)], page_size=2)

    snapshot = build_connector(fake).get_issues(PROJECT, max_issues=None)

    assert len(snapshot.issues) == 7
    assert snapshot.truncated is False


def test_a_server_ignoring_max_results_cannot_exceed_the_cap() -> None:
    fake = FakeJira(
        search_body={"issues": [make_issue(f"TRACK-{i}") for i in range(9)],
                     "isLast": True}
    )

    snapshot = build_connector(fake).get_issues(PROJECT, max_issues=4)

    assert len(snapshot.issues) == 4


# --------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, JiraAuthenticationError),
        (403, JiraPermissionError),
        (404, JiraNotFoundError),
        (429, JiraRateLimitError),
        (500, JiraApiError),
        (503, JiraApiError),
    ],
)
def test_credential_failures_map_to_our_errors(
    status: int, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        build_connector(FakeJira(myself_status=status)).get_issues(PROJECT)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, JiraAuthenticationError),
        (403, JiraPermissionError),
        (404, JiraNotFoundError),
        (429, JiraRateLimitError),
        (500, JiraApiError),
    ],
)
def test_project_lookup_failures_map_to_our_errors(
    status: int, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        build_connector(FakeJira(project_status=status)).get_issues(PROJECT)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, JiraApiError),
        (401, JiraAuthenticationError),
        (403, JiraPermissionError),
        (404, JiraNotFoundError),
        (429, JiraRateLimitError),
        (500, JiraApiError),
    ],
)
def test_search_failures_map_to_our_errors(
    status: int, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        build_connector(FakeJira(search_status=status)).get_issues(PROJECT)


def test_a_rejected_search_names_the_search_not_the_caller() -> None:
    """A 400 here means our own JQL is wrong, so it is an upstream failure."""
    with pytest.raises(JiraApiError) as caught:
        build_connector(FakeJira(search_status=400)).get_issues(PROJECT)

    assert "search" in caught.value.message


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("no route"),
        httpx.ReadError("reset"),
    ],
)
def test_network_failures_map_to_a_bad_gateway(error: Exception) -> None:
    with pytest.raises(JiraApiError):
        build_connector(FakeJira(raises=error)).get_issues(PROJECT)


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("slow"), httpx.ReadTimeout("slow")]
)
def test_a_timeout_says_it_did_not_respond_in_time(error: Exception) -> None:
    """TimeoutException subclasses RequestError - the wrong except order would
    silently lose this message."""
    with pytest.raises(JiraApiError) as caught:
        build_connector(FakeJira(raises=error)).get_issues(PROJECT)

    assert "in time" in caught.value.message


def test_a_non_json_credential_body_is_reported_as_an_api_error() -> None:
    fake = FakeJira(myself_text="<html>login</html>")

    with pytest.raises(JiraApiError):
        build_connector(fake).get_issues(PROJECT)


def test_a_non_json_search_body_is_reported_as_an_api_error() -> None:
    with pytest.raises(JiraApiError):
        build_connector(FakeJira(search_text="<html>oops</html>")).get_issues(PROJECT)


def test_a_json_array_body_is_reported_as_an_api_error() -> None:
    with pytest.raises(JiraApiError):
        build_connector(FakeJira(search_body=["not", "an", "object"])).get_issues(
            PROJECT
        )


# ------------------------------------------------------------------- logging


def test_each_page_is_logged(caplog) -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(3)], page_size=2)

    with caplog.at_level("INFO", logger="app.connectors.jira_connector"):
        build_connector(fake).get_issues(PROJECT)

    assert "Jira page 1 returned 2 issues" in caplog.text
    assert "Jira page 2 returned 1 issues" in caplog.text


def test_the_summary_counts_issues_pages_and_api_calls(caplog) -> None:
    """Two validation calls plus one per page."""
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(3)], page_size=2)

    with caplog.at_level("INFO", logger="app.connectors.jira_connector"):
        build_connector(fake).get_issues(PROJECT)

    assert "Retrieved 3 Jira issues from 2 page(s) (4 Jira API calls)" in caplog.text


def test_the_run_names_the_project_not_the_credentials(caplog) -> None:
    fake = FakeJira(issues=[])

    with caplog.at_level("INFO", logger="app.connectors.jira_connector"):
        build_connector(fake).get_issues(PROJECT)

    assert "Searching Epics and Stories in TRACK" in caplog.text
    assert "Jira authentication successful" in caplog.text
    assert EMAIL not in caplog.text


def test_a_jira_error_body_is_logged_but_not_returned(caplog) -> None:
    """Jira's wording helps an operator; it does not belong in a response."""
    sentinel = "instance-internal-detail-sentinel"

    def handler(request: httpx.Request) -> httpx.Response:
        if api_path(request) == "/_edge/tenant_info":
            return httpx.Response(200, json={"cloudId": CLOUD_ID})
        return httpx.Response(500, json={"errorMessages": [sentinel]})

    connector = JiraConnector(
        SITE, EMAIL, FAKE_TOKEN, transport=httpx.MockTransport(handler)
    )

    with caplog.at_level("WARNING", logger="app.connectors.jira_connector"):
        with pytest.raises(JiraApiError) as caught:
            connector.get_issues(PROJECT)

    assert sentinel in caplog.text
    assert sentinel not in caught.value.message


def test_the_cap_is_logged_as_a_warning(caplog) -> None:
    fake = FakeJira(issues=[make_issue(f"TRACK-{i}") for i in range(10)], page_size=2)

    with caplog.at_level("WARNING", logger="app.connectors.jira_connector"):
        build_connector(fake).get_issues(PROJECT, max_issues=3)

    assert "Limiting ingestion to 3 issues" in caplog.text


def test_issue_content_is_not_logged_at_info(caplog) -> None:
    sentinel = "sentinel-issue-description"
    fake = FakeJira(issues=[make_issue("TRACK-1", description=sentinel)])

    with caplog.at_level("INFO", logger="app.connectors.jira_connector"):
        build_connector(fake).get_issues(PROJECT)

    assert sentinel not in caplog.text


# ------------------------------------------------------------------ security


def test_the_api_token_never_reaches_the_logs(caplog) -> None:
    secret = FAKE_TOKEN.get_secret_value()
    credential = base64.b64encode(f"{EMAIL}:{secret}".encode()).decode()
    fake = FakeJira(issues=[make_issue("TRACK-1")])

    with caplog.at_level("DEBUG", logger="app.connectors.jira_connector"):
        build_connector(fake).get_issues(PROJECT)

    assert secret not in caplog.text
    assert credential not in caplog.text
    assert "jira_fake" not in caplog.text


def test_the_api_token_never_appears_in_an_error_message() -> None:
    fake = FakeJira(myself_status=401)

    with pytest.raises(JiraAuthenticationError) as caught:
        build_connector(fake).get_issues(PROJECT)

    assert FAKE_TOKEN.get_secret_value() not in caught.value.message
    assert EMAIL not in caught.value.message


def test_the_connector_does_not_keep_the_credentials_on_an_attribute() -> None:
    """The token reaches the auth tuple and nothing else."""
    connector = build_connector(FakeJira())
    state = repr(vars(connector))

    assert FAKE_TOKEN.get_secret_value() not in state
    assert EMAIL not in state


def test_closing_releases_the_http_client() -> None:
    """The session holding the caller's token must not outlive their request."""
    with build_connector(FakeJira(issues=[])) as connector:
        connector.get_issues(PROJECT)
        assert connector._client.is_closed is False

    assert connector._client.is_closed is True
