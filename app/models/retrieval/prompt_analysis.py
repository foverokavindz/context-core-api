"""What the prompt processor understood about one question."""

from pydantic import BaseModel, ConfigDict, Field

from app.entities.data_sources.source_type import SourceType
from app.models.retrieval.ontology.entity_type import EntityType
from app.models.retrieval.ontology.information_need import InformationNeed
from app.models.retrieval.ontology.query_intent import QueryIntent


class ExtractedEntity(BaseModel):

    type: EntityType = Field(
        description="Which kind of thing this is. Use UNKNOWN rather than "
        "guessing when the category is genuinely unclear.",
    )

    value: str = Field(
        description="The thing itself, as the conversation named it.",
        examples=["TRACK-25", "authentication", "Redis"],
    )


class PromptAnalysis(BaseModel):

    model_config = ConfigDict(extra="forbid")

    resolved_query: str = Field(
        description="What the person is actually asking, written out in full "
        "natural language with any reference to the conversation resolved. "
        "Preserves their meaning - it does not answer them.",
        examples=["What tests are related to the implementation of TRACK-25?"],
    )

    intent: QueryIntent = Field(
        description="The primary thing they are trying to do. BROAD_CONTEXT "
        "when they ask for several major knowledge areas at once.",
    )

    entities: list[ExtractedEntity] = Field(
        default_factory=list,
        description="Only things named in the query or the conversation "
        "history. Never invented.",
    )

    information_needs: list[InformationNeed] = Field(
        default_factory=list,
        description="What kinds of information answering this would take. One "
        "question can need several, which is why this is separate from intent.",
    )

    improved_query: str = Field(
        description="A short, descriptive query for semantic search. Adds no "
        "fact that was not in the input.",
        examples=["authentication previous changes implementation architecture design"],
    )

    explicit_sources: list[SourceType] = Field(
        default_factory=list,
        description="Populated only when the person names a source outright "
        "(\"search Jira\", \"check Slack\"). A topic never implies a source - "
        "choosing where to look is a later stage's job.",
    )

    retrieval_required: bool = Field(
        default=True,
        description="False only for requests that the conversation alone "
        "answers, such as thanks or asking for a previous reply to be repeated.",
    )
