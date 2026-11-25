import { cn } from "cn"

/**
 * The Scrinium mark (docs/brand): a scroll whose rods are code braces, with the rule it read in
 * plum. The braces and lines take the text color; only the rule takes the accent, so it follows
 * light and dark. Below 24 px the small form keeps a single, thicker rule line.
 */
export function ScriniumMark({ className, small = false }: { className?: string; small?: boolean }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true" className={cn("shrink-0", className)}>
      <path
        d="M21 10c-6 0-7 3-7 9v8c0 3-2 5-5 5 3 0 5 2 5 5v8c0 6 1 9 7 9M43 10c6 0 7 3 7 9v8c0 3 2 5 5 5-3 0-5 2-5 5v8c0 6-1 9-7 9"
        fill="none"
        stroke="currentColor"
        strokeWidth="5.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {[22, 42].map((x) =>
        [10, 54].map((y) => <circle key={`${x}-${y}`} cx={x} cy={y} r="4.2" fill="currentColor" />),
      )}
      {small ? (
        <path d="M25 32h14" className="stroke-primary" strokeWidth="6.5" strokeLinecap="round" />
      ) : (
        <>
          <path d="M26 23h12M26 41h8" stroke="currentColor" strokeWidth="5" strokeLinecap="round" />
          <path d="M26 32h12" className="stroke-primary" strokeWidth="5" strokeLinecap="round" />
        </>
      )}
    </svg>
  )
}
