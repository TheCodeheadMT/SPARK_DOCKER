import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, type RosterRow } from '../api'
import { CourseHeader } from '../components/CourseNav'
import FigureCard from '../components/FigureCard'
import { IngestNotes, timeAgo } from '../components/IngestStatus'

type SortKey = 'STUDENT_NAME' | 'SECTION_NAME' | 'CURRENT_SCORE' | 'late_rate' | 'missed_rate' | 'success_prob'

const pct = (v: number | null | undefined, digits = 0) => (v == null ? '–' : `${(v * 100).toFixed(digits)}%`)

export default function CoursePage() {
  const { courseId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const section = params.get('section') ?? ''
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: 'CURRENT_SCORE', asc: true })
  const prefs = useQuery({ queryKey: ['prefs'], queryFn: api.prefs })
  const course = useQuery({ queryKey: ['course', courseId], queryFn: () => api.course(courseId) })
  const roster = useQuery({
    queryKey: ['students', courseId, section],
    queryFn: () => api.students(courseId, section || undefined),
    enabled: !!course.data?.has_data,
  })
  const cutoff = prefs.data?.cutoff_percentage ?? 60
  const cdata = useQuery({ queryKey: ['course-data', courseId], queryFn: () => api.courseData(courseId) })
  const hasModel = !!roster.data?.model

  const rows = useMemo(() => {
    const list = [...(roster.data?.students ?? [])]
    list.sort((a: RosterRow, b: RosterRow) => {
      const av = a[sort.key] as string | number | null | undefined
      const bv = b[sort.key] as string | number | null | undefined
      const aMissing = av == null || (typeof av === 'number' && Number.isNaN(av))
      const bMissing = bv == null || (typeof bv === 'number' && Number.isNaN(bv))
      if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1   // blanks last, both ways
      const cmp = typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: 'base' })
      if (cmp !== 0) return sort.asc ? cmp : -cmp
      return String(a.STUDENT_SORTABLE_NAME ?? a.STUDENT_NAME).localeCompare(String(b.STUDENT_SORTABLE_NAME ?? b.STUDENT_NAME))
    })
    return list
  }, [roster.data, sort])

  if (course.isLoading) return <p className="muted">Loading…</p>
  if (course.error) return <p className="error">{(course.error as Error).message}</p>
  const c = course.data!
  const below = rows.filter((r) => r.CURRENT_SCORE != null && r.CURRENT_SCORE < cutoff).length

  const th = (key: SortKey, label: string, cls = '') => (
    <th className={cls}>
      <button className="sort" onClick={() => setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))}>
        {label}{sort.key === key ? (sort.asc ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  )

  return (
    <section>
      <CourseHeader courseId={courseId} name={c.name}
        extra={<p className="muted small">Data updated {timeAgo(c.last_success?.finished_at)} · role {c.role}</p>} />
      <IngestNotes run={c.last_success} />

      {!c.has_data ? (
        <p className="muted">No data yet. Run an ingest from the course list.</p>
      ) : (
        <>
          <div className="chips" role="tablist" aria-label="Sections">
            <button className={`chip ${!section ? 'on' : ''}`} onClick={() => setParams({})}>All sections</button>
            {c.sections.map((s) => (
              <button key={s.SECTION_ID} className={`chip ${section === s.SECTION_ID ? 'on' : ''}`}
                      onClick={() => setParams({ section: s.SECTION_ID })}>
                {s.SECTION_NAME} <span className="muted">· {s.STUDENTS} · avg {s.AVG_SCORE?.toFixed(1) ?? '–'}%</span>
              </button>
            ))}
          </div>

          <div className="summary-row">
            <div className="stat"><span>{rows.length}</span><small>students</small></div>
            <div className={`stat ${below ? 'warn' : ''}`}><span>{below}</span><small>below {cutoff}% cutoff</small></div>
            <div className="stat"><span>{rows.filter((r) => (r.missed_rate ?? 0) > 0).length}</span><small>with a missed item</small></div>
          </div>

          {roster.isLoading ? <p className="muted">Loading roster…</p> : (
            <div className="table-wrap">
              <table className="roster">
                <thead>
                  <tr>
                    {th('STUDENT_NAME', 'Student')}
                    {th('SECTION_NAME', 'Section')}
                    {th('CURRENT_SCORE', 'Current grade', 'num')}
                    {th('late_rate', 'Late (timed items)', 'num')}
                    {th('missed_rate', 'Missed', 'num')}
                    {hasModel && th('success_prob', 'Success prob.', 'num')}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const flag = r.CURRENT_SCORE != null && r.CURRENT_SCORE < cutoff
                    return (
                      <tr key={r.STUDENT_ID} className={flag ? 'flag' : ''}>
                        <td>
                          <Link to={`/courses/${encodeURIComponent(courseId)}/students/${encodeURIComponent(r.STUDENT_ID)}`}>
                            {r.STUDENT_NAME}
                          </Link>
                          <span className="muted small"> {r.STUDENT_ID}</span>
                        </td>
                        <td>{r.SECTION_NAME}</td>
                        <td className="num">
                          <span className="bar"><i style={{ width: `${Math.min(100, r.CURRENT_SCORE ?? 0)}%` }} /></span>
                          {r.CURRENT_SCORE?.toFixed(1) ?? '–'}% <span className="muted">{r.CURRENT_GRADE ?? ''}</span>
                        </td>
                        <td className="num">{pct(r.late_rate)} <span className="muted small">of {r.n_timed_items}</span></td>
                        <td className="num">{pct(r.missed_rate)}</td>
                        {hasModel && <td className={`num ${r.success_prob != null && r.success_prob < 0.5 ? 'warn-text' : ''}`}>{pct(r.success_prob)}</td>}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {cdata.data?.zybooks.exists && (
            <details className="notes zy-matrix">
              <summary>zyBooks class matrix{section ? ' (this section)' : ''}: lowest lab average at the top; large red circles mean high effort, low score</summary>
              <FigureCard url={`/api/courses/${encodeURIComponent(courseId)}/zybooks/matrix${section ? `?section_id=${encodeURIComponent(section)}` : ''}`} />
            </details>
          )}
        </>
      )}
    </section>
  )
}
