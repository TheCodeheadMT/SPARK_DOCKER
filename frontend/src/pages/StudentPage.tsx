import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Preferences } from '../api'
import { RiskPill } from '../components/CourseNav'
import FigureCard from '../components/FigureCard'

// Plotly is ~4.5 MB; load it only when a chart is on screen.
const Plot = lazy(() => import('../components/Plot'))

const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`
}
const DEFAULT_PREFS: Preferences = { cutoff_percentage: 60, show_cutoff: true, smooth_curve: false }
const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–')

function Figure({ courseId, studentId, name, title, prefs }:
  { courseId: string; studentId: string; name: string; title: string; prefs: Preferences }) {
  const fig = useQuery({
    queryKey: ['figure', courseId, studentId, name, prefs],
    queryFn: () => api.figure(courseId, studentId, name, prefs),
  })
  return (
    <div className="card">
      <h3>{title}</h3>
      {fig.isLoading ? <p className="muted">Drawing…</p> : fig.data ? (
        <Suspense fallback={<p className="muted">Loading chart library…</p>}><Plot figure={fig.data} /></Suspense>
      ) : <p className="error">Could not load chart.</p>}
    </div>
  )
}

export default function StudentPage() {
  const { courseId = '', studentId = '' } = useParams()
  const qc = useQueryClient()
  const prefsQ = useQuery({ queryKey: ['prefs'], queryFn: api.prefs })
  const prefs = prefsQ.data ?? DEFAULT_PREFS
  const save = useMutation({
    mutationFn: api.savePrefs,
    onSuccess: (p) => qc.setQueryData(['prefs'], p),
  })
  const s = useQuery({ queryKey: ['student', courseId, studentId], queryFn: () => api.student(courseId, studentId) })
  const course = useQuery({ queryKey: ['course', courseId], queryFn: () => api.course(courseId) })
  const zy = useQuery({ queryKey: ['zy-student', courseId, studentId], queryFn: () => api.zybooksStudent(courseId, studentId) })

  if (s.isLoading) return <p className="muted">Loading…</p>
  if (s.error) return <p className="error">{(s.error as Error).message}</p>
  const { profile, kpis, assignments, model } = s.data!
  const modelNote = model
    ? `${model.aliased ? `Model reused from ${model.source_course_name}` : 'Model trained on this course'}; success = grade at or above ${model.threshold}%` +
      (model.metrics?.roc_auc != null ? `; cross-validated AUC ${model.metrics.roc_auc.toFixed(2)}` : '; not validated (too few students below threshold)')
    : ''
  const below = profile.CURRENT_SCORE != null && profile.CURRENT_SCORE < prefs.cutoff_percentage

  return (
    <section>
      <p className="crumbs">
        <Link to="/">Courses</Link> / <Link to={`/courses/${encodeURIComponent(courseId)}`}>{course.data?.name ?? courseId}</Link> / {profile.STUDENT_NAME}
      </p>
      <div className="page-head">
        <div>
          <h1>{profile.STUDENT_NAME}</h1>
          <p className="muted small">{profile.STUDENT_ID} · section {profile.SECTION_NAME} · last Blackboard access {fmtDate(profile.LAST_ACCESS)}</p>
          <p className="small">Risk cluster <RiskPill risk={kpis.risk_label} />{' '}
            {kpis.risk_label && <Link to={`/courses/${encodeURIComponent(courseId)}/analytics?student=${encodeURIComponent(studentId)}`}>see in Analytics</Link>}</p>
        </div>
        <div className="controls">
          <Link className="btn" to={`/courses/${encodeURIComponent(courseId)}/students/${encodeURIComponent(studentId)}/ei`}>
            Extra Instruction{kpis.risk_label === 'Critical' || kpis.risk_label === 'High' ? ' ⚑' : ''}</Link>
          <label className="small">
            Cutoff {prefs.cutoff_percentage}%
            <input type="range" min={0} max={100} step={1} value={prefs.cutoff_percentage}
                   onChange={(e) => qc.setQueryData(['prefs'], { ...prefs, cutoff_percentage: Number(e.target.value) })}
                   onMouseUp={() => save.mutate(prefs)} onKeyUp={() => save.mutate(prefs)} onTouchEnd={() => save.mutate(prefs)} />
          </label>
          <label className="small check">
            <input type="checkbox" checked={prefs.show_cutoff}
                   onChange={(e) => save.mutate({ ...prefs, show_cutoff: e.target.checked })} /> Show cutoff line
          </label>
        </div>
      </div>

      <div className="kpis">
        <div className={`stat ${below ? 'warn' : ''}`}><span>{profile.CURRENT_SCORE?.toFixed(1) ?? '–'}%</span><small>current grade {profile.CURRENT_GRADE ?? ''}</small></div>
        <div className="stat"><span>{kpis.points_earned.toFixed(1)} / {kpis.points_possible.toFixed(0)}</span><small>points</small></div>
        <div className="stat"><span>{kpis.class_rank_pct != null ? ordinal(Math.round(kpis.class_rank_pct * 100)) : '–'}</span><small>percentile in course</small></div>
        <div className={`stat ${kpis.items_missed ? 'warn' : ''}`}><span>{kpis.items_missed}</span><small>missed of {kpis.items_graded}</small></div>
        <div className={`stat ${kpis.late ? 'warn' : ''}`}><span>{kpis.late}</span><small>late of {kpis.items_with_submit_time} timed</small></div>
        <div className="stat"><span>{kpis.avg_days_before_due != null ? kpis.avg_days_before_due.toFixed(1) : '–'}</span><small>avg days before due</small></div>
        <div className={`stat ${kpis.success_prob != null && kpis.success_prob < 0.5 ? 'warn' : ''}`} title={modelNote}>
          <span>{kpis.success_prob != null ? `${(kpis.success_prob * 100).toFixed(0)}%` : '–'}</span>
          <small>{kpis.success_prob != null ? `success probability${model?.aliased ? ' (reused model)' : ''}` : 'no success model'}</small>
        </div>
      </div>

      <div className="fig-grid">
        <Figure courseId={courseId} studentId={studentId} name="cumulative" title="Cumulative grade" prefs={prefs} />
        <Figure courseId={courseId} studentId={studentId} name="scores" title="Assignment scores" prefs={prefs} />
      </div>

      {zy.data?.available && zy.data.kpis && (
        <>
          <h2 className="section-title">zyBooks</h2>
          <div className="summary-row">
            <div className="stat"><span>{zy.data.kpis.avg_participation.toFixed(1)}%</span><small>avg participation</small></div>
            <div className="stat"><span>{zy.data.kpis.avg_zylab.toFixed(1)}%</span><small>avg zyLab</small></div>
            <div className="stat"><span>{(zy.data.kpis.total_minutes / 60).toFixed(1)} h</span><small>total time</small></div>
          </div>
          <FigureCard url={`/api/courses/${encodeURIComponent(courseId)}/zybooks/students/${encodeURIComponent(studentId)}/figure`} />
        </>
      )}

      <div className="card">
        <h3>Assignments</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Item</th><th>Category</th><th>Due</th><th>Submitted</th><th className="num">Score</th><th>Status</th></tr>
            </thead>
            <tbody>
              {assignments.map((a) => {
                const p = a.PERCENTAGE
                return (
                  <tr key={a.ASSIGNMENT_ID} className={p != null && p * 100 < prefs.cutoff_percentage ? 'flag' : ''}>
                    <td>{a.ASSIGNMENT}</td>
                    <td>{a.ASSIGNMENT_GROUP_ID}</td>
                    <td>
                      {fmtDate(a.DUE_AT)}
                      {a.DUE_SOURCE === 'inferred_auto_zero' && <span className="tag" title="Inferred from Blackboard automatic zeros">inferred</span>}
                      {a.DUE_SOURCE === 'manifest' && <span className="tag">manifest</span>}
                    </td>
                    <td>{a.SUBMITTED_AT ? fmtDate(a.SUBMITTED_AT) : <span className="muted">no submit time</span>}</td>
                    <td className="num">{a.POINTS_EARNED} / {a.POINTS_POSSIBLE}</td>
                    <td>
                      {a.MISSED_ASSIGNMENT ? <span className="pill bad">missed</span>
                        : a.LATE ? <span className="pill warn">late</span>
                        : a.SUBMITTED_AT ? <span className="pill ok">on time</span> : <span className="muted small">graded</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
