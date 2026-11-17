import { Workflow } from "lucide-react"
import { useEffect, useState } from "react"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { RuleLink } from "@/components/rule-link"
import { type Process, getProcesses } from "@/lib/api"
import { t } from "@/lib/i18n"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted, type ScreenProps } from "@/screens/shared"

/**
 * Processes (phase 8.3): from each entry point (an action, a route, a scheduled job), the
 * functions with rules a run goes through, in call order. Found in the code, without AI.
 */
export function ProcessesScreen({ project, onSessionLost }: ScreenProps) {
  const [found, setFound] = useState<Process[] | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  useEffect(() => {
    getProcesses(project.id).then(setFound).catch(fail)
  }, [project.id, fail])

  return (
    <>
      <PageHeader
        title={t("Procesos")}
        description={t(
          "Desde cada punto de entrada (una acción, una ruta, una tarea programada), las funciones con reglas por las que pasa, en el orden en que se llaman. Sale del código, sin IA.",
        )}
      />
      <ErrorText>{error}</ErrorText>
      {found === null ? (
        <Muted>{t("Cargando…")}</Muted>
      ) : found.length === 0 ? (
        <EmptyState icon={Workflow} title={t("Todavía no hay procesos")}>
          {t("Aparecen cuando un punto de entrada llama a funciones que tienen reglas.")}
        </EmptyState>
      ) : (
        found.map((process) => (
          <Section
            key={`${process.path}:${process.start_line}`}
            title={process.name}
            description={t("{0} reglas · empieza en {1}:{2}", process.rules, process.path, process.start_line)}
          >
            <ol className="grid gap-1.5 text-sm">
              {process.steps.map((step) => (
                <li
                  key={`${step.path}:${step.start_line}`}
                  className="grid min-w-0 gap-0.5 border-l-2 pl-3"
                  style={{ marginLeft: `${Math.min(step.depth, 5) * 1}rem` }}
                >
                  <span className="font-mono text-xs break-all text-muted-foreground">
                    {step.function} · {step.path}:{step.start_line}
                  </span>
                  {step.rules.map((rule) => (
                    <RuleLink key={rule.id} id={rule.id} className="hover:underline">
                      {rule.title}
                      {rule.verified && <span className="ml-1 text-xs text-emerald-600">✓</span>}
                    </RuleLink>
                  ))}
                </li>
              ))}
            </ol>
          </Section>
        ))
      )}
    </>
  )
}
