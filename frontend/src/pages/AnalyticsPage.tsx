import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, enc, type Risk } from '../api'
import { CourseHeader, RISK_COLOR, RiskPill } from '../components/CourseNav'
import FigureCard from '../components/FigureCard'
import { timeAgo } from '../components/IngestStatus'

const RISKS: Risk[] = ['Critical', 'High', 'Medium', 'Low']
const pct = (v: number | null | undefined, d = 0) => (v == null ? '–' : `${(v * 100).toFixed(d)}%`)

const INSIGHTS: [string, string][] = [
  ['grade_violin', 'Grade by risk cluster'], ['avg_violin', 'Average score by risk cluster'],
  ['missed_late', 'Missed vs late'], ['perf_consistency', 'Performance vs consistency'],
  ['days_since', 'Days since last submission'], ['pace', 'Pace'],
]

export default function AnalyticsPage() {
  const { courseId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const selected = params.get('student') ?? ''
  const [view3d, setView3d] = useState(true)
  const [admMode, setAdmMode] = useState<'numeric' | 'yesno' | 'category'>('numeric')
  const [admMetric, setAdmMetric] = useState<string>('')
  const [admField, setAdmField] = useState<string>('')
  const [stacked, setStacked] = useState(false)
  const [riskFilter, setRiskFilter] = useState<Risk | ''>('')

  const course = useQuery({ queryKey: ['course', courseId], queryFn: () => api.course(courseId) })
  const a = useQuery({ queryKey: ['analytics', courseId], queryFn: () => api.analytics(courseId), enabled: !!course.data?.has_data })

  const base = `/api/courses/${enc(courseId)}/analytics/figures`
  const sel = selected ? `student_id=${enc(selected)}` : ''
  const name = course.data?.name ?? courseId

  if (course.isLoading || a.isLoading) return <p className="muted">Loading…</p>
  if (course.error) return <p className="error">{(course.error as Error).message}</p>
  const data = a.data
  const header = <CourseHeader courseId={courseId} name={name}
    extra={<p className="muted small">Risk clusters from the last ingest ({timeAgo(course.data?.last_success?.finished_at)}). K-means, k=4, on six behaviour and performance features.</p>} />

  if (!course.data?.has_data) return <section>{header}<p className="muted">No data yet. Run an ingest from the course list.</p></section>
  if (!data?.available) return <section>{header}<div className="card"><p>{data?.note}</p></div></section>

  const adm = data.admissions
  const metric = admMetric || Object.keys(adm?.numeric_fields ?? {})[0] || ''
  const field = admField || Object.keys(adm?.category_fields ?? {})[0] || ''
  const students = data.students.filter((s) => !riskFilter || s.cluster_label === riskFilter)
  const selStudent = data.students.find((s) => s.STUDENT_ID === selected)

  return (
    <section>
      {header}

      <div className="risk-cards">
        {data.summary.map((c) => (
          <button key={c.cluster_label} className={`risk-card ${riskFilter === c.cluster_label ? 'on' : ''}`}
                  style={{ ['--risk' as string]: RISK_COLOR[c.cluster_label] }}
                  onClick={() => setRiskFilter(riskFilter === c.cluster_label ? '' : c.cluster_label)}>
            <span className="risk-name">{c.cluster_label}</span>
            <span className="risk-n">{c.students}</span>
            <span className="small muted">avg grade {pct(c.mean_grade_ratio, 1)} · missed {pct(c.mean_missed_rate, 1)} · late {pct(c.mean_late_rate, 1)}</span>
          </button>
        ))}
      </div>

      <div className="toolbar">
        <label className="small">Highlight student{' '}
          <select value={selected} onChange={(e) => setParams(e.target.value ? { student: e.target.value } : {})}>
            <option value="">(none)</option>
            {RISKS.map((r) => (
              <optgroup key={r} label={r}>
                {data.students.filter((s) => s.cluster_label === r).map((s) =>
                  <option key={s.STUDENT_ID} value={s.STUDENT_ID}>{s.STUDENT_NAME} ({s.SECTION_NAME ?? '–'})</option>)}
              </optgroup>
            ))}
          </select>
        </label>
        {selStudent && (
          <span className="small">Risk <RiskPill risk={selStudent.cluster_label} />{' '}
            <Link to={`/courses/${enc(courseId)}/students/${enc(selStudent.STUDENT_ID)}`}>open student</Link></span>
        )}
        <span className="spacer" />
        <div className="seg">
          <button className={view3d ? 'on' : ''} onClick={() => setView3d(true)}>3D</button>
          <button className={!view3d ? 'on' : ''} onClick={() => setView3d(false)}>2D</button>
        </div>
        <a className="btn ghost" href={api.riskReportUrl(courseId)}>Download risk report (CSV)</a>
      </div>

      <FigureCard url={`${base}/${view3d ? 'pca3d' : 'pca2d'}?${sel}`} height={view3d ? 640 : 560} />

      <details className="notes">
        <summary>About the PCA view</summary>
        <p className="muted small">
          Each student is a point described by six features: grade ratio, average score, score spread, late rate,
          on-time rate and missed rate. PCA rotates those six measures into uncorrelated axes; PC1 carries the most
          variance, PC2 the most of what remains, and so on. The percentage on each axis is the share of variance it
          explains. Colours are the K-means risk clusters, ordered by mean grade ratio (lowest is Critical). In 3D the
          translucent shells are each cluster's convex hull. Late and on-time rates use only items with a submission
          time, so quizzes graded by hand do not count as "on time".
        </p>
      </details>

      <h2 className="section-title">Cluster insights</h2>
      <div className="fig-grid">
        {INSIGHTS.map(([n, t]) => <FigureCard key={n} url={`${base}/${n}?${sel}`} title={t} />)}
      </div>

      <h2 className="section-title">Admissions context</h2>
      {!adm?.available ? (
        <div className="card"><p className="muted small">
          No admissions records matched this course. Admissions upload arrives with the Data &amp; Models page; until then,
          an <code>admissions.duckdb</code> from the Streamlit app can be copied into the data directory.
        </p></div>
      ) : (
        <>
          <p className="small muted">Admissions data matched for {adm.matched} students ({(adm.match_rate * 100).toFixed(0)}%), by name key.</p>
          <div className="toolbar">
            <div className="seg">
              {(['numeric', 'yesno', 'category'] as const).map((m) => (
                <button key={m} className={admMode === m ? 'on' : ''} onClick={() => setAdmMode(m)}>
                  {{ numeric: 'Numeric', yesno: 'Yes/No flags', category: 'Categories' }[m]}
                </button>))}
            </div>
            {admMode === 'numeric' && (
              <select value={metric} onChange={(e) => setAdmMetric(e.target.value)}>
                {Object.entries(adm.numeric_fields).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>)}
            {admMode === 'category' && (<>
              <select value={field} onChange={(e) => setAdmField(e.target.value)}>
                {Object.entries(adm.category_fields).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <label className="small check"><input type="checkbox" checked={stacked} onChange={(e) => setStacked(e.target.checked)} /> 100% stacked</label>
            </>)}
          </div>
          {admMode === 'numeric' && metric && (
            <div className="fig-grid">
              <FigureCard url={`${base}/adm_numeric_box?metric=${metric}&${sel}`} />
              <FigureCard url={`${base}/adm_numeric_scatter?metric=${metric}&${sel}`} />
            </div>)}
          {admMode === 'yesno' && <FigureCard url={`${base}/adm_yesno`} />}
          {admMode === 'category' && field && <FigureCard url={`${base}/adm_category?field=${field}&stacked=${stacked}`} />}
        </>
      )}

      <h2 className="section-title">Students by risk {riskFilter && <span className="muted">· {riskFilter}</span>}</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Student</th><th>Section</th><th>Risk</th><th className="num">Grade</th><th className="num">Avg score</th>
            <th className="num">Score SD</th><th className="num">Late (timed)</th><th className="num">Missed</th><th /></tr></thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.STUDENT_ID} className={s.STUDENT_ID === selected ? 'selected' : ''}>
                <td><Link to={`/courses/${enc(courseId)}/students/${enc(s.STUDENT_ID)}`}>{s.STUDENT_NAME}</Link></td>
                <td>{s.SECTION_NAME ?? '–'}</td>
                <td><RiskPill risk={s.cluster_label} /></td>
                <td className="num">{pct(s.grade_ratio, 1)}</td>
                <td className="num">{pct(s.avg_percentage, 1)}</td>
                <td className="num">{pct(s.std_percentage, 1)}</td>
                <td className="num">{pct(s.late_rate)} <span className="muted small">of {s.n_timed_items}</span></td>
                <td className="num">{pct(s.missed_rate)}</td>
                <td><button className="linkish" onClick={() => { setParams({ student: s.STUDENT_ID }); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>highlight</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
