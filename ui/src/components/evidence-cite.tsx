import { t } from "@/lib/i18n"
import { Placeholder } from "@/components/ui/skeleton"
import { Check, ChevronDown, Copy, FileCode2 } from "lucide-react"
import { type ReactNode, useState } from "react"
import { cn } from "cn"
import { Button } from "@/components/ui/button"
import { getCitedLines } from "@/lib/api"
import { shortSha } from "@/lib/labels"

type Lines = { start: number; lines: string[] } | "error" | null

/**
 * Citation to a file and lines, the same in answers, rules, analyses and the map.
 * "Ver líneas" reads them from the indexed revision; the text is always shown as plain text.
 */
export function EvidenceCite({
  path,
  startLine,
  endLine,
  commit,
  revisionId,
  excerpt,
  label,
  extra,
  defaultOpen = false,
  className,
}: {
  path: string
  startLine?: number
  endLine?: number
  commit?: string | null
  revisionId?: string | null
  excerpt?: string | null
  label?: ReactNode
  extra?: ReactNode
  defaultOpen?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  const [lines, setLines] = useState<Lines>(
    excerpt && !revisionId ? { start: startLine ?? 1, lines: excerpt.split("\n") } : null,
  )
  const [copied, setCopied] = useState(false)
  const range = startLine ? `L${startLine}${endLine && endLine !== startLine ? `–L${endLine}` : ""}` : ""
  const reference = `${path}${startLine ? `:${startLine}${endLine && endLine !== startLine ? `-${endLine}` : ""}` : ""}`
  const canOpen = Boolean(revisionId && startLine && endLine) || Boolean(excerpt)

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (next && lines === null && revisionId && startLine && endLine) {
      getCitedLines(revisionId, startLine, endLine)
        .then((loaded) => setLines({ start: loaded.start, lines: loaded.lines }))
        .catch(() => setLines("error"))
    }
  }
  const copy = () => {
    navigator.clipboard
      ?.writeText(reference)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => setCopied(false))
  }

  return (
    <div className={cn("overflow-hidden rounded-lg border bg-card text-sm", className)}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2">
        <FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        {label && <span className="text-xs font-medium text-muted-foreground">{label}</span>}
        <code className="min-w-0 flex-1 truncate text-xs" title={reference}>
          {path}
          {range && <span className="text-muted-foreground"> : {range}</span>}
          {commit && <span className="text-muted-foreground"> @ {shortSha(commit)}</span>}
        </code>
        {extra}
        <span className="flex gap-1">
          {canOpen && (
            <Button size="xs" variant="ghost" aria-expanded={open} onClick={toggle}>
              <ChevronDown data-icon="inline-start" className={cn("transition-transform", open && "rotate-180")} />
              {open ? t("Ocultar") : t("Abrir")}
            </Button>
          )}
          <Button size="xs" variant="ghost" onClick={copy} aria-label={t("Copiar {0}", reference)}>
            {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
            {copied ? t("Copiado") : t("Copiar")}
          </Button>
        </span>
      </div>
      {open && (
        <div className="max-h-64 overflow-auto border-t bg-muted/50 py-2 font-mono text-xs">
          {lines === null ? (
            <div className="px-3"><Placeholder shape="text" rows={4} label={t("Cargando")} /></div>
          ) : lines === "error" ? (
            <p className="px-3 text-muted-foreground">{t("Este archivo ya no está en las fuentes: puede que se haya borrado.")}</p>
          ) : (
            <table className="w-full border-collapse">
              <tbody>
                {lines.lines.map((line, index) => (
                  <tr key={index}>
                    <td className="w-10 pr-3 pl-3 text-right align-top text-muted-foreground select-none">
                      {lines.start + index}
                    </td>
                    <td className="pr-3 whitespace-pre-wrap break-all">{line || " "}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
