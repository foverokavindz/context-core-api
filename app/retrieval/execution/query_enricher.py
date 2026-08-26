import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.core.exceptions import LLMError
from app.models.retrieval.query_enrichment import EnrichedQuery, QueryEnrichmentInput
from app.retrieval.execution.prompt import PROMPT

logger = logging.getLogger(__name__)

_NOT_WORTH_READING = {
    "dependency_results": {
        "__all__": {
            "chunk_id",
            "resource_id",
            "external_data_source_id",
            "score",
            "chunk_metadata",
            "resource_metadata",
        }
    }
}


class QueryEnricher:
    """Turns what an earlier step found into what the next one should search for."""

    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm.with_structured_output(EnrichedQuery)

    def enrich(self, request: QueryEnrichmentInput) -> EnrichedQuery:
        messages = PROMPT.format_messages(
            request=[
                HumanMessage(
                    content=request.model_dump_json(
                        indent=2, exclude=_NOT_WORTH_READING
                    )
                )
            ],
        )

        try:
            enriched = self.llm.invoke(messages)

        except Exception as exc:
            logger.error("The chat model failed: %s", type(exc).__name__)
            raise LLMError() from exc

        if not isinstance(enriched, EnrichedQuery):
            logger.error(
                "The chat model returned %s rather than an EnrichedQuery",
                type(enriched).__name__,
            )
            raise LLMError()

        logger.info(
            "Enriched a query from %d dependency result(s)",
            len(request.dependency_results),
        )
        return enriched
