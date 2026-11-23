import { t } from "@/lib/i18n"
import { FolderPlus } from "lucide-react"
import { type FormEvent, useState } from "react"
import { Button } from "@/components/ui/button"
import { Section } from "@/components/page"
import { Input, Label, Textarea } from "@/components/ui/input"
import { ApiError, type Project, type Workspace, createProject } from "@/lib/api"

const FIELD_NAMES: Record<string, string> = {
  name: t("nombre"),
  project_key: t("clave"),
  description: t("descripción"),
}

// Must match the runtime's PROJECT_KEY_PATTERN.
const PROJECT_KEY_PATTERN = "[a-z0-9]+(-[a-z0-9]+)*"

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "project_key_taken") {
    return t("Ya existe un proyecto con esa clave. Elige otra.")
  }
  if (error instanceof ApiError && error.code === "invalid_request") {
    const fields = error.fields.map((field) => FIELD_NAMES[field] ?? field)
    return fields.length > 0
      ? t("Revisa estos campos: {0}.", fields.join(", "))
      : t("Revisa los campos del formulario.")
  }
  return t("No se pudo crear el proyecto. Comprueba que Scrinium sigue en marcha.")
}

function ProjectList({ projects }: { projects: Project[] }) {
  if (projects.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("Todavía no hay proyectos.")}</p>
  }
  return (
    <ul className="grid content-start gap-2">
      {projects.map((project) => (
        <li key={project.id} className="rounded-lg border p-3">
          <div className="flex items-baseline justify-between gap-4">
            <span className="font-medium">{project.name}</span>
            <code className="text-xs text-muted-foreground">{project.project_key}</code>
          </div>
          {project.description && (
            <p className="mt-0.5 text-sm text-muted-foreground">{project.description}</p>
          )}
        </li>
      ))}
    </ul>
  )
}

export function ProjectsCard({
  workspace,
  projects,
  onCreated,
  onSessionLost,
}: {
  workspace: Workspace
  projects: Project[]
  onCreated: (project: Project) => void
  onSessionLost: (error: unknown) => void
}) {
  const [name, setName] = useState("")
  const [projectKey, setProjectKey] = useState("")
  const [description, setDescription] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    createProject({ name, project_key: projectKey || undefined, description })
      .then((project) => {
        onCreated(project)
        setName("")
        setProjectKey("")
        setDescription("")
      })
      .catch((caught: unknown) => {
        if (caught instanceof ApiError) setError(errorMessage(caught))
        else onSessionLost(caught)
      })
      .finally(() => setSaving(false))
  }

  return (
    <Section title={t("Proyectos")} description={t("{0}. Se guardan solo en este equipo.", workspace.name)}>
      <div className="grid gap-6 md:grid-cols-2">
        <ProjectList projects={projects} />
        <form className="grid content-start gap-3" onSubmit={submit}>
          <p className="text-sm font-medium">{t("Nuevo proyecto")}</p>
          <div className="grid gap-1.5">
            <Label htmlFor="project-name">{t("Nombre")}</Label>
            <Input
              id="project-name"
              required
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="project-key">{t("Clave (opcional)")}</Label>
            <Input
              id="project-key"
              maxLength={64}
              pattern={PROJECT_KEY_PATTERN}
              placeholder={t("Se genera a partir del nombre")}
              title={t("Minúsculas, números y guiones; por ejemplo, pagos-movil")}
              value={projectKey}
              onChange={(event) => setProjectKey(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="project-description">{t("Descripción (opcional)")}</Label>
            <Textarea
              id="project-description"
              maxLength={2000}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" disabled={saving} className="justify-self-start">
            <FolderPlus data-icon="inline-start" />
            {saving ? t("Creando…") : t("Crear proyecto")}
          </Button>
        </form>
      </div>
    </Section>
  )
}
