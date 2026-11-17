import { t } from "@/lib/i18n"
import { PageHeader } from "@/components/page"
import type { Project, Workspace } from "@/lib/api"
import { ProjectsCard } from "@/ProjectsCard"
import { SourcesCard } from "@/SourcesCard"

export function SourcesScreen({
  project,
  workspace,
  projects,
  onProjectCreated,
  onSessionLost,
}: {
  project: Project | null
  workspace: Workspace
  projects: Project[]
  onProjectCreated: (project: Project) => void
  onSessionLost: (error: unknown) => void
}) {
  return (
    <>
      <PageHeader
        title={t("Fuentes")}
        description={t("Los proyectos y lo que Scrinium lee de cada uno: repositorios y carpetas de este equipo, con lo que se excluye y por qué.")}
      />
      {project && <SourcesCard key={project.id} projects={[project]} onSessionLost={onSessionLost} />}
      <ProjectsCard
        workspace={workspace}
        projects={projects}
        onCreated={onProjectCreated}
        onSessionLost={onSessionLost}
      />
    </>
  )
}
