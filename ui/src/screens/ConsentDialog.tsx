import { t } from "@/lib/i18n"
import { type FormEvent, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { TASK_LABELS } from "@/lib/ai"
import { type AiTask, type ModelProvider, type Project, type RetentionNotice, getRetention, giveAiConsent } from "@/lib/api"
import { RetentionDetails } from "@/screens/ai-shared"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

/** Consent to send these tasks of a project to this model, read in a modal (ADR-010). With
 * `makeDefault`, the tasks then use it by default; without it, it is only allowed for them. */
export function ConsentDialog({
  project,
  provider,
  model,
  tasks,
  makeDefault = true,
  onClose,
  onGiven,
  onSessionLost,
}: {
  project: Project
  provider: ModelProvider
  model: string
  tasks: AiTask[]
  makeDefault?: boolean
  onClose: () => void
  onGiven: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [notice, setNotice] = useState<RetentionNotice | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  useEffect(() => {
    getRetention(provider.id, model).then(setNotice).catch(fail)
  }, [provider.id, model, fail])
  const allow = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    giveAiConsent(project.id, {
      provider_id: provider.id,
      model,
      tasks,
      retention_acknowledged: acknowledged,
      make_default: makeDefault,
    })
      .then(onGiven)
      .catch(fail)
      .finally(() => setBusy(false))
  }
  return (
    <Dialog
      open
      onOpenChange={(next) => !next && onClose()}
      title={t("Usar {0} · {1}", provider.name, model)}
      description={t("Para {0} de «{1}».", tasks.map((task) => TASK_LABELS[task].toLowerCase()).join(", "), project.name)}
    >
      <form className="grid gap-3" onSubmit={allow}>
        <p className="text-sm">
          {t("Lo que se envíe queda en manos de un tercero: los pasajes que necesite cada tarea salen a")}{" "}
          <strong>{provider.host}</strong>{t(". Antes, Scrinium quita las claves que reconoce y nunca envía las rutas marcadas en Límites. Cada envío queda en Actividad.")}
        </p>
        <div className="grid gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
          <span className="font-medium">{t("Qué guarda")} {provider.name}</span>
          {notice ? <RetentionDetails notice={notice} /> : <Muted>{t("Cargando el aviso…")}</Muted>}
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={acknowledged}
            disabled={notice === null}
            onChange={(e) => setAcknowledged(e.target.checked)}
          />
          {t("He leído qué guarda")} {provider.name} {t("y acepto enviarle esas tareas de este proyecto.")}
        </label>
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("Cancelar")}
          </Button>
          <Button type="submit" disabled={!acknowledged || busy}>
            {t("Permitir")}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
