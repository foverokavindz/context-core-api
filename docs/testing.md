# Testing

[← Documentation](README.md)

```bash
pytest app/tests -v
```

1,192 tests, no network access and no credentials required. PyGithub is replaced
with fakes, and Jira, Confluence and Slack with an `httpx.MockTransport`, all of
which record which API calls were made — which is how the suite proves that
ignored files are never downloaded, that the Jira issue cap shrinks the *request*
rather than trimming the answer, that every Confluence page request carries the
resolved space ID, that every Slack request names the one channel and that
`conversations.replies` is never called at all, and that a token never reaches a
response, a log or an error message.

| Module                           | Covers                                                                                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `test_file_filter.py`            | every include/exclude rule, plus segment- and suffix-matching regressions                                                 |
| `test_typescript_parser.py`      | each symbol kind, parent links, exact source spans, line ranges, TSX, fallbacks, syntax errors, the line-index regression |
| `test_github_connector.py`       | branch resolution, filter-before-fetch, binary/UTF-8 skips, error mapping, token containment                              |
| `test_ingestion_service.py`      | the real GitHub pipeline end to end with only the network faked                                                           |
| `test_api.py`                    | request validation, response projection, HTTP status mapping, token never echoed                                          |
| `test_jira_adf.py`               | every ADF node kind, nesting, unknown nodes, malformed shapes, recursion depth — all exact-string assertions              |
| `test_jira_parser.py`            | each field and each fallback, null tolerance, the one fatal case (no issue key)                                           |
| `test_jira_chunker.py`           | the chunk template, omitted lines, one-chunk-per-issue, an Epic never carrying a child's description                      |
| `test_jira_connector.py`         | cloud-ID resolution and the gateway, the JQL, the six fields, pagination and its three loop guards, the cap, error mapping, credential containment |
| `test_jira_ingestion_service.py` | the real Jira pipeline end to end; linking, orphans, and the no-N+1 guarantee                                             |
| `test_jira_api.py`               | request validation, sampling vs truncation, HTTP status mapping, token never echoed                                       |
| `test_confluence_storage.py`     | every storage element kind, macros, nesting, noise removal, malformed markup, recursion depth — all exact-string assertions |
| `test_confluence_parser.py`      | each field and each fallback, null tolerance, the one fatal case (no page id)                                            |
| `test_confluence_chunker.py`     | the chunk template, omitted lines, one-chunk-per-page, ids kept out of the text                                          |
| `test_confluence_connector.py`   | cloud-ID resolution and the gateway, the space lookup, **the space-ID confinement**, cursor pagination and its guards, the cap, error mapping, credential containment |
| `test_confluence_ingestion_service.py` | the real Confluence pipeline end to end with only the network faked                                                |
| `test_confluence_api.py`         | request validation, sampling vs truncation, HTTP status mapping, token never echoed                                      |
| `test_slack_connector.py`        | **the channel confinement and the one-method guarantee**, bearer auth, cursor pagination and its guards, the cap, `ok: false` mapping, the no-sleep rate limit, chronological ordering, token containment |
| `test_slack_parser.py`           | every filter rule and its near-miss twin, the three escapes, markup left verbatim, reactions/files/attachments/blocks ignored, a filtered message recording no error |
| `test_slack_chunker.py`          | the chunk is the message text alone, ids kept out of it, one-message-one-chunk                                          |
| `test_slack_ingestion_service.py`| the real Slack pipeline end to end with only the network faked; the funnel gap, and the connector closed before parsing  |
| `test_slack_api.py`              | request validation, sampling vs truncation vs filtering, HTTP status mapping, token never echoed                        |
| `test_ingestion_controller.py`   | the common endpoint: source resolution, the per-source config checks, what the ExternalDataSource is built from, and — running the pipeline directly — that permissions reach every item and chunk and that the token reaches neither the response nor the run file |
| `test_source_retrievers.py`      | the four retrievers over a faked search service: each searches its own source, every SourceType has one, query/top_k/access pass through unchanged |
| `test_knowledge_search_service.py` | the query embedded once and *that* vector reaching the repository, a session per search closed on every path including failure, never committing, and the three ways of having nothing to search costing neither a call nor a connection |
| `test_answer_generator.py`       | the last stage over a faked model: **that nothing is reranked** — every retrieved chunk shown, in plan order, and the returned sources being the ones its numbering points at — plus the three context-window trims (a duplicate chunk, an over-long chunk cut for the model but returned whole, the cap), the history window, and a model failure never carrying the vendor's words into the response |
| `test_chunk_search_query.py`     | **the vector query's two permission filters**, compiled to real SQL with `literal_binds`: team source ownership, the access-scope disjunction read from `resources` and not the chunk's stale copy, another team's ids appearing nowhere, unembedded chunks skipped, cosine ranking and `top_k` — plus the row mapping, and that no vector, token or connector config is ever selected |

The Jira, Confluence and Slack connector tests drive a **real** `httpx.Client`
through a mock transport, so base-URL joining, the auth header and query-string
encoding are all genuinely exercised rather than patched out.

## Verifying against real sources

The automated suite never touches the network. Each connector page ends with a
by-hand checklist for running it against a real account:

- [GitHub — against a real repository](connectors/github.md#manual-verification-against-a-real-repository)
- [Jira — against a real project](connectors/jira.md#manual-verification-against-a-real-jira-project)
- [Confluence — against a real space](connectors/confluence.md#manual-verification-against-a-real-confluence-space)
- [Slack — against a real channel](connectors/slack.md#manual-verification-against-a-real-slack-channel)
