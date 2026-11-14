import { useCallback, useEffect, useMemo, useState } from "react"
import { Select } from "@/components/ui/select"
import { LOCAL, choiceOf, currentChoice, permission, splitChoice } from "@/lib/ai"
import {
  type AiTask,
  type ModelChoice,
  type ModelProvider,
  type Project,
  type ProjectAi,
  getProjectAi,
  listModelProviders,
} from "@/lib/api"
import { ConsentDialog } from "@/screens/ConsentDialog"
import { useErrors } from "@/screens/hooks"
import { ErrorText } from "@/screens/shared"

/** The model for one run of a task: its default, the local model, or another allowed one.
 * Choosing a model not yet allowed for the task asks for consent first, without changing the
 * default. Hidden while the project has no external model to choose. */
export function ModelPicker({
  project,
  task,
  value,
  onChange,
  onSessionLost,
}: {
  project: Project
  task: AiTask
  value: ModelChoice
  onChange: (choice: ModelChoice) => void
  onSessionLost: (error: unknown) => void
}) {
  const [ai, setAi] = useState<ProjectAi | null>(null)
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const [asking, setAsking] = useState<{ provider: ModelProvider; model: string } | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    getProjectAi(project.id).then(setAi).catch(fail)
    listModelProviders()
      .then((loaded) => setProviders(loaded.providers))
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])
  const options = useMemo(
    () => [
      { value: LOCAL, label: "Local (este equipo)" },
      ...providers.flatMap((provider) =>
        provider.models.map((option) => ({
          value: choiceOf(provider.id, option.model),
          label: `${provider.name} · ${option.model}`,
        })),
      ),
    ],
    [providers],
  )
  if (ai === null || options.length < 2) return null
  const fallback = currentChoice(ai, task)
  const shown = value ?? fallback
  const pick = (choice: string) => {
    if (choice === LOCAL) {
      onChange(fallback === LOCAL ? undefined : LOCAL)
      return
    }
    const [providerId, model] = splitChoice(choice)
    if (permission(ai, task, providerId, model)) {
      onChange(choice === fallback ? undefined : choice)
      return
    }
    const provider = providers.find((candidate) => candidate.id === providerId)
    if (provider) setAsking({ provider, model })
  }
  const host =
    shown === LOCAL ? null : providers.find((provider) => provider.id === splitChoice(shown)[0])?.host
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">Modelo</span>
      <Select
        aria-label="Modelo para esta ejecución"
        value={shown}
        options={options}
        onValueChange={pick}
        className="max-w-72"
      />
      {shown === fallback ? (
        <span className="text-xs text-muted-foreground">por defecto</span>
      ) : (
        <button
          type="button"
          className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          onClick={() => onChange(undefined)}
        >
          volver al de por defecto
        </button>
      )}
      {host && <span className="text-xs text-amber-700 dark:text-amber-400">Sale a {host}</span>}
      <ErrorText>{error}</ErrorText>
      {asking && (
        <ConsentDialog
          project={project}
          provider={asking.provider}
          model={asking.model}
          tasks={[task]}
          makeDefault={false}
          onClose={() => setAsking(null)}
          onGiven={() => {
            const choice = choiceOf(asking.provider.id, asking.model)
            setAsking(null)
            load()
            onChange(choice)
          }}
          onSessionLost={onSessionLost}
        />
      )}
    </div>
  )
}
