import { t } from "@/lib/i18n"
import { ApiError } from "@/lib/api"

// What went wrong adding or syncing a source, in the person's words.
const ERRORS: Record<string, string> = {
  path_not_absolute: t("Escribe una ruta absoluta, por ejemplo /home/tu-usuario/proyecto."),
  path_not_found: t("Esa carpeta no existe."),
  not_a_directory: t("Esa ruta no es una carpeta."),
  wrong_owner: t("Esa carpeta pertenece a otra cuenta."),
  contains_scrinium_data: t("Esa carpeta contiene los datos de Scrinium, o está dentro de ellos."),
  folders_unsupported: t("Este sistema no permite leer carpetas de forma segura."),
  not_a_git_repository: t("Esa carpeta no es un repositorio Git."),
  not_repository_root: t("Elige la carpeta raíz del repositorio, no una subcarpeta."),
  empty_repository: t("El repositorio todavía no tiene commits."),
  git_unavailable: t("No se encontró git en este equipo."),
  git_failed: t("Git no pudo leer el repositorio."),
  sync_in_progress: t("Ya hay una sincronización en marcha."),
  interrupted: t("Se interrumpió al cerrar Scrinium."),
  internal_error: t("Error interno."),
}

/** The message for a stored error code, or null if there is none. */
export function sourceErrorLabel(code: string | null | undefined): string | null {
  return (code && ERRORS[code]) || null
}

export function sourceErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code && ERRORS[error.code]) return ERRORS[error.code]
    if (error.code === "invalid_request") return t("Revisa la ruta y los patrones de exclusión.")
  }
  return t("No se pudo completar la operación.")
}
