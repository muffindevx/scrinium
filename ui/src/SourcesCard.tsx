import { t, tn } from "@/lib/i18n"
import { cn } from "cn"
import { FolderGit2, FolderOpen, FolderX, GitBranch, RefreshCw, ScanSearch, Trash2 } from "lucide-react"
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
  default_path: t("Dependencias o build"),
  sensitive_file: t("Archivo sensible"),
  user_pattern: t("Tu exclusión"),
  unsupported_type: t("Tipo no admitido"),
  too_large: t("Demasiado grande"),
  binary: t("Binario"),
  not_utf8: t("No es UTF-8"),
  possible_secret: t("Posible secreto"),
  symlink: t("Enlace simbólico"),
  submodule: t("Submódulo"),
  special_file: t("Archivo especial"),
  hard_link: t("Enlace duro"),
  too_deep: t("Demasiado profundo"),
  bad_path: t("Ruta no válida"),
  limit_reached: t("Límite alcanzado"),
  generated: t("Código generado"),
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return t("{0} KB", (bytes / 1024).toFixed(1))
  return t("{0} MB", (bytes / 1024 / 1024).toFixed(1))
}

function samePreviewInput(a: SourceRequest, b: SourceRequest | null): boolean {
  return (
    b !== null &&
    a.kind === b.kind &&
    a.path === b.path &&
    a.exclude_patterns.join("\n") === b.exclude_patterns.join("\n") &&
    Boolean(a.include_generated) === Boolean(b.include_generated)
  )
}

function PreviewView({ preview }: { preview: SourcePreview }) {
  return (
    <div className="grid gap-3 rounded-lg border bg-muted/30 p-3 text-sm">
      <p>
        {tn(
          "Se leerán {0} archivos ({1}) y se excluirán {2}.",
          <strong>{preview.included_count}</strong>,
          formatBytes(preview.included_bytes),
          <strong>{preview.excluded_count}</strong>,
        )}
        {preview.commit && (
          <>
            {" "}
            {t("Commit")} <code>{preview.commit.slice(0, 12)}</code>.
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
        <summary className="cursor-pointer">{t("Archivos incluidos")}</summary>
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
          <summary className="cursor-pointer">{t("Archivos excluidos y motivo")}</summary>
          <ul className="mt-2 max-h-48 overflow-auto text-xs">
            {preview.excluded.map((file) => (
              <li key={file.path} className="flex justify-between gap-3 py-0.5">
                <span className="truncate font-mono">{file.path}</span>
                <span className="shrink-0 text-muted-foreground">
                  {REASONS[file.reason] ?? file.reason}
                  {file.rule && ` (${file.rule}${file.line ? t(", línea {0}", file.line) : ""})`}
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
  if (!run) return t("Todavía no se ha leído")
  if (run.status === "queued" || run.status === "running") return t("Leyendo archivos…")
  if (run.status === "failed") return t("Falló: {0}", sourceErrorLabel(run.error_summary) ?? "error")
  const when = source.last_successful_sync_at ? `${relativeTime(source.last_successful_sync_at)}: ` : ""
  return when + t("{0} nuevos, {1} cambiados, {2} quitados, {3} excluidos", run.items_created, run.items_updated, run.items_deleted, run.items_excluded)
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
  const missing = source.root_exists === false && !source.remote_url
  const [tone, label] = missing ? (["attention", t("Carpeta no encontrada")] as const) : sourceTone(source)
  const KindIcon = source.kind === "git_repository" ? GitBranch : FolderOpen

  return (
    <li className={cn("grid gap-2 rounded-lg border p-3", missing && "border-destructive/40")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-medium">
          <KindIcon className="size-4 text-muted-foreground" aria-hidden="true" />
          {source.display_name}
        </span>
        <StatusBadge tone={tone}>{label}</StatusBadge>
      </div>
      <code className="truncate text-xs text-muted-foreground">{source.remote_url ?? source.root}</code>
      {source.kind === "git_repository" && source.indexed_commit && (
        <p className="text-xs text-muted-foreground">
          {t("Rama {0} · commit indexado {1}", source.branch ?? "—", source.indexed_commit.slice(0, 8))}
        </p>
      )}
      {missing && (
        <p role="alert" className="flex items-start gap-1.5 text-xs text-destructive">
          <FolderX className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          {t("Esta carpeta ya no existe: se movió, se renombró o el disco no está montado. Vuelve a conectarlo, o añade la fuente con su ruta nueva y borra esta.")}
        </p>
      )}
      {source.head_moved && (
        <p role="status" className="text-xs text-amber-700 dark:text-amber-400">
          {t("El repositorio avanzó desde la última sincronización: sincroniza para ver lo nuevo.")}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        {source.kind === "git_repository" ? t("Repositorio Git") : t("Carpeta de documentos")} ·{" "}
        {source.active_files} {t("archivos ·")} {syncSummary(source)}
      </p>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={busy || missing} onClick={() => run(syncSource(source.id))}>
          <RefreshCw data-icon="inline-start" />
          {t("Sincronizar")}
        </Button>
        {impact === null && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={askImpact}>
            <Trash2 data-icon="inline-start" />
            {t("Borrar")}
          </Button>
        )}
      </div>
      {impact !== null && (
        <div className="grid gap-2 rounded-lg border border-destructive/40 p-3 text-sm">
          <p>
            {t("Se borrarán")} {plural(impact.files, t("archivo"), t("archivos"))},{" "}
            {plural(impact.revisions, t("revisión"), t("revisiones"))}{t(", sus fragmentos y vectores,")}{" "}
            {plural(impact.evidence, t("evidencia"), t("evidencias"))} {t("y")}{" "}
            {plural(impact.releases, t("release"), t("releases"))}.
            {impact.entities_to_delete > 0 &&
              t(" También {0} el LLM solo a partir de esta fuente.", plural(impact.entities_to_delete, t("regla que propuso"), t("reglas que propuso")))}
            {impact.entities_to_review - impact.entities_reviewed_here > 0 &&
              t(" {0} para revisar.", plural(impact.entities_to_review - impact.entities_reviewed_here, t("regla que también cita otras fuentes se conserva, marcada"), t("reglas que también citan otras fuentes se conservan, marcadas")))}
          </p>
          {impact.entities_reviewed_here > 0 && (
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={deleteReviewed}
                onChange={(event) => setDeleteReviewed(event.target.checked)}
              />
              {t("Borrar también")}{" "}
              {plural(impact.entities_reviewed_here, t("regla revisada o escrita por una persona"), t("reglas revisadas o escritas por personas"))}{" "}
              {t("que solo citan esta fuente (si no, se conservan marcadas para revisar)")}
            </label>
          )}
          {citing > 0 && (
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={deleteCiting}
                onChange={(event) => setDeleteCiting(event.target.checked)}
              />
              {t("Borrar también")}{" "}
              {[
                impact.citing_questions && plural(impact.citing_questions, t("pregunta"), t("preguntas")),
                impact.citing_analyses && plural(impact.citing_analyses, t("análisis"), t("análisis")),
              ]
                .filter(Boolean)
                .join(" y ")}{" "}
              {t("que la citan (sus respuestas pueden repetir su contenido)")}
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
              {t("Borrar fuente")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setImpact(null)}>
              {t("Cancelar")}
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
  const [includeGenerated, setIncludeGenerated] = useState(false)
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
    include_generated: includeGenerated,
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

  const cloneUrl = () => {
    setBusy(true)
    setError(null)
    createSource(selected, { ...input, path: "", url: path.trim() })
      .then(() => {
        setPath("")
        setPatterns("")
        refresh()
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
      title={t("Fuentes del proyecto")}
      description={t("Scrinium lee sin modificar nada. De un repositorio Git lee el último commit; de una carpeta, solo Markdown y texto.")}
    >
      <div className="grid gap-6 md:grid-cols-2">
        <div className="grid content-start gap-3">
          {sources.length === 0 ? (
            <EmptyState icon={FolderGit2} title={t("Sin fuentes todavía")}>
              {t("Añade el repositorio del proyecto o una carpeta con su documentación.")}
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
          <p className="text-sm font-medium">{t("Añadir una fuente")}</p>
          <Tabs value={kind} onValueChange={(value) => setKind(value as SourceKind)}>
            <TabsList aria-label={t("Tipo de fuente")}>
              <TabsTrigger value="git_repository">
                <GitBranch />
                {t("Repositorio Git")}
              </TabsTrigger>
              <TabsTrigger value="folder">
                <FolderOpen />
                {t("Carpeta de documentos")}
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="grid gap-1.5">
            <Label htmlFor="source-path">
              {kind === "git_repository" ? t("Ruta de la carpeta del proyecto") : t("Ruta de la carpeta de documentos")}
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
            {looksLikeUrl && kind === "git_repository" && path.trim().startsWith("https://") ? (
              <div role="status" className="grid gap-2 rounded-md border border-sky-500/30 bg-sky-500/5 p-3 text-xs">
                <p>
                  {t("Es la dirección del repositorio en un servidor. Scrinium puede clonarlo en su directorio de datos: solo el último commit de la rama principal, sin ejecutar nada del repositorio. Si es privado, usa las credenciales de Git de este equipo.")}
                </p>
                <div>
                  <Button type="button" size="sm" disabled={busy} onClick={cloneUrl}>
                    {busy ? t("Clonando…") : t("Clonar y sincronizar")}
                  </Button>
                </div>
              </div>
            ) : looksLikeUrl ? (
              <p role="alert" className="text-xs text-amber-700 dark:text-amber-400">
                {t("Eso es la dirección del repositorio en un servidor. Escribe la ruta de la carpeta del proyecto en este equipo, por ejemplo /home/tu-usuario/repos/mi-aplicacion.")}
              </p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              {kind === "git_repository"
                ? t("La carpeta de tu proyecto en este equipo, que use Git (tiene una carpeta .git). Scrinium lee lo confirmado en la rama actual; no se conecta a GitHub ni a ningún otro servidor.")
                : t("Una carpeta con documentos de texto (Markdown, .txt, reStructuredText, AsciiDoc), leída tal como está en el disco.")}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="source-patterns">{t("Exclusiones extra (una por línea, opcional)")}</Label>
            <Textarea
              id="source-patterns"
              placeholder={"docs/borradores/\n*.log"}
              value={patterns}
              onChange={(event) => setPatterns(event.target.value)}
            />
          </div>
          {kind === "git_repository" && (
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={includeGenerated}
                onChange={(event) => setIncludeGenerated(event.target.checked)}
              />
              <span>
                {t("Incluir el código generado")}
                <span className="block text-xs text-muted-foreground">
                  {t("Por defecto se excluyen los archivos que dicen ser generados (@generated, DO NOT EDIT) y los de generadores conocidos (__generated__/, *.pb.go, *_pb2.py): no los escribe una persona y no contienen sus reglas.")}
                </span>
              </span>
            </label>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="outline" disabled={busy}>
              <ScanSearch data-icon="inline-start" />
              {busy && !preview ? t("Revisando…") : t("Revisar qué se leerá")}
            </Button>
            <Button
              type="button"
              disabled={busy || !preview || !samePreviewInput(input, previewedInput)}
              onClick={index}
            >
              <FolderGit2 data-icon="inline-start" />
              {t("Añadir y sincronizar")}
            </Button>
          </div>
          {preview && <PreviewView preview={preview} />}
        </form>
      </div>
    </Section>
  )
}
