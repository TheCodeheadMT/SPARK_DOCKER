import type { IngestRun } from '../api'

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export function StatusPill({ run }: { run: IngestRun | null }) {
  if (!run) return <span className="pill muted">No data yet</span>
  const cls = { succeeded: 'ok', skipped: 'ok', running: 'warn', failed: 'bad' }[run.status]
  const label = run.status === 'skipped' ? 'up to date' : run.status
  return <span className={`pill ${cls}`}>{label}</span>
}

/** Validation notes from the last ingest, written for instructors rather than engineers. */
export function IngestNotes({ run }: { run: IngestRun | null }) {
  if (!run) return null
  const r = run.report || {}
  const notes: string[] = []
  if (r.synthetic) notes.push('Synthetic demo data (no real students).')
  if (r.current_grade_source === 'computed_points')
    notes.push('Current grade is calculated by SPARK from points earned over points possible on graded items (no ' +
      'percentage total column was found in the gradebook). It will differ from Blackboard if the course weights categories.')
  else if (r.current_grade_source === 'percentage_column' && r.current_grade_column)
    notes.push(`Current grade is taken from the gradebook's "${r.current_grade_column}" column.`)
  if (r.excluded_no_section)
    notes.push(`${r.excluded_no_section} ${r.excluded_no_section === 1 ? 'person' : 'people'} with no section ` +
      '(enrolled only in the parent course, e.g. instructors, TAs or preview users) left out.')
  if (r.due_dates_inferred != null)
    notes.push(`${r.due_dates_inferred} due date(s) inferred from automatic zeros` +
      (r.due_dates_manifest ? `, ${r.due_dates_manifest} from the manifest` : '') + '.')
  if (r.due_dates_missing?.length) notes.push(`No due date yet for: ${r.due_dates_missing.join(', ')}.`)
  if (r.with_submit_time != null && r.graded_cells != null)
    notes.push(`${r.with_submit_time} of ${r.graded_cells} graded items have a submission time; lateness uses only those.`)
  if (r.needs_grading_excluded) notes.push(`${r.needs_grading_excluded} item(s) awaiting grading were left out, not counted as zero.`)
  if (r.items_not_yet_active?.length) notes.push(`${r.items_not_yet_active.length} future item(s) skipped until scored.`)
  if (r.history_users_not_in_gradebook) notes.push(`${r.history_users_not_in_gradebook} user(s) in Grade History are not on the current roster.`)
  if (r.history_loaded === false) notes.push('No Grade History export found: submission times and due dates unavailable.')
  if (!notes.length) return null
  return (
    <details className="notes">
      <summary>Data notes from last ingest{r.files?.gradebook ? ` (${r.files.gradebook})` : ''}</summary>
      <ul>{notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </details>
  )
}
