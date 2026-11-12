"""Rule and flow content: closed types, size limits and redacted secrets.

What does not pass these models is not stored. Arbitrary JSON, from a person or the LLM,
does not become a rule the agent can use.
"""

import re
import uuid
from typing import Annotated, Any, Final, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from scrinium.core.secret_patterns import redact_tokens
from scrinium.domain.enums import EntityType, FlowStepType, TransitionType

MAX_ITEMS: Final = 20
MAX_STEPS: Final = 100
MAX_TRANSITIONS: Final = 300


Short = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(redact_tokens),
]
Line = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    AfterValidator(redact_tokens),
]
Long = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
    AfterValidator(redact_tokens),
]
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
    AfterValidator(redact_tokens),
]
StepKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")]


class _Content(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuleScope(_Content):
    products: list[Short] = Field(default_factory=list, max_length=MAX_ITEMS)
    modules: list[Short] = Field(default_factory=list, max_length=MAX_ITEMS)
    services: list[Short] = Field(default_factory=list, max_length=MAX_ITEMS)
    paths: list[Short] = Field(default_factory=list, max_length=MAX_ITEMS)
    entities: list[Short] = Field(default_factory=list, max_length=MAX_ITEMS)


class Condition(_Content):
    description: Line


class RuleException(_Content):
    condition: Line
    behavior: Line


class ImpactTarget(_Content):
    kind: EntityType
    ref: Short


class RuleContent(_Content):
    """A business-rule version. It goes to `business_rule_versions`, not to `payload`."""

    title: Title
    statement: Long
    rationale: Long | None = None
    scope: RuleScope = Field(default_factory=RuleScope)
    preconditions: list[Condition] = Field(default_factory=list, max_length=MAX_ITEMS)
    trigger_conditions: list[Condition] = Field(default_factory=list, max_length=MAX_ITEMS)
    expected_behavior: list[Condition] = Field(default_factory=list, max_length=MAX_ITEMS)
    exceptions: list[RuleException] = Field(default_factory=list, max_length=MAX_ITEMS)
    impact_targets: list[ImpactTarget] = Field(default_factory=list, max_length=MAX_ITEMS)


class FlowStepInput(_Content):
    key: StepKey
    step_type: FlowStepType
    label: Title
    description: Long | None = None
    entity_id: uuid.UUID | None = None
    # A description for people. Never evaluated.
    condition_expression: Line | None = None


class FlowTransitionInput(_Content):
    from_key: StepKey
    to_key: StepKey
    transition_type: TransitionType
    condition: Line | None = None
    evidence_id: uuid.UUID | None = None


class FlowContent(_Content):
    description: Long | None = None
    entry_conditions: list[Line] = Field(default_factory=list, max_length=MAX_ITEMS)
    success_conditions: list[Line] = Field(default_factory=list, max_length=MAX_ITEMS)
    failure_conditions: list[Line] = Field(default_factory=list, max_length=MAX_ITEMS)
    steps: list[FlowStepInput] = Field(min_length=1, max_length=MAX_STEPS)
    transitions: list[FlowTransitionInput] = Field(default_factory=list, max_length=MAX_TRANSITIONS)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        keys = [step.key for step in self.steps]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate_step_key")
        known = set(keys)
        seen = set()
        for transition in self.transitions:
            if transition.from_key not in known or transition.to_key not in known:
                raise ValueError("unknown_step")
            if (
                transition.transition_type is TransitionType.CONDITIONAL
                and not transition.condition
            ):
                raise ValueError("condition_required")
            edge = (transition.from_key, transition.to_key, transition.transition_type)
            if edge in seen:
                raise ValueError("duplicate_transition")
            seen.add(edge)
        return self


# F3: a statement written as "Si <when>, <then>." is split into its two halves.
_CONDITION_START: Final = re.compile(
    r"^(?:si|cuando|mientras|al|en cuanto|solo si)\b", re.IGNORECASE
)
_PART_MAX_CHARS: Final = 1000


def split_statement(statement: str) -> tuple[str | None, str]:
    """(when, then) of a statement. Without a leading condition, the whole statement is what
    the rule demands. Cuts at the first comma outside parentheses and quotes."""
    text = statement.strip()
    if not _CONDITION_START.match(text):
        return None, text
    depth, quoted = 0, None
    for index, char in enumerate(text):
        if quoted:
            quoted = None if char == quoted else quoted
        elif char in "\"'«":
            quoted = "»" if char == "«" else char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            when, then = text[:index].strip(), text[index + 1 :].strip().rstrip(".")
            if when and then:
                return when, then[0].upper() + then[1:] + "."
            break
    return None, text


def shaped(statement: str) -> dict[str, Any]:
    """The statement and, split from it, when the rule applies and what it demands (F3)."""
    when, then = split_statement(statement)
    if len(then) > _PART_MAX_CHARS or (when and len(when) > _PART_MAX_CHARS):
        # A long text written by a person: kept whole, not split.
        when, then = None, statement.strip()[:_PART_MAX_CHARS]
    return {
        "statement": statement.strip(),
        "trigger_conditions": [{"description": when}] if when else [],
        "expected_behavior": [{"description": then}],
    }
