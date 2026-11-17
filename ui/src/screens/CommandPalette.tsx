import { BookOpen, FileCode2, Search, SquareFunction, Tag } from "lucide-react"
import { type KeyboardEvent, useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router"
import { cn } from "cn"
import { Dialog } from "@/components/ui/dialog"
import { type Palette, getPalette } from "@/lib/api"
import { t } from "@/lib/i18n"

type Entry = { key: string; group: string; icon: typeof Search; label: string; hint: string; to: string }

function entries(found: Palette): Entry[] {
  const rules = (path: string) => `/reglas?q=${encodeURIComponent(path)}`
  return [
    ...found.rules.map((rule) => ({
      key: `r${rule.id}`, group: t("Reglas"), icon: BookOpen, label: rule.title, hint: "",
      to: `/reglas/${rule.id}`,
    })),
    ...found.functions.map((fn) => ({
      key: `f${fn.path}:${fn.line}:${fn.name}`, group: t("Funciones"), icon: SquareFunction,
      label: fn.qualified_name, hint: `${fn.path}:${fn.line}`, to: rules(fn.path),
    })),
    ...found.files.map((file) => ({
      key: `a${file.path}`, group: t("Archivos"), icon: FileCode2, label: file.path, hint: "",
      to: rules(file.path),
    })),
    ...found.tickets.map((ticket) => ({
      key: `t${ticket.ticket}${ticket.rule_id}`, group: t("Tickets"), icon: Tag, label: ticket.ticket,
      hint: ticket.rule, to: `/reglas/${ticket.rule_id}`,
    })),
  ]
}

/**
 * ⌘K / Ctrl+K (phase 6.2): rules, functions, files and tickets by name, as you type. Enter opens
 * a rule, or the rules of a file. The same names `scrinium mcp` gives an agent.
 */
export function CommandPalette({ projectId }: { projectId: string | null }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [found, setFound] = useState<Palette | null>(null)
  const [selected, setSelected] = useState(0)
  const navigate = useNavigate()

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        setOpen((current) => !current)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  useEffect(() => {
    if (!open || !projectId || query.trim().length < 2) return
    let current = true
    const timer = window.setTimeout(() => {
      getPalette(projectId, query)
        .then((result) => {
          if (current) {
            setFound(result)
            setSelected(0)
          }
        })
        .catch(() => current && setFound(null))
    }, 80)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [open, projectId, query])

  const list = useMemo(() => (found && query.trim().length >= 2 ? entries(found) : []), [found, query])

  const go = (entry: Entry | undefined) => {
    if (!entry) return
    setOpen(false)
    setQuery("")
    setFound(null)
    navigate(entry.to)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") setSelected((index) => Math.min(index + 1, list.length - 1))
    else if (event.key === "ArrowUp") setSelected((index) => Math.max(index - 1, 0))
    else if (event.key === "Enter") go(list[selected])
    else return
    event.preventDefault()
  }

  return (
    <Dialog open={open} onOpenChange={setOpen} title={t("Buscar")} className="w-[min(40rem,calc(100vw-2rem))]">
      <div className="flex items-center gap-2 rounded-md border px-3">
        <Search className="size-4 text-muted-foreground" aria-hidden="true" />
        <input
          autoFocus
          aria-label={t("Buscar reglas, funciones, archivos o tickets")}
          placeholder={t("Reglas, funciones, archivos o tickets…")}
          className="h-10 min-w-0 flex-1 bg-transparent text-sm outline-none"
          value={query}
          maxLength={200}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
      {!projectId ? (
        <p className="text-sm text-muted-foreground">{t("Primero, un proyecto")}</p>
      ) : query.trim().length >= 2 && found && list.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("Nada con ese nombre.")}</p>
      ) : (
        <ul className="grid max-h-[60vh] gap-0.5 overflow-y-auto" role="listbox" aria-label={t("Resultados")}>
          {list.map((entry, index) => {
            const Icon = entry.icon
            const first = index === 0 || list[index - 1].group !== entry.group
            return (
              <li key={entry.key} role="option" aria-selected={index === selected}>
                {first && <p className="px-2 pt-2 pb-1 text-xs font-medium text-muted-foreground">{entry.group}</p>}
                <button
                  type="button"
                  className={cn(
                    "flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                    index === selected && "bg-muted",
                  )}
                  onMouseEnter={() => setSelected(index)}
                  onClick={() => go(entry)}
                >
                  <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="min-w-0 truncate">{entry.label}</span>
                  {entry.hint && (
                    <span className="ml-auto min-w-0 shrink truncate text-xs text-muted-foreground">{entry.hint}</span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </Dialog>
  )
}
