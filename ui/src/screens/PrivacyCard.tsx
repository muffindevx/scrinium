import { Download, ShieldCheck, Trash2, Upload } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Section } from "@/components/page"
import { Input, Label } from "@/components/ui/input"
import {
  type Project,
  type StoredData,
  deleteProject,
  exportWorkspace,
  getStoredData,
  restoreWorkspace,
  wipeWorkspace,
} from "@/lib/api"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

const WIPE_CONFIRMATION = "BORRAR TODO"
const MIN_PASSPHRASE = 12

const COUNT_LABELS: Record<string, string> = {
  projects: "Proyectos",
  sources: "Fuentes",
  files: "Archivos",
  revisions: "Revisiones",
  fragments: "Citas de código preparadas",
  rules: "Reglas",
  evidence: "Evidencias",
  analyses: "Preguntas y análisis",
  findings: "Hallazgos",
  releases: "Versiones",
  audit_events: "Eventos de auditoría",
}

function size(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function ExportForm({ onSessionLost }: { onSessionLost: (error: unknown) => void }) {
  const [passphrase, setPassphrase] = useState("")
  const [repeat, setRepeat] = useState("")
  const [unencrypted, setUnencrypted] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const mismatch = !unencrypted && repeat !== "" && passphrase !== repeat
  const ready = unencrypted || (passphrase.length >= MIN_PASSPHRASE && passphrase === repeat)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setDone(null)
    setBusy(true)
    exportWorkspace(unencrypted ? null : passphrase)
      .then((name) => {
        setDone(name)
        setPassphrase("")
        setRepeat("")
      })
      .catch(fail)
      .finally(() => setBusy(false))
  }
  return (
    <form className="grid gap-3" onSubmit={submit}>
      <p className="text-sm font-medium">Exportar</p>
      <Muted>
        Todo el workspace, con el texto de las fuentes, en un archivo. Va cifrado con tu
        contraseña: sin ella no se puede abrir, y Scrinium no la guarda. Los vectores y fragmentos no
        van dentro; se regeneran.
      </Muted>
      {!unencrypted && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor="export-passphrase">Contraseña (mínimo {MIN_PASSPHRASE})</Label>
            <Input
              id="export-passphrase"
              type="password"
              autoComplete="new-password"
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="export-repeat">Repítela</Label>
            <Input
              id="export-repeat"
              type="password"
              autoComplete="new-password"
              aria-invalid={mismatch}
              value={repeat}
              onChange={(event) => setRepeat(event.target.value)}
            />
          </div>
        </div>
      )}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={unencrypted}
          onChange={(event) => setUnencrypted(event.target.checked)}
        />
        Exportar sin cifrar
      </label>
      {unencrypted && (
        <ErrorText>
          Sin cifrar, cualquiera que tenga el archivo puede leer tu código y tus documentos.
        </ErrorText>
      )}
      {mismatch && <ErrorText>Las contraseñas no coinciden.</ErrorText>}
      <Button type="submit" variant="outline" disabled={!ready || busy} className="justify-self-start">
        <Download data-icon="inline-start" />
        {busy ? "Preparando…" : "Descargar exportación"}
      </Button>
      {done && <Muted>Descargado: {done}. Guárdalo en un sitio seguro.</Muted>}
      <ErrorText>{error}</ErrorText>
    </form>
  )
}

function RestoreForm({
  onRestored,
  onSessionLost,
}: {
  onRestored: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [passphrase, setPassphrase] = useState("")
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!file) return
    setError(null)
    setBusy(true)
    restoreWorkspace(file, passphrase)
      .then(onRestored)
      .catch(fail)
      .finally(() => setBusy(false))
  }
  return (
    <form className="grid gap-3" onSubmit={submit}>
      <p className="text-sm font-medium">Restaurar una exportación</p>
      <Muted>
        Solo con el workspace vacío. Se comprueba el archivo entero antes de escribir nada; si algo
        no cuadra, no se restaura nada. Después se vuelven a calcular el índice y los vectores.
      </Muted>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1.5">
          <Label htmlFor="restore-file">Archivo (.scrinium o .json)</Label>
          <Input
            id="restore-file"
            type="file"
            accept=".scrinium,.json"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="restore-passphrase">Contraseña (si está cifrado)</Label>
          <Input
            id="restore-passphrase"
            type="password"
            autoComplete="off"
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
          />
        </div>
      </div>
      <Button type="submit" variant="outline" disabled={!file || busy} className="justify-self-start">
        <Upload data-icon="inline-start" />
        {busy ? "Restaurando…" : "Restaurar"}
      </Button>
      <ErrorText>{error}</ErrorText>
    </form>
  )
}

function ConfirmedDelete({
  title,
  description,
  expected,
  action,
  onSessionLost,
}: {
  title: string
  description: string
  expected: string
  action: () => Promise<unknown>
  onSessionLost: (error: unknown) => void
}) {
  const [typed, setTyped] = useState("")
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const id = `confirm-${expected.replace(/\W+/g, "-")}`
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    action()
      .catch(fail)
      .finally(() => setBusy(false))
  }
  return (
    <form className="grid gap-2" onSubmit={submit}>
      <p className="text-sm font-medium">{title}</p>
      <Muted>{description}</Muted>
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid min-w-48 flex-1 gap-1.5">
          <Label htmlFor={id}>
            Escribe <code>{expected}</code> para confirmar
          </Label>
          <Input
            id={id}
            autoComplete="off"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
        </div>
        <Button type="submit" variant="destructive" disabled={typed !== expected || busy}>
          <Trash2 data-icon="inline-start" />
          {busy ? "Borrando…" : "Borrar"}
        </Button>
      </div>
      <ErrorText>{error}</ErrorText>
    </form>
  )
}

export function PrivacyCard({
  project,
  onProjectDeleted,
  onWiped,
  onSessionLost,
}: {
  project: Project | null
  onProjectDeleted: (projectId: string) => void
  onWiped: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [stored, setStored] = useState<StoredData | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    getStoredData().then(setStored).catch(fail)
  }, [fail])
  useEffect(load, [load])

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <ShieldCheck className="size-4" />
          Datos guardados
        </span>
      }
      description="Qué guarda Scrinium en este equipo, cómo llevártelo y cómo borrarlo. Al borrar, también se purga del disco: base de datos, índice de texto y vectores."
    >
        <ErrorText>{error}</ErrorText>
        {stored && (
          <div className="grid gap-2">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
              {Object.entries(COUNT_LABELS).map(([key, label]) => (
                <div key={key} className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="font-medium">{stored.counts[key] ?? 0}</dd>
                </div>
              ))}
            </dl>
            <Muted>
              En disco: base de datos {size(stored.bytes_on_disk.database)}, vectores{" "}
              {size(stored.bytes_on_disk.vectors)}.
            </Muted>
          </div>
        )}
        <div className="border-t pt-4">
          <ExportForm onSessionLost={onSessionLost} />
        </div>
        {stored?.counts.projects === 0 && stored.counts.sources === 0 && (
          <div className="border-t pt-4">
            <RestoreForm onRestored={onWiped} onSessionLost={onSessionLost} />
          </div>
        )}
        {project && (
          <div className="border-t pt-4">
            <ConfirmedDelete
              key={project.id}
              title={`Borrar el proyecto ${project.name}`}
              description="Borra sus fuentes, revisiones, índices, reglas, versiones, preguntas y análisis. Los demás proyectos no se tocan."
              expected={project.project_key}
              action={() => deleteProject(project.id, project.project_key).then(() => onProjectDeleted(project.id))}
              onSessionLost={onSessionLost}
            />
          </div>
        )}
        <div className="border-t pt-4">
          <ConfirmedDelete
            title="Borrar todos los datos"
            description="Borra todo el workspace, la auditoría incluida (queda un único registro de que se borró), las credenciales de conectores del keyring y tu nombre de perfil. Las exportaciones que ya descargaste no se pueden borrar desde aquí."
            expected={WIPE_CONFIRMATION}
            action={() => wipeWorkspace(WIPE_CONFIRMATION).then(onWiped)}
            onSessionLost={onSessionLost}
          />
        </div>
        <Muted>
          Límites: el sistema de archivos o el SSD pueden conservar copias en bloques liberados;
          el cifrado de disco es lo que las protege. Las copias de seguridad del equipo tampoco se
          borran desde aquí.
        </Muted>
    </Section>
  )
}
