import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, type ModelMetrics, type ModelSummary } from '../api'
import { CourseHeader } from '../components/CourseNav'
import { IngestNotes, StatusPill, timeAgo } from '../components/IngestStatus'
import { WatchPill } from '../components/WatchStatus'
import UploadExports, { HowToExport } from '../components/UploadExports'

const pct = (v: number | null | undefined, d = 0) => (v == null ? '–' : `${(v * 100).toFixed(d)}%`)
const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–')

const FEATURE_LABELS: Record<string, string> = {
  grade_ratio: 'Grade ratio', late_rate: 'Late rate', missed_rate: 'Missed rate', on_time_rate: 'On-time rate',
  avg_percentage: 'Average score', std_percentage: 'Score spread', num_assignments: 'Items graded', pace: 'Pace',
  weeks_active: 'Weeks active', days_since_last_submission: 'Days since last submission',
  mean_submission_delta_days: 'Avg days early/late', has_due_dates: 'Has due dates',
  progress_assignments_pct: 'Progress (items)', progress_points_pct: 'Progress (points)',
}

export function Metrics({ m }: { m: ModelMetrics }) {
  if (!m.folds) return <p className="small warn-text">{m.note}</p>
  return (
    <p className="small">
      Cross-validated ({m.folds}-fold): accuracy <strong>{pct(m.accuracy, 1)}</strong> vs {pct(m.baseline_accuracy, 1)} by always guessing the
      majority · ROC AUC <strong>{m.roc_auc?.toFixed(2)}</strong>
      {m.note && <span className="warn-text"> · {m.note}</span>}
    </p>
  )
}

function ModelCard({ m, title }: { m: ModelSummary; title: string }) {
  return (
    <div className="model-card">
      <h4>{title}</h4>
      <p className="small muted">
        {m.course_name} · trained {when(m.trained_at)} by {m.trained_by} · threshold {m.config.success_threshold}% ·{' '}
        {m.config.classifier.toUpperCase()}{m.config.include_grade_features ? ' · includes grade' : ''}
      </p>
      <p className="small">{m.n_students} students, {m.n_success} at or above threshold ({pct(m.success_rate)})</p>
      <Metrics m={m.metrics} />
      {m.importance && (
        <div className="importance">
          {m.importance.slice(0, 6).map((f) => (
            <div key={f.feature} className="imp-row">
              <span className="small">{FEATURE_LABELS[f.feature] ?? f.feature}</span>
              <span className="bar"><i style={{ width: `${Math.round(f.importance / m.importance![0].importance * 100)}%` }} /></span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function DataPage() {
  const { courseId = '' } = useParams()
  const qc = useQueryClient()
  const course = useQuery({ queryKey: ['course', courseId], queryFn: () => api.course(courseId) })
  const data = useQuery({ queryKey: ['course-data', courseId], queryFn: () => api.courseData(courseId) })
  const models = useQuery({ queryKey: ['models', courseId], queryFn: () => api.models(courseId), enabled: !!data.data?.has_data })
  const [cfg, setCfg] = useState({ success_threshold: 72, include_grade_features: false, classifier: 'hgb' })
  const [zyFile, setZyFile] = useState<File | null>(null)
  const watch = useQuery({ queryKey: ['watch', courseId], queryFn: () => api.courseWatch(courseId), refetchInterval: 30_000 })

  const refresh = () => qc.invalidateQueries({ predicate: (q) => JSON.stringify(q.queryKey).includes(courseId) })
  const ingest = useMutation({ mutationFn: (force: boolean) => api.ingest(courseId, force), onSuccess: refresh })
  const train = useMutation({ mutationFn: () => api.trainModel(courseId, cfg), onSuccess: refresh })
  const alias = useMutation({ mutationFn: (src: string | null) => api.setAlias(courseId, src), onSuccess: refresh })
  const zy = useMutation({ mutationFn: (f: File) => api.uploadZybooks(courseId, f), onSuccess: () => { setZyFile(null); refresh() } })

  if (course.isLoading || data.isLoading) return <p className="muted">Loading…</p>
  if (course.error) return <p className="error">{(course.error as Error).message}</p>
  const c = course.data!
  const d = data.data!
  const mv = models.data
  const canEdit = ['Instructor', 'CourseDirector', 'Admin'].includes(c.role)

  return (
    <section>
      <CourseHeader courseId={courseId} name={c.name}
        extra={<p className="muted small">Course data, success model and zyBooks. {c.lms === 'bb' ? 'Data comes from Blackboard gradebook exports, uploaded here or read from the Blackboard folder.' : 'Data comes from the LMS.'}</p>} />

      {/* ------------------------------------------------ course data */}
      <h2 className="section-title">Course data</h2>
      <div className="card">
        <div className="card-head">
          <h3>Last update: {timeAgo(c.last_success?.finished_at)}</h3>
          <div className="card-actions">
            {canEdit && <button className="btn" onClick={() => ingest.mutate(false)} disabled={ingest.isPending}>
              {ingest.isPending ? 'Ingesting…' : 'Ingest latest exports'}</button>}
            {canEdit && <button className="btn ghost" onClick={() => ingest.mutate(true)} disabled={ingest.isPending}
              title="Rebuild even if the export files have not changed">Force rebuild</button>}
            {canEdit && d.has_data && <a className="btn ghost" href={api.exportDbUrl(courseId)}>Export DuckDB ({d.db_size_mb} MB)</a>}
          </div>
        </div>
        {ingest.isError && <p className="error small">{(ingest.error as Error).message}</p>}
        {watch.data?.watched && (
          <p className="small watch-line">
            {watch.data.auto
              ? <>New exports in the Blackboard folder are ingested automatically (checked every {Math.round((watch.data.interval_s ?? 0) / 60)} min). </>
              : <>Automatic ingest is off on this server; use the button after saving a new export. </>}
            {watch.data.watch && <>Last check {timeAgo(watch.data.watch.last_checked_at)}: <WatchPill status={watch.data.watch.status} />
              {watch.data.watch.detail && <span className="muted"> {watch.data.watch.detail}</span>}</>}
          </p>
        )}
        <IngestNotes run={c.last_success} />
        {canEdit && c.lms === 'bb' && (
          <details className="upload-box" open={!d.has_data}>
            <summary><strong>Upload new exports</strong> <span className="small muted">from your computer, after downloading them from the Blackboard gradebook</span></summary>
            <HowToExport />
            <UploadExports mode="course" courseId={courseId} onDone={refresh} />
          </details>
        )}
        <div className="table-wrap">
          <table>
            <thead><tr><th>Run</th><th>Status</th><th>Started</th><th>By</th><th>Files</th><th className="num">Students</th><th>Version</th></tr></thead>
            <tbody>
              {d.runs.map((r) => (
                <tr key={r.id}>
                  <td>#{r.id}</td>
                  <td><StatusPill run={r} />{r.error && <span className="error small"> {r.error}</span>}</td>
                  <td>{when(r.started_at)}</td>
                  <td className="small">{r.triggered_by === 'system:poller' ? <span className="tag">automatic</span> : r.triggered_by}</td>
                  <td className="small">{r.report?.files?.gradebook ?? (r.report?.synthetic ? 'synthetic' : '–')}</td>
                  <td className="num">{r.report?.students ?? '–'}</td>
                  <td className="small muted">{r.source_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ------------------------------------------------ success model */}
      <h2 className="section-title">Success model</h2>
      {!d.has_data ? <p className="muted">Ingest course data first.</p> : !mv ? <p className="muted">Loading…</p> : (
        <div className="two-col">
          <div className="card">
            <h3>In use for this course</h3>
            {mv.used ? <ModelCard m={mv.used} title={mv.alias_source ? `Reused from ${mv.used.course_name}` : 'Trained on this course'} />
              : <p className="muted small">No success model yet. Train one here or reuse one from another course. Until then the student page shows no success probability.</p>}

            <div className="field">
              <label className="small">Which model should this course use?</label>
              <select value={mv.alias_source ?? ''} disabled={!canEdit || alias.isPending}
                      onChange={(e) => alias.mutate(e.target.value || null)}>
                <option value="">This course's own model{mv.own ? '' : ' (none trained)'}</option>
                {mv.candidates.map((m) => (
                  <option key={m.course_id} value={m.course_id}>
                    Reuse: {m.course_name} (threshold {m.config.success_threshold}%, AUC {m.metrics.roc_auc?.toFixed(2) ?? 'n/a'})
                  </option>))}
              </select>
              <p className="small muted">Reuse is the stronger use: train on a finished term where final grades are known, then apply it to the current term.</p>
            </div>
          </div>

          <div className="card">
            <h3>Train on this course</h3>
            {!canEdit ? <p className="muted small">Your role cannot train models.</p> : (
              <>
                <div className="field">
                  <label className="small">Classifier</label>
                  <select value={cfg.classifier} onChange={(e) => setCfg({ ...cfg, classifier: e.target.value })}>
                    {Object.entries(mv.classifiers).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label className="small">Success threshold: grade at or above {cfg.success_threshold}%</label>
                  <input type="number" min={0} max={100} step={0.5} value={cfg.success_threshold}
                         onChange={(e) => setCfg({ ...cfg, success_threshold: Number(e.target.value) })} />
                </div>
                <label className="small check">
                  <input type="checkbox" checked={cfg.include_grade_features}
                         onChange={(e) => setCfg({ ...cfg, include_grade_features: e.target.checked })} />
                  Include current grade as a feature (makes the model mostly restate the grade)
                </label>
                <div className="card-actions">
                  <button className="btn" onClick={() => train.mutate()} disabled={train.isPending}>
                    {train.isPending ? 'Training…' : 'Train model'}</button>
                </div>
                {train.isError && <p className="error small pre">{(train.error as Error).message}</p>}
                {train.isSuccess && <p className="small ok-text">Trained. It is now this course's own model{mv.alias_source ? ' (this course still reuses another course\'s model; switch above to use it)' : ''}.</p>}
                <details className="notes">
                  <summary>How training works</summary>
                  <ul>
                    <li>One row per student: lateness (timed items only), missed rate, average score and spread, pace, weeks active,
                      recency, due-date timing, and progress through the course's items and points.</li>
                    <li>Label: success if the Blackboard current grade (else points ratio) is at or above the threshold.</li>
                    <li>Accuracy and AUC come from stratified cross-validation, so they estimate performance on students the model did not see.</li>
                    <li>Trained on a course's current students and applied back to them, the probability largely describes where they already are.</li>
                  </ul>
                </details>
              </>
            )}
          </div>
        </div>
      )}
      {mv && mv.history.length > 1 && (
        <details className="notes">
          <summary>Previous models for this course ({mv.history.length - 1})</summary>
          <ul>{mv.history.slice(1).map((m) => (
            <li key={m.id}>{when(m.trained_at)} · {m.config.classifier} · threshold {m.config.success_threshold}% · AUC {m.metrics.roc_auc?.toFixed(2) ?? 'n/a'}</li>))}
          </ul>
        </details>
      )}

      {/* ------------------------------------------------ zyBooks */}
      <h2 className="section-title">zyBooks</h2>
      <div className="card">
        {d.zybooks.exists ? (
          <p className="small">Loaded <strong>{d.zybooks.file_name}</strong> {timeAgo(d.zybooks.uploaded_at)}: {d.zybooks.people} people,{' '}
            {d.zybooks.matched} matched to the roster, {d.zybooks.chapters} chapters. Uploading again replaces this course's zyBooks data only.</p>
        ) : <p className="small muted">No zyBooks data for this course.</p>}
        {canEdit && (
          <div className="toolbar">
            <input type="file" accept=".csv" onChange={(e) => setZyFile(e.target.files?.[0] ?? null)} />
            <button className="btn" disabled={!zyFile || zy.isPending} onClick={() => zyFile && zy.mutate(zyFile)}>
              {zy.isPending ? 'Uploading…' : 'Upload Class Roster Report (CSV)'}</button>
          </div>
        )}
        {zy.isError && <p className="error small">{(zy.error as Error).message}</p>}
        {zy.isSuccess && (
          <p className="small ok-text">Matched {zy.data.result.matched} of {zy.data.result.people} people
            ({Object.entries(zy.data.result.matched_by).map(([k, v]) => `${v} by ${k}`).join(', ') || 'none'}).
            {zy.data.result.people > zy.data.result.matched && ' Unmatched people are kept but not shown on student pages.'}</p>
        )}
        <p className="small muted">Students are matched by email, then by email username against the Blackboard username, then by first and last name.</p>
      </div>
    </section>
  )
}
