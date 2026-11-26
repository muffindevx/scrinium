import { t } from "@/lib/i18n"
import { useEffect, useState } from "react"
import { Link } from "react-router"
import { type AiSetup, type SecurityStatus, getAiSetup, getSecurityStatus } from "@/lib/api"
import { setupLabel } from "@/lib/labels"

/**
 * Extraction cannot start because the answer model does not respond. It says which model is set
 * and why, in the same words as Settings › AI models, and sends there: the steps live in one place.
 */
export function ModelNeeded() {
  const [setup, setSetup] = useState<AiSetup | null>(null)
  const [status, setStatus] = useState<SecurityStatus | null>(null)
  useEffect(() => {
    getAiSetup().then(setSetup).catch(() => undefined)
    getSecurityStatus().then(setStatus).catch(() => undefined)
  }, [])
  const llm = status?.providers.llm
  const why =
    llm === "model_missing"
      ? t("Aún no se ha descargado.")
      : t("No responde.")
  return (
    <div className="grid gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
      <h3 className="font-semibold">{t("El modelo de respuestas no está listo")}</h3>
      <p className="text-muted-foreground">
        {setup ? t("Ahora usas: {0}.", setupLabel(setup)) : null} {why}{" "}
        {t("Sin él no se pueden extraer reglas.")}
      </p>
      <div>
        <Link
          to="/ajustes/modelos"
          className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground"
        >
          {t("Revisar en Ajustes › Modelos de IA")}
        </Link>
      </div>
    </div>
  )
}

/** The included answer model works, but it is small: said before extracting with it. */
export function IncludedModelNote() {
  return (
    <div className="grid gap-1 rounded-lg border p-3 text-sm">
      <p className="font-medium">{t("Usas el modelo incluido: su precisión es limitada")}</p>
      <p className="text-muted-foreground">
        {t("Es pequeño: se deja reglas y puede equivocarse en alguna. Revisa cada una antes de verificarla. Para mejores reglas, usa un modelo mayor o tu propia API key en")}{" "}
        <Link to="/ajustes/modelos" className="font-medium text-foreground underline-offset-4 hover:underline">
          {t("Ajustes › Modelos de IA")}
        </Link>
        .
      </p>
    </div>
  )
}
