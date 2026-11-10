import { AlertCircle, AlertTriangle, CheckCircle2, HelpCircle, Loader2, XCircle } from "lucide-react"
import type { ReactNode } from "react"
import { cn } from "cn"
import type { AnalysisStatus, EmbeddingStatus, VerificationStatus } from "@/lib/api"
import type { Tone } from "@/lib/status"


const TONES: Record<Tone, { className: string; icon: typeof CheckCircle2 }> = {
  verified: {
    className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
    icon: CheckCircle2,
  },
  proposed: {
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
    icon: AlertCircle,
  },
  discarded: {
    className: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
    icon: XCircle,
  },
  no_evidence: {
    className: "bg-neutral-500/10 text-neutral-600 dark:text-neutral-400 border-neutral-500/20",
    icon: HelpCircle,
  },
  in_progress: {
    className: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
    icon: Loader2,
  },
  attention: {
    className: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
    icon: AlertTriangle,
  },
}

export function StatusBadge({
  tone,
  children,
  className,
}: {
  tone: Tone
  children: ReactNode
  className?: string
}) {
  const Icon = TONES[tone].icon
  return (
    <span
      className={cn(
        "inline-flex h-5 w-fit shrink-0 items-center gap-1 rounded-full border px-2 text-xs font-medium whitespace-nowrap",
        TONES[tone].className,
        className,
      )}
    >
      <Icon className={cn("size-3", tone === "in_progress" && "animate-spin")} aria-hidden="true" />
      {children}
    </span>
  )
}

const VERIFICATION: Record<VerificationStatus, [Tone, string]> = {
  unverified: ["proposed", "Propuesta sin verificar"],
  source_supported: ["verified", "Verificada con el código"],
  test_supported: ["verified", "Verificada con un test"],
  human_verified: ["verified", "Verificada"],
  conflicted: ["attention", "En conflicto"],
  rejected: ["discarded", "Descartada (falso positivo)"],
}

export function VerificationStatusBadge({ status }: { status: VerificationStatus }) {
  const [tone, label] = VERIFICATION[status]
  return <StatusBadge tone={tone}>{label}</StatusBadge>
}

const ANALYSIS: Record<AnalysisStatus, [Tone, string]> = {
  queued: ["in_progress", "En cola…"],
  running: ["in_progress", "Analizando…"],
  completed: ["verified", "Terminado"],
  partial: ["attention", "Terminado en parte"],
  failed: ["attention", "Falló"],
  cancelled: ["no_evidence", "En pausa"],
}

export function AnalysisStatusBadge({ status }: { status: AnalysisStatus }) {
  const [tone, label] = ANALYSIS[status]
  return <StatusBadge tone={tone}>{label}</StatusBadge>
}

const MODEL: Record<EmbeddingStatus, [Tone, string]> = {
  ready: ["verified", "Listo"],
  model_missing: ["attention", "Sin modelo local"],
  unavailable: ["attention", "El motor no responde"],
}

export function ModelStatusBadge({ status }: { status: EmbeddingStatus }) {
  const [tone, label] = MODEL[status]
  return <StatusBadge tone={tone}>{label}</StatusBadge>
}
