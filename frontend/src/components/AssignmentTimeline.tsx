import type { AssignmentRow } from '../api'

// One row's timeline: assigned (left edge) -> due (right marker), with the student's first attempt and
// final submission placed on it. Blackboard's exports have no release or start times, so "assigned" may be
// an estimate (the class's first submission) and "first attempt" is the first submitted attempt.

const DAY = 86_400_000
const fmt = (t: number) => new Date(t).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const ts = (iso?: string | null) => (iso ? new Date(iso).getTime() : null)

function relative(ms: number): string {
  const days = Math.abs(ms) / DAY
  const amount = days >= 1 ? `${days.toFixed(1)} days` : `${Math.max(1, Math.round(days * 24))} h`
  return ms <= 0 ? `${amount} before due` : `${amount} after due`
}

export default function AssignmentTimeline({ a }: { a: AssignmentRow }) {
  const assigned = ts(a.ASSIGNED_AT)
  const due = ts(a.DUE_AT)
  const submitted = ts(a.SUBMITTED_AT)
  const attempts = a.ATTEMPTS ?? null
  const firstRaw = ts(a.FIRST_ATTEMPT_AT)
  const first = firstRaw != null && submitted != null && firstRaw < submitted ? firstRaw : null

  const points = [assigned, first, submitted, due].filter((t): t is number => t != null)
  if (!points.length || (due == null && submitted == null))
    return <span className="muted small">{a.MISSED_ASSIGNMENT ? 'missed' : 'no dates'}</span>

  let lo = Math.min(...points)
  const hi = Math.max(...points)
  if (hi - lo < 60_000) lo = hi - DAY            // a single point: give it a one-day window
  const pos = (t: number) => 3 + (94 * (t - lo)) / (hi - lo)

  const late = submitted != null && due != null && submitted > due
  const windowStart = assigned ?? lo
  const windowEnd = due ?? hi

  const lines: string[] = []
  if (assigned != null)
    lines.push(`Assigned: ${fmt(assigned)}${a.ASSIGNED_SOURCE === 'manifest' ? '' : ' (estimated: first submission in the class)'}`)
  else lines.push('Assigned: not known')
  if (first != null) lines.push(`First attempt submitted: ${fmt(first)}${due != null ? ` (${relative(first - due)})` : ''}`)
  if (submitted != null)
    lines.push(`Submitted${attempts && attempts > 1 ? ` (attempt ${attempts})` : ''}: ${fmt(submitted)}` +
      (due != null ? ` (${relative(submitted - due)})` : ''))
  else lines.push(a.MISSED_ASSIGNMENT ? 'Not submitted' : 'No submission time (graded by the instructor)')
  lines.push(due != null ? `Due: ${fmt(due)}${a.DUE_SOURCE === 'inferred_auto_zero' ? ' (inferred)' : ''}` : 'Due: not known')

  return (
    <div className={`timeline${a.MISSED_ASSIGNMENT ? ' missed' : ''}`} role="img" aria-label={lines.join('; ')}
      title={lines.join('\n')}>
      <span className={`tl-window${assigned == null ? ' unknown-start' : ''}`}
        style={{ left: `${pos(windowStart)}%`, width: `${Math.max(0, pos(windowEnd) - pos(windowStart))}%` }} />
      {late && due != null && submitted != null && (
        <span className="tl-overdue" style={{ left: `${pos(due)}%`, width: `${pos(submitted) - pos(due)}%` }} />
      )}
      {assigned != null && <span className="tl-tick tl-assigned" style={{ left: `${pos(assigned)}%` }} />}
      {due != null && <span className="tl-tick tl-due" style={{ left: `${pos(due)}%` }} />}
      {first != null && <span className="tl-dot tl-first" style={{ left: `${pos(first)}%` }} />}
      {submitted != null && (
        <span className={`tl-dot tl-submitted ${late ? 'late' : 'on-time'}`} style={{ left: `${pos(submitted)}%` }} />
      )}
    </div>
  )
}
