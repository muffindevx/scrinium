import { t } from "@/lib/i18n"
import { ArrowRight, Cpu } from "lucide-react"
import { Link } from "react-router"
import { PageHeader, Section } from "@/components/page"
import type { Project, Workspace } from "@/lib/api"
import { ProjectsCard } from "@/ProjectsCard"
import { TicketConnections } from "@/screens/TicketConnections"
import { SourcesCard } from "@/SourcesCard"

/**
 * Everything Scrinium is connected to, in one place: what it reads (repositories and folders),
 * the ticket trackers, the AI models and the projects. Models are set up in Ajustes and allowed
 * per project in IA del proyecto; this screen only points there.
 */
export function IntegrationsScreen({
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
        title={t("Integraciones")}
        description={t("Lo que Scrinium lee y con qué se conecta: repositorios y carpetas de este equipo, gestores de tickets y modelos de IA.")}
      />
      {project && <SourcesCard key={project.id} projects={[project]} onSessionLost={onSessionLost} />}
      {project && <TicketConnections key={`tickets-${project.id}`} project={project} onSessionLost={onSessionLost} />}
      <Section
        title={
          <span className="flex items-center gap-2">
            <Cpu className="size-4" />
            {t("Modelos de IA")}
          </span>
        }
        description={t("Los proveedores y servidores locales se registran una vez para todo el equipo; cada proyecto decide cuáles puede usar.")}
      >
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <Link to="/ajustes/modelos" className="inline-flex items-center gap-1.5 font-medium hover:underline">
            {t("Proveedores y modelos")} <ArrowRight className="size-3.5" aria-hidden="true" />
          </Link>
          {project && (
            <Link to="/ia" className="inline-flex items-center gap-1.5 font-medium hover:underline">
              {t("Permisos de este proyecto")} <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
          )}
        </div>
      </Section>
      <ProjectsCard
        workspace={workspace}
        projects={projects}
        onCreated={onProjectCreated}
        onSessionLost={onSessionLost}
      />
    </>
  )
}
