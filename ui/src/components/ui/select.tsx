import { Select as SelectPrimitive } from "@base-ui/react/select"
import { Check, ChevronsUpDown } from "lucide-react"
import { cn } from "cn"

export interface SelectOption {
  value: string
  label: string
}

/** shadcn Select on top of Base UI, with text options. Replaces the native `<select>`. */
function Select({
  id,
  value,
  options,
  onValueChange,
  placeholder = "Elige",
  className,
  "aria-label": ariaLabel,
}: {
  id?: string
  value: string
  options: SelectOption[]
  onValueChange: (value: string) => void
  placeholder?: string
  className?: string
  "aria-label"?: string
}) {
  const items = Object.fromEntries(options.map((option) => [option.value, option.label]))
  return (
    <SelectPrimitive.Root
      items={items}
      value={value}
      onValueChange={(next) => onValueChange(String(next ?? ""))}
    >
      <SelectPrimitive.Trigger
        id={id}
        aria-label={ariaLabel}
        className={cn(
          "flex h-8 min-w-36 items-center justify-between gap-2 rounded-lg border border-input bg-background px-2.5 text-sm outline-none select-none hover:bg-muted focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 data-[popup-open]:bg-muted dark:bg-input/30",
          className,
        )}
      >
        <SelectPrimitive.Value className="truncate data-[placeholder]:text-muted-foreground">
          {(current: string | null) => (current ? items[current] : placeholder)}
        </SelectPrimitive.Value>
        <SelectPrimitive.Icon>
          <ChevronsUpDown className="size-3.5 text-muted-foreground" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Positioner sideOffset={4} alignItemWithTrigger={false} className="z-50">
          <SelectPrimitive.Popup className="max-h-80 min-w-(--anchor-width) overflow-y-auto rounded-lg border bg-popover p-1 text-sm text-popover-foreground shadow-md">
            {options.map((option) => (
              <SelectPrimitive.Item
                key={option.value}
                value={option.value}
                className="flex cursor-default items-center justify-between gap-3 rounded-md px-2 py-1.5 outline-none select-none data-[highlighted]:bg-muted"
              >
                <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
                <SelectPrimitive.ItemIndicator>
                  <Check className="size-3.5" />
                </SelectPrimitive.ItemIndicator>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Popup>
        </SelectPrimitive.Positioner>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  )
}

export { Select }
