from app.entities.data_sources.source_type import SourceType
from app.ingestion.embedding_service import ChunkEmbedder
from app.models.chat.message import ChatMessage
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.pipeline_result import RetrievalPipelineResult
from app.retrieval.execution.executor import RetrievalExecutor
from app.retrieval.execution.query_enricher import QueryEnricher
from app.retrieval.llm import build_chat_model
from app.retrieval.pipeline import RetrievalPipeline
from app.retrieval.planning.planner import RetrievalPlanner
from app.retrieval.prompt_processing.processor import PromptProcessor
from app.retrieval.retrievers.confluence_retriever import ConfluenceRetriever
from app.retrieval.retrievers.github_retriever import GitHubRetriever
from app.retrieval.retrievers.jira_retriever import JiraRetriever
from app.retrieval.retrievers.slack_retriever import SlackRetriever
from app.retrieval.search.knowledge_search_service import KnowledgeSearchService


class RetrievalService:

    def __init__(self, pipeline: RetrievalPipeline) -> None:
        self.pipeline = pipeline

    def start(
        self,
        query: str,
        access: AccessContext,
        history: list[ChatMessage] | None = None,
    ) -> RetrievalPipelineResult:
        return self.pipeline.run(query=query, access=access, history=history)


def build_retrieval_service() -> RetrievalService:

    llm = build_chat_model()

    search = KnowledgeSearchService(ChunkEmbedder())

    executor = RetrievalExecutor(
        retrievers={
            SourceType.JIRA: JiraRetriever(search),
            SourceType.GITHUB: GitHubRetriever(search),
            SourceType.SLACK: SlackRetriever(search),
            SourceType.CONFLUENCE: ConfluenceRetriever(search),
        },
        query_enricher=QueryEnricher(llm),
    )

    return RetrievalService(
        RetrievalPipeline(PromptProcessor(llm), RetrievalPlanner(llm), executor)
    )
