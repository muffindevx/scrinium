import { t } from "@/lib/i18n"
import type {
  AiSetup,
  SetupChoice,
  AnalysisStatus,
  AnalysisType,
  AssessmentStatus,
  Evidence,
  NodeState,
  Severity,
  VerificationStatus,
} from "@/lib/api"

export const VERIFICATION_LABELS: Record<VerificationStatus, string> = {
  unverified: t("Sin verificar"),
  source_supported: t("Respaldada por fuente"),
  test_supported: t("Respaldada por test"),
  human_verified: t("Confirmada"),
  conflicted: t("En conflicto"),
  rejected: t("Rechazada"),
}

export const ANALYSIS_TYPE_LABELS: Record<AnalysisType, string> = {
  knowledge_extraction: t("Extracción de reglas"),
  code_diff: t("Diff de Git"),
  sql_review: t("Consulta SQL"),
  release_comparison: t("Comparación de versiones"),
  impact_analysis: t("Impacto"),
  bug_investigation: t("Descripción de bug"),
}

export const ANALYSIS_STATUS_LABELS: Record<AnalysisStatus, string> = {
  queued: t("En cola"),
  running: t("En marcha"),
  completed: t("Terminado"),
  partial: t("Terminado en parte"),
  failed: t("Falló"),
  cancelled: t("En pausa"),
}

export const ASSESSMENT_LABELS: Record<AssessmentStatus, string> = {
  hypothesis: t("Hipótesis"),
  evidence_supported: t("Con evidencia"),
  test_verified: t("Comprobado con test"),
  human_confirmed: t("Confirmado"),
  false_positive: t("Falso positivo"),
  unresolved: t("Sin resolver"),
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  info: t("Info"),
  low: t("Baja"),
  medium: t("Media"),
  high: t("Alta"),
  critical: t("Crítica"),
}

export const METHOD_LABELS: Record<Evidence["method"], string> = {
  direct_reference: t("Cita comprobada"),
  static_analysis: t("Análisis estático"),
  test_result: t("Resultado de test"),
  human_review: t("Revisión humana"),
  llm_proposal: t("Propuesta por la IA"),
}

export const NODE_STATE_LABELS: Record<NodeState, string> = {
  confirmed: t("Verificado"),
  supported: t("Respaldado por el código"),
  inferred: t("Inferido"),
  unverified: t("Sin verificar"),
  conflicted: t("En conflicto"),
  rejected: t("Rechazado"),
  no_version: t("Sin versión"),
}

// Stable API codes (KnowledgeError and analyses) in the person's language.
export const ERROR_LABELS: Record<string, string> = {
  llm_unavailable: t("El motor de IA local no está disponible."),
  low_memory: t("Al equipo le queda muy poca memoria libre. Cierra alguna aplicación y vuelve a preguntar."),
  model_error: t("El modelo no devolvió una respuesta válida."),
  remote_budget_spent:
    t("Se agotó el presupuesto mensual de tokens del proveedor externo. No se envió nada más; amplíalo en Ajustes o vuelve a solo local."),
  remote_auth: t("El proveedor externo rechazó la clave o el modelo. Revísalos en Ajustes."),
  remote_unavailable: t("El proveedor externo no responde ahora. Prueba más tarde."),
  // Providers and consent (ADR-010).
  invalid_base_url: t("La dirección debe ser https:// con un nombre de servidor, sin usuario ni parámetros."),
  invalid_api_key: t("Esa clave no parece válida."),
  invalid_name: t("Escribe un nombre de hasta 100 caracteres."),
  name_taken: t("Ya hay un proveedor con ese nombre."),
  keyring_unavailable: t("No hay un llavero del sistema disponible: sin él, Scrinium no guarda claves."),
  key_missing: t("Ese proveedor no tiene clave guardada en el llavero. Vuelve a registrarlo."),
  key_not_for_this_host: t("La clave guardada es de otro servidor. Vuelve a registrar el proveedor."),
  invalid_patterns: t("Alguna ruta no es válida. Usa una por línea, como contracts/ o *.sql."),
  invalid_budget: t("El presupuesto debe ser un número entero de tokens."),
  invalid_consent: t("Elige un proveedor, un modelo y al menos una tarea."),
  retention_not_acknowledged: t("Confirma que has leído qué guarda el proveedor."),
  model_not_permitted: t("Ese modelo no está permitido para esta tarea en este proyecto. Elígelo de nuevo para dar permiso."),
  invalid_model_choice: t("Ese modelo no es válido."),
  invalid_model: t("Escribe el nombre exacto del modelo, sin espacios."),
  key_required_for_new_host: t("Al cambiar de servidor hace falta su clave de API."),
  internal_error: t("Error interno."),
  interrupted: t("Se interrumpió al cerrar Scrinium."),
  invalid_diff: t("Eso no parece un diff unificado."),
  diff_too_large: t("El diff es demasiado grande."),
  diff_too_many_files: t("El diff tiene demasiados archivos."),
  diff_too_many_hunks: t("El diff tiene demasiados cambios."),
  sql_parse_error: t("El SQL no se pudo interpretar."),
  sql_empty: t("No hay ninguna sentencia SQL."),
  schema_parse_error: t("El esquema no se pudo interpretar."),
  schema_too_large: t("El esquema es demasiado grande."),
  release_not_indexed:
    t("Solo se puede guardar el estado de la versión cuyo commit está sincronizado. Sincroniza la fuente en ese commit."),
  not_a_git_source: t("Esa fuente no es un repositorio Git."),
  same_release: t("Elige dos versiones distintas."),
  profile_name_required: t("Escribe tu nombre en el perfil local antes de revisar."),
  reviewer_required: t("Escribe tu nombre en el perfil local antes de revisar."),
  insufficient_evidence: t("No hay evidencia comprobada suficiente para ese estado."),
  git_failed: t("Git no pudo leer el repositorio."),
  analysis_in_progress: t("El análisis todavía está en marcha."),
  workspace_not_empty: t("Solo se puede restaurar con el workspace vacío. Borra todos los datos antes."),
  passphrase_required: t("Ese archivo está cifrado: escribe su contraseña."),
  wrong_passphrase_or_modified: t("La contraseña no es correcta o el archivo se ha modificado."),
  invalid_backup: t("El archivo no es una exportación válida de Scrinium, o está dañado."),
  unsupported_backup_version: t("Esa exportación es de una versión de Scrinium que esta no entiende."),
  backup_too_large: t("El archivo es demasiado grande."),
  payload_too_large: t("El archivo o el texto es demasiado grande."),
  passphrase_too_short: t("La contraseña debe tener al menos 12 caracteres."),
  confirmation_mismatch: t("La confirmación no coincide."),
  work_in_progress: t("Hay una sincronización o un análisis en marcha. Espera a que termine."),
  rule_exists: t("Ya hay una regla con ese título en el proyecto."),
}

export function errorMessage(code: string | null | undefined, fallback = t("No se pudo completar.")) {
  return (code && ERROR_LABELS[code]) || fallback
}

export function shortSha(sha: string | null | undefined) {
  return sha ? sha.slice(0, 8) : ""
}

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  requirement: t("Requisitos"),
  architecture_decision: t("Decisiones"),
  business_rule: t("Reglas"),
  business_flow: t("Procesos"),
  user_journey: t("Recorridos"),
  architecture_flow: t("Flujos técnicos"),
  data_flow: t("Flujos de datos"),
  service: t("Servicios"),
  module: t("Módulos"),
  api_endpoint: t("Endpoints"),
  code_symbol: t("Código"),
  event: t("Eventos"),
  data_entity: t("Datos"),
  test_case: t("Tests"),
  ticket: t("Tickets"),
  document: t("Documentos"),
  release: t("Versiones"),
}

const SETUP_LABELS: Record<SetupChoice, string> = {
  provider: t("Tu proveedor"),
  local_server: t("Un servidor local"),
  isolated: t("Modo aislado"),
}

/** The first-run choice (ADR-013), in a few words. */
export function setupLabel(setup: AiSetup): string {
  if (!setup.choice) return t("Sin elegir")
  if (setup.choice === "local_server" && setup.local_server) {
    return t("{0}: {1} en {2}", SETUP_LABELS.local_server, setup.local_server.model, setup.local_server.url)
  }
  return SETUP_LABELS[setup.choice]
}
