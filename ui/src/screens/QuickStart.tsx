import { t } from "@/lib/i18n"
import { FolderGit2, Loader2 } from "lucide-react"
import { type FormEvent, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input, Label } from "@/components/ui/input"
import { ApiError, type Project, type SourceKind, createProject, createSource, deleteProject } from "@/lib/api"
import type { Drop } from "@/lib/folder-drop"
import { sourceErrorText } from "@/lib/source-errors"
import { ErrorText } from "@/screens/shared"

/** The folder's own name: "/home/ana/facturacion/" → "facturacion". */
function folderName(path: string): string {
  return path.trim().replace(/[\\/]+$/, "").split(/[\\/]/).pop() || t("Mi proyecto")
}

/**
 * Adds the project's folder as a source, trying it as a Git repository first and, if it is not
 * one, as a folder of documents. Sync starts on its own.
 */
async function addFolder(projectId: string, path: string): Promise<SourceKind> {
  const source = { path: path.trim(), exclude_patterns: [] }
  try {
    await createSource(projectId, { ...source, kind: "git_repository" })
    return "git_repository"
  } catch (error) {
    if (!(error instanceof ApiError && error.code === "not_a_git_repository")) throw error
  }
  await createSource(projectId, { ...source, kind: "folder" })
  return "folder"
}

/**
 * From an empty window to a project being read, in one step: the path of the repository or
 * folder. Without a project it creates one named after the folder.
 */
export function QuickStart({
  project,
  drop,
  onProjectCreated,
  onAdded,
  onSessionLost,
}: {
  project: Project | null
  drop: Drop | null
  onProjectCreated: (project: Project) => void
  onAdded: (kind: SourceKind) => void
  onSessionLost: (error: unknown) => void
}) {
  const [path, setPath] = useState(drop?.kind === "path" ? drop.path : "")
  const [added, setAdded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const attempt = async () => {
      if (project) return addFolder(project.id, path)
      const made = await createProject({ name: folderName(path) })
      try {
        const kind = await addFolder(made.id, path)
        onProjectCreated(made)
        return kind
      } catch (error) {
        // A project with a wrong path is not left behind: it was made in this same attempt, empty.
        await deleteProject(made.id, made.project_key).catch(() => undefined)
        throw error
      }
    }
    attempt()
      .then((kind) => {
        setAdded(true)
        onAdded(kind)
      })
      .catch((caught: unknown) => {
        if (caught instanceof ApiError) setError(sourceErrorText(caught))
        else onSessionLost(caught)
      })
      .finally(() => setBusy(false))
  }

  // Added: Home shows the project syncing, and a dropped path is not offered again.
  if (added) return null

  return (
    <section className="grid gap-4 rounded-xl border bg-card p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
          <FolderGit2 className="size-5 text-muted-foreground" aria-hidden="true" />
        </span>
        <div className="grid gap-1">
          <h2 className="font-heading text-lg font-medium">
            {project ? t("Añade la carpeta al proyecto") : t("Empieza por la carpeta de tu proyecto")}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t("Scrinium la lee en este equipo, sin modificar nada, y deja fuera los secretos y archivos sensibles. De un repositorio Git lee el último commit; de otra carpeta, sus documentos. También puedes soltar la carpeta sobre la ventana.")}
          </p>
        </div>
      </div>
      <form onSubmit={start} className="grid gap-2">
        <Label htmlFor="quick-path">{t("Ruta de la carpeta")}</Label>
        <div className="flex flex-wrap gap-2">
          <Input
            id="quick-path"
            required
            maxLength={4096}
            placeholder="/home/tu-usuario/proyecto"
            className="min-w-0 flex-1 font-mono"
            value={path}
            onChange={(event) => setPath(event.target.value)}
          />
          <Button type="submit" disabled={busy || !path.trim()}>
            {busy && <Loader2 className="animate-spin" data-icon="inline-start" />}
            {busy ? t("Añadiendo…") : t("Leer el proyecto")}
          </Button>
        </div>
        {!project && path.trim() && (
          <p className="text-xs text-muted-foreground">
            {t("Se creará el proyecto «")}{folderName(path)}{t("». Podrás añadirle más fuentes en Fuentes.")}
          </p>
        )}
        {drop?.kind === "no_path" && !path && (
          <p className="text-xs text-muted-foreground" role="status">
            {t("El navegador no dice dónde está lo que sueltas: escribe la ruta. En la ventana de Scrinium sí se puede soltar.")}
          </p>
        )}
        <ErrorText>{error}</ErrorText>
      </form>
    </section>
  )
}
