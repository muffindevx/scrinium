import { t, tn } from "@/lib/i18n"
import { FolderInput } from "lucide-react"
import { type FormEvent, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input, Label } from "@/components/ui/input"
import { ApiError, type DownloadsInfo, getEngine, moveDownloads } from "@/lib/api"
import { errorMessage } from "@/lib/labels"
import { ErrorText, Eyebrow, Muted } from "@/screens/shared"

const GB = 1024 ** 3
const POLL_MS = 1000

const gigabytes = (bytes: number) =>
  t("{0} GB", (bytes / GB).toLocaleString("es", { maximumFractionDigits: 1 }))

/**
 * Where llama.cpp and the models live, and moving them to another folder (ADR-015): a larger
 * disk, for example. The data directory stays where it is: it holds the text of the sources.
 */
export function DownloadsSection({ initial }: { initial: DownloadsInfo }) {
  const [info, setInfo] = useState(initial)
  const [target, setTarget] = useState("")
  const [error, setError] = useState<string | null>(null)
  const running = info.move?.state === "running"

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => {
      getEngine()
        .then((engine) => setInfo(engine.downloads))
        .catch(() => undefined)
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [running])

  const start = (dir: string) => {
    setError(null)
    moveDownloads(dir)
      .then(setInfo)
      .catch((failure: unknown) =>
        setError(failure instanceof ApiError ? errorMessage(failure.code) : t("No se pudo completar.")),
      )
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (target.trim()) start(target.trim())
  }

  const move = info.move
  const percent = move && move.total > 0 ? Math.min(100, Math.round((move.done / move.total) * 100)) : 0
  return (
    <div className="grid gap-2">
      <Eyebrow>{t("Dónde están las descargas")}</Eyebrow>
      <div className="grid gap-2 rounded-lg border p-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-0 text-xs break-all">{info.dir}</code>
          {info.private && <span className="text-xs text-muted-foreground">{t("(la carpeta de datos)")}</span>}
        </div>
        <Muted>
          {t("llama.cpp y los modelos ocupan {0}; quedan {1} libres en ese disco.", gigabytes(info.used_bytes), gigabytes(info.free_bytes))}{" "}
          {info.private
            ? t("Tus datos (proyectos, reglas, el texto indexado) se quedan siempre en la carpeta de datos.")
            : t("Fuera de la carpeta de datos, Scrinium comprueba los archivos antes de usarlos.")}
        </Muted>

        {info.from_env ? (
          <Muted>{tn("La fija la variable {0}: cámbiala allí para usar otra carpeta.", <code>{info.env_name}</code>)}</Muted>
        ) : running ? (
          <div className="grid gap-1">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-sky-500 transition-all" style={{ width: `${percent}%` }} />
            </div>
            <Muted>{t("Copiando y comprobando cada archivo en {0}… {1} %", move?.target ?? "", percent)}</Muted>
          </div>
        ) : (
          <form className="grid gap-1.5" onSubmit={submit}>
            <Label htmlFor="downloads-target">{t("Mover a otra carpeta")}</Label>
            <div className="flex flex-wrap gap-2">
              <Input
                id="downloads-target"
                className="max-w-md min-w-0 flex-1 font-mono text-xs"
                placeholder="/media/disco/scrinium"
                maxLength={4096}
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              />
              <Button type="submit" size="sm" variant="outline" disabled={!target.trim()}>
                <FolderInput data-icon="inline-start" />
                {t("Mover aquí")}
              </Button>
              {!info.private && (
                <Button type="button" size="sm" variant="ghost" onClick={() => start(info.data_dir)}>
                  {t("Volver a la carpeta de datos")}
                </Button>
              )}
            </div>
            <Muted>
              {t("Se copian, se comprueban byte a byte y solo entonces se cambia de carpeta. Si algo falla, todo sigue como estaba.")}
            </Muted>
          </form>
        )}

        {move?.state === "done" && (
          <p role="status" className="text-sm text-emerald-600 dark:text-emerald-400">
            {t("Movidas. llama.cpp usará la nueva carpeta al reiniciar Scrinium; la copia anterior se borra entonces.")}
          </p>
        )}
        {move?.state !== "done" && info.pending_cleanup && (
          <Muted>{t("La copia anterior en {0} se borra al reiniciar Scrinium.", info.pending_cleanup)}</Muted>
        )}
        <ErrorText>{move?.state === "failed" ? errorMessage(move.error) : error}</ErrorText>
      </div>
    </div>
  )
}
