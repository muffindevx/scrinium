import type { AiConsent, AiTask, ProjectAi, ProviderCheck, ProviderKind } from "@/lib/api"

export const TASK_LABELS: Record<AiTask, string> = {
  questions: "Preguntas",
  rule_map: "Extracción de reglas",
  rule_statements: "Redacción de reglas",
  consistency: "Revisión de consistencia",
  change_impact: "Impacto de cambios",
}
export const TASKS = Object.keys(TASK_LABELS) as AiTask[]

export const KIND_LABELS: Record<ProviderKind, string> = {
  openai_compatible: "Compatible con OpenAI",
  anthropic: "Anthropic (Claude)",
}

// Starting points only: the person can change any field before registering.
export const PRESETS: { label: string; name: string; kind: ProviderKind; base_url: string }[] = [
  { label: "DeepSeek", name: "DeepSeek", kind: "openai_compatible", base_url: "https://api.deepseek.com" },
  { label: "GLM (Z.ai)", name: "GLM", kind: "openai_compatible", base_url: "https://api.z.ai/api/paas/v4" },
  { label: "Claude", name: "Claude", kind: "anthropic", base_url: "https://api.anthropic.com" },
]

export const OUTCOME_LABELS: Record<string, { label: string; tone: "verified" | "attention" | "discarded" }> = {
  completed: { label: "Completada", tone: "verified" },
  length: { label: "Sin espacio para responder", tone: "attention" },
  refused: { label: "Rechazada por el proveedor", tone: "discarded" },
  invalid: { label: "Respuesta no válida", tone: "attention" },
  unavailable: { label: "No respondió", tone: "attention" },
  budget: { label: "Presupuesto agotado", tone: "attention" },
  error: { label: "Error", tone: "discarded" },
}

export const CHECK_LABELS: Record<ProviderCheck["reason"], string> = {
  ok: "Responde",
  key_rejected: "Rechazó la clave: revísala o vuelve a registrar el proveedor",
  model_not_found: "No encuentra ese modelo, o tu clave no tiene acceso a él",
  request_rejected: "Rechazó la petición",
  unavailable: "No responde ahora",
  no_room: "No le bastó el espacio de respuesta",
  no_answer: "No dio una respuesta válida",
}

export function tokens(value: number) {
  return value.toLocaleString("es")
}

// Hosts whose own API Scrinium speaks; any other provider is reached through its
// OpenAI-compatible endpoint, which nearly all of them offer.
export function kindFor(url: string): ProviderKind {
  try {
    const host = new URL(url).hostname
    return host === "anthropic.com" || host.endsWith(".anthropic.com") ? "anthropic" : "openai_compatible"
  } catch {
    return "openai_compatible"
  }
}

export const LOCAL = "local"

/** An external model of the catalog, as a choice: "<provider id> <model>". */
export function choiceOf(providerId: string, model: string) {
  return `${providerId} ${model}`
}

export function currentChoice(ai: ProjectAi, task: AiTask): string {
  const chosen = ai.task_models[task]
  return chosen ? choiceOf(chosen.provider_id, chosen.model) : LOCAL
}

/** The consent that lets this task use this model, if any. */
export function permission(ai: ProjectAi, task: AiTask, providerId: string, model: string): AiConsent | undefined {
  return ai.consents.find(
    (consent) => consent.provider_id === providerId && consent.model === model && consent.tasks.includes(task),
  )
}

/** "<provider id> <model>" back into its parts. */
export function splitChoice(choice: string): [string, string] {
  const space = choice.indexOf(" ")
  return [choice.slice(0, space), choice.slice(space + 1)]
}
