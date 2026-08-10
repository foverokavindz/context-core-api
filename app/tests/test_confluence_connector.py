"""Tests for the Confluence connector, with Confluence replaced by a transport.

No test here needs a real Confluence site, a real account or a network call. The
fake records every request it served, which is what lets us assert the property
this connector exists to guarantee: that pages are asked for by the resolved
space id, so a run cannot reach a space the caller did not name.

Using httpx.MockTransport rather than patching the client out means these tests
run through the real httpx.Client - real base URL joining, real query-string
encoding, a real Basic auth header.
"""

import base64

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.confluence_connector import (
    CURRENT_STATUS,
    STORAGE_BODY_FORMAT,
    ConfluenceConnector,
)
from app.core.exceptions import (
    ConfluenceApiError,
    ConfluenceAuthenticationError,
    ConfluenceNotFoundError,
    ConfluencePermissionError,
    ConfluenceRateLimitError,
)

SITE = "https://example.atlassian.net"
EMAIL = "ingest@example.com"
FAKE_TOKEN = SecretStr("confluence_fake_api_token_for_tests_only")
SPACE = "TR"
SPACE_ID = "6422530"
SPACE_NAME = "TrackIt"
CLOUD_ID = "c57406ca-88a7-49e4-8c62-9919bdc039a7"

# What the gateway puts in front of every authenticated REST path.
GATEWAY_PREFIX = f"/ex/confluence/{CLOUD_ID}"

TENANT_PATH = "/_edge/tenant_info"
SPACES_PATH = "/wiki/api/v2/spaces"
PAGES_PATH = "/wiki/api/v2/pages"


def api_path(request: httpx.Request) -> str:
    """The REST path with the gateway prefix removed, if it has one.

    Lets the fake and its assertions talk about "/wiki/api/v2/pages" while the
    connector really requests
    "https://api.atlassian.com/ex/confluence/<cloud id>/wiki/api/v2/pages".
    """
    path = str(request.url.path)
    if path.startswith(GATEWAY_PREFIX):
        return path[len(GATEWAY_PREFIX) :]
    return path


# ------------------------------------------------------------------- fakes


def make_page(page_id: str, *, parent: str | None = None, body: str = "<p>x</p>") -> dict:
    """One raw page, shaped as the v2 API returns it."""
    return {
        "id": page_id,
        "status": CURRENT_STATUS,
        "title": f"Page {page_id}",
        "spaceId": SPACE_ID,
        "parentId": parent,
        "version": {"number": 1},
        "body": {"storage": {"representation": "storage", "value": body}},
    }


class FakeConfluence:
    """A Confluence site that serves the three endpoints and records every call.

    Batches are handed out by integer offset, and the cursor is that offset as a
    string wrapped in the relative next link the real API returns - so a test can
    assert exactly which cursor was echoed back.
    """

    def __init__(
        self,
        *,
        pages: list[dict] | None = None,
        spaces: list[dict] | None = None,
        tenant_status: int = 200,
        tenant_body: object = None,
        spaces_status: int = 200,
        spaces_body: object = None,
        spaces_text: str | None = None,
        pages_status: int = 200,
        pages_body: object = None,
        pages_text: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.pages = pages if pages is not None else []
        self.spaces = (
            spaces
            if spaces is not None
            else [{"id": SPACE_ID, "key": SPACE, "name": SPACE_NAME}]
        )
        self.tenant_status = tenant_status
        self.tenant_body = (
            tenant_body if tenant_body is not None else {"cloudId": CLOUD_ID}
        )
        self.spaces_status = spaces_status
        self.spaces_body = spaces_body
        self.spaces_text = spaces_text
        self.pages_status = pages_status
        self.pages_body = pages_body
        self.pages_text = pages_text
        self.raises = raises

        # Call recorders - the point of the fake.
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises

        path = api_path(request)

        if path == TENANT_PATH:
            return httpx.Response(self.tenant_status, json=self.tenant_body)

        if path == SPACES_PATH:
            if self.spaces_text is not None:
                return httpx.Response(self.spaces_status, text=self.spaces_text)
            if self.spaces_status != 200:
                return httpx.Response(
                    self.spaces_status, json={"errors": [{"title": "nope"}]}
                )
            if self.spaces_body is not None:
                return httpx.Response(200, json=self.spaces_body)
            return httpx.Response(200, json={"results": self.spaces})

        if path == PAGES_PATH:
            if self.pages_text is not None:
                return httpx.Response(self.pages_status, text=self.pages_text)
            if self.pages_status != 200:
                return httpx.Response(
                    self.pages_status, json={"errors": [{"title": "nope"}]}
                )
            if self.pages_body is not None:
                return httpx.Response(200, json=self.pages_body)
            return httpx.Response(200, json=self._batch(request))

        return httpx.Response(404, json={"errors": [{"title": "No such endpoint"}]})

    def _batch(self, request: httpx.Request) -> dict:
        """One batch of pages, cursored by offset."""
        limit = int(request.url.params.get("limit", 100))
        start = int(request.url.params.get("cursor", 0))
        following = start + limit

        payload: dict = {"results": self.pages[start:following], "_links": {}}
        if following < len(self.pages):
            payload["_links"]["next"] = (
                f"/wiki/api/v2/pages?cursor={following}&limit={limit}"
            )
        return payload

    @property
    def paths(self) -> list[str]:
        return [api_path(request) for request in self.requests]

    @property
    def page_requests(self) -> list[httpx.Request]:
        return [
            request for request in self.requests if api_path(request) == PAGES_PATH
        ]

    @property
    def space_requests(self) -> list[httpx.Request]:
        return [
            request for request in self.requests if api_path(request) == SPACES_PATH
        ]


def build_connector(
    fake: FakeConfluence, site: str = SITE, **kwargs
) -> ConfluenceConnector:
    return ConfluenceConnector(
        site,
        EMAIL,
        FAKE_TOKEN,
        transport=httpx.MockTransport(fake.handler),
        **kwargs,
    )


def get_pages(fake: FakeConfluence, **kwargs):
    """Run one ingestion against the fake."""
    connector_kwargs = {
        key: kwargs.pop(key)
        for key in ("page_size", "max_batches")
        if key in kwargs
    }
    return build_connector(fake, **connector_kwargs).get_pages(SPACE, **kwargs)


# --------------------------------------------------------------- happy path


def test_every_page_is_retrieved() -> None:
    fake = FakeConfluence(pages=[make_page("1"), make_page("2")])

    snapshot = get_pages(fake)

    assert snapshot.retrieved_pages == 2
    assert [page["id"] for page in snapshot.pages] == ["1", "2"]


def test_the_raw_payload_is_handed_back_untouched() -> None:
    """Parsing belongs downstream - the connector is a transport, not a parser."""
    raw = make_page("1", body="<h2>Auth</h2>")
    snapshot = get_pages(FakeConfluence(pages=[raw]))

    assert snapshot.pages[0] == raw


def test_the_resolved_space_is_reported() -> None:
    snapshot = get_pages(FakeConfluence(pages=[make_page("1")]))

    assert snapshot.space_id == SPACE_ID
    assert snapshot.space_key == SPACE
    assert snapshot.space_name == SPACE_NAME


def test_the_site_is_reported_without_a_trailing_slash() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    snapshot = build_connector(fake, f"{SITE}/").get_pages(SPACE)

    assert snapshot.site_url == SITE


def test_an_empty_space_is_not_an_error() -> None:
    snapshot = get_pages(FakeConfluence(pages=[]))

    assert snapshot.retrieved_pages == 0
    assert snapshot.pages == []
    assert snapshot.truncated is False
    assert snapshot.errors == []


def test_the_endpoints_are_called_in_order() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert fake.paths == [TENANT_PATH, SPACES_PATH, PAGES_PATH]


# ----------------------------------------------------------------- cloud id


def test_the_cloud_id_is_read_from_the_site_before_anything_else() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert fake.paths[0] == TENANT_PATH
    assert fake.requests[0].url.host == "example.atlassian.net"


def test_every_api_call_goes_through_the_gateway() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    hosts = {request.url.host for request in fake.requests[1:]}
    assert hosts == {"api.atlassian.com"}


def test_the_cloud_id_lands_in_the_api_path() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert str(fake.requests[1].url.path).startswith(GATEWAY_PREFIX)


def test_the_tenant_lookup_carries_no_credential() -> None:
    """It is public, and a scoped token cannot authenticate anywhere yet."""
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert "authorization" not in fake.requests[0].headers


def test_the_cloud_id_is_resolved_once_per_run() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(6)])

    get_pages(fake, page_size=2)

    assert fake.paths.count(TENANT_PATH) == 1


def test_an_unreachable_site_is_a_not_found() -> None:
    with pytest.raises(ConfluenceNotFoundError) as caught:
        get_pages(FakeConfluence(tenant_status=404))

    assert "site" in caught.value.message.lower()


@pytest.mark.parametrize(
    "body", [{}, {"cloudId": ""}, {"cloudId": 123}, {"cloudId": None}]
)
def test_a_site_that_reports_no_cloud_id_is_an_api_error(body: dict) -> None:
    with pytest.raises(ConfluenceApiError) as caught:
        get_pages(FakeConfluence(tenant_body=body))

    assert "cloud id" in caught.value.message.lower()


def test_nothing_is_requested_when_the_site_cannot_be_resolved() -> None:
    fake = FakeConfluence(tenant_status=404)

    with pytest.raises(ConfluenceNotFoundError):
        get_pages(fake)

    assert fake.paths == [TENANT_PATH]


# ------------------------------------------------------------ authentication


def test_credentials_are_sent_as_http_basic_auth() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    scheme, encoded = fake.requests[1].headers["authorization"].split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == (
        f"{EMAIL}:{FAKE_TOKEN.get_secret_value()}"
    )


def test_every_authenticated_call_carries_the_credential() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    for request in fake.requests[1:]:
        assert "authorization" in request.headers


# ------------------------------------------------------------- space lookup


def test_the_space_is_filtered_by_key_at_the_api() -> None:
    """Not "list every space and pick one" - the filter is Confluence's job."""
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert fake.space_requests[0].url.params["keys"] == SPACE


def test_exactly_one_space_lookup_happens() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(6)])

    get_pages(fake, page_size=2)

    assert len(fake.space_requests) == 1


def test_a_space_that_does_not_exist_is_a_not_found() -> None:
    with pytest.raises(ConfluenceNotFoundError) as caught:
        get_pages(FakeConfluence(spaces=[]))

    assert SPACE in caught.value.message
    assert "not accessible" in caught.value.message


def test_a_space_the_account_cannot_see_is_the_same_answer() -> None:
    """Confluence omits it rather than refusing, so the two are one case."""
    with pytest.raises(ConfluenceNotFoundError):
        get_pages(FakeConfluence(spaces_body={"results": []}))


def test_no_pages_are_requested_when_the_space_is_missing() -> None:
    fake = FakeConfluence(spaces=[])

    with pytest.raises(ConfluenceNotFoundError):
        get_pages(fake)

    assert PAGES_PATH not in fake.paths


def test_only_the_exact_key_match_is_chosen() -> None:
    """A keyed lookup can also match a space's former alias."""
    fake = FakeConfluence(
        spaces=[
            {"id": "111", "key": "TRACKOLD", "name": "Old"},
            {"id": SPACE_ID, "key": SPACE, "name": SPACE_NAME},
            {"id": "222", "key": "TRX", "name": "Other"},
        ],
        pages=[make_page("1")],
    )

    snapshot = get_pages(fake)

    assert snapshot.space_id == SPACE_ID
    assert fake.page_requests[0].url.params["space-id"] == SPACE_ID


def test_the_key_match_is_case_insensitive() -> None:
    fake = FakeConfluence(
        spaces=[{"id": SPACE_ID, "key": "TR", "name": SPACE_NAME}],
        pages=[make_page("1")],
    )

    snapshot = build_connector(fake).get_pages("tr")

    assert snapshot.space_id == SPACE_ID


def test_a_space_with_no_name_is_still_usable() -> None:
    fake = FakeConfluence(
        spaces=[{"id": SPACE_ID, "key": SPACE}], pages=[make_page("1")]
    )

    snapshot = get_pages(fake)

    assert snapshot.space_name is None
    assert snapshot.retrieved_pages == 1


def test_a_numeric_space_id_is_accepted() -> None:
    fake = FakeConfluence(
        spaces=[{"id": 6422530, "key": SPACE, "name": SPACE_NAME}],
        pages=[make_page("1")],
    )

    assert get_pages(fake).space_id == SPACE_ID


@pytest.mark.parametrize(
    "results",
    [
        [{"key": SPACE, "name": SPACE_NAME}],  # no id at all
        [{"id": "", "key": SPACE}],
        [{"id": None, "key": SPACE}],
        [{"id": SPACE_ID}],  # no key to match against
        ["not a dict"],
        [],
    ],
)
def test_a_space_we_cannot_address_is_a_not_found(results: list) -> None:
    with pytest.raises(ConfluenceNotFoundError):
        get_pages(FakeConfluence(spaces_body={"results": results}))


@pytest.mark.parametrize(
    "body", [{}, {"results": None}, {"results": "not a list"}, {"results": {}}]
)
def test_a_malformed_space_response_is_a_not_found(body: dict) -> None:
    with pytest.raises(ConfluenceNotFoundError):
        get_pages(FakeConfluence(spaces_body=body))


# ------------------------------------------------- confined to one space


def test_pages_are_requested_by_the_resolved_space_id() -> None:
    """The test this connector exists for: a run cannot leave its space."""
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert fake.page_requests[0].url.params["space-id"] == SPACE_ID


def test_every_batch_carries_the_space_id() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(6)])

    get_pages(fake, page_size=2)

    assert len(fake.page_requests) == 3
    for request in fake.page_requests:
        assert request.url.params["space-id"] == SPACE_ID


def test_the_space_key_is_never_used_as_a_page_filter() -> None:
    """The key names a space to a human; the id is what the API filters on."""
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert "space-key" not in fake.page_requests[0].url.params


def test_the_caller_cannot_reach_another_space() -> None:
    """Whatever the space resolves to is what every page request asks for."""
    fake = FakeConfluence(
        spaces=[{"id": "999", "key": SPACE, "name": SPACE_NAME}],
        pages=[make_page("1")],
    )

    get_pages(fake)

    assert fake.page_requests[0].url.params["space-id"] == "999"


# --------------------------------------------------------- page parameters


def test_the_body_is_requested_in_storage_format() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert fake.page_requests[0].url.params["body-format"] == STORAGE_BODY_FORMAT


def test_only_current_pages_are_requested() -> None:
    """A knowledge base, not an archive: no drafts, no trash, no history."""
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert fake.page_requests[0].url.params["status"] == CURRENT_STATUS


def test_the_first_batch_carries_no_cursor() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    get_pages(fake)

    assert "cursor" not in fake.page_requests[0].url.params


# -------------------------------------------------------------- pagination


def test_every_batch_is_followed_to_the_end() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(5)])

    snapshot = get_pages(fake, page_size=2)

    assert snapshot.retrieved_pages == 5
    assert len(fake.page_requests) == 3


def test_the_next_cursor_is_sent_on_the_following_batch() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(5)])

    get_pages(fake, page_size=2)

    assert fake.page_requests[1].url.params["cursor"] == "2"
    assert fake.page_requests[2].url.params["cursor"] == "4"


def test_a_full_run_of_batches_is_not_truncation() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(5)])

    assert get_pages(fake, page_size=2).truncated is False


def test_a_single_batch_needs_no_second_request() -> None:
    fake = FakeConfluence(pages=[make_page("1"), make_page("2")])

    get_pages(fake, page_size=10)

    assert len(fake.page_requests) == 1


def test_a_repeated_cursor_does_not_loop_forever() -> None:
    """A server that keeps offering the same cursor would spin indefinitely."""
    fake = FakeConfluence(
        pages_body={
            "results": [make_page("1")],
            "_links": {"next": "/wiki/api/v2/pages?cursor=same&limit=2"},
        }
    )

    snapshot = get_pages(fake, page_size=2)

    assert snapshot.truncated is True
    assert len(fake.page_requests) == 2


def test_an_empty_batch_promising_more_stops_the_walk() -> None:
    fake = FakeConfluence(
        pages_body={
            "results": [],
            "_links": {"next": "/wiki/api/v2/pages?cursor=9&limit=2"},
        }
    )

    snapshot = get_pages(fake, page_size=2)

    assert snapshot.retrieved_pages == 0
    assert snapshot.truncated is True


@pytest.mark.parametrize(
    "links",
    [
        {},
        {"next": None},
        {"next": ""},
        {"next": 42},
        {"next": "/wiki/api/v2/pages?limit=2"},  # a next link with no cursor
        {"next": "/wiki/api/v2/pages?cursor="},  # an empty cursor
        {"next": "::::"},
    ],
)
def test_an_unusable_next_link_ends_the_walk_cleanly(links: dict) -> None:
    """A malformed link is not worth failing a run that already has its pages."""
    fake = FakeConfluence(pages_body={"results": [make_page("1")], "_links": links})

    snapshot = get_pages(fake)

    assert snapshot.retrieved_pages == 1
    assert snapshot.truncated is False


@pytest.mark.parametrize("links", [None, "not a dict", []])
def test_a_malformed_links_object_ends_the_walk_cleanly(links: object) -> None:
    fake = FakeConfluence(pages_body={"results": [make_page("1")], "_links": links})

    assert get_pages(fake).truncated is False


def test_a_response_with_no_results_key_is_survivable() -> None:
    fake = FakeConfluence(pages_body={"_links": {}})

    snapshot = get_pages(fake)

    assert snapshot.retrieved_pages == 0


@pytest.mark.parametrize("results", [None, "not a list", {}, 42])
def test_a_malformed_results_value_is_survivable(results: object) -> None:
    fake = FakeConfluence(pages_body={"results": results, "_links": {}})

    assert get_pages(fake).retrieved_pages == 0


def test_the_batch_ceiling_stops_a_server_that_never_finishes() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if api_path(request) != PAGES_PATH:
            return fake.handler(request)
        counter["n"] += 1
        return httpx.Response(
            200,
            json={
                "results": [make_page(str(counter["n"]))],
                "_links": {"next": f"/wiki/api/v2/pages?cursor={counter['n']}"},
            },
        )

    fake = FakeConfluence()
    connector = ConfluenceConnector(
        SITE,
        EMAIL,
        FAKE_TOKEN,
        max_batches=4,
        transport=httpx.MockTransport(handler),
    )

    snapshot = connector.get_pages(SPACE)

    assert counter["n"] == 4
    assert snapshot.truncated is True
    assert snapshot.errors == [(SPACE, "Ingestion stopped at the batch ceiling.")]


# ---------------------------------------------------------------- max_pages


def test_the_page_cap_stops_the_run() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(5)])

    snapshot = get_pages(fake, page_size=2, max_pages=3)

    assert snapshot.retrieved_pages == 3
    assert snapshot.truncated is True


def test_the_cap_shrinks_the_request_rather_than_trimming_the_answer() -> None:
    """A cap of 3 should not cost a full batch of 100 and then throw 97 away."""
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(50)])

    get_pages(fake, page_size=100, max_pages=3)

    assert int(fake.page_requests[0].url.params["limit"]) == 3


def test_the_final_request_asks_only_for_what_is_still_wanted() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(10)])

    get_pages(fake, page_size=2, max_pages=3)

    limits = [int(request.url.params["limit"]) for request in fake.page_requests]
    assert limits == [2, 1]


def test_the_cap_records_why_the_run_stopped() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(5)])

    snapshot = get_pages(fake, page_size=2, max_pages=3)

    assert snapshot.errors == [
        (SPACE, "Ingestion stopped at the 3-page cap; the space contains more.")
    ]


def test_a_cap_that_lands_exactly_on_the_last_page_is_not_truncation() -> None:
    """Nothing was missed, so nothing should say otherwise."""
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(3)])

    snapshot = get_pages(fake, page_size=2, max_pages=3)

    assert snapshot.retrieved_pages == 3
    assert snapshot.truncated is False
    assert snapshot.errors == []


def test_a_cap_above_the_page_count_changes_nothing() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(3)])

    snapshot = get_pages(fake, page_size=2, max_pages=100)

    assert snapshot.retrieved_pages == 3
    assert snapshot.truncated is False


def test_no_cap_retrieves_everything() -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(7)])

    assert get_pages(fake, page_size=2).retrieved_pages == 7


def test_a_server_that_ignores_the_limit_is_trimmed_anyway() -> None:
    """Confluence honours limit, but a proxy in front of it might not."""
    fake = FakeConfluence(
        pages_body={"results": [make_page(str(n)) for n in range(10)], "_links": {}}
    )

    snapshot = get_pages(fake, max_pages=4)

    assert snapshot.retrieved_pages == 4


# ------------------------------------------------------------ error mapping


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ConfluenceAuthenticationError),
        (403, ConfluencePermissionError),
        (429, ConfluenceRateLimitError),
        (500, ConfluenceApiError),
        (502, ConfluenceApiError),
        (503, ConfluenceApiError),
    ],
)
def test_space_lookup_failures_map_to_our_errors(
    status: int, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        get_pages(FakeConfluence(spaces_status=status))


def test_a_404_from_the_space_lookup_is_a_credential_problem() -> None:
    """Measured against the real gateway, not assumed.

    api.atlassian.com answers 404 for the entire /ex/confluence/{cloud id}
    prefix when it will not accept a credential - a wrong token, no token and a
    token for another site all look like this, and 401 never appears. Reporting
    it as "space not found" would send an operator to check the wrong thing.
    """
    with pytest.raises(ConfluenceAuthenticationError) as caught:
        get_pages(FakeConfluence(spaces_status=404))

    assert "credentials" in caught.value.message.lower()


def test_a_missing_space_is_told_apart_from_a_bad_credential() -> None:
    """The two 404s a caller could see come from genuinely different responses."""
    with pytest.raises(ConfluenceNotFoundError):
        get_pages(FakeConfluence(spaces=[]))  # 200, no matching space

    with pytest.raises(ConfluenceAuthenticationError):
        get_pages(FakeConfluence(spaces_status=404))  # the gateway refusing us


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ConfluenceAuthenticationError),
        (403, ConfluencePermissionError),
        (404, ConfluenceNotFoundError),
        (429, ConfluenceRateLimitError),
        (500, ConfluenceApiError),
        (503, ConfluenceApiError),
    ],
)
def test_page_read_failures_map_to_our_errors(
    status: int, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        get_pages(FakeConfluence(pages_status=status))


def test_a_rejected_page_request_is_reported_as_ours_not_the_callers() -> None:
    """A 400 means the request this module built is wrong."""
    with pytest.raises(ConfluenceApiError) as caught:
        get_pages(FakeConfluence(pages_status=400))

    assert "rejected" in caught.value.message.lower()


def test_a_bad_credential_stops_before_any_page_is_read() -> None:
    fake = FakeConfluence(spaces_status=401)

    with pytest.raises(ConfluenceAuthenticationError):
        get_pages(fake)

    assert PAGES_PATH not in fake.paths


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("no route"), httpx.ReadError("reset")]
)
def test_a_network_failure_is_an_api_error(error: Exception) -> None:
    with pytest.raises(ConfluenceApiError):
        get_pages(FakeConfluence(raises=error))


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("slow"), httpx.ReadTimeout("slow")]
)
def test_a_timeout_says_so(error: Exception) -> None:
    with pytest.raises(ConfluenceApiError) as caught:
        get_pages(FakeConfluence(raises=error))

    assert "in time" in caught.value.message


def test_a_timeout_reading_pages_says_so() -> None:
    """The earlier cases fail at the tenant lookup; this one gets much further."""

    def handler(request: httpx.Request) -> httpx.Response:
        if api_path(request) == PAGES_PATH:
            raise httpx.ReadTimeout("slow")
        return fake.handler(request)

    fake = FakeConfluence()
    connector = ConfluenceConnector(
        SITE, EMAIL, FAKE_TOKEN, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(ConfluenceApiError) as caught:
        connector.get_pages(SPACE)

    assert "in time" in caught.value.message


def test_a_network_failure_reading_pages_is_an_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if api_path(request) == PAGES_PATH:
            raise httpx.ConnectError("no route")
        return fake.handler(request)

    fake = FakeConfluence()
    connector = ConfluenceConnector(
        SITE, EMAIL, FAKE_TOKEN, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(ConfluenceApiError):
        connector.get_pages(SPACE)


@pytest.mark.parametrize(
    "text", ["<html>Login</html>", "", "null", "[1, 2, 3]", '"a string"']
)
def test_a_body_that_is_not_a_json_object_is_an_api_error(text: str) -> None:
    with pytest.raises(ConfluenceApiError) as caught:
        get_pages(FakeConfluence(spaces_text=text))

    assert "could not be read" in caught.value.message


def test_an_unreadable_page_body_is_an_api_error() -> None:
    with pytest.raises(ConfluenceApiError):
        get_pages(FakeConfluence(pages_text="<html>Login</html>"))


def test_an_unreadable_tenant_body_is_an_api_error() -> None:
    with pytest.raises(ConfluenceApiError):
        get_pages(FakeConfluence(tenant_body=["not", "an", "object"]))


# ----------------------------------------------------------------- logging


def test_the_run_narrates_its_batches(caplog) -> None:
    fake = FakeConfluence(pages=[make_page(str(n)) for n in range(3)])

    with caplog.at_level("INFO", logger="app.connectors.confluence_connector"):
        get_pages(fake, page_size=2)

    assert "Confluence page batch 1 returned 2 pages" in caplog.text
    assert "Confluence page batch 2 returned 1 pages" in caplog.text
    assert "Retrieved 3 Confluence pages" in caplog.text


def test_the_resolved_space_is_logged(caplog) -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    with caplog.at_level("INFO", logger="app.connectors.confluence_connector"):
        get_pages(fake)

    assert f"Resolved Confluence space {SPACE} ({SPACE_ID})" in caplog.text


def test_an_upstream_error_body_is_logged_but_not_returned(caplog) -> None:
    """Atlassian's own wording helps an operator and hurts a client."""

    def handler(request: httpx.Request) -> httpx.Response:
        if api_path(request) == SPACES_PATH:
            return httpx.Response(500, text="instance-internal-detail-sentinel")
        return fake.handler(request)

    fake = FakeConfluence()
    connector = ConfluenceConnector(
        SITE, EMAIL, FAKE_TOKEN, transport=httpx.MockTransport(handler)
    )

    with caplog.at_level("WARNING", logger="app.connectors.confluence_connector"):
        with pytest.raises(ConfluenceApiError) as caught:
            connector.get_pages(SPACE)

    assert "instance-internal-detail-sentinel" in caplog.text
    assert "instance-internal-detail-sentinel" not in caught.value.message


def test_a_page_body_is_never_logged(caplog) -> None:
    """Log volume tracks batches, not prose."""
    fake = FakeConfluence(pages=[make_page("1", body="<p>sentinel-page-body</p>")])

    with caplog.at_level("DEBUG"):
        get_pages(fake)

    assert "sentinel-page-body" not in caplog.text


def test_page_ids_are_available_at_debug(caplog) -> None:
    fake = FakeConfluence(pages=[make_page("6422643")])

    with caplog.at_level("DEBUG", logger="app.connectors.confluence_connector"):
        get_pages(fake)

    assert "Retrieved page 6422643" in caplog.text


# ---------------------------------------------------------------- security


def test_the_api_token_never_reaches_the_logs(caplog) -> None:
    fake = FakeConfluence(pages=[make_page("1")])
    secret = FAKE_TOKEN.get_secret_value()
    encoded = base64.b64encode(f"{EMAIL}:{secret}".encode()).decode()

    with caplog.at_level("DEBUG"):
        get_pages(fake)

    assert secret not in caplog.text
    assert "confluence_fake" not in caplog.text
    assert encoded not in caplog.text


def test_the_email_is_treated_as_a_credential(caplog) -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    with caplog.at_level("DEBUG"):
        get_pages(fake)

    assert EMAIL not in caplog.text


def test_no_authorization_header_is_logged(caplog) -> None:
    fake = FakeConfluence(spaces_status=500)

    with caplog.at_level("DEBUG"):
        with pytest.raises(ConfluenceApiError):
            get_pages(fake)

    assert "authorization" not in caplog.text.lower()
    assert "Basic " not in caplog.text


def test_the_api_token_never_appears_in_an_error_message() -> None:
    with pytest.raises(ConfluenceApiError) as caught:
        get_pages(FakeConfluence(spaces_status=500))

    assert FAKE_TOKEN.get_secret_value() not in caught.value.message
    assert EMAIL not in caught.value.message


def test_the_connector_does_not_keep_the_credentials_on_an_attribute() -> None:
    """The token reaches one expression - the httpx auth tuple - and stops."""
    connector = build_connector(FakeConfluence())

    state = repr(vars(connector))

    assert FAKE_TOKEN.get_secret_value() not in state
    assert EMAIL not in state


def test_closing_releases_the_http_clients() -> None:
    fake = FakeConfluence(pages=[make_page("1")])

    with build_connector(fake) as connector:
        connector.get_pages(SPACE)
        assert connector._client.is_closed is False

    assert connector._client.is_closed is True
    assert connector._public_client.is_closed is True


def test_the_connector_closes_even_when_the_run_fails() -> None:
    with build_connector(FakeConfluence(spaces_status=401)) as connector:
        with pytest.raises(ConfluenceAuthenticationError):
            connector.get_pages(SPACE)

    assert connector._client.is_closed is True
