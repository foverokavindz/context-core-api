from enum import Enum


class MemberRole(str, Enum):

    TEAM_LEAD = "TEAM_LEAD"
    TEAM_MEMBER = "TEAM_MEMBER"
