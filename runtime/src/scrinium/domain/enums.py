"""Closed domain vocabulary. The database enforces the same values with CHECK."""

from enum import StrEnum


class WorkspaceType(StrEnum):
    PERSONAL_LOCAL = "personal_local"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AiMode(StrEnum):
    """Where a project's text may be sent to a model (ADR-004, ADR-010). Per project."""

    # Every model runs on this machine. The default, for every project.
    LOCAL_ONLY = "local_only"
    # A person consented to the remote models named in this project's consent.
    REMOTE_ALLOWED = "remote_allowed"


class ProviderKind(StrEnum):
    """How Scrinium talks to a remote model provider (ADR-010)."""

    # `/v1/chat/completions` over HTTPS: DeepSeek, GLM and other compatible APIs.
    OPENAI_COMPATIBLE = "openai_compatible"
    # Anthropic's Messages API, through its official SDK.
    ANTHROPIC = "anthropic"


class AiTask(StrEnum):
    """The tasks that use a chat model; each can be sent to its own model (M2, ADR-010)."""

    QUESTIONS = "questions"
    RULE_MAP = "rule_map"
    RULE_STATEMENTS = "rule_statements"
    CONSISTENCY = "consistency"
    CHANGE_IMPACT = "change_impact"
    # One sentence on how a verified rule drifted, for `scrinium check --explain` (phase 2.4).
    CHECK_EXPLAIN = "check_explain"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    REVOKED = "revoked"
    DISABLED = "disabled"


class SyncTrigger(StrEnum):
    INITIAL = "initial"
    WEBHOOK = "webhook"
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    RECONCILIATION = "reconciliation"


class SyncStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemLifecycle(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"
    INACCESSIBLE = "inaccessible"
    ARCHIVED = "archived"


class AuditResult(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


class AnalysisType(StrEnum):
    QUESTION = "question"
    KNOWLEDGE_EXTRACTION = "knowledge_extraction"
    CODE_DIFF = "code_diff"
    SQL_REVIEW = "sql_review"
    RELEASE_COMPARISON = "release_comparison"
    IMPACT_ANALYSIS = "impact_analysis"
    BUG_INVESTIGATION = "bug_investigation"


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    # Finished, but a part could not be done: the result says which.
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# Knowledge model.


class EntityType(StrEnum):
    BUSINESS_RULE = "business_rule"
    REQUIREMENT = "requirement"
    ARCHITECTURE_DECISION = "architecture_decision"
    BUSINESS_FLOW = "business_flow"
    USER_JOURNEY = "user_journey"
    ARCHITECTURE_FLOW = "architecture_flow"
    DATA_FLOW = "data_flow"
    RELEASE = "release"
    SERVICE = "service"
    MODULE = "module"
    CODE_SYMBOL = "code_symbol"
    API_ENDPOINT = "api_endpoint"
    DATA_ENTITY = "data_entity"
    EVENT = "event"
    TEST_CASE = "test_case"
    TICKET = "ticket"
    DOCUMENT = "document"


class EntityLifecycle(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class VersionOrigin(StrEnum):
    HUMAN = "human"
    SOURCE_EXTRACTION = "source_extraction"
    DERIVED_ANALYSIS = "derived_analysis"
    IMPORT = "import"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    SOURCE_SUPPORTED = "source_supported"
    TEST_SUPPORTED = "test_supported"
    HUMAN_VERIFIED = "human_verified"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"


class RuleCategory(StrEnum):
    INVARIANT = "invariant"
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    CALCULATION = "calculation"
    STATE_TRANSITION = "state_transition"
    WORKFLOW = "workflow"
    INTEGRATION_CONTRACT = "integration_contract"
    PERFORMANCE_CONSTRAINT = "performance_constraint"
    SECURITY_CONSTRAINT = "security_constraint"
    OTHER = "other"


class Criticality(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceRole(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"
    IMPLEMENTS = "implements"
    TESTS = "tests"
    REQUESTS = "requests"
    SUPERSEDES = "supersedes"


class EvidenceType(StrEnum):
    CODE = "code"
    TEST = "test"
    TICKET = "ticket"
    DOCUMENT = "document"
    COMMIT = "commit"
    SCHEMA = "schema"
    HUMAN_REVIEW = "human_review"
    OTHER = "other"


class VerificationMethod(StrEnum):
    DIRECT_REFERENCE = "direct_reference"
    STATIC_ANALYSIS = "static_analysis"
    TEST_RESULT = "test_result"
    HUMAN_REVIEW = "human_review"
    LLM_PROPOSAL = "llm_proposal"


class RelationType(StrEnum):
    DEPENDS_ON = "depends_on"
    IMPLEMENTED_BY = "implemented_by"
    TESTED_BY = "tested_by"
    CALLS = "calls"
    READS = "reads"
    WRITES = "writes"
    TRIGGERS = "triggers"
    IMPACTS = "impacts"
    DOCUMENTS = "documents"
    IMPLEMENTS_REQUIREMENT = "implements_requirement"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    PUBLISHES = "publishes"
    SUBSCRIBES = "subscribes"
    ROUTES_TO = "routes_to"
    RELATED_TO = "related_to"


class FindingType(StrEnum):
    RULE_VIOLATION = "rule_violation"
    POSSIBLE_REGRESSION = "possible_regression"
    MISSING_TEST = "missing_test"
    CONTRADICTION = "contradiction"
    IMPACT_RISK = "impact_risk"
    KNOWLEDGE_GAP = "knowledge_gap"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    OTHER = "other"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AssessmentStatus(StrEnum):
    HYPOTHESIS = "hypothesis"
    EVIDENCE_SUPPORTED = "evidence_supported"
    TEST_VERIFIED = "test_verified"
    HUMAN_CONFIRMED = "human_confirmed"
    FALSE_POSITIVE = "false_positive"
    UNRESOLVED = "unresolved"


class EvidenceRelevance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ReleaseStatus(StrEnum):
    DISCOVERED = "discovered"
    INDEXING = "indexing"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


class FlowType(StrEnum):
    BUSINESS = "business"
    USER_JOURNEY = "user_journey"
    ARCHITECTURE = "architecture"
    DATA_FLOW = "data_flow"
    INTEGRATION = "integration"


class FlowStepType(StrEnum):
    START = "start"
    ACTION = "action"
    DECISION = "decision"
    EVENT = "event"
    EXTERNAL_CALL = "external_call"
    END = "end"


class TransitionType(StrEnum):
    NEXT = "next"
    CONDITIONAL = "conditional"
    ERROR = "error"
    RETRY = "retry"
    ASYNC_EVENT = "async_event"


class TicketProvider(StrEnum):
    """A ticket tracker Scrinium reads (ADR-014). Jira follows Linear with the same design."""

    LINEAR = "linear"
    # Jira Cloud (REST v3, API token with the account e-mail) and Data Center (REST v2, PAT).
    JIRA = "jira"


class TicketLinkLevel(StrEnum):
    """How sure Scrinium is that a ticket belongs to a rule or a commit (ADR-014, annex C.4)."""

    # The provider links the commit or pull request itself.
    EXPLICIT = "explicit"
    # The ticket's key appears in a commit message, a branch or a pull request title.
    MENTIONED = "mentioned"
    # Same period, same files, similar text: a proposal for a person to review.
    INFERRED = "inferred"
    # A person linked it, or accepted an inferred link.
    CONFIRMED = "confirmed"
