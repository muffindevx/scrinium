import { Keyboard } from "lucide-react"
import { Confirm } from "@/components/ui/confirm"
import { t } from "@/lib/i18n"

export const SHORTCUTS: [string, string][] = [
  ["J / K", t("Siguiente / anterior")],
  ["Enter", t("Abrir la regla")],
  ["X", t("Seleccionar")],
  ["C", t("Es correcta")],
  ["R", t("No es correcta")],
  ["U", t("Deshacer, o volver a la lista")],
  ["?", t("Ver estos atajos")],
  ["Ctrl+K / ⌘K", t("Buscar o ejecutar un comando, en cualquier pantalla")],
]

/** The keys, in a dialog opened with "?" or the keyboard button, as Gmail does. */
export function showShortcuts() {
  return Confirm.call({
    icon: Keyboard,
    title: t("Atajos de teclado"),
    description: t("En Revisar, una tecla sola, sin Ctrl ni Cmd, como en Gmail: así no chocan con los atajos del navegador. No funcionan mientras escribes en un campo."),
    content: (
      <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
        {SHORTCUTS.map(([keys, what]) => (
          <div key={keys} className="contents">
            <dt>
              <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-xs">{keys}</kbd>
            </dt>
            <dd className="text-muted-foreground">{what}</dd>
          </div>
        ))}
      </dl>
    ),
    confirmLabel: t("Entendido"),
    cancelLabel: null,
  })
}

