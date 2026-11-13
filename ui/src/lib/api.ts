export interface SecurityStatus {
  runtime: {
    mode: "local"
    version: string
    bind_address: string
    port: number
    running_as_admin: boolean
  }
  providers: {
    llm: EmbeddingStatus
    llm_model: string
    embeddings: EmbeddingStatus
    embedding_model: string
  }
  storage: {
    data_dir_permissions: "private" | "not_checked"
    disk_encryption: "encrypted" | "not_detected" | "unknown"
    secret_store: "available" | "unavailable"
    secret_store_backend: string | null
  }
  ai_mode: "local_only"
  connected_sources: number
  limits: string[]
}

export interface DataFlow {
  ai_mode: "local_only"
  remote_ai_providers: string[]
  outbound_destinations: string[]
  stored_locally: string[]
}

export interface Workspace {
  id: string
  name: string
  workspace_type: "personal_local"
  created_at: string
}

export interface Project {
  id: string
  name: string
  description: string
  project_key: string
  status: "active" | "archived"
  created_at: string
  updated_at: string
}

export interface NewProject {
  name: string
  project_key?: string
  description?: string
}

export type EmbeddingStatus = "ready" | "model_missing" | "unavailable"

export interface SearchHit {
  chunk_id: string
  source_id: string
  revision_id: string
  path: string
  commit: string | null
  start_line: number
  end_line: number
  heading: string | null
  text: string
  score: number
  matched_by: ("symbol" | "lexical" | "semantic")[]
}

export interface IndexStatus {
  chunks: number
  embedded: number
  provider: EmbeddingStatus
  model: string
}

export type SourceKind = "git_repository" | "folder"

export interface SourceRequest {
  kind: SourceKind
  path: string
  exclude_patterns: string[]
}

export interface SourcePreview {
  kind: SourceKind
  root: string
  commit: string | null
  included_count: number
  included_bytes: number
  excluded_count: number
  excluded_by_reason: Record<string, number>
  included: { path: string; size: number }[]
  excluded: { path: string; reason: string; rule: string | null; line: number | null }[]
}

export interface SyncRun {
  id: string
  trigger: string
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled"
  cursor_before: string | null
  cursor_after: string | null
  items_seen: number
  items_created: number
  items_updated: number
  items_deleted: number
  items_excluded: number
  error_summary: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Source {
  id: string
  kind: SourceKind
  display_name: string
  root: string
  exclude_patterns: string[]
  status: "pending" | "active" | "error" | "revoked" | "disabled"
  active_files: number
  last_successful_sync_at: string | null
  latest_sync: SyncRun | null
}

export class SessionRequiredError extends Error {}

/** API error with its code (`{"error": ...}`) and, on a 422, the rejected fields. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly fields: string[]

  constructor(status: number, code: string | null, fields: string[] = []) {
    super(`Respuesta inesperada: ${status}`)
    this.status = status
    this.code = code
    this.fields = fields
  }
}

async function apiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as {
      error?: string
      fields?: { loc: (string | number)[] }[]
    }
    const fields = (body.fields ?? []).map((field) => String(field.loc.at(-1)))
    return new ApiError(response.status, body.error ?? null, fields)
  } catch {
    return new ApiError(response.status, null)
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { credentials: "same-origin", ...init })
  if (response.status === 401) throw new SessionRequiredError()
  if (!response.ok) throw await apiError(response)
  return response
}

async function getJson<T>(path: string): Promise<T> {
  return (await request(path)).json() as Promise<T>
}

export const getSecurityStatus = () => getJson<SecurityStatus>("/api/v1/system/security-status")
export const getDataFlow = () => getJson<DataFlow>("/api/v1/privacy/data-flow")
export const getWorkspace = () => getJson<Workspace>("/api/v1/workspace")
export const getProjects = async () =>
  (await getJson<{ projects: Project[] }>("/api/v1/projects")).projects

// The token is requested on each operation and not stored in localStorage: so it never hits disk.
async function mutate(path: string, body?: unknown, method = "POST"): Promise<Response> {
  const { csrf_token } = await getJson<{ csrf_token: string }>("/api/v1/session")
  if (body === undefined) {
    return request(path, { method, headers: { "X-CSRF-Token": csrf_token } })
  }
  return request(path, {
    method,
    headers: { "X-CSRF-Token": csrf_token, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

export const logout = () => mutate("/api/v1/session/logout")
export const createProject = async (project: NewProject) =>
  (await mutate("/api/v1/projects", project)).json() as Promise<Project>

export const listSources = async (projectId: string) =>
  (await getJson<{ sources: Source[] }>(`/api/v1/projects/${projectId}/sources`)).sources
export const previewSource = async (projectId: string, source: SourceRequest) =>
  (await mutate(`/api/v1/projects/${projectId}/sources/preview`, source)).json() as Promise<SourcePreview>
export const createSource = async (projectId: string, source: SourceRequest) =>
  (await mutate(`/api/v1/projects/${projectId}/sources`, source)).json() as Promise<{
    source: Source
    sync_run: SyncRun
  }>
export const syncSource = async (sourceId: string) =>
  (await mutate(`/api/v1/sources/${sourceId}/sync`)).json() as Promise<SyncRun>
export const deleteSource = async (sourceId: string, deleteCiting = false) => {
  await mutate(
    `/api/v1/sources/${sourceId}?${new URLSearchParams({ delete_citing: String(deleteCiting) })}`,
    undefined,
    "DELETE",
  )
}
export const searchProject = (projectId: string, query: string) =>
  getJson<{ semantic: EmbeddingStatus; results: SearchHit[] }>(
    `/api/v1/projects/${projectId}/search?${new URLSearchParams({ q: query })}`,
  )
export const getIndexStatus = (projectId: string) =>
  getJson<IndexStatus>(`/api/v1/projects/${projectId}/index-status`)
export const rebuildIndex = async () => {
  await mutate("/api/v1/index/rebuild")
}
export interface LlmCheck {
  status: "ok" | "model_missing" | "unavailable" | "failed"
  model: string
  duration_ms: number
}
export const checkLlm = async () => (await mutate("/api/v1/llm/check")).json() as Promise<LlmCheck>

export interface CitedSource {
  number: number
  chunk_id: string
  revision_id: string
  source_id: string
  path: string
  commit: string | null
  start_line: number
  end_line: number
  heading: string | null
  matched_by: string[]
}

export interface QuestionResult {
  summary?: string
  model_explanation?: string | null
  claims?: { text: string; sources: number[] }[]
  dropped_claims?: number
  limitations?: string[]
  next_step?: string | null
  sources?: CitedSource[]
  sources_considered?: number
}

export interface Question {
  id: string
  project_id: string
  question: string
  status: "queued" | "running" | "completed" | "failed"
  answer_status: "answered" | "insufficient_evidence" | null
  error_summary: string | null
  created_at: string
  finished_at: string | null
  model_name?: string | null
  result?: QuestionResult
}

export interface CitedLines {
  path: string
  commit: string | null
  start: number
  end: number
  lines: string[]
}

export const askQuestion = async (projectId: string, question: string) =>
  (await mutate(`/api/v1/projects/${projectId}/questions`, { question })).json() as Promise<Question>
export const getQuestion = (questionId: string) => getJson<Question>(`/api/v1/questions/${questionId}`)
export const listQuestions = async (projectId: string) =>
  (await getJson<{ questions: Question[] }>(`/api/v1/projects/${projectId}/questions`)).questions
export const deleteQuestion = async (questionId: string) => {
  await mutate(`/api/v1/questions/${questionId}`, undefined, "DELETE")
}
export const getCitedLines = (revisionId: string, start: number, end: number) =>
  getJson<CitedLines>(
    `/api/v1/source-revisions/${revisionId}/lines?${new URLSearchParams({ start: String(start), end: String(end) })}`,
  )

// Knowledge, analyses and releases.

export type VerificationStatus =
  | "unverified"
  | "source_supported"
  | "test_supported"
  | "human_verified"
  | "conflicted"
  | "rejected"
export type AnalysisStatus = "queued" | "running" | "completed" | "partial" | "failed" | "cancelled"
export type AnalysisType =
  | "knowledge_extraction"
  | "code_diff"
  | "sql_review"
  | "release_comparison"
  | "impact_analysis"
  | "bug_investigation"
export type AssessmentStatus =
  | "hypothesis"
  | "evidence_supported"
  | "test_verified"
  | "human_confirmed"
  | "false_positive"
  | "unresolved"
export type Severity = "info" | "low" | "medium" | "high" | "critical"

export interface VersionSummary {
  id: string
  version_number: number
  verification_status: VerificationStatus
  origin: "human" | "source_extraction" | "derived_analysis" | "import"
  model_name: string | null
  extractor_version: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
}

export interface RuleSummary {
  entity_id: string
  canonical_key: string
  name: string
  summary: string
  lifecycle_status: "proposed" | "active" | "deprecated" | "retired"
  rule_category: string
  criticality: "low" | "medium" | "high" | "critical"
  latest_version: VersionSummary | null
  evidence_count: number
  review_reason: string | null
  /** Ticket keys named by the commits that introduced or changed the cited lines. */
  tickets: string[]
  /** Where the rule lives in the code: its first citation. */
  citation: {
    revision_id: string
    path: string
    commit: string | null
    start_line: number
    end_line: number
  } | null
}

export interface Evidence {
  id: string
  /** In a rule's detail: the function or method that holds the citation. */
  symbol?: {
    id: string
    name: string
    kind: string
    start_line: number
    end_line: number
    /** The huge top-level function that wraps this nested one, if any. */
    nested_in: string | null
  } | null
  role: string
  type: string
  method: "direct_reference" | "static_analysis" | "test_result" | "human_review" | "llm_proposal"
  status: VerificationStatus
  locator: { path?: string; commit?: string | null; start_line?: number; end_line?: number; reviewer?: string }
  excerpt: string | null
  revision_id: string | null
  created_at: string
}

export interface RuleVersion extends VersionSummary {
  content: {
    title: string
    statement: string
    rationale: string | null
    scope: Record<string, string[]>
    preconditions: { description: string }[]
    trigger_conditions: { description: string }[]
    expected_behavior: { description: string }[]
    exceptions: { condition: string; behavior: string }[]
    impact_targets: { kind: string; ref: string }[]
    from_analysis_id: string | null
  } | null
  evidence: Evidence[]
  profile: Record<"requested" | "documented" | "implemented" | "tested" | "contradicted", number>
  findings: { id: string; title: string; finding_type: string; assessment_status: AssessmentStatus; analysis_id: string }[]
}

export interface Relationship {
  id: string
  relation_type: string
  direction: "in" | "out"
  other: { id: string; name: string; entity_type: string }
  evidence_id: string | null
  /** Why Scrinium related them from the code ("same_function:end", "calls:2", "constant:MAX"). */
  basis: string | null
}

export interface RuleDetail extends Omit<RuleSummary, "latest_version" | "evidence_count"> {
  owner_ref: string | null
  versions: RuleVersion[]
  entity_evidence: Evidence[]
  relationships: Relationship[]
}

export interface Finding {
  id: string
  finding_type: string
  title: string
  description: string
  severity: Severity
  assessment_status: AssessmentStatus
  rule_version_id: string | null
  location: Record<string, unknown> | null
  recommended_next_step: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
  evidence: { evidence_id: string; relevance: string }[]
}

export interface Analysis {
  id: string
  project_id: string
  analysis_type: AnalysisType
  question: string
  status: AnalysisStatus
  error_summary: string | null
  base_revision: string | null
  target_revision: string | null
  release_ref: string | null
  created_at: string
  finished_at: string | null
  model_name?: string | null
  result?: Record<string, unknown>
  findings?: Finding[]
}

export interface Release {
  id: string
  source_connection_id: string
  release_name: string
  commit_sha: string | null
  previous_release_id: string | null
  released_at: string | null
  status: "discovered" | "indexing" | "ready" | "partial" | "failed"
  snapshot_id: string | null
  snapshot_version: number | null
}

export type NodeState =
  | "confirmed"
  | "supported"
  | "inferred"
  | "unverified"
  | "conflicted"
  | "rejected"
  | "no_version"
export type EdgeOrigin = "observed" | "supported" | "inferred" | "confirmed"

export interface GraphNode {
  id: string
  name: string
  entity_type: string
  canonical_key: string
  lifecycle_status: string
  state: NodeState
}

export interface GraphEdge {
  id: string
  from: string
  to: string
  relation_type: string
  origin: EdgeOrigin
  evidence_id: string | null
}

export interface KnowledgeGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  coverage: {
    entities_total: number
    entities_shown: number
    relationships_total: number
    relationships_shown: number
    entities_without_relationships: number
  }
}

export interface FlowSummary {
  entity_id: string
  name: string
  summary: string
  entity_type: string
  flow_type: string
  lifecycle_status: string
  latest_version: VersionSummary | null
}

export interface FlowDetail {
  entity_id: string
  name: string
  summary: string
  entity_type: string
  lifecycle_status: string
  versions: (VersionSummary & {
    description: string
    entry_conditions: string[]
    success_conditions: string[]
    failure_conditions: string[]
    steps: { key: string; position: number; step_type: string; label: string; description: string; condition_expression: string | null }[]
    transitions: { from: string; to: string; transition_type: string; condition: string | null; evidence_id: string | null }[]
  })[]
}

export const getProfile = () => getJson<{ display_name: string | null }>("/api/v1/profile")
export const updateProfile = async (displayName: string) =>
  (await mutate("/api/v1/profile", { display_name: displayName }, "PUT")).json() as Promise<{
    display_name: string | null
  }>

export const getSearchSettings = () => getJson<{ include_tests: boolean }>("/api/v1/search/settings")
export const updateSearchSettings = async (includeTests: boolean) =>
  (await mutate("/api/v1/search/settings", { include_tests: includeTests }, "PUT")).json() as Promise<{
    include_tests: boolean
  }>

/** The rule is real but badly worded: the text becomes a new version, confirmed by the profile. */
export const correctRule = async (projectId: string, entityId: string, statement: string) =>
  (await mutate(`/api/v1/projects/${projectId}/rules/${entityId}/correction`, { statement })).json() as Promise<RuleDetail>

export interface FunctionRef {
  symbol_id: string
  name: string
  kind: string
  path: string
  revision_id: string
  start_line: number
  end_line: number
}

/** Who a function calls and who calls it, resolved without the model (stage 3, phase B). */
export const getNeighbours = (projectId: string, symbolId: string) =>
  getJson<{ calls: FunctionRef[]; called_by: FunctionRef[]; ambiguous_calls: number }>(
    `/api/v1/projects/${projectId}/symbols/${symbolId}/neighbours`,
  )

export interface ArchitectureModule {
  key: string
  name: string
  kind: "folder" | "file" | "function"
  files: number
  functions: number
  rules_count: number
  rules: { entity_id: string; name: string; verification_status: VerificationStatus | null }[]
  calls_in: number
  calls_out: number
  start_line: number | null
  end_line: number | null
  revision_id: string | null
}

export interface Architecture {
  path: string
  level: "folder" | "file"
  modules: ArchitectureModule[]
  dependencies: { from: string; to: string; calls: number }[]
}

/** One level of the code as modules with their calls (stage 3, phase D). */
export const getArchitecture = (projectId: string, path = "") =>
  getJson<Architecture>(`/api/v1/projects/${projectId}/architecture?${new URLSearchParams({ path })}`)

export const listRules = async (
  projectId: string,
  filters: { verification?: VerificationStatus; lifecycle?: string; q?: string } = {},
) => {
  const params = new URLSearchParams(
    Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
  )
  return (await getJson<{ rules: RuleSummary[] }>(`/api/v1/projects/${projectId}/rules?${params}`)).rules
}
export const getRule = (projectId: string, entityId: string) =>
  getJson<RuleDetail>(`/api/v1/projects/${projectId}/rules/${entityId}`)
// S3: the rule map arrives before the statements. A rule opened without a statement gets it
// written right then, ahead of background extraction.
export const PENDING_STATEMENT_PREFIX = "Pendiente de redactar."
export const writeStatement = async (projectId: string, entityId: string) =>
  (await mutate(`/api/v1/projects/${projectId}/rules/${entityId}/statement`)).json() as Promise<RuleDetail>
export const setVerification = async (versionId: string, status: VerificationStatus) =>
  (await mutate(`/api/v1/knowledge-versions/${versionId}/verification`, { status })).json() as Promise<{
    id: string
    verification_status: VerificationStatus
    reviewed_by: string | null
  }>

export const getGraph = (projectId: string) => getJson<KnowledgeGraph>(`/api/v1/projects/${projectId}/graph`)
export const listFlows = async (projectId: string) =>
  (await getJson<{ flows: FlowSummary[] }>(`/api/v1/projects/${projectId}/flows`)).flows
export const getFlow = (projectId: string, entityId: string) =>
  getJson<FlowDetail>(`/api/v1/projects/${projectId}/flows/${entityId}`)

const started = async (path: string, body: unknown) => (await mutate(path, body)).json() as Promise<Analysis>
export const startExtraction = (projectId: string, pathPrefix: string, allFunctions = false) =>
  started(`/api/v1/projects/${projectId}/extractions`, {
    path_prefix: pathPrefix || null,
    ...(allFunctions ? { min_score: 0 } : {}),
  })
export const analyzeDiff = (projectId: string, diff: string) =>
  started(`/api/v1/projects/${projectId}/analyses/code-diff`, { diff })
export const analyzeSql = (projectId: string, sql: string, dialect: string, schemaDdl: string) =>
  started(`/api/v1/projects/${projectId}/analyses/sql`, {
    sql,
    dialect,
    schema_ddl: schemaDdl || null,
  })
export const compareReleases = (projectId: string, baseId: string, targetId: string) =>
  started(`/api/v1/projects/${projectId}/analyses/release-comparison`, {
    base_release_id: baseId,
    target_release_id: targetId,
  })
export const investigateBug = (projectId: string, description: string) =>
  started(`/api/v1/projects/${projectId}/analyses/bug`, { description })
export const getAnalysis = (analysisId: string) => getJson<Analysis>(`/api/v1/analyses/${analysisId}`)
export const listAnalyses = async (projectId: string, type?: AnalysisType) =>
  (
    await getJson<{ analyses: Analysis[] }>(
      `/api/v1/projects/${projectId}/analyses${type ? `?${new URLSearchParams({ type })}` : ""}`,
    )
  ).analyses
export const deleteAnalysis = async (analysisId: string) => {
  await mutate(`/api/v1/analyses/${analysisId}`, undefined, "DELETE")
}
export const sendFeedback = async (findingId: string, status: AssessmentStatus) =>
  (await mutate(`/api/v1/findings/${findingId}/feedback`, { status })).json() as Promise<Finding>

export const listReleases = async (projectId: string) =>
  (await getJson<{ releases: Release[] }>(`/api/v1/projects/${projectId}/releases`)).releases
export const discoverReleases = async (sourceId: string) =>
  (await mutate(`/api/v1/sources/${sourceId}/releases/discover`)).json() as Promise<{
    created: string[]
    existing: number
  }>
export const snapshotRelease = async (releaseId: string) =>
  (await mutate(`/api/v1/releases/${releaseId}/snapshots`)).json() as Promise<Release>

export const isFinished = (analysis: Analysis) =>
  analysis.status !== "queued" && analysis.status !== "running"

// Privacidad.

export interface DeletionImpact {
  files: number
  revisions: number
  releases: number
  evidence: number
  entities_to_delete: number
  entities_to_review: number
  citing_questions: number
  citing_analyses: number
}

export interface StoredData {
  counts: Record<string, number>
  bytes_on_disk: { database: number; vectors: number }
}

export const getDeletionImpact = (sourceId: string) =>
  getJson<DeletionImpact>(`/api/v1/sources/${sourceId}/deletion-impact`)
export const getStoredData = () => getJson<StoredData>("/api/v1/privacy/stored-data")
export const deleteProject = async (projectId: string, confirmation: string) =>
  (await mutate(`/api/v1/projects/${projectId}`, { confirmation }, "DELETE")).json() as Promise<
    Record<string, number>
  >
export const wipeWorkspace = async (confirmation: string) =>
  (await mutate("/api/v1/privacy/workspace-data", { confirmation }, "DELETE")).json() as Promise<
    Record<string, number>
  >

/** Downloads the export. Without a password, unencrypted (the UI warns first). */
export async function exportWorkspace(passphrase: string | null) {
  const response = await mutate(
    "/api/v1/privacy/export",
    passphrase === null ? { unencrypted: true } : { passphrase },
  )
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "scrinium-export"
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement("a")
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
  return name
}

/** Restores an export into an empty workspace. The password goes in a header. */
export async function restoreWorkspace(file: File, passphrase: string) {
  const { csrf_token } = await getJson<{ csrf_token: string }>("/api/v1/session")
  const headers: Record<string, string> = {
    "X-CSRF-Token": csrf_token,
    "Content-Type": "application/octet-stream",
  }
  if (passphrase) headers["X-Backup-Passphrase"] = passphrase
  const response = await request("/api/v1/privacy/restore", { method: "POST", headers, body: file })
  return response.json() as Promise<{ restored: Record<string, number> }>
}

// Hand-written rules and the local AI engine (Settings).

export type RuleCategory =
  | "invariant"
  | "validation"
  | "authorization"
  | "calculation"
  | "state_transition"
  | "workflow"
  | "integration_contract"
  | "performance_constraint"
  | "security_constraint"
  | "other"

export interface NewRule {
  title: string
  statement: string
  rationale?: string
  category: RuleCategory
  criticality: RuleSummary["criticality"]
}

export const createRule = async (projectId: string, rule: NewRule) =>
  (await mutate(`/api/v1/projects/${projectId}/rules`, rule)).json() as Promise<RuleDetail>

export interface EngineInfo {
  provider: "llama" | "ollama"
  memory_bytes: number | null
  engine: {
    name: string
    version: string
    installed: boolean
    running: boolean
    accelerator: "vulkan" | "metal" | "cpu" | null
  }
  models: { role: "chat" | "embeddings"; name: string; file: string; size_bytes: number; installed: boolean }[]
}

export const getEngine = () => getJson<EngineInfo>("/api/v1/system/engine")

export interface ExtractionStatus {
  pending: number
  done: number
  failed: number
  running_analysis_id: string | null
  /** Extraction waits until the machine has free memory; it resumes by itself. */
  waiting_for_memory: boolean
}

export const getExtractionStatus = (projectId: string) =>
  getJson<ExtractionStatus>(`/api/v1/projects/${projectId}/extraction-status`)
export const cancelAnalysis = async (analysisId: string) => {
  await mutate(`/api/v1/analyses/${analysisId}/cancel`)
}

export const startConsistencyReview = (projectId: string) =>
  started(`/api/v1/projects/${projectId}/consistency`, {})
export const mergeRules = async (projectId: string, keepId: string, duplicateId: string) =>
  (await mutate(`/api/v1/projects/${projectId}/rules/${keepId}/merge`, { duplicate_id: duplicateId })).json() as Promise<{
    merged: string
    evidence_moved: number
  }>
