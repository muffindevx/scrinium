import { useCallback, useEffect, useState } from "react"

// Sent by Scrinium's desktop window (cli/window.py) with the full path of a dropped folder.
const DROP_EVENT = "scrinium:folder-dropped"

/** What the last drop on the window gave: a path (desktop window) or nothing usable (browser). */
export type Drop = { kind: "path"; path: string; at: number } | { kind: "no_path"; at: number }

declare global {
  interface Window {
    pywebview?: unknown
  }
}

/**
 * Listens for folders dropped anywhere on the window. A browser never says where a dropped file
 * lives, so only Scrinium's own window gives a path; in a browser the drop says so instead.
 */
export function useFolderDrop(): [Drop | null, () => void] {
  const [drop, setDrop] = useState<Drop | null>(null)
  const clear = useCallback(() => setDrop(null), [])
  useEffect(() => {
    const onPath = (event: Event) => {
      const path = (event as CustomEvent<unknown>).detail
      if (typeof path === "string" && path) setDrop({ kind: "path", path, at: Date.now() })
    }
    // Without this the browser would open the dropped file in place of Scrinium.
    const allow = (event: DragEvent) => {
      if (event.dataTransfer?.types.includes("Files")) event.preventDefault()
    }
    const onDrop = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes("Files")) return
      event.preventDefault()
      // In the desktop window the path arrives a moment later through DROP_EVENT.
      if (!window.pywebview) setDrop({ kind: "no_path", at: Date.now() })
    }
    window.addEventListener(DROP_EVENT, onPath)
    window.addEventListener("dragover", allow)
    window.addEventListener("drop", onDrop)
    return () => {
      window.removeEventListener(DROP_EVENT, onPath)
      window.removeEventListener("dragover", allow)
      window.removeEventListener("drop", onDrop)
    }
  }, [])
  return [drop, clear]
}
