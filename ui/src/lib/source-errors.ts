import { ApiError } from "@/lib/api"

// What went wrong adding or syncing a source, in the person's words.
const ERRORS: Record<string, string> = {
  path_not_absolute: "Escribe una ruta absoluta, por ejemplo /home/tu-usuario/proyecto.",
  path_not_found: "Esa carpeta no existe.",
  not_a_directory: "Esa ruta no es una carpeta.",
  wrong_owner: "Esa carpeta pertenece a otra cuenta.",
  contains_scrinium_data: "Esa carpeta contiene los datos de Scrinium, o está dentro de ellos.",
  folders_unsupported: "Este sistema no permite leer carpetas de forma segura.",
  not_a_git_repository: "Esa carpeta no es un repositorio Git.",
  not_repository_root: "Elige la carpeta raíz del repositorio, no una subcarpeta.",
  empty_repository: "El repositorio todavía no tiene commits.",
  git_unavailable: "No se encontró git en este equipo.",
  git_failed: "Git no pudo leer el repositorio.",
  sync_in_progress: "Ya hay una sincronización en marcha.",
  interrupted: "Se interrumpió al cerrar Scrinium.",
  internal_error: "Error interno.",
}

/** The message for a stored error code, or null if there is none. */
export function sourceErrorLabel(code: string | null | undefined): string | null {
  return (code && ERRORS[code]) || null
}

export function sourceErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code && ERRORS[error.code]) return ERRORS[error.code]
    if (error.code === "invalid_request") return "Revisa la ruta y los patrones de exclusión."
  }
  return "No se pudo completar la operación."
}
