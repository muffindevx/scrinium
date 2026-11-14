import { FolderGit2, FolderOpen, GitBranch, RefreshCw, ScanSearch, Trash2 } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { EmptyState, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { sourceTone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import { Input, Label, Textarea } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { relativeTime } from "@/lib/time"
import { sourceErrorLabel, sourceErrorText } from "@/lib/source-errors"
import {
  ApiError,
  type Project,
  type Source,
  type SourceKind,
  type SourcePreview,
  type SourceRequest,
  createSource,
  type DeletionImpact,
  deleteSource,
  getDeletionImpact,
  listSources,
  previewSource,
  syncSource,
} from "@/lib/api"

const POLL_MS = 1500

const REASONS: Record<string, string> = {
  default_path: "Dependencias o build",
  sensitive_file: "Archivo sensible",
  user_pattern: "Tu exclusión",
  unsupported_type: "Tipo no admitido",
  too_large: "Demasiado grande",
  binary: "Binario",
  not_utf8: "No es UTF-8",
  possible_secret: "Posible secreto",
  symlink: "Enlace simbólico",
  submodule: "Submódulo",
  special_file: "Archivo especial",
  hard_link: "Enlace duro",
  too_deep: "Demasiado profundo",
  bad_path: "Ruta no válida",
  limit_reached: "Límite alcanzado",
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function samePreviewInput(a: SourceRequest, b: SourceRequest | null): boolean {
  return (
    b !== null &&
    a.kind === b.kind &&
    a.path === b.path &&
    a.exclude_patterns.join("\n") === b.exclude_patterns.join("\n")
  )
}

function PreviewView({ preview }: { preview: SourcePreview }) {
  return (
    <div className="grid gap-3 rounded-lg border bg-muted/30 p-3 text-sm">
      <p>
        Se leerán <strong>{preview.included_count}</strong> archivos (
        {formatBytes(preview.included_bytes)}) y se excluirán{" "}
        <strong>{preview.excluded_count}</strong>.
        {preview.commit && (
          <>
            {" "}
            Commit <code>{preview.commit.slice(0, 12)}</code>.
          </>
        )}
      </p>
      {preview.excluded_count > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {Object.entries(preview.excluded_by_reason).map(([reason, count]) => (
            <li key={reason}>
              <StatusBadge tone={reason === "possible_secret" ? "attention" : "no_evidence"}>
                {REASONS[reason] ?? reason}: {count}
              </StatusBadge>
            </li>
          ))}
        </ul>
      )}
      <details>
        <summary className="cursor-pointer">Archivos incluidos</summary>
        <ul className="mt-2 max-h-48 overflow-auto font-mono text-xs">
          {preview.included.map((file) => (
            <li key={file.path} className="truncate">
              {file.path}
            </li>
          ))}
        </ul>
      </details>
      {preview.excluded_count > 0 && (
        <details>
          <summary className="cursor-pointer">Archivos excluidos y motivo</summary>
          <ul className="mt-2 max-h-48 overflow-auto text-xs">
            {preview.excluded.map((file) => (
              <li key={file.path} className="flex justify-between gap-3 py-0.5">
                <span className="truncate font-mono">{file.path}</span>
                <span className="shrink-0 text-muted-foreground">
                  {REASONS[file.reason] ?? file.reason}
                  {file.rule && ` (${file.rule}${file.line ? `, línea ${file.line}` : ""})`}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function syncSummary(source: Source): string {
  const run = source.latest_sync
  if (!run) return "Todavía no se ha leído"
  if (run.status === "queued" || run.status === "running") return "Leyendo archivos…"
  if (run.status === "failed") return `Falló: ${sourceErrorLabel(run.error_summary) ?? "error"}`
  const when = source.last_successful_sync_at ? `${relativeTime(source.last_successful_sync_at)}: ` : ""
  return `${when}${run.items_created} nuevos, ${run.items_updated} cambiados, ${run.items_deleted} quitados, ${run.items_excluded} excluidos`
}

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`
}

function SourceRow({
  source,
  onChanged,
  onError,
}: {
  source: Source
  onChanged: () => void
  onError: (error: unknown) => void
}) {
  const [impact, setImpact] = useState<DeletionImpact | null>(null)
  const [deleteCiting, setDeleteCiting] = useState(true)
  const [deleteReviewed, setDeleteReviewed] = useState(true)
  const citing = impact ? impact.citing_questions + impact.citing_analyses : 0
  const askImpact = () => {
    getDeletionImpact(source.id).then(setImpact).catch(onError)
  }
  const busy = source.latest_sync?.status === "queued" || source.latest_sync?.status === "running"
  const run = (action: Promise<unknown>) => action.then(onChanged).catch(onError)
  const [tone, label] = sourceTone(source)
  const KindIcon = source.kind === "git_repository" ? GitBranch : FolderOpen

  return (
    <li className="grid gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-medium">
          <KindIcon className="size-4 text-muted-foreground" aria-hidden="true" />
          {source.display_name}
        </span>
        <StatusBadge tone={tone}>{label}</StatusBadge>
      </div>
      <code className="truncate text-xs text-muted-foreground">{source.root}</code>
      <p className="text-xs text-muted-foreground">
        {source.kind === "git_repository" ? "Repositorio Git" : "Carpeta de documentos"} ·{" "}
        {source.active_files} archivos · {syncSummary(source)}
      </p>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => run(syncSource(source.id))}>
          <RefreshCw data-icon="inline-start" />
          Sincronizar
        </Button>
        {impact === null && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={askImpact}>
            <Trash2 data-icon="inline-start" />
            Borrar
          </Button>
        )}
      </div>
      {impact !== null && (
        <div className="grid gap-2 rounded-lg border border-destructive/40 p-3 text-sm">
          <p>
            Se borrarán {plural(impact.files, "archivo", "archivos")},{" "}
            {plural(impact.revisions, "revisión", "revisiones")}, sus fragmentos y vectores,{" "}
            {plural(impact.evidence, "evidencia", "evidencias")} y{" "}
            {plural(impact.releases, "release", "releases")}.
            {impact.entities_to_delete > 0 &&
              ` También ${plural(impact.entities_to_delete, "regla que propuso", "reglas que propuso")} el LLM solo a partir de esta fuente.`}
            {impact.entities_to_review - impact.entities_reviewed_here > 0 &&
              ` ${plural(impact.entities_to_review - impact.entities_reviewed_here, "regla que también cita otras fuentes se conserva, marcada", "reglas que también citan otras fuentes se conservan, marcadas")} para revisar.`}
          </p>
          {impact.entities_reviewed_here > 0 && (
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={deleteReviewed}
                onChange={(event) => setDeleteReviewed(event.target.checked)}
              />
              Borrar también{" "}
              {plural(impact.entities_reviewed_here, "regla revisada o escrita por una persona", "reglas revisadas o escritas por personas")}{" "}
              que solo citan esta fuente (si no, se conservan marcadas para revisar)
            </label>
          )}
          {citing > 0 && (
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={deleteCiting}
                onChange={(event) => setDeleteCiting(event.target.checked)}
              />
              Borrar también{" "}
              {[
                impact.citing_questions && plural(impact.citing_questions, "pregunta", "preguntas"),
                impact.citing_analyses && plural(impact.citing_analyses, "análisis", "análisis"),
              ]
                .filter(Boolean)
                .join(" y ")}{" "}
              que la citan (sus respuestas pueden repetir su contenido)
            </label>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="destructive"
              disabled={busy}
              onClick={() =>
                run(deleteSource(source.id, citing > 0 && deleteCiting, impact.entities_reviewed_here > 0 && deleteReviewed))
              }
            >
              <Trash2 data-icon="inline-start" />
              Borrar fuente
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setImpact(null)}>
              Cancelar
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}

export function SourcesCard({
  projects,
  onSessionLost,
}: {
  projects: Project[]
  onSessionLost: (error: unknown) => void
}) {
  const projectId = projects[0]?.id ?? ""
  const [sources, setSources] = useState<Source[]>([])
  const [kind, setKind] = useState<SourceKind>("git_repository")
  const [path, setPath] = useState("")
  // A repository's address on a server (https, ssh, git@…) instead of a folder of this machine.
  const looksLikeUrl = /^(https?:\/\/|ssh:\/\/|git@|git:\/\/)/i.test(path.trim())
  const [patterns, setPatterns] = useState("")
  const [preview, setPreview] = useState<SourcePreview | null>(null)
  const [previewedInput, setPreviewedInput] = useState<SourceRequest | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selected = projectId || projects[0]?.id || ""
  const input: SourceRequest = {
    kind,
    path: path.trim(),
    exclude_patterns: patterns
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean),
  }

  const handleError = useCallback(
    (caught: unknown) => {
      if (caught instanceof ApiError) setError(sourceErrorText(caught))
      else onSessionLost(caught)
    },
    [onSessionLost],
  )

  const refresh = useCallback(() => {
    if (!selected) return
    listSources(selected).then(setSources).catch(handleError)
  }, [selected, handleError])

  useEffect(refresh, [refresh])

  const syncing = sources.some(
    (source) => source.latest_sync?.status === "queued" || source.latest_sync?.status === "running",
  )
  useEffect(() => {
    if (!syncing) return
    const timer = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(timer)
  }, [syncing, refresh])

  if (projects.length === 0) return null

  const runPreview = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setPreview(null)
    previewSource(selected, input)
      .then((result) => {
        setPreview(result)
        setPreviewedInput(input)
      })
      .catch(handleError)
      .finally(() => setBusy(false))
  }

  const index = () => {
    setBusy(true)
    setError(null)
    createSource(selected, input)
      .then(() => {
        setPreview(null)
        setPreviewedInput(null)
        setPath("")
        setPatterns("")
        refresh()
      })
      .catch(handleError)
      .finally(() => setBusy(false))
  }

  return (
    <Section
      title="Fuentes del proyecto"
      description="Scrinium lee sin modificar nada. De un repositorio Git lee el último commit; de una carpeta, solo Markdown y texto."
    >
      <div className="grid gap-6 md:grid-cols-2">
        <div className="grid content-start gap-3">
          {sources.length === 0 ? (
            <EmptyState icon={FolderGit2} title="Sin fuentes todavía">
              Añade el repositorio del proyecto o una carpeta con su documentación.
            </EmptyState>
          ) : (
            <ul className="grid gap-2">
              {sources.map((source) => (
                <SourceRow
                  key={source.id}
                  source={source}
                  onChanged={refresh}
                  onError={handleError}
                />
              ))}
            </ul>
          )}
        </div>

        <form className="grid content-start gap-3" onSubmit={runPreview}>
          <p className="text-sm font-medium">Añadir una fuente</p>
          <Tabs value={kind} onValueChange={(value) => setKind(value as SourceKind)}>
            <TabsList aria-label="Tipo de fuente">
              <TabsTrigger value="git_repository">
                <GitBranch />
                Repositorio Git
              </TabsTrigger>
              <TabsTrigger value="folder">
                <FolderOpen />
                Carpeta de documentos
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="grid gap-1.5">
            <Label htmlFor="source-path">
              {kind === "git_repository" ? "Ruta de la carpeta del proyecto" : "Ruta de la carpeta de documentos"}
            </Label>
            <Input
              id="source-path"
              required
              maxLength={4096}
              placeholder={
                kind === "git_repository" ? "/home/tu-usuario/repos/mi-aplicacion" : "/home/tu-usuario/Documentos/especificaciones"
              }
              value={path}
              onChange={(event) => setPath(event.target.value)}
            />
            {looksLikeUrl && (
              <p role="alert" className="text-xs text-amber-700 dark:text-amber-400">
                Eso es la dirección del repositorio en un servidor. Escribe la ruta de la carpeta del proyecto en este
                equipo, por ejemplo /home/tu-usuario/repos/mi-aplicacion.
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {kind === "git_repository"
                ? "La carpeta de tu proyecto en este equipo, que use Git (tiene una carpeta .git). Scrinium lee lo confirmado en la rama actual; no se conecta a GitHub ni a ningún otro servidor."
                : "Una carpeta con documentos de texto (Markdown, .txt, reStructuredText, AsciiDoc), leída tal como está en el disco."}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="source-patterns">Exclusiones extra (una por línea, opcional)</Label>
            <Textarea
              id="source-patterns"
              placeholder={"docs/borradores/\n*.log"}
              value={patterns}
              onChange={(event) => setPatterns(event.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="outline" disabled={busy}>
              <ScanSearch data-icon="inline-start" />
              {busy && !preview ? "Revisando…" : "Revisar qué se leerá"}
            </Button>
            <Button
              type="button"
              disabled={busy || !preview || !samePreviewInput(input, previewedInput)}
              onClick={index}
            >
              <FolderGit2 data-icon="inline-start" />
              Añadir y sincronizar
            </Button>
          </div>
          {preview && <PreviewView preview={preview} />}
        </form>
      </div>
    </Section>
  )
}
