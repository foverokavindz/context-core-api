from enum import Enum


class InformationNeed(str, Enum):
    """make the Prompt Processor return consistent, predictable"""

    # Requirements / work tracking
    REQUIREMENTS = "REQUIREMENTS"
    ACCEPTANCE_CRITERIA = "ACCEPTANCE_CRITERIA"
    PREVIOUS_WORK = "PREVIOUS_WORK"
    CHANGE_HISTORY = "CHANGE_HISTORY"
    BUG_HISTORY = "BUG_HISTORY"

    # Implementation / code
    IMPLEMENTATION = "IMPLEMENTATION"
    CODE_STRUCTURE = "CODE_STRUCTURE"
    TESTS = "TESTS"
    DEPENDENCIES = "DEPENDENCIES"
    IMPACT = "IMPACT"

    # Architecture / system understanding
    ARCHITECTURE = "ARCHITECTURE"
    DATA_FLOW = "DATA_FLOW"
    SERVICE_RELATIONSHIPS = "SERVICE_RELATIONSHIPS"
    INTEGRATIONS = "INTEGRATIONS"

    # Decisions / collaboration
    DECISIONS = "DECISIONS"
    DISCUSSIONS = "DISCUSSIONS"
    RATIONALE = "RATIONALE"

    # Supporting knowledge
    DOCUMENTATION = "DOCUMENTATION"
    CONFIGURATION = "CONFIGURATION"
    RISKS = "RISKS"
    LIMITATIONS = "LIMITATIONS"
