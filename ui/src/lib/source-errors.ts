import { t } from "@/lib/i18n"
import { ApiError } from "@/lib/api"

// What went wrong adding or syncing a source, in the person's words.
const ERRORS: Record<string, string> = {
  path_not_absolute: t("Escribe una ruta absoluta, por ejemplo /home/tu-usuario/proyecto."),
  path_not_found: t("Esa carpeta no existe."),
  not_shared_with_docker: t("Esa carpeta de tu equipo no está compartida con Docker. Arranca Scrinium con ella: sh docker/scrinium.sh start <carpeta>"),
  not_a_directory: t("Esa ruta no es una carpeta."),
  wrong_owner: t("Esa carpeta pertenece a otra cuenta."),
  contains_scrinium_data: t("Esa carpeta contiene los datos de Scrinium, o está dentro de ellos."),
  folders_unsupported: t("Este sistema no permite leer carpetas de forma segura."),
  not_a_git_repository: t("Esa carpeta no es un repositorio Git."),
  not_repository_root: t("Elige la carpeta raíz del repositorio, no una subcarpeta."),
  empty_repository: t("El repositorio todavía no tiene commits."),
  invalid_url: t("Solo direcciones https:// a un servidor con nombre, sin usuario ni contraseña en la dirección."),
  clone_failed: t("No se pudo clonar: revisa la dirección, la red o tus credenciales de Git."),
  fetch_failed: t("No se pudo traer el último commit del servidor: revisa la red o tus credenciales de Git."),
  partial_clone: t("Es un clon parcial y faltan archivos en este equipo. Scrinium no los descarga: ejecuta git fetch o clona el repositorio entero."),
  git_unavailable: t("No se encontró git en este equipo."),
  git_failed: t("Git no pudo leer el repositorio."),
  not_same_source: t("Esa carpeta no es el mismo repositorio: no tiene el último commit que Scrinium leyó."),
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
