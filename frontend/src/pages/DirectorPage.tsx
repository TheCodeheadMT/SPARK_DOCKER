import { useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, enc } from '../api'
import { CourseHeader, RiskPill } from '../components/CourseNav'
import FigureCard from '../components/FigureCard'
import { timeAgo } from '../components/IngestStatus'

const pct = (v: number | null | undefined, d = 0) => (v == null ? '–' : `${(v * 100).toFixed(d)}%`)
const num = (v: number | null | undefined, d = 1) => (v == null ? '–' : v.toFixed(d))

const WEIGHT_LABELS: Record<string, string> = {
  grade: 'grade / 100', coverage: 'coverage (has graded work)', on_time: '1 - late rate',
  not_missed: '1 - missed rate', recency: '1 - days since last submission / 30',
}

export default function DirectorPage() {
  const { courseId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const metric = params.get('metric') ?? 'HEALTH_INDEX'
  const sectionId = params.get('section') ?? ''
  const prefs = useQuery({ queryKey: ['prefs'], queryFn: api.prefs })
  const cutoff = prefs.data?.cutoff_percentage ?? 60

  const course = useQuery({ queryKey: ['course', courseId], queryFn: () => api.course(courseId) })
  const d = useQuery({ queryKey: ['director', courseId, metric, cutoff], queryFn: () => api.director(courseId, metric, cutoff),
    enabled: !!course.data?.has_data && prefs.isSuccess })
  const sec = useQuery({ queryKey: ['director-section', courseId, sectionId], queryFn: () => api.directorSection(courseId, sectionId),
    enabled: !!sectionId })

  const set = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p) }
  const base = `/api/courses/${enc(courseId)}/director/figures`
  const name = course.data?.name ?? courseId

  if (course.isLoading) return <p className="muted">Loading…</p>
  if (course.error) return <p className="error">{(course.error as Error).message}</p>
  const header = <CourseHeader courseId={courseId} name={name}
    extra={<p className="muted small">Section comparison from the last ingest ({timeAgo(course.data?.last_success?.finished_at)}). Cutoff {cutoff}% (set on any student page).</p>} />
  if (!course.data?.has_data) return <section>{header}<p className="muted">No data yet. Run an ingest from the course list.</p></section>
  if (d.isLoading || !d.data) return <section>{header}<p className="muted">Computing section metrics…</p></section>
  const dir = d.data
  const m = dir.metrics[metric]

  return (
    <section>
      {header}
      <div className="kpis">
        <div className="stat"><span>{dir.kpis.sections}</span><small>sections</small></div>
        <div className="stat"><span>{dir.kpis.students}</span><small>unique students</small></div>
        <div className="stat"><span>{dir.kpis.top_section ?? '–'}</span><small>top section (health)</small></div>
        <div className="stat"><span>{dir.kpis.lowest_section ?? '–'}</span><small>lowest section (health)</small></div>
        <div className={`stat ${dir.kpis.below_cutoff ? 'warn' : ''}`}><span>{dir.kpis.below_cutoff}</span><small>students below {cutoff}%</small></div>
      </div>

      <div className="toolbar">
        <label className="small">Rank sections by{' '}
          <select value={metric} onChange={(e) => set('metric', e.target.value)}>
            {Object.entries(dir.metrics).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </label>
        <span className="small muted">{m.higher_is_better ? 'Higher is better' : 'Lower is better'}</span>
      </div>

      <div className="fig-grid">
        <FigureCard url={`${base}/rank_bar?metric=${metric}&cutoff=${cutoff}`} title={`Ranked: ${m.label}`} />
        <FigureCard url={`${base}/grade_box?cutoff=${cutoff}&show_cutoff=${prefs.data?.show_cutoff ?? true}`} title="Grade distribution by section" />
      </div>

      <div className="table-wrap">
        <table>
          <thead><tr><th>Rank</th><th>Section</th><th className="num">Students</th><th className="num">Health</th>
            <th className="num">Mean grade</th><th className="num">Median</th><th className="num">Below cutoff</th>
            <th className="num">Late</th><th className="num">Missed</th><th /></tr></thead>
          <tbody>
            {dir.sections.map((s) => (
              <tr key={s.SECTION_ID} className={s.SECTION_ID === sectionId ? 'selected' : ''}>
                <td>{s.RANK}</td>
                <td><strong>{s.SECTION_NAME}</strong></td>
                <td className="num">{s.N_STUDENTS}</td>
                <td className="num">{num(s.HEALTH_INDEX, 3)}</td>
                <td className="num">{num(s.MEAN_GRADE_PCT)}%</td>
                <td className="num">{num(s.MEDIAN_GRADE_PCT)}%</td>
                <td className="num">{num(s.AT_RISK_PCT, 0)}%</td>
                <td className="num">{pct(s.LATE_RATE, 1)}</td>
                <td className="num">{pct(s.MISSED_RATE, 1)}</td>
                <td><button className="linkish" onClick={() => set('section', s.SECTION_ID)}>roster</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <details className="notes">
        <summary>How the Health Index is calculated</summary>
        <ul>
          {Object.entries(dir.weights).map(([k, w]) => <li key={k}>{w.toFixed(2)} × {WEIGHT_LABELS[k] ?? k}</li>)}
        </ul>
        <p className="muted small">Grade uses Blackboard's current grade column when present. Late rate counts only items
          with a submission time. Days since last submission is measured to the time of the export, not today.</p>
      </details>

      {sectionId && (
        <div className="card">
          <div className="card-head">
            <h3>Section roster: {sec.data?.section_name ?? ''} {sec.data && <span className="muted">· least healthy first</span>}</h3>
            <button className="btn ghost" onClick={() => set('section', '')}>Close</button>
          </div>
          {sec.isLoading ? <p className="muted">Loading…</p> : sec.error ? <p className="error">{(sec.error as Error).message}</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Student</th><th className="num">Health</th><th className="num">Grade</th><th>Risk</th>
                  <th className="num">Avg score</th><th className="num">Late (timed)</th><th className="num">Missed</th><th>Status</th></tr></thead>
                <tbody>
                  {sec.data!.students.map((s) => (
                    <tr key={s.STUDENT_ID} className={s.GRADE_PCT != null && s.GRADE_PCT < cutoff ? 'flag' : ''}>
                      <td><Link to={`/courses/${enc(courseId)}/students/${enc(s.STUDENT_ID)}`}>{s.STUDENT_NAME}</Link></td>
                      <td className="num">{num(s.HEALTH_INDEX, 3)}</td>
                      <td className="num">{num(s.GRADE_PCT)}% <span className="muted">{s.CURRENT_GRADE ?? ''}</span></td>
                      <td><RiskPill risk={s.cluster_label} /></td>
                      <td className="num">{pct(s.avg_percentage, 1)}</td>
                      <td className="num">{pct(s.late_rate)} <span className="muted small">of {s.n_timed_items}</span></td>
                      <td className="num">{pct(s.missed_rate)}</td>
                      <td>{s.ENROLLMENT_STATE}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
