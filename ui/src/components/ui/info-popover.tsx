import { Popover as PopoverPrimitive } from "@base-ui/react/popover"
import { Info } from "lucide-react"
import type { ReactNode } from "react"
import { cn } from "cn"

/** An (i) button that opens a short explanation: for text that would crowd the form. It opens
 * on click and on hover, so it also works by keyboard and on touch screens. */
function InfoPopover({
  label,
  title,
  children,
  className,
}: {
  label: string
  title?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger
        openOnHover
        delay={150}
        aria-label={label}
        className={cn(
          "inline-grid size-5 shrink-0 place-items-center rounded-full text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
          className,
        )}
      >
        <Info className="size-4" aria-hidden="true" />
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner sideOffset={6} collisionPadding={16} className="z-50">
          <PopoverPrimitive.Popup className="grid max-w-[min(26rem,calc(100vw-2rem))] gap-2 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-md">
            {title && <PopoverPrimitive.Title className="font-medium">{title}</PopoverPrimitive.Title>}
            <PopoverPrimitive.Description render={<div />} className="grid gap-2">
              {children}
            </PopoverPrimitive.Description>
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}

export { InfoPopover }
