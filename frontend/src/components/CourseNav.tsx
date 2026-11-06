import { Link, NavLink } from 'react-router-dom'
import type { Risk } from '../api'

export function CourseHeader({ courseId, name, extra }: { courseId: string; name: string; extra?: React.ReactNode }) {
  const base = `/courses/${encodeURIComponent(courseId)}`
  return (
    <>
      <p className="crumbs"><Link to="/">Courses</Link> / {name}</p>
      <div className="page-head">
        <div>
          <h1>{name}</h1>
          {extra}
        </div>
      </div>
      <nav className="tabs" aria-label="Course views">
        <NavLink end to={base}>Roster</NavLink>
        <NavLink to={`${base}/analytics`}>Analytics</NavLink>
        <NavLink to={`${base}/director`}>Course Director</NavLink>
        <NavLink to={`${base}/data`}>Data &amp; Models</NavLink>
      </nav>
    </>
  )
}

export const RISK_COLOR: Record<Risk, string> = { Critical: '#DC2626', High: '#F97316', Medium: '#FBBF24', Low: '#10B981' }

export function RiskPill({ risk }: { risk: Risk | null | undefined }) {
  if (!risk) return <span className="muted small">–</span>
  return <span className="risk" style={{ ['--risk' as string]: RISK_COLOR[risk] }}>{risk}</span>
}
