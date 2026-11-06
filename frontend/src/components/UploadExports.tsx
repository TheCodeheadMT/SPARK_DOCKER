import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api, type UploadResult } from '../api'

const KIND_LABEL: Record<string, string> = { gradebook: 'Full Gradebook', grade_history: 'Grade History' }
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '')

export function HowToExport() {
  return (
    <details className="notes">
      <summary>How to download the exports from Blackboard</summary>
      <ol className="small howto">
        <li>Open the course in Blackboard and go to <strong>Gradebook</strong>.</li>
        <li>Select the <strong>Download</strong> icon (the arrow pointing down, top right).</li>
        <li>Choose <strong>Full Gradebook</strong>, tick <strong>Select all items</strong>, file type <strong>CSV</strong>,
          save location <strong>My Device</strong>, then <strong>Download</strong>.</li>
        <li>Open Download again and choose <strong>Grade History</strong> (CSV, My Device). This gives SPARK submission
          times, so it can show late work and due dates.</li>
        <li>Drop both files here. File names don't matter; SPARK tells them apart.</li>
      </ol>
    </details>
  )
}

/** Drop exports, see what SPARK found, then upload. mode "course" updates a course; "new" adds one. */
export default function UploadExports({ mode, courseId, onDone }:
  { mode: 'course' | 'new'; courseId?: string; onDone?: (r: UploadResult) => void }) {
  const [files, setFiles] = useState<File[]>([])
  const [check, setCheck] = useState<UploadResult | null>(null)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState<'check' | 'upload' | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const courses = useQuery({ queryKey: ['courses'], queryFn: api.courses })
  const maxMb = courses.data?.upload_max_mb ?? 200

  const send = (fs: File[], dry: boolean, c = code, n = name) =>
    mode === 'course' ? api.uploadExports(courseId!, fs, dry) : api.addCourse(fs, c, n, dry)

  async function pick(list: FileList | null) {
    if (!list?.length) return
    const tooBig = Array.from(list).filter((f) => f.size > maxMb * 1024 * 1024)
    if (tooBig.length) {
      setErr(`${tooBig.map((f) => f.name).join(', ')}: larger than the ${maxMb} MB limit.`)
      return
    }
    const merged = [...files.filter((f) => !Array.from(list).some((n) => n.name === f.name)), ...Array.from(list)].slice(-4)
    setFiles(merged)
    setResult(null)
    setErr(null)
    setBusy('check')
    try {
      const r = await send(merged, true)
      setCheck(r)
      if (mode === 'new' && !code && r.course_code) setCode(r.course_code)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function recheck(c: string) {
    if (!files.length) return
    try { setCheck(await send(files, true, c)) } catch (e) { setErr((e as Error).message) }
  }

  async function upload() {
    setBusy('upload')
    setErr(null)
    try {
      const r = await send(files, false)
      if (r.ok) {
        setResult(r)
        setFiles([])
        setCheck(null)
        onDone?.(r)
      } else {
        setCheck(r)
      }
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  function clear() {
    setFiles([]); setCheck(null); setErr(null)
    if (input.current) input.current.value = ''
  }

  return (
    <div className="upload">
      <div className={`dropzone ${over ? 'over' : ''}`}
           onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
           onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files) }}
           onClick={() => input.current?.click()} role="button" tabIndex={0}
           onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}>
        <input ref={input} type="file" multiple accept=".csv,.xls,.txt" hidden onChange={(e) => pick(e.target.files)} />
        <strong>Drop the Full Gradebook and Grade History exports here</strong>
        <span className="small muted">or click to choose files (.csv or .xls, up to {maxMb} MB each)</span>
      </div>

      {busy === 'check' && <p className="small muted">Uploading and checking files…</p>}
      {err && <p className="error small">{err}</p>}

      {check && check.files.length > 0 && (
        <div className="table-wrap upload-files">
          <table>
            <thead><tr><th>File</th><th>Recognised as</th><th className="num">Students</th><th className="num">Items</th><th>Details</th></tr></thead>
            <tbody>{check.files.map((f) => (
              <tr key={f.filename} className={f.error ? 'flag' : ''}>
                <td>{f.filename}</td>
                <td>{f.kind ? <span className="pill ok">{KIND_LABEL[f.kind]}</span> : <span className="pill bad">Not recognised</span>}</td>
                <td className="num">{f.kind ? f.students : ''}</td>
                <td className="num">{f.kind ? f.items : ''}</td>
                <td className="small wrap-cell">
                  {f.error ?? (f.kind === 'gradebook'
                    ? (f.sections.length ? `${f.sections.length} section${f.sections.length === 1 ? '' : 's'}` : '')
                    : f.first_date ? `${day(f.first_date)} to ${day(f.last_date)}` : '')}
                </td>
              </tr>))}
            </tbody>
          </table>
        </div>
      )}

      {mode === 'new' && check && (
        <div className="two-col tight-cols">
          <label className="field">
            <strong>Blackboard course ID</strong>
            <input value={code} onChange={(e) => setCode(e.target.value)} onBlur={() => recheck(code)}
                   placeholder="e.g. 202620-DEMO-CS101" />
            <span className="small muted">{check.suggested_course_code ? 'Read from the gradebook sections.' : 'Enter the parent course ID.'}</span>
          </label>
          <label className="field">
            <strong>Display name (optional)</strong>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. CS 471 Artificial Intelligence, Spring" />
          </label>
        </div>
      )}

      {check && check.errors.length > 0 && <ul className="msgs error-list">{check.errors.map((m) => <li key={m}>{m}</li>)}</ul>}
      {check && check.warnings.length > 0 && <ul className="msgs warn-list">{check.warnings.map((m) => <li key={m}>{m}</li>)}</ul>}

      {check && (
        <div className="card-actions">
          <button className="btn" disabled={!check.ok || busy !== null || (mode === 'new' && !code.trim())} onClick={upload}>
            {busy === 'upload' ? 'Uploading and ingesting…'
              : check.warnings.length ? (mode === 'new' ? 'Add course anyway' : 'Upload anyway')
              : mode === 'new' ? 'Add course' : 'Upload and ingest'}
          </button>
          <button className="btn ghost" disabled={busy !== null} onClick={clear}>Clear</button>
        </div>
      )}

      {result && (
        <p className={`small ${result.run?.status === 'failed' ? 'error' : 'ok-text'}`}>
          {result.run?.status === 'failed' ? `Saved, but the ingest failed: ${result.run.error}`
            : result.note ?? (result.run?.status === 'skipped' ? 'Saved. The data was already up to date.' : 'Uploaded and ingested.')}
        </p>
      )}
    </div>
  )
}
