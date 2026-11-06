import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, enc, type AdmissionsResult, type PollSummary } from '../api'
import { timeAgo } from '../components/IngestStatus'
import { WatchFiles, WatchPill } from '../components/WatchStatus'

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–')

function ResultsTable({ rows }: { rows: AdmissionsResult[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Workbook</th><th>Status</th><th className="num">Rows</th><th className="num">New students</th><th className="num">Total after</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.file_name}><td>{r.file_name}</td><td>{r.status}</td><td className="num">{r.rows_in_file}</td>
            <td className="num">{r.new_rows}</td><td className="num">{r.total_after}</td></tr>))}
        </tbody>
      </table>
    </div>
  )
}

function pollText(s: PollSummary | null | undefined) {
  if (!s) return 'not run yet'
  if (s.skipped) return s.skipped
  if (s.error) return `error: ${s.error}`
  const parts = [`${s.checked} checked`]
  if (s.ingested?.length) parts.push(`${s.ingested.length} ingested`)
  if (s.waiting?.length) parts.push(`${s.waiting.length} settling`)
  if (s.failed?.length) parts.push(`${s.failed.length} failed`)
  return `${parts.join(', ')} in ${s.duration_s} s (${timeAgo(s.started_at)})`
}

function MountPanel() {
  const qc = useQueryClient()
  const m = useQuery({ queryKey: ['mount'], queryFn: api.mount, refetchInterval: 30_000 })
  const poll = useMutation({
    mutationFn: (courseId?: string) => api.pollNow(courseId),
    onSuccess: (r) => { qc.setQueryData(['mount'], r); qc.invalidateQueries({ queryKey: ['inventory'] }) },
  })
  if (m.isLoading) return <div className="card"><p className="muted">Checking the Blackboard mount…</p></div>
  if (m.error) return <div className="card"><p className="error">{(m.error as Error).message}</p></div>
  const { mount, poller, watches } = m.data!
  return (
    <div className={`card mount ${mount.reachable ? '' : 'flagged'}`}>
      <div className="card-head">
        <h3>Blackboard mount</h3>
        <div className="card-actions tight">
          {poller && <button className="btn" disabled={poll.isPending} onClick={() => poll.mutate(undefined)}>
            {poll.isPending ? 'Checking…' : 'Check for new exports now'}</button>}
        </div>
      </div>
      {!mount.configured ? <p className="small muted">BB_ROOT is not set on this server.</p> : (
        <>
          <p>
            {mount.reachable ? <span className="pill ok">Reachable</span> : <span className="pill bad">Not reachable</span>}{' '}
            <code>{mount.root}</code>
            {mount.reachable && <span className="small muted"> · {mount.courses} course folder{mount.courses === 1 ? '' : 's'} · listed in {mount.latency_ms} ms
              {mount.is_mount ? ` · ${mount.is_webdav ? 'WebDAV' : mount.fstype ?? ''} mount at ${mount.mount_point}` : ' · local folder (not a mount)'}
              {mount.server_ok ? ` · Blackboard server answered in ${mount.server_ms} ms` : ''}</span>}
          </p>
          {mount.error && <p className="error small">{mount.error}</p>}
          {mount.hint && <p className="small muted">{mount.hint}</p>}
          {poller && (
            <p className="small">
              Automatic ingest: {poller.enabled
                ? <>every {Math.round(poller.interval_s / 60)} min, after an export is unchanged for {poller.settle_s} s{poller.leader ? '' : ' (another server process is polling)'}</>
                : <>off (set <code>BB_POLL_INTERVAL_S</code>, e.g. 300)</>}
              {' · '}last poll: {pollText(poller.last_run)}
              {poller.stuck_for_s != null && poller.stuck_for_s > poller.io_timeout_s &&
                <span className="error"> · a mount call has been stuck for {Math.round(poller.stuck_for_s)} s</span>}
            </p>
          )}
          {poll.isError && <p className="error small">{(poll.error as Error).message}</p>}
          {watches.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Course</th><th>Status</th><th>Exports seen</th><th>Checked</th><th></th></tr></thead>
                <tbody>{watches.map((w) => (
                  <tr key={w.course_id}>
                    <td><Link to={`/courses/${enc(w.course_id)}/data`}>{w.course_id.replace(/^bb:/, '')}</Link></td>
                    <td><WatchPill status={w.status} />{w.detail && <div className="small muted wrap">{w.detail}</div>}</td>
                    <td className="wrap-cell"><WatchFiles w={w} /></td>
                    <td className="small">{timeAgo(w.last_checked_at)}</td>
                    <td><button className="linkish" disabled={poll.isPending} onClick={() => poll.mutate(w.course_id)}
                                title="Ingest now, without waiting for the export to settle">Ingest now</button></td>
                  </tr>))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function AdminPage() {
  const qc = useQueryClient()
  const inv = useQuery({ queryKey: ['inventory'], queryFn: api.inventory })
  const [file, setFile] = useState<File | null>(null)
  const preview = useMutation({ mutationFn: (f: File) => api.uploadAdmissions(f, true) })
  const commit = useMutation({
    mutationFn: (f: File) => api.uploadAdmissions(f, false),
    onSuccess: () => { preview.reset(); setFile(null); qc.invalidateQueries() },
  })

  const scrub = useMutation({ mutationFn: api.scrubAdmissions, onSuccess: () => qc.invalidateQueries() })

  if (inv.isLoading) return <p className="muted">Loading…</p>
  if (inv.error) return <p className="error">{(inv.error as Error).message}</p>
  const { courses, admissions } = inv.data!

  return (
    <section>
      <h1>Administration</h1>

      <MountPanel />

      <h2 className="section-title">Course inventory</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Course</th><th>LMS</th><th className="num">Students</th><th>Last ingest</th><th className="num">DB size</th>
            <th>Success model</th><th>zyBooks</th></tr></thead>
          <tbody>{courses.map((c) => (
            <tr key={c.id}>
              <td>{c.has_data ? <Link to={`/courses/${enc(c.id)}/data`}>{c.name}</Link> : c.name}</td>
              <td>{c.lms}</td>
              <td className="num">{c.students ?? '–'}</td>
              <td>{timeAgo(c.last_ingest)}</td>
              <td className="num">{c.has_data ? `${c.db_size_mb} MB` : '–'}</td>
              <td>{c.model ? 'own' : c.alias_source ? `reuses ${c.alias_source}` : <span className="muted">none</span>}</td>
              <td>{c.zybooks ? 'yes' : <span className="muted">no</span>}</td>
            </tr>))}
          </tbody>
        </table>
      </div>

      <h2 className="section-title">Admissions data (shared by all courses)</h2>
      <div className="card">
        <p className="small">
          {admissions.exists ? <>{admissions.students} students from {admissions.files} file(s); last added {when(admissions.last_ingest)}.</>
            : 'No admissions database yet.'}{' '}
          Uploads only add new students (matched by first+last name key) and never overwrite existing records. A file that was
          already loaded is skipped.
        </p>
        {!!admissions.legacy_sensitive_columns?.length && (
          <div className="alert">
            <p className="small"><strong>This admissions database still contains {admissions.legacy_sensitive_columns.join(' and ')}</strong>{' '}
              (carried over from the Streamlit app). SPARK no longer stores these. Remove them now, or they will be removed on the next upload.</p>
            <button className="btn" disabled={scrub.isPending} onClick={() => scrub.mutate()}>
              {scrub.isPending ? 'Removing…' : `Remove ${admissions.legacy_sensitive_columns.join(' and ')}`}</button>
            {scrub.isError && <p className="error small">{(scrub.error as Error).message}</p>}
          </div>
        )}
        {scrub.isSuccess && scrub.data.dropped.length > 0 && <p className="small ok-text">Removed {scrub.data.dropped.join(' and ')}. The file was rewritten so the old values are gone, not just hidden.</p>}
        <div className="toolbar">
          <input type="file" accept=".xlsx,.zip" onChange={(e) => { setFile(e.target.files?.[0] ?? null); preview.reset(); commit.reset() }} />
          <button className="btn ghost" disabled={!file || preview.isPending} onClick={() => file && preview.mutate(file)}>
            {preview.isPending ? 'Checking…' : '1. Check file'}</button>
          <button className="btn" disabled={!file || !preview.isSuccess || commit.isPending} onClick={() => file && commit.mutate(file)}>
            {commit.isPending ? 'Adding…' : '2. Add to admissions DB'}</button>
        </div>
        {preview.isError && <p className="error small">{(preview.error as Error).message}</p>}
        {preview.isSuccess && <><p className="small">Dry run, nothing written yet:</p><ResultsTable rows={preview.data.results} /></>}
        {commit.isError && <p className="error small">{(commit.error as Error).message}</p>}
        {commit.isSuccess && <><p className="small ok-text">Added.</p><ResultsTable rows={commit.data.results} /></>}
        <p className="small muted">Accepts AdmissionsData.xlsx or a .zip of several. Uploaded workbooks are not stored; only a fingerprint is
          logged. Sex and race columns in the workbook are ignored. Student-level admissions rows are never sent to the browser; pages show only cluster-level comparisons.</p>
        {admissions.history.length > 0 && (
          <details className="notes">
            <summary>Ingest history</summary>
            <ResultsTable rows={admissions.history.map((h) => ({ ...h, status: when(h.added_at) }))} />
          </details>
        )}
      </div>
    </section>
  )
}
