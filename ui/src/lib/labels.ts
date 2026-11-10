import type {
  AnalysisStatus,
  AnalysisType,
  AssessmentStatus,
  EdgeOrigin,
  Evidence,
  NodeState,
  Severity,
  VerificationStatus,
} from "@/lib/api"

export const VERIFICATION_LABELS: Record<VerificationStatus, string> = {
  unverified: "Sin verificar",
  source_supported: "Respaldada por fuente",
  test_supported: "Respaldada por test",
  human_verified: "Confirmada",
  conflicted: "En conflicto",
  rejected: "Rechazada",
}

export const ANALYSIS_TYPE_LABELS: Record<AnalysisType, string> = {
  knowledge_extraction: "Extracción de reglas",
  code_diff: "Diff de Git",
  sql_review: "Consulta SQL",
  release_comparison: "Comparación de versiones",
  impact_analysis: "Impacto",
  bug_investigation: "Descripción de bug",
}

export const ANALYSIS_STATUS_LABELS: Record<AnalysisStatus, string> = {
  queued: "En cola",
  running: "En marcha",
  completed: "Terminado",
  partial: "Terminado en parte",
  failed: "Falló",
  cancelled: "En pausa",
}

export const ASSESSMENT_LABELS: Record<AssessmentStatus, string> = {
  hypothesis: "Hipótesis",
  evidence_supported: "Con evidencia",
  test_verified: "Comprobado con test",
  human_confirmed: "Confirmado",
  false_positive: "Falso positivo",
  unresolved: "Sin resolver",
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  info: "Info",
  low: "Baja",
  medium: "Media",
  high: "Alta",
  critical: "Crítica",
}

export const METHOD_LABELS: Record<Evidence["method"], string> = {
  direct_reference: "Cita comprobada",
  static_analysis: "Análisis estático",
  test_result: "Resultado de test",
  human_review: "Revisión humana",
  llm_proposal: "Propuesta por la IA",
}

export const NODE_STATE_LABELS: Record<NodeState, string> = {
  confirmed: "Verificado",
  supported: "Respaldado por el código",
  inferred: "Inferido",
  unverified: "Sin verificar",
  conflicted: "En conflicto",
  rejected: "Rechazado",
  no_version: "Sin versión",
}

export const EDGE_ORIGIN_LABELS: Record<EdgeOrigin, string> = {
  observed: "Observada en una fuente",
  supported: "Respaldada por un test",
  inferred: "Inferida",
  confirmed: "Confirmada por una persona",
}

// Stable API codes (KnowledgeError and analyses) in the person's language.
export const ERROR_LABELS: Record<string, string> = {
  llm_unavailable: "El motor de IA local no está disponible.",
  low_memory: "Al equipo le queda muy poca memoria libre. Cierra alguna aplicación y vuelve a preguntar.",
  model_error: "El modelo no devolvió una respuesta válida.",
  internal_error: "Error interno.",
  interrupted: "Se interrumpió al cerrar Scrinium.",
  invalid_diff: "Eso no parece un diff unificado.",
  diff_too_large: "El diff es demasiado grande.",
  diff_too_many_files: "El diff tiene demasiados archivos.",
  diff_too_many_hunks: "El diff tiene demasiados cambios.",
  sql_parse_error: "El SQL no se pudo interpretar.",
  sql_empty: "No hay ninguna sentencia SQL.",
  schema_parse_error: "El esquema no se pudo interpretar.",
  schema_too_large: "El esquema es demasiado grande.",
  release_not_indexed:
    "Solo se puede guardar el estado de la versión cuyo commit está sincronizado. Sincroniza la fuente en ese commit.",
  not_a_git_source: "Esa fuente no es un repositorio Git.",
  same_release: "Elige dos versiones distintas.",
  profile_name_required: "Escribe tu nombre en el perfil local antes de revisar.",
  reviewer_required: "Escribe tu nombre en el perfil local antes de revisar.",
  insufficient_evidence: "No hay evidencia comprobada suficiente para ese estado.",
  git_failed: "Git no pudo leer el repositorio.",
  analysis_in_progress: "El análisis todavía está en marcha.",
  workspace_not_empty: "Solo se puede restaurar con el workspace vacío. Borra todos los datos antes.",
  passphrase_required: "Ese archivo está cifrado: escribe su contraseña.",
  wrong_passphrase_or_modified: "La contraseña no es correcta o el archivo se ha modificado.",
  invalid_backup: "El archivo no es una exportación válida de Scrinium, o está dañado.",
  unsupported_backup_version: "Esa exportación es de una versión de Scrinium que esta no entiende.",
  backup_too_large: "El archivo es demasiado grande.",
  payload_too_large: "El archivo o el texto es demasiado grande.",
  passphrase_too_short: "La contraseña debe tener al menos 12 caracteres.",
  confirmation_mismatch: "La confirmación no coincide.",
  work_in_progress: "Hay una sincronización o un análisis en marcha. Espera a que termine.",
  rule_exists: "Ya hay una regla con ese título en el proyecto.",
}

export function errorMessage(code: string | null | undefined, fallback = "No se pudo completar.") {
  return (code && ERROR_LABELS[code]) || fallback
}

export function shortSha(sha: string | null | undefined) {
  return sha ? sha.slice(0, 8) : ""
}

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  requirement: "Requisitos",
  architecture_decision: "Decisiones",
  business_rule: "Reglas",
  business_flow: "Procesos",
  user_journey: "Recorridos",
  architecture_flow: "Flujos técnicos",
  data_flow: "Flujos de datos",
  service: "Servicios",
  module: "Módulos",
  api_endpoint: "Endpoints",
  code_symbol: "Código",
  event: "Eventos",
  data_entity: "Datos",
  test_case: "Tests",
  ticket: "Tickets",
  document: "Documentos",
  release: "Versiones",
}
