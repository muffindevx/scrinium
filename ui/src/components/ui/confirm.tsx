import type { LucideIcon } from "lucide-react"
import { type FormEvent, type ReactNode, useState } from "react"
import { createPortal } from "react-dom"
import { createCallable } from "react-call"
import { cn } from "cn"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { Label, Textarea } from "@/components/ui/input"
import { t } from "@/lib/i18n"

/**
 * A dialog that is called, not placed: `const { confirmed, value } = await Confirm.call({...})`
 * from any handler, with no state or markup where it is used (react-call). `<DialogHost />`,
 * mounted once at the root, renders it in a portal on `document.body`.
 *
 * Everything is a parameter: title, text, free content, the buttons' labels, the tone (a
 * destructive question shows its button in red), an icon, and an optional text field whose
 * value comes back with the answer. With `cancelLabel: null` it is a notice with one button.
 */
export type ConfirmTone = "default" | "danger" | "success"

export type ConfirmProps = {
  title: ReactNode
  description?: ReactNode
  /** Anything else to show between the text and the buttons. */
  content?: ReactNode
  confirmLabel?: string
  /** null: a single button, as a notice. */
  cancelLabel?: string | null
  tone?: ConfirmTone
  icon?: LucideIcon
  /** A text field; what was typed comes back as `value`. */
  input?: { label: string; placeholder?: string; required?: boolean; initial?: string; maxLength?: number }
}

export type ConfirmResult = { confirmed: boolean; value: string }

const TONES: Record<ConfirmTone, { button: "default" | "destructive"; icon: string }> = {
  default: { button: "default", icon: "bg-muted text-foreground" },
  danger: { button: "destructive", icon: "bg-destructive/15 text-destructive" },
  success: { button: "default", icon: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
}

// Time the dialog stays mounted after it is answered, so it can close without a jump.
const UNMOUNT_MS = 150

export const Confirm = createCallable<ConfirmProps, ConfirmResult>(
  ({ call, title, description, content, confirmLabel, cancelLabel, tone = "default", icon: Icon, input }) => {
    const [value, setValue] = useState(input?.initial ?? "")
    const style = TONES[tone]
    const answer = (confirmed: boolean) => call.end({ confirmed, value: value.trim() })
    const submit = (event: FormEvent) => {
      event.preventDefault()
      if (input?.required && !value.trim()) return
      answer(true)
    }
    return (
      <Dialog
        open={!call.ended}
        onOpenChange={(open) => {
          if (!open && !call.ended) answer(false)
        }}
        title={
          <span className="flex items-center gap-2">
            {Icon && (
              <span className={cn("grid size-7 shrink-0 place-items-center rounded-full", style.icon)}>
                <Icon className="size-4" aria-hidden="true" />
              </span>
            )}
            {title}
          </span>
        }
        description={description}
      >
        <form className="grid gap-4" onSubmit={submit}>
          {content}
          {input && (
            <div className="grid gap-1.5">
              <Label htmlFor="confirm-input">{input.label}</Label>
              <Textarea
                id="confirm-input"
                rows={3}
                autoFocus
                maxLength={input.maxLength ?? 2000}
                placeholder={input.placeholder}
                value={value}
                onChange={(event) => setValue(event.target.value)}
              />
            </div>
          )}
          <div className="flex justify-end gap-2">
            {cancelLabel !== null && (
              <Button type="button" variant="ghost" onClick={() => answer(false)}>
                {cancelLabel ?? t("Cancelar")}
              </Button>
            )}
            <Button
              type="submit"
              variant={style.button}
              autoFocus={!input}
              disabled={Boolean(input?.required) && !value.trim()}
            >
              {confirmLabel ?? t("Aceptar")}
            </Button>
          </div>
        </form>
      </Dialog>
    )
  },
  UNMOUNT_MS,
)

/** Mounted once, at the root: where every called dialog appears, in a portal on the body. */
export function DialogHost() {
  return createPortal(<Confirm />, document.body)
}
