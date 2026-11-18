import { t } from "@/lib/i18n"
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
  ai_mode: AiMode
  profile: "standard" | "isolated"
  profile_managed: boolean
  ai_setup: SetupChoice | null
  connected_sources: number
  limits: string[]
}

export type AiMode = "local_only" | "remote_allowed" | "isolated"

/** Where the writing model lives, chosen on the first run (ADR-013). */
export type SetupChoice = "provider" | "local_server" | "isolated"

export interface AiSetup {
  choice: SetupChoice | null
  isolated: boolean
  managed: boolean
  local_server: { url: string; model: string } | null
  local_model?: string
}

export interface DataFlow {
  ai_mode: AiMode
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
  /** Generated code (`@generated`, `DO NOT EDIT`, `*.pb.go`…) is left out unless true. */
  include_generated?: boolean
  /** Phase 9.6: an https URL that Scrinium clones instead of a folder (Git only). */
  url?: string
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
  include_generated: boolean
  status: "pending" | "active" | "error" | "revoked" | "disabled"
  active_files: number
  last_successful_sync_at: string | null
  latest_sync: SyncRun | null
  remote_url?: string | null
  branch?: string | null
  indexed_commit?: string | null
  head_commit?: string | null
  head_moved?: boolean
}

export class SessionRequiredError extends Error {}

/** API error with its code (`{"error": ...}`) and, on a 422, the rejected fields. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly fields: string[]

  constructor(status: number, code: string | null, fields: string[] = []) {
    super(t("Respuesta inesperada: {0}", status))
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

// ETags (phase 9.4): the API answers 304 when the console already holds that version. The
// copies live only in this tab's memory: API answers are `no-store`, so nothing reaches the
// browser's disk cache.
const MAX_REMEMBERED = 200
const remembered = new Map<string, { etag: string; body: unknown }>()

async function getJson<T>(path: string): Promise<T> {
  const known = path === "/api/v1/session" ? undefined : remembered.get(path)
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: known ? { "If-None-Match": known.etag } : undefined,
  })
  if (response.status === 304 && known) return known.body as T
  if (response.status === 401) throw new SessionRequiredError()
  if (!response.ok) throw await apiError(response)
  const body = (await response.json()) as T
  const etag = response.headers.get("ETag")
  if (etag && path !== "/api/v1/session") {
    remembered.delete(path)
    remembered.set(path, { etag, body })
    if (remembered.size > MAX_REMEMBERED) remembered.delete(remembered.keys().next().value as string)
  }
  return body
}

/** Forgets what was remembered (after signing out or wiping data). */
export function forgetRemembered() {
  remembered.clear()
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

export const logout = () => {
  forgetRemembered()
  return mutate("/api/v1/session/logout")
}
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
export const deleteSource = async (sourceId: string, deleteCiting = false, deleteReviewed = false) => {
  const query = new URLSearchParams({ delete_citing: String(deleteCiting), delete_reviewed: String(deleteReviewed) })
  await mutate(
    `/api/v1/sources/${sourceId}?${query}`,
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

/** Another rule (or entity) as the impact of a rule names it. */
export interface ImpactedEntity {
  id: string
  name: string
  entity_type: string
  criticality: RuleSummary["criticality"] | null
  tickets: string[]
  basis: string | null
}

/** N7: what may break if this rule changes, read backwards from the relationships. */
export interface RuleImpact {
  /** The same decision written in another place: it has to change with this one. */
  same_decision: ImpactedEntity[]
  /** Rules whose code calls this one's, directly or through others (`via`), closest first. */
  dependents: (ImpactedEntity & { depth: number; via: { id: string; name: string } | null })[]
  truncated: boolean
  max_depth: number
  /** Processes with a step on this rule or on one of those rules (`through`). */
  flows: { id: string; name: string; through: string[] }[]
  tickets: string[]
}

export interface RuleDetail extends Omit<RuleSummary, "latest_version" | "evidence_count" | "citation"> {
  owner_ref: string | null
  versions: RuleVersion[]
  entity_evidence: Evidence[]
  relationships: Relationship[]
  impact: RuleImpact
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
  /** Why Scrinium related them from the code ("calls:2", "same_data:users"); null if a person did. */
  basis: string | null
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
    groups_total: number
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

export const listRules = async (
  projectId: string,
  filters: { verification?: VerificationStatus; lifecycle?: string; q?: string } = {},
) => {
  const params = new URLSearchParams(
    Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
  )
  return (await getJson<{ rules: RuleSummary[] }>(`/api/v1/projects/${projectId}/rules?${params}`)).rules
}
export type RulebookHealth = {
  rules: number
  verified: number
  rejected: number
  unreviewed: number
  drifted: number
  removed: number
  consistency: number
  inbox: number
  last_review_at: string | null
  oldest_unreviewed_at: string | null
  last_sync_at: string | null
}
export const getHealth = (projectId: string) => getJson<RulebookHealth>(`/api/v1/projects/${projectId}/health`)
export type InboxItem = {
  entity_id: string
  version_id: string
  title: string
  statement: string
  verification_status: VerificationStatus
  origin: string
  review_reason: string | null
  criticality: string
  score: number
  // `bugs`: bug tickets pointing to the rule (phase 10.5); they raise its impact.
  parts: { impact: number; uncertainty: number; cost: number; bugs?: number }
  citation: { revision_id: string; path: string; start_line: number; end_line: number } | null
  now: { revision_id: string; path: string; start_line: number; end_line: number } | null
  group: { key: string; size: number; confirmed: boolean; members: string[] } | null
}
export type Inbox = {
  total: number
  // How many rules each tab holds, counted over all of them, not only the page.
  buckets?: { attention: number; high: number; rest: number }
  items: InboxItem[]
  unanchored: { entity_id: string; title: string; missing: string[] }[]
  unanchored_total: number
}
export const getInbox = (projectId: string, offset = 0, limit = 100) =>
  getJson<Inbox>(`/api/v1/projects/${projectId}/inbox?offset=${offset}&limit=${limit}`)
export const reviewBatch = async (
  projectId: string,
  versionIds: string[],
  status: VerificationStatus,
  note?: string,
) =>
  (
    await mutate(`/api/v1/projects/${projectId}/inbox/review`, {
      version_ids: versionIds,
      status,
      ...(note?.trim() ? { note } : {}),
    })
  ).json() as Promise<{ reviewed: string[]; verification_status: VerificationStatus }>
export type RulebookSource = {
  source_id: string
  name: string
  available: boolean
  reviewed: number
  unexported: number
  extra: number
}
export const getRulebook = async (projectId: string) =>
  (await getJson<{ sources: RulebookSource[] }>(`/api/v1/projects/${projectId}/rulebook`)).sources
export const getRule = (projectId: string, entityId: string) =>
  getJson<RuleDetail>(`/api/v1/projects/${projectId}/rules/${entityId}`)
// S3: the rule map arrives before the statements. A rule opened without a statement gets it
// written right then, ahead of background extraction.
export const PENDING_STATEMENT_PREFIX = "Pendiente de redactar."
export const writeStatement = async (projectId: string, entityId: string) =>
  (await mutate(`/api/v1/projects/${projectId}/rules/${entityId}/statement`)).json() as Promise<RuleDetail>
export const setVerification = async (versionId: string, status: VerificationStatus, note?: string) =>
  (
    await mutate(`/api/v1/knowledge-versions/${versionId}/verification`, note?.trim() ? { status, note } : { status })
  ).json() as Promise<{
    id: string
    verification_status: VerificationStatus
    reviewed_by: string | null
  }>

const started = async (path: string, body: unknown) => (await mutate(path, body)).json() as Promise<Analysis>
export const startExtraction = (projectId: string, pathPrefix: string, allFunctions = false, model?: string) =>
  started(`/api/v1/projects/${projectId}/extractions`, {
    path_prefix: pathPrefix || null,
    ...(allFunctions ? { min_score: 0 } : {}),
    ...(model ? { model } : {}),
  })
export const analyzeDiff = (projectId: string, diff: string) =>
  started(`/api/v1/projects/${projectId}/analyses/code-diff`, { diff })
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
  /** Of `entities_to_review`: rules a person wrote or reviewed that only this source backs. */
  entities_reviewed_here: number
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

export const getAiSetup = () => getJson<AiSetup>("/api/v1/ai-setup")

/** Cambios (phase 7.1). */
export interface ChangeRefs {
  source_id: string
  sources: { id: string; name: string }[]
  branches: string[]
  tags: string[]
  commits: { sha: string; date: string; subject: string }[]
}
export interface ChangeSide {
  path: string
  start_line: number
  end_line: number
  symbol: string | null
  values: string[]
}
export interface ChangeTicket {
  key: string
  level: "explicit" | "mentioned"
  origin: "commit" | "branch" | "linear"
  where: string
  ticket: TicketInfo | null
}
export interface Changes {
  base: string
  head: string
  changed_files: string[]
  rules: {
    rule_id: string
    title: string
    status: string
    level: string
    before: ChangeSide
    after: ChangeSide | null
    processes?: string[]
    // Phase 10.5: the tickets the change names for this rule, resolved when a tracker knows them.
    tickets?: ChangeTicket[]
    without_ticket?: boolean
  }[]
  processes_affected?: string[]
  without_ticket?: number
  branch?: string | null
  new_decisions: (ChangeSide & { kind: string })[]
  unanchored: number
  seconds: number
}
export const getChangeRefs = (projectId: string, sourceId?: string) =>
  getJson<ChangeRefs>(
    `/api/v1/projects/${projectId}/changes/refs${sourceId ? `?${new URLSearchParams({ source_id: sourceId })}` : ""}`,
  )
export const compareChanges = (projectId: string, base: string, head: string, sourceId?: string) =>
  getJson<Changes>(
    `/api/v1/projects/${projectId}/changes?${new URLSearchParams({ base, head, ...(sourceId ? { source_id: sourceId } : {}) })}`,
  )

/** A rule's values over its Git history (phase 7.2). */
export interface HistoryCommit {
  sha: string
  date: string
  author: string
  subject: string
  path: string
}
export interface RuleHistory {
  rule_id: string
  title: string
  anchors: {
    path: string
    symbol: string | null
    commits: number
    parsed: number
    seconds: number
    periods: { values: string[] | null; since: HistoryCommit; until: HistoryCommit | null }[]
  }[]
}
export const getRuleHistory = (projectId: string, entityId: string) =>
  getJson<RuleHistory>(`/api/v1/projects/${projectId}/rules/${entityId}/history`)

/** Business release notes between two refs, in Markdown (phase 7.2). */
export async function releaseNotes(projectId: string, base: string, head: string, sourceId?: string) {
  const query = new URLSearchParams({ base, head, format: "markdown", ...(sourceId ? { source_id: sourceId } : {}) })
  return (await request(`/api/v1/projects/${projectId}/changes?${query}`)).text()
}

/** A stack trace to functions, rules and history (phase 7.3). */
export interface TriageCommit {
  sha: string
  date: string
  author: string
  subject: string
}
export interface Triage {
  parsed: number
  in_project: number
  resolved: number
  frames: {
    raw: string
    language: string
    trace_path: string
    line: number
    trace_function: string | null
    path: string | null
    function: { name: string; start_line: number; end_line: number } | null
    rules: { id: string; title: string; start_line: number; end_line: number }[]
    recent: TriageCommit[]
    fixes: TriageCommit[]
  }[]
}
export const triageTrace = async (projectId: string, trace: string) =>
  (await mutate(`/api/v1/projects/${projectId}/triage`, { trace })).json() as Promise<Triage>

/** Coverage (phase 8.1). */
export interface CoverageNode {
  path: string
  name: string
  kind: "folder" | "file" | "domain"
  // Domains only: named in .scrinium/domains.yaml, or proposed from folders.
  declared?: boolean
  files: number
  lines: number
  decisions: number
  verified: number
  unverified: number
  uncovered: number
  rules: number
  // Distinct bug tickets of the rules here (phase 10.5).
  bugs?: number
}
export interface Coverage {
  path: string
  children: CoverageNode[]
  uncovered: { start_line: number; end_line: number; kind: string }[]
  total: Omit<CoverageNode, "path" | "name" | "kind">
  pending: number
  layer_recall: { rules: number; inside_a_decision: number; sets: number }
  domain?: string | null
  by_domain?: boolean
  domain_errors?: string[]
}
export interface DocConflict {
  rule_id: string
  title: string
  document: string
  line: number
  sentence: string
  says: string[]
  code: string[]
  place: string
}
export const getDocConflicts = async (projectId: string) =>
  (await getJson<{ conflicts: DocConflict[] }>(`/api/v1/projects/${projectId}/doc-conflicts`)).conflicts
/** Processes from entry points (phase 8.3). */
export interface Process {
  name: string
  entry: string
  // What sets it off: an HTTP route, an action (a button), a scheduled job or a handler.
  trigger: { kind: "http" | "action" | "schedule" | "handler"; label: string }
  path: string
  start_line: number
  rules: number
  verified: number
  steps: {
    function: string
    path: string
    start_line: number
    end_line: number
    depth: number
    rules: { id: string; title: string; statement: string; verified: boolean; start_line: number; end_line: number }[]
  }[]
}
export const getProcesses = async (projectId: string) =>
  (await getJson<{ processes: Process[] }>(`/api/v1/projects/${projectId}/processes`)).processes
export const getCoverage = (projectId: string, path: string, byDomain = false, domain: string | null = null) =>
  getJson<Coverage>(
    `/api/v1/projects/${projectId}/coverage?${new URLSearchParams({
      path,
      ...(byDomain ? { by_domain: "true" } : {}),
      ...(domain !== null ? { domain } : {}),
    })}`,
  )

/** ⌘K (phase 6.2): rules, functions, files and tickets by name. */
export interface Palette {
  rules: { id: string; title: string }[]
  functions: { name: string; qualified_name: string; kind: string; path: string; line: number }[]
  files: { path: string }[]
  tickets: { ticket: string; rule_id: string; rule: string }[]
}
export const getPalette = (projectId: string, query: string) =>
  getJson<Palette>(`/api/v1/projects/${projectId}/palette?${new URLSearchParams({ q: query })}`)

/** The workspace's language: the console's and the models' (phase 5.5). */
export const getLanguage = () => getJson<{ language: "es" | "en" }>("/api/v1/workspace/language")
export async function setLanguage(language: "es" | "en"): Promise<{ language: "es" | "en" }> {
  return (await mutate("/api/v1/workspace/language", { language }, "PUT")).json()
}

export async function chooseAiSetup(
  choice: SetupChoice,
  localServer?: { url: string; model: string },
): Promise<AiSetup & { consents_revoked: number }> {
  const body = choice === "local_server" ? { choice, local_server: localServer } : { choice }
  return (await mutate("/api/v1/ai-setup", body, "PUT")).json()
}

export async function checkLocalServer(url: string, model: string): Promise<{ status: EmbeddingStatus }> {
  return (await mutate("/api/v1/ai-setup/check-local", { url, model })).json()
}

export interface ExtractionStatus {
  pending: number
  done: number
  failed: number
  running_analysis_id: string | null
  /** Extraction waits until the machine has free memory; it resumes by itself. */
  waiting_for_memory: boolean
}

/** Before extracting, what would go to the project's remote model (only when it has one). */
export type ExtractionEstimate =
  | { remote: false }
  | {
      remote: true
      host: string
      model: string
      pending: number
      batches: number
      remote_batches: number
      local_batches: number
      files: number
      sent_chars: number
      input_tokens: number
      max_output_tokens: number
      measured: boolean
      budget: number | null
      spent_this_month: number
      statements_remote: boolean
    }

export const getExtractionEstimate = (projectId: string, pathPrefix: string, allFunctions: boolean, model?: string) => {
  const query = new URLSearchParams({ path_prefix: pathPrefix })
  if (allFunctions) query.set("min_score", "0")
  if (model) query.set("model", model)
  return getJson<ExtractionEstimate>(`/api/v1/projects/${projectId}/extraction-estimate?${query}`)
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

// Remote model providers and each project's consent (ADR-010). The API key is sent once, on
// registration, and never comes back: only whether there is one and its last characters.
export type ProviderKind = "openai_compatible" | "anthropic"
export type AiTask =
  | "questions"
  | "rule_map"
  | "rule_statements"
  | "consistency"
  | "change_impact"
  | "check_explain"

export interface ModelProvider {
  id: string
  name: string
  kind: ProviderKind
  base_url: string
  host: string
  has_key: boolean
  key_hint: string | null
  created_at: string
  models: { id: string; model: string }[]
}

export interface RetentionNotice {
  verified: boolean
  key: string | null
  notice: string
  sources: string[]
  checked_on: string | null
  stale: boolean
}

export interface AiConsent {
  id: string
  provider_id: string | null
  provider_name: string
  host: string
  model: string
  tasks: AiTask[]
  retention: RetentionNotice
  consented_by: string | null
  created_at: string
  revoked_at: string | null
}

export interface ProjectAi {
  ai_mode: AiMode
  consents: AiConsent[]
  never_send: string[]
  remote_token_budget: number | null
  remote_tokens_this_month: number
  /** Each task's default model; a task not here uses the local model. */
  task_models: Partial<Record<AiTask, { provider_id: string; model: string }>>
  cache_hits: number
  mask_personal: boolean
  mask_literals: boolean
}

export interface AiRequestRecord {
  id: string
  task: AiTask
  host: string
  model: string
  answered_by: string
  outcome: string
  sources: string[]
  sent_chars: number
  prompt_tokens: number
  cached_tokens: number
  completion_tokens: number
  masked: number
  created_at: string
}

export const listModelProviders = () =>
  getJson<{ providers: ModelProvider[]; keyring: boolean }>("/api/v1/model-providers")
export const registerModelProvider = async (provider: {
  name: string
  kind: ProviderKind
  base_url: string
  api_key: string
}) => (await mutate("/api/v1/model-providers", provider)).json() as Promise<ModelProvider>
export const updateModelProvider = async (
  providerId: string,
  changes: { name?: string; kind?: ProviderKind; base_url?: string; api_key?: string },
) =>
  (await mutate(`/api/v1/model-providers/${providerId}`, changes, "PATCH")).json() as Promise<
    ModelProvider & { consents_revoked: number }
  >
export const addProviderModel = async (providerId: string, model: string) =>
  (await mutate(`/api/v1/model-providers/${providerId}/models`, { model })).json() as Promise<{
    id: string
    model: string
  }>
export const removeProviderModel = async (providerId: string, modelId: string) =>
  (await mutate(`/api/v1/model-providers/${providerId}/models/${modelId}`, undefined, "DELETE")).json() as Promise<{
    consents_revoked: number
  }>
export const removeModelProvider = async (providerId: string) => {
  await mutate(`/api/v1/model-providers/${providerId}`, undefined, "DELETE")
}
export const getRetention = (providerId: string, model: string) =>
  getJson<RetentionNotice>(
    `/api/v1/model-providers/${providerId}/retention?model=${encodeURIComponent(model)}`,
  )
export interface ProviderCheck {
  status: "ready" | "model_missing" | "unavailable"
  reason: "ok" | "key_rejected" | "model_not_found" | "request_rejected" | "unavailable" | "no_room" | "no_answer"
  answered_by?: string
  http_status?: number
  error_type?: string
  detail?: string
}
export const checkModelProvider = async (providerId: string, model: string) =>
  (await mutate(`/api/v1/model-providers/${providerId}/check`, { model })).json() as Promise<ProviderCheck>
export const getProjectAi = (projectId: string) => getJson<ProjectAi>(`/api/v1/projects/${projectId}/ai`)
export const updateProjectAiSettings = async (
  projectId: string,
  settings: {
    never_send: string[]
    remote_token_budget: number | null
    mask_personal: boolean
    mask_literals: boolean
  },
) => (await mutate(`/api/v1/projects/${projectId}/ai/settings`, settings, "PUT")).json() as Promise<ProjectAi>
export const giveAiConsent = async (
  projectId: string,
  consent: {
    provider_id: string
    model: string
    tasks: AiTask[]
    retention_acknowledged: boolean
    /** false: only allowed, to choose it for a run; the tasks keep their default. */
    make_default?: boolean
  },
) => (await mutate(`/api/v1/projects/${projectId}/ai/consents`, consent)).json() as Promise<AiConsent>
export const revokeAiConsents = async (projectId: string) =>
  (await mutate(`/api/v1/projects/${projectId}/ai/consents`, undefined, "DELETE")).json() as Promise<{
    revoked: number
    ai_mode: AiMode
  }>
/** One task back to the local model; its consent's other tasks keep their model. */
export const taskToLocal = async (projectId: string, task: AiTask) =>
  (await mutate(`/api/v1/projects/${projectId}/ai/tasks/${task}`, undefined, "DELETE")).json() as Promise<ProjectAi>
export const setTaskDefault = async (projectId: string, task: AiTask, providerId: string, model: string) =>
  (
    await mutate(`/api/v1/projects/${projectId}/ai/tasks/${task}`, { provider_id: providerId, model }, "PUT")
  ).json() as Promise<ProjectAi>
export const revokeAiConsent = async (projectId: string, consentId: string) =>
  (await mutate(`/api/v1/projects/${projectId}/ai/consents/${consentId}`, undefined, "DELETE")).json() as Promise<ProjectAi>

/** A run's model: "local", "<provider id> <model>", or undefined for the task's default. */
export type ModelChoice = string | undefined

export const listAiRequests = async (projectId: string) =>
  (await getJson<{ requests: AiRequestRecord[] }>(`/api/v1/projects/${projectId}/ai/requests`)).requests

// Ticket trackers (phase 10, ADR-014): read-only, Linear first. The token goes to the keyring and
// never comes back: an answer only says whether there is one and its last four characters.
export type TrackerTeam = { id: string; key: string; name: string }

export type TicketConnection = {
  id: string
  project_id: string
  provider: "linear"
  host: string
  display_name: string
  status: "pending" | "active" | "error" | "revoked" | "disabled"
  auth: "api_key" | "oauth"
  has_token: boolean
  key_hint: string
  scope: TrackerTeam[]
  last_sync_at: string | null
  last_sweep_at: string | null
  last_error: string | null
  tickets: number | null
  created_at: string
}

export type TicketLinkLevel = "confirmed" | "explicit" | "mentioned" | "inferred"

export type TicketInfo = {
  id: string
  key: string
  previous_keys: string[]
  title: string
  type: string
  status: string
  status_category: "" | "open" | "started" | "done" | "canceled"
  labels: string[]
  url: string
  opened_at: string | null
  changed_at: string | null
  closed_at: string | null
  deleted: boolean
}

export type RuleTicket = TicketInfo & { level: TicketLinkLevel; commits: string[] }

export type RuleTickets = { tickets: RuleTicket[]; unmatched_keys: string[] }

const trackers = (projectId: string) => `/api/v1/projects/${projectId}/ticket-connections`

export const listTicketConnections = (projectId: string) =>
  getJson<{ connections: TicketConnection[] }>(trackers(projectId))

export async function connectLinear(projectId: string, token: string) {
  const response = await mutate(trackers(projectId), { provider: "linear", token })
  return (await response.json()) as TicketConnection & { teams: TrackerTeam[] }
}

export const getTrackerTeams = (projectId: string, connectionId: string) =>
  getJson<{ teams: TrackerTeam[] }>(`${trackers(projectId)}/${connectionId}/teams`)

export async function setTrackerScope(projectId: string, connectionId: string, teamIds: string[]) {
  const response = await mutate(`${trackers(projectId)}/${connectionId}/scope`, { team_ids: teamIds }, "PUT")
  return (await response.json()) as TicketConnection & { tickets_removed: number }
}

export async function syncTickets(projectId: string, connectionId: string) {
  return (await (await mutate(`${trackers(projectId)}/${connectionId}/sync`)).json()) as TicketConnection
}

export async function removeTicketConnection(projectId: string, connectionId: string) {
  const response = await mutate(`${trackers(projectId)}/${connectionId}`, undefined, "DELETE")
  return (await response.json()) as {
    tickets_deleted: number
    links_deleted: number
    token_deleted: number
    revoke_at_provider: boolean
  }
}

export const searchTickets = (projectId: string, query: string) =>
  getJson<{ tickets: TicketInfo[] }>(
    `/api/v1/projects/${projectId}/tickets?${new URLSearchParams({ q: query, limit: "10" })}`,
  )

export const getRuleTickets = (projectId: string, entityId: string) =>
  getJson<RuleTickets>(`/api/v1/projects/${projectId}/rules/${entityId}/tickets`)

export async function linkRuleTicket(projectId: string, entityId: string, ticketId: string) {
  const path = `/api/v1/projects/${projectId}/rules/${entityId}/tickets`
  return (await (await mutate(path, { ticket_id: ticketId })).json()) as RuleTickets
}

export async function unlinkRuleTicket(projectId: string, entityId: string, ticketId: string) {
  await mutate(`/api/v1/projects/${projectId}/rules/${entityId}/tickets/${ticketId}`, undefined, "DELETE")
}

export type TicketTriage = {
  ticket: TicketInfo
  commits: {
    sha: string
    date: string
    author: string
    subject: string
    level: "explicit" | "mentioned"
    files: string[]
  }[]
  rules: { id: string; title: string; path: string; start_line: number; end_line: number; commits: string[] }[]
  similar: (TicketInfo & { similarity: number })[]
}

export const searchBugTickets = (projectId: string, query: string) =>
  getJson<{ tickets: TicketInfo[] }>(
    `/api/v1/projects/${projectId}/tickets?${new URLSearchParams({ q: query, limit: "20", bugs: "true" })}`,
  )

export const triageTicket = (projectId: string, ticketId: string) =>
  getJson<TicketTriage>(`/api/v1/projects/${projectId}/tickets/${ticketId}/triage`)
