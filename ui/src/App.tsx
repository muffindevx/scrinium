import { DialogHost } from "@/components/ui/confirm"
import { Placeholder } from "@/components/ui/skeleton"
import { adoptLanguage, t } from "@/lib/i18n"
import {
  Blocks,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Cloud,
  FileCode2,
  FolderGit2,
  GitCompare,
  Grid2x2,
  House,
  Inbox,
  LockKeyhole,
  type LucideIcon,
  Moon,
  ScanSearch,
  Search,
  Settings,
  ShieldOff,
  Sparkles,
  Sun,
  TriangleAlert,
  Workflow,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react"
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router"
import { cn } from "cn"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Select } from "@/components/ui/select"
import { EmptyState } from "@/components/page"
import {
  type AiSetup,
  type DataFlow,
  type Project,
  type ProjectAi,
  type SecurityStatus,
  SessionRequiredError,
  type Workspace,
  getAiSetup,
  getDataFlow,
  getHealth,
  getLanguage,
  getProfile,
  getProjectAi,
  getProjects,
  getSecurityStatus,
  getWorkspace,
  logout,
} from "@/lib/api"
import { useFolderDrop } from "@/lib/folder-drop"
import { setTheme } from "@/lib/theme"
import { ActivityScreen } from "@/screens/ActivityScreen"
import { ChangesScreen } from "@/screens/ChangesScreen"
import { ProcessesScreen } from "@/screens/ProcessesScreen"
import { CoverageScreen } from "@/screens/CoverageScreen"
import { CommandPalette } from "@/screens/CommandPalette"
import { HomeScreen } from "@/screens/HomeScreen"
import { ImpactScreen } from "@/screens/ImpactScreen"
import { InboxScreen } from "@/screens/InboxScreen"
import { ProjectAiScreen } from "@/screens/ProjectAiScreen"
import { RulesScreen } from "@/screens/RulesScreen"
import { SettingsScreen } from "@/screens/SettingsScreen"
import { IntegrationsScreen } from "@/screens/IntegrationsScreen"
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
const NAVIGATION: { group: string; items: NavItem[] }[] = [
  {
    group: t("Proyecto"),
    items: [
      { path: "/", label: t("Inicio"), icon: House },
      { path: "/bandeja", label: t("Revisar"), icon: Inbox },
      { path: "/reglas", label: t("Reglas"), icon: BookOpen },
    ],
  },
  {
    group: t("Cambios"),
    items: [
      { path: "/cambios", label: t("Cambios"), icon: GitCompare },
      { path: "/procesos", label: t("Procesos"), icon: Workflow },
      { path: "/cobertura", label: t("Cobertura"), icon: Grid2x2 },
      { path: "/impacto", label: t("Triaje"), icon: ScanSearch },
    ],
  },
]
const FOOTER_NAVIGATION: NavItem[] = [
  { path: "/integraciones", label: t("Integraciones"), icon: Blocks },
  { path: "/ia", label: t("IA del proyecto"), icon: Sparkles },
  { path: "/ajustes", label: t("Ajustes"), icon: Settings },
]

/** `/fuentes` was the old name of `/integraciones`: old links (and the Linear return) still work. */
function OldSourcesPath() {
  const location = useLocation()
  return <Navigate to={{ pathname: "/integraciones", search: location.search }} replace />
}

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

const COLLAPSED_KEY = "scrinium.sidebarCollapsed"

function storedCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1"
  } catch {
    return false
  }
}

function SidebarLink({ item, collapsed, count = 0 }: { item: NavItem; collapsed: boolean; count?: number }) {
  const Icon = item.icon
  const shown = count > 999 ? "999+" : String(count)
  return (
    <NavLink
      to={item.path}
      end={item.path === "/"}
      title={count > 0 ? `${item.label} · ${count}` : item.label}
      className={({ isActive }) =>
        cn(
          "relative flex h-10 items-center gap-3 rounded-lg px-3 text-sm transition-colors",
          collapsed && "justify-center px-0",
          isActive
            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
            : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground",
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon className={cn("size-[1.1rem] shrink-0", isActive && "text-primary")} aria-hidden="true" />
          <span className={cn("flex-1", collapsed && "sr-only")}>{item.label}</span>
          {count > 0 && (
            <>
              <span className={cn("text-xs text-muted-foreground tabular-nums", collapsed && "sr-only")}>
                {shown}
              </span>
              <span
                aria-hidden="true"
                className={cn("absolute top-2 right-2 size-1.5 rounded-full bg-primary", !collapsed && "hidden")}
              />
            </>
          )}
        </>
      )}
    </NavLink>
  )
}

const NARROW = "(max-width: 1023px)"

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches)
  useEffect(() => {
    const query = window.matchMedia(NARROW)
    const follow = () => setNarrow(query.matches)
    query.addEventListener("change", follow)
    return () => query.removeEventListener("change", follow)
  }, [])
  return narrow
}

/**
 * Full labels or icons only. On a wide window the choice is kept in this browser and the page
 * makes room; on a narrow one the menu starts as icons and opens over the page, closing again
 * when a section is chosen, so the content never gets squeezed.
 */
function Sidebar({ inbox }: { inbox: number }) {
  const narrow = useNarrow()
  const [stored, setStored] = useState(storedCollapsed)
  // Open only on the screen where it was opened: choosing a section closes it.
  const location = useLocation()
  const [openOn, setOpenOn] = useState<string | null>(null)
  const open = narrow && openOn === location.pathname
  const setOpen = (value: boolean) => setOpenOn(value ? location.pathname : null)
  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => event.key === "Escape" && setOpenOn(null)
    window.addEventListener("keydown", close)
    return () => window.removeEventListener("keydown", close)
  }, [open])
  const collapsed = narrow ? !open : stored
  const toggle = () => {
    if (narrow) {
      setOpen(!open)
      return
    }
    setStored(!stored)
    try {
      window.localStorage.setItem(COLLAPSED_KEY, stored ? "0" : "1")
    } catch {
      // Without storage the choice lasts as long as the window.
    }
  }
  const overlay = narrow && open
  return (
    <div className={cn("relative shrink-0 transition-[width]", narrow || stored ? "w-16" : "w-52")}>
      {overlay && (
        <button
          type="button"
          aria-label={t("Cerrar menú")}
          className="fixed inset-0 top-16 z-30 bg-black/20"
          onClick={() => setOpen(false)}
        />
      )}
    <nav
      aria-label={t("Secciones")}
      className={cn(
        "flex h-full flex-col justify-between bg-sidebar text-sidebar-foreground",
        collapsed ? "w-16 p-2.5" : "w-52 p-3",
        overlay && "absolute inset-y-0 left-0 z-40 shadow-xl",
      )}
    >
      <div className="grid gap-5">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          title={collapsed ? t("Expandir menú") : t("Contraer menú")}
          className={cn(
            "flex h-9 items-center gap-2 rounded-lg text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
            collapsed ? "justify-center" : "px-3",
          )}
        >
          {collapsed ? (
            <ChevronRight className="size-4" aria-hidden="true" />
          ) : (
            <ChevronLeft className="size-4" aria-hidden="true" />
          )}
          <span className={cn(collapsed && "sr-only")}>{collapsed ? t("Expandir menú") : t("Contraer menú")}</span>
        </button>
        {NAVIGATION.map(({ group, items }) => (
          <div key={group} role="group" aria-label={group} className="grid gap-1">
            {items.map((item) => (
              <SidebarLink
                key={item.path}
                item={item}
                collapsed={collapsed}
                count={item.path === "/bandeja" ? inbox : 0}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="grid gap-1">
        {FOOTER_NAVIGATION.map((item) => (
          <SidebarLink key={item.path} item={item} collapsed={collapsed} />
        ))}
      </div>
    </nav>
    </div>
  )
}

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"))
  const label = dark ? t("Tema claro") : t("Tema oscuro")
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      onClick={() => {
        setTheme(dark ? "light" : "dark")
        setDark(!dark)
      }}
    >
      {dark ? <Sun /> : <Moon />}
    </Button>
  )
}

/** Says what really leaves: local unless the current project consented to a remote model. */
export function PrivacyPill({ ai, isolated = false }: { ai: ProjectAi | null; isolated?: boolean }) {
  if (isolated) {
    return (
      <NavLink
        to="/ajustes/modelos"
        title={t("Modo aislado: no se puede registrar ni usar ningún proveedor externo.")}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs font-medium whitespace-nowrap text-primary hover:bg-accent"
      >
        <ShieldOff className="size-3.5" aria-hidden="true" />
        <span className="max-sm:sr-only">{t("Modo aislado")}</span>
      </NavLink>
    )
  }
  // What leaves by default: the tasks whose default model is external. A model only allowed
  // leaves when someone chooses it for a run, and that screen says so.
  const defaults = Object.values(ai?.task_models ?? {})
  const remote = (ai?.consents ?? []).filter((consent) =>
    defaults.some((chosen) => chosen.provider_id === consent.provider_id && chosen.model === consent.model),
  )
  if (remote.length === 0) {
    return (
      <span className="inline-flex h-8 items-center gap-1.5 px-2 text-xs font-medium whitespace-nowrap text-primary">
        <LockKeyhole className="size-3.5" aria-hidden="true" />
        <span className="max-sm:sr-only">{t("Local y privado")}</span>
      </span>
    )
  }
  const names = [...new Set(remote.map((consent) => consent.provider_name))].join(", ")
  return (
    <NavLink
      to="/ia"
      title={t("Este proyecto envía tareas a {0}. Ver o cambiar en IA del proyecto.", names)}
      className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs font-medium whitespace-nowrap text-amber-700 hover:bg-amber-500/10 dark:text-amber-400"
    >
      <Cloud className="size-3.5" aria-hidden="true" />
      <span className="max-sm:sr-only">{t("IA externa:")} {names}</span>
    </NavLink>
  )
}

function Shell({ data, setView }: { data: ReadyData; setView: (view: ViewState) => void }) {
  const [projectId, setProjectId] = useState(storedProject)
  const project =
    data.projects.find((candidate) => candidate.id === projectId) ?? data.projects[0] ?? null
  const sessionLost = useCallback((error: unknown) => setView(errorState(error)), [setView])
  // The current project's AI mode, for the privacy pill; only ever shown for that project.
  const [loadedAi, setLoadedAi] = useState<{ projectId: string; ai: ProjectAi } | null>(null)
  const projectKey = project?.id ?? null
  const projectAi = loadedAi?.projectId === projectKey ? loadedAi.ai : null
  const loadAi = useCallback(() => {
    if (!projectKey) return
    getProjectAi(projectKey)
      .then((ai) => setLoadedAi({ projectId: projectKey, ai }))
      .catch(() => setLoadedAi(null))
  }, [projectKey])
  useEffect(loadAi, [loadAi])
  // Where the writing model lives (ADR-013); Home asks until someone chooses.
  const [setup, setSetup] = useState<AiSetup | null>(null)
  useEffect(() => {
    getAiSetup()
      .then(setSetup)
      .catch(() => setSetup(null))
  }, [])
  const setupChosen = (chosen: AiSetup) => {
    setSetup(chosen)
    loadAi()
  }
  // The inbox count beside "Revisar": read again on every screen change, so a review shows.
  const location = useLocation()
  const [inbox, setInbox] = useState<{ projectId: string; count: number } | null>(null)
  useEffect(() => {
    if (!projectKey) return
    getHealth(projectKey)
      .then((health) => setInbox({ projectId: projectKey, count: health.inbox }))
      .catch(() => setInbox(null))
  }, [projectKey, location.pathname])
  const [drop, clearDrop] = useFolderDrop()
  const navigate = useNavigate()
  // A folder dropped anywhere opens the Home screen with its path ready to read, once per drop:
  // `navigate` changes on every move, and the drop stays until it is used.
  const shownDrop = useRef<number | null>(null)
  useEffect(() => {
    if (!drop || shownDrop.current === drop.at) return
    shownDrop.current = drop.at
    navigate("/")
  }, [drop, navigate])

  const choose = (id: string) => {
    setProjectId(id)
    storeProject(id)
  }
  const addProject = (created: Project) => {
    setView({ kind: "ready", ...data, projects: [...data.projects, created] })
    choose(created.id)
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
        title={t("Primero, un proyecto")}
        action={
          <Button render={<NavLink to="/" />} nativeButton={false}>
            {t("Empezar")}
          </Button>
        }
      >
        {t("Empieza en el Inicio: basta con la ruta de tu repositorio o de su carpeta de documentos.")}
      </EmptyState>
    )
  const props = (current: Project) => ({ project: current, onSessionLost: sessionLost })

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-16 shrink-0 items-center justify-between gap-3 bg-sidebar px-5 max-sm:px-3">
        <div className="flex min-w-0 items-center gap-4">
          <span className="flex items-center gap-2.5">
            <span className="grid size-7 place-items-center rounded-lg bg-foreground text-background">
              <FileCode2 className="size-4" aria-hidden="true" />
            </span>
            <span className="font-heading text-[1.05rem] font-semibold tracking-tight lowercase max-sm:sr-only">
              {t("Scrinium")}
            </span>
            <span className="text-[0.62rem] font-semibold tracking-[0.14em] text-muted-foreground uppercase max-md:hidden">
              {data.workspace.name}
            </span>
          </span>
          {data.projects.length > 0 && (
            <Select
              aria-label={t("Proyecto")}
              value={project?.id ?? ""}
              options={data.projects.map((candidate) => ({ value: candidate.id, label: candidate.name }))}
              onValueChange={choose}
              className="h-8 max-w-56 border-transparent bg-muted dark:bg-muted"
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="flex h-8 w-56 items-center gap-2 rounded-lg bg-muted px-2.5 text-xs text-muted-foreground transition-colors hover:text-foreground max-md:w-auto max-sm:hidden"
            title={t("Buscar (Ctrl+K)")}
            onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }))}
          >
            <Search className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="flex-1 text-left max-md:sr-only">{t("Buscar en el proyecto")}</span>
            <kbd className="text-[0.65rem] tracking-wider max-md:hidden">⌘K</kbd>
          </button>
          <PrivacyPill ai={projectAi} isolated={setup?.isolated ?? data.status.profile === "isolated"} />
          <ThemeToggle />
          <NavLink
            to="/ajustes"
            title={data.profileName ? t("{0} · Ajustes y sesión", data.profileName) : t("Ajustes y sesión")}
            aria-label={t("Ajustes y sesión")}
            className="grid size-8 place-items-center rounded-full bg-[oklch(0.86_0.03_60)] text-xs font-semibold text-[oklch(0.3_0.03_60)] dark:bg-[oklch(0.82_0.03_60)]"
          >
            {(data.profileName?.trim()[0] ?? "·").toUpperCase()}
          </NavLink>
        </div>
      </header>
      <CommandPalette projectId={project?.id ?? null} />
      <div className="flex min-h-0 flex-1">
        <Sidebar inbox={inbox?.projectId === projectKey ? inbox.count : 0} />
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto grid max-w-[66rem] gap-6 px-10 py-10 *:min-w-0 max-md:px-6 max-sm:px-4 max-sm:py-6">
            <Routes>
              <Route
                path="/"
                element={
                  <HomeScreen
                    key={project?.id ?? "none"}
                    project={project}
                    status={data.status}
                    localOnly={(projectAi?.consents.length ?? 0) === 0}
                    setup={setup}
                    onSetupChosen={setupChosen}
                    profileName={data.profileName}
                    onProjectCreated={addProject}
                    drop={drop}
                    onDropUsed={clearDrop}
                    onSessionLost={sessionLost}
                  />
                }
              />
              <Route path="/procesos" element={needsProject((c) => <ProcessesScreen key={c.id} {...props(c)} />)} />
              <Route path="/cobertura" element={needsProject((c) => <CoverageScreen key={c.id} {...props(c)} />)} />
              <Route path="/cambios" element={needsProject((c) => <ChangesScreen key={c.id} {...props(c)} />)} />
              <Route path="/bandeja/:entityId?" element={needsProject((c) => <InboxScreen key={c.id} {...props(c)} />)} />
              <Route
                path="/reglas/:entityId?"
                element={needsProject((c) => <RulesScreen key={c.id} {...props(c)} />)}
              />
              <Route
                path="/impacto/:analysisId?"
                element={needsProject((c) => <ImpactScreen key={c.id} {...props(c)} />)}
              />
              <Route
                path="/versiones/:analysisId?"
                element={needsProject((c) => <VersionsScreen key={c.id} {...props(c)} />)}
              />
              <Route path="/actividad" element={needsProject((c) => <ActivityScreen key={c.id} {...props(c)} />)} />
              <Route path="/fuentes" element={<OldSourcesPath />} />
              <Route
                path="/integraciones"
                element={
                  <IntegrationsScreen
                    project={project}
                    workspace={data.workspace}
                    projects={data.projects}
                    onProjectCreated={addProject}
                    onSessionLost={sessionLost}
                  />
                }
              />
              <Route
                path="/ia/:tab?"
                element={needsProject((c) => (
                  <ProjectAiScreen key={c.id} project={c} onAiChanged={loadAi} onSessionLost={sessionLost} />
                ))}
              />
              <Route
                path="/ajustes/:tab?"
                element={
                  <SettingsScreen
                    project={project}
                    status={data.status}
                    dataFlow={data.dataFlow}
                    profileName={data.profileName}
                    setup={setup}
                    onSetupChosen={setupChosen}
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
                      window.location.assign("/integraciones")
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
    Promise.all([getSecurityStatus(), getDataFlow(), getWorkspace(), getProjects(), getProfile(), getLanguage()])
      .then(([status, dataFlow, workspace, projects, profile, chosen]) => {
        // The workspace's language wins over this browser's copy: reload once if they differ.
        if (adoptLanguage(chosen.language)) {
          window.location.reload()
          return
        }
        setView({
          kind: "ready",
          status,
          dataFlow,
          workspace,
          projects,
          profileName: profile.display_name,
        })
      })
      .catch((error: unknown) => setView(errorState(error)))
  }, [])

  if (view.kind === "ready") {
    return (
      <BrowserRouter>
        <Shell data={view} setView={setView} />
        {/* Dialogs opened with `await Confirm.call(...)` from anywhere appear here. */}
        <DialogHost />
      </BrowserRouter>
    )
  }
  return (
    <main className="mx-auto grid max-w-xl gap-6 px-4 py-16">
      <span className="flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-lg bg-foreground text-background">
          <FileCode2 className="size-4" aria-hidden="true" />
        </span>
        <span className="font-heading text-xl font-semibold tracking-tight lowercase">{t("Scrinium")}</span>
      </span>
      {view.kind === "loading" && <Placeholder shape="text" rows={3} label={t("Cargando")} />}
      {view.kind === "signed_out" && (
        <Notice title={t("Sesión cerrada")}>
          {t("Para volver a entrar, reinicia Scrinium con")} <code>scrinium start</code>.
        </Notice>
      )}
      {view.kind === "no_session" && (
        <Notice title={t("Se necesita una sesión")}>
          {t("Abre el enlace de un solo uso que aparece en la terminal. Si ya no sirve, reinicia Scrinium con")} <code>scrinium start</code>.
        </Notice>
      )}
      {view.kind === "error" && (
        <Notice title={t("No se pudo leer el estado")}>
          {t("Comprueba que Scrinium sigue en marcha en la terminal y recarga la página.")}
        </Notice>
      )}
    </main>
  )
}
