"""Turns the four sources' item models into `resources` rows."""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.entities.knowledge_sources.resource import Resource
from app.models.confluence.page import ConfluencePage
from app.models.github.file import RepositoryFile
from app.models.jira.issue import JiraIssue
from app.models.slack.message import SlackMessage

logger = logging.getLogger(__name__)

#TODO : inherit from a base class for the four sources, so this can be a single type
ResourceItem = RepositoryFile | JiraIssue | ConfluencePage | SlackMessage

_NOT_METADATA = frozenset(
    {
        "external_data_source_id",
        "external_id",
        "title",
        "version_key",
        "resource_type",
        "access_scope",
        "team_id",
        "department_id",
        "content",
        "text",
    }
)


class ResourceRepository:
    """Writes `resources` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_new_resources(self, items: list[ResourceItem]) -> list[Resource]:
        """Insert one row per item and return them."""
        resources: list[Resource] = []
        seen: set[str] = set()

        for item in items:
            # (source, external_id) is unique, and a duplicate would abort the
            # whole run at the second insert rather than at this one item.
            if item.external_id in seen:
                logger.warning(
                    "Skipping duplicate external_id %s in this run", item.external_id
                )
                continue
            seen.add(item.external_id)

            resources.append(
                Resource(
                    external_data_source_id=item.external_data_source_id,
                    document_id=None,  
                    resource_type=item.resource_type,
                    external_id=item.external_id,
                    title=item.title,
                    version_key=item.version_key,
                    access_scope=item.access_scope,
                    team_id=item.team_id,
                    department_id=item.department_id,
                    resource_metadata=item.model_dump(
                        mode="json", exclude=_NOT_METADATA
                    ),
                )
            )

        self.session.add_all(resources)
        self.session.flush()
        return resources

    def list_by_source(self, source_id: UUID, limit: int) -> list[Resource]:
        """What this source has produced, most recently touched first."""
        statement = (
            select(Resource)
            .where(Resource.external_data_source_id == source_id)
            .order_by(Resource.updated_at.desc(), Resource.id)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def count_by_source_ids(self, source_ids: Sequence[UUID]) -> dict[UUID, int]:
        """How many resources each of these sources produced, in one query."""
        if not source_ids:
            return {}

        rows = self.session.execute(
            select(
                Resource.external_data_source_id,
                func.count(Resource.id),
            )
            .where(Resource.external_data_source_id.in_(source_ids))
            .group_by(Resource.external_data_source_id)
        ).all()
        return {source_id: count for source_id, count in rows}
