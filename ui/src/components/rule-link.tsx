import type { ComponentProps } from "react"
import { Link, useLocation } from "react-router"

type RuleLinkProps = Omit<ComponentProps<typeof Link>, "to" | "state"> & { id: string }

/** A link to a rule that remembers the page it was opened from, so "back" returns there. */
export function RuleLink({ id, ...props }: RuleLinkProps) {
  const location = useLocation()
  return <Link to={`/reglas/${id}`} state={{ from: location.pathname + location.search }} {...props} />
}
