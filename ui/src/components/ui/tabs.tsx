import { Tabs as TabsPrimitive } from "@base-ui/react/tabs"
import type * as React from "react"
import { cn } from "cn"

function Tabs({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return <TabsPrimitive.Root className={cn("grid gap-3", className)} {...props} />
}

function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn(
        "inline-flex w-fit flex-wrap items-center gap-1 rounded-lg bg-muted p-1 text-muted-foreground",
        className,
      )}
      {...props}
    />
  )
}

function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Tab>) {
  return (
    <TabsPrimitive.Tab
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium whitespace-nowrap outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 data-[active]:bg-background data-[active]:text-foreground data-[active]:shadow-sm [&_svg]:size-4",
        className,
      )}
      {...props}
    />
  )
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Panel>) {
  return <TabsPrimitive.Panel className={cn("outline-none", className)} {...props} />
}

export { Tabs, TabsContent, TabsList, TabsTrigger }
