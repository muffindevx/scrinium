import { CircleSlash, GitCommit, Wrench } from "lucide-react"
import { RuleLink } from "@/components/rule-link"
import { type Triage } from "@/lib/api"
import { t } from "@/lib/i18n"
import { Muted } from "@/screens/shared"

/** What a stack trace leads to (phase 7.3): per frame, its function, rules and history. */
export function TraceResult({ result }: { result: Triage }) {
  return (
    <div className="grid gap-3">
      <Muted>
        {t("{0} marcos, {1} del proyecto, {2} resueltos a su función.", result.parsed, result.in_project, result.resolved)}
      </Muted>
      <ol className="grid gap-2">
        {result.frames.map((frame, index) => (
          <li key={`${index}${frame.raw}`} className="grid min-w-0 gap-1 rounded-md border p-3 text-sm">
            <code className="text-xs break-all text-muted-foreground">{frame.raw}</code>
            {!frame.path ? (
              <Muted>
                <CircleSlash className="mr-1 inline size-3.5" aria-hidden="true" />
                {t("No es un archivo del proyecto.")}
              </Muted>
            ) : (
              <>
                <p className="font-medium break-all">
                  {frame.path}:{frame.line}
                  {frame.function && ` · ${frame.function.name}`}
                </p>
                {frame.rules.length > 0 ? (
                  <ul className="grid gap-0.5">
                    {frame.rules.map((rule) => (
                      <li key={rule.id}>
                        <RuleLink id={rule.id} className="hover:underline">
                          {rule.title}
                        </RuleLink>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Muted>{t("Ninguna regla conocida en esta función.")}</Muted>
                )}
                {frame.recent.length > 0 && (
                  <div className="grid gap-0.5 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1 font-medium">
                      <GitCommit className="size-3.5" aria-hidden="true" />
                      {t("Cambios recientes del archivo")}
                    </span>
                    {frame.recent.map((commit) => (
                      <span key={commit.sha}>
                        {commit.date.slice(0, 10)} · {commit.sha.slice(0, 8)} · {commit.subject}
                      </span>
                    ))}
                  </div>
                )}
                {frame.fixes.length > 0 && (
                  <div className="grid gap-0.5 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1 font-medium">
                      <Wrench className="size-3.5" aria-hidden="true" />
                      {t("Arreglos anteriores en el archivo")}
                    </span>
                    {frame.fixes.map((commit) => (
                      <span key={commit.sha}>
                        {commit.date.slice(0, 10)} · {commit.sha.slice(0, 8)} · {commit.subject}
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
