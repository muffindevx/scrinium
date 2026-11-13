import {
  BookOpen,
  FolderGit2,
  House,
  LockKeyhole,
  LogOut,
  type LucideIcon,
  Map as MapIcon,
  MessageSquare,
  Network,
  ScanSearch,
  ScrollText,
  Settings,
  Tags,
  TriangleAlert,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useState } from "react"
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router"
import { cn } from "cn"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Select } from "@/components/ui/select"
import { EmptyState } from "@/components/page"
import {
  type DataFlow,
  type Project,
  type SecurityStatus,
  SessionRequiredError,
  type Workspace,
  getDataFlow,
  getProfile,
  getProjects,
  getSecurityStatus,
  getWorkspace,
  logout,
} from "@/lib/api"
import { AskScreen } from "@/screens/AskScreen"
import { HomeScreen } from "@/screens/HomeScreen"
import { ImpactScreen } from "@/screens/ImpactScreen"
import { ArchitectureScreen } from "@/screens/ArchitectureScreen"
import { MapScreen } from "@/screens/MapScreen"
import { RulesScreen } from "@/screens/RulesScreen"
import { SettingsScreen } from "@/screens/SettingsScreen"
import { SourcesScreen } from "@/screens/SourcesScreen"
import { VersionsScreen } from "@/screens/VersionsScreen"

export interface ReadyData {
  status: SecurityStatus
  dataFlow: DataFlow
  workspace: Workspace
  projects: Project[]
  profileName: string | null
}

type ViewState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyData)
  | { kind: "signed_out" }
  | { kind: "no_session" }
  | { kind: "error" }

function errorState(error: unknown): ViewState {
  return error instanceof SessionRequiredError ? { kind: "no_session" } : { kind: "error" }
}

function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Alert className="max-w-xl">
      <TriangleAlert />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}

interface NavItem {
  path: string
  label: string
  icon: LucideIcon
}

// The same routes the runtime accepts in api/static.py (UI_ROUTES).
const NAVIGATION: { group: string | null; items: NavItem[] }[] = [
  { group: "Proyecto", items: [{ path: "/", label: "Inicio", icon: House }] },
  {
    group: "Conocimiento",
    items: [
      { path: "/preguntar", label: "Preguntar", icon: MessageSquare },
      { path: "/reglas", label: "Reglas", icon: BookOpen },
      { path: "/mapa", label: "Mapa", icon: MapIcon },
      { path: "/arquitectura", label: "Arquitectura", icon: Network },
    ],
  },
  {
    group: "Cambios",
    items: [
      { path: "/impacto", label: "Impacto", icon: ScanSearch },
      { path: "/versiones", label: "Versiones", icon: Tags },
    ],
  },
]
const FOOTER_NAVIGATION: NavItem[] = [
  { path: "/fuentes", label: "Fuentes", icon: FolderGit2 },
  { path: "/ajustes", label: "Ajustes", icon: Settings },
]

const PROJECT_KEY = "scrinium.project"

function storedProject(): string | null {
  try {
    return window.localStorage.getItem(PROJECT_KEY)
  } catch {
    return null
  }
}

function forgetProject() {
  try {
    window.localStorage.removeItem(PROJECT_KEY)
  } catch {
    // Nothing to forget if there is no storage.
  }
}

function storeProject(projectId: string) {
  try {
    window.localStorage.setItem(PROJECT_KEY, projectId)
  } catch {
    // Only a convenience: without storage the first project is chosen.
  }
}

function SidebarLink({ item }: { item: NavItem }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.path}
      end={item.path === "/"}
      title={item.label}
      className={({ isActive }) =>
        cn(
          "flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-sm transition-colors max-lg:justify-center max-lg:px-0",
          isActive
            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
            : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
        )
      }
    >
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span className="max-lg:sr-only">{item.label}</span>
    </NavLink>
  )
}

function Sidebar() {
  return (
    <nav
      aria-label="Secciones"
      className="flex w-56 shrink-0 flex-col justify-between border-r bg-sidebar p-3 text-sidebar-foreground max-lg:w-14 max-lg:p-2"
    >
      <div className="grid gap-4">
        {NAVIGATION.map(({ group, items }) => (
          <div key={group} className="grid gap-0.5">
            <p className="px-2.5 pb-1 text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase max-lg:sr-only">
              {group}
            </p>
            {items.map((item) => (
              <SidebarLink key={item.path} item={item} />
            ))}
          </div>
        ))}
      </div>
      <div className="grid gap-0.5 border-t pt-3">
        {FOOTER_NAVIGATION.map((item) => (
          <SidebarLink key={item.path} item={item} />
        ))}
      </div>
    </nav>
  )
}

export function PrivacyPill() {
  return (
    <span className="inline-flex h-7 items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 text-xs font-medium text-emerald-600 dark:text-emerald-400">
      <LockKeyhole className="size-3.5" aria-hidden="true" />
      100% local y privado
    </span>
  )
}

function Shell({ data, setView }: { data: ReadyData; setView: (view: ViewState) => void }) {
  const [projectId, setProjectId] = useState(storedProject)
  const project =
    data.projects.find((candidate) => candidate.id === projectId) ?? data.projects[0] ?? null
  const sessionLost = useCallback((error: unknown) => setView(errorState(error)), [setView])

  const choose = (id: string) => {
    setProjectId(id)
    storeProject(id)
  }
  const endSession = () => {
    logout()
      .then(() => setView({ kind: "signed_out" }))
      .catch((error: unknown) => setView(errorState(error)))
  }
  const needsProject = (screen: (project: Project) => ReactNode) =>
    project ? (
      screen(project)
    ) : (
      <EmptyState
        icon={FolderGit2}
        title="Primero, un proyecto"
        action={
          <Button render={<NavLink to="/fuentes" />} nativeButton={false}>
            Ir a Fuentes
          </Button>
        }
      >
        Crea un proyecto y añádele su repositorio o su carpeta de documentos.
      </EmptyState>
    )
  const props = (current: Project) => ({ project: current, onSessionLost: sessionLost })

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b px-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
              <ScrollText className="size-4" aria-hidden="true" />
            </span>
            <span className="font-heading text-base font-semibold tracking-tight max-sm:sr-only">
              Scrinium
            </span>
          </span>
          {data.projects.length > 0 && (
            <Select
              aria-label="Proyecto"
              value={project?.id ?? ""}
              options={data.projects.map((candidate) => ({ value: candidate.id, label: candidate.name }))}
              onValueChange={choose}
              className="max-w-56"
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          <PrivacyPill />
          <Button variant="ghost" size="icon-sm" aria-label="Cerrar sesión" title="Cerrar sesión" onClick={endSession}>
            <LogOut />
          </Button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto grid max-w-5xl gap-6 px-6 py-6 max-sm:px-4">
            <Routes>
              <Route
                path="/"
                element={
                  <HomeScreen
                    key={project?.id ?? "none"}
                    project={project}
                    status={data.status}
                    onSessionLost={sessionLost}
                  />
                }
              />
              <Route path="/preguntar" element={needsProject((c) => <AskScreen key={c.id} {...props(c)} />)} />
              <Route
                path="/reglas/:entityId?"
                element={needsProject((c) => <RulesScreen key={c.id} {...props(c)} />)}
              />
              <Route path="/mapa/:entityId?" element={needsProject((c) => <MapScreen key={c.id} {...props(c)} />)} />
              <Route
                path="/arquitectura"
                element={needsProject((c) => <ArchitectureScreen key={c.id} {...props(c)} />)}
              />
              <Route
                path="/impacto/:analysisId?"
                element={needsProject((c) => <ImpactScreen key={c.id} {...props(c)} />)}
              />
              <Route
                path="/versiones/:analysisId?"
                element={needsProject((c) => <VersionsScreen key={c.id} {...props(c)} />)}
              />
              <Route
                path="/fuentes"
                element={
                  <SourcesScreen
                    project={project}
                    workspace={data.workspace}
                    projects={data.projects}
                    onProjectCreated={(created) => {
                      setView({ kind: "ready", ...data, projects: [...data.projects, created] })
                      choose(created.id)
                    }}
                    onSessionLost={sessionLost}
                  />
                }
              />
              <Route
                path="/ajustes"
                element={
                  <SettingsScreen
                    project={project}
                    status={data.status}
                    dataFlow={data.dataFlow}
                    profileName={data.profileName}
                    onProfileSaved={(profileName) => setView({ kind: "ready", ...data, profileName })}
                    onProjectDeleted={(deleted) =>
                      setView({
                        kind: "ready",
                        ...data,
                        projects: data.projects.filter((candidate) => candidate.id !== deleted),
                      })
                    }
                    onWiped={() => {
                      forgetProject()
                      // Full reload: nothing the screen had in memory remains.
                      window.location.assign("/fuentes")
                    }}
                    onEndSession={endSession}
                    onSessionLost={sessionLost}
                  />
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  const [view, setView] = useState<ViewState>({ kind: "loading" })

  useEffect(() => {
    Promise.all([getSecurityStatus(), getDataFlow(), getWorkspace(), getProjects(), getProfile()])
      .then(([status, dataFlow, workspace, projects, profile]) =>
        setView({
          kind: "ready",
          status,
          dataFlow,
          workspace,
          projects,
          profileName: profile.display_name,
        }),
      )
      .catch((error: unknown) => setView(errorState(error)))
  }, [])

  if (view.kind === "ready") {
    return (
      <BrowserRouter>
        <Shell data={view} setView={setView} />
      </BrowserRouter>
    )
  }
  return (
    <main className="mx-auto grid max-w-xl gap-6 px-4 py-16">
      <span className="flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
          <ScrollText className="size-4" aria-hidden="true" />
        </span>
        <span className="font-heading text-xl font-semibold tracking-tight">Scrinium</span>
      </span>
      {view.kind === "loading" && <p className="text-sm text-muted-foreground">Cargando…</p>}
      {view.kind === "signed_out" && (
        <Notice title="Sesión cerrada">
          Para volver a entrar, reinicia Scrinium con <code>scrinium start</code>.
        </Notice>
      )}
      {view.kind === "no_session" && (
        <Notice title="Se necesita una sesión">
          Abre el enlace de un solo uso que aparece en la terminal. Si ya no sirve, reinicia Scrinium
          con <code>scrinium start</code>.
        </Notice>
      )}
      {view.kind === "error" && (
        <Notice title="No se pudo leer el estado">
          Comprueba que Scrinium sigue en marcha en la terminal y recarga la página.
        </Notice>
      )}
    </main>
  )
}
