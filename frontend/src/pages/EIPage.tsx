import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link, useParams } from 'react-router-dom'
import {
  api, streamGenerate, type EIContext, type EIInputs, type EIModule, type EIPrompt, type WorkbookConfig,
} from '../api'
import { RiskPill } from '../components/CourseNav'

type Part = 'plan' | 'workbook'
const pct = (v: number | null | undefined, d = 0) => (v == null ? '–' : `${(v * 100).toFixed(d)}%`)
const num = (v: number | null | undefined, d = 0) => (v == null ? '–' : v.toFixed(d))
const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–'

const SECTIONS: [keyof WorkbookConfig, string][] = [
  ['include_overview', 'Overview'], ['include_student_snapshot', 'Student snapshot'],
  ['include_time_plan', 'Time & planning'], ['include_study_plan', 'Study plan'],
  ['include_practice', 'Practice activities'], ['include_reflection', 'Reflection prompts'],
  ['include_resources', 'Resources & help-seeking'], ['include_checklist', 'Weekly checklist'],
]

function Signals({ ctx }: { ctx: EIContext }) {
  const s = ctx.signals
  const warn = (b: boolean) => (b ? 'stat warn' : 'stat')
  return (
    <div className="kpis ei-kpis">
      <div className={warn(s.current_grade_pct != null && s.current_grade_pct < 70)}><span>{num(s.current_grade_pct, 1)}%</span><small>current grade</small></div>
      <div className={warn(ctx.activity_index != null && ctx.activity_index < 60)}><span>{num(ctx.activity_index)}</span><small>Activity Index (0 to 100)</small></div>
      <div className={warn((s.missing_rate ?? 0) >= 0.1)}><span>{pct(s.missing_rate)}</span><small>missing ({s.missing_count} of {s.items})</small></div>
      <div className={warn((s.late_rate ?? 0) >= 0.2)}><span>{pct(s.late_rate)}</span><small>late ({s.timed_items} timed items)</small></div>
      <div className="stat"><span>{pct(s.near_due_rate)}</span><small>within 24 h of due</small></div>
      <div className={warn((s.days_since_last_submission ?? 0) >= 10)}><span>{num(s.days_since_last_submission)}</span><small>days since last submission</small></div>
      <div className="stat"><span>{num(s.std_pct, 1)}</span><small>score spread (std %)</small></div>
      <div className="stat"><span>{s.low_score_count}</span><small>scores below 70%</small></div>
    </div>
  )
}

function Output({ md, streaming, sessionId, part, courseId, studentId }:
  { md: string; streaming: boolean; sessionId: number | null; part: Part; courseId: string; studentId: string }) {
  const [copied, setCopied] = useState(false)
  if (!md && !streaming) return null
  const url = (fmt: 'html' | 'pdf' | 'md') => (sessionId ? api.eiExportUrl(courseId, studentId, sessionId, part, fmt) : '#')
  return (
    <div className="ei-output">
      <div className="ei-output-bar">
        <span className="small muted">{streaming ? 'Writing…' : 'Review before sharing with the student.'}</span>
        <span className="spacer" />
        <button className="btn ghost small-btn" disabled={streaming}
                onClick={() => navigator.clipboard.writeText(md).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })}>
          {copied ? 'Copied' : 'Copy markdown'}</button>
        {sessionId && !streaming && <>
          <a className="btn ghost small-btn" href={url('html')} download>HTML</a>
          <a className="btn ghost small-btn" href={url('pdf')} download>PDF</a>
        </>}
      </div>
      <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{md || '…'}</ReactMarkdown></div>
    </div>
  )
}

function PromptPreview({ p, onClose }: { p: EIPrompt; onClose: () => void }) {
  return (
    <div className="ei-preview">
      <div className="ei-output-bar">
        <strong className="small">Prompt preview ({p.kind})</strong>
        <span className="small muted">
          {p.redactions ? `${p.redactions} identifier${p.redactions === 1 ? '' : 's'} replaced in your text` : 'No identifiers found in your text'}
        </span>
        <span className="spacer" />
        <button className="btn ghost small-btn" onClick={() => navigator.clipboard.writeText(`${p.system}\n\n${p.user}`)}>Copy</button>
        <button className="btn ghost small-btn" onClick={onClose}>Close</button>
      </div>
      <p className="small muted">This is exactly what is sent to the AI model.</p>
      <h4>System</h4><pre>{p.system}</pre>
      <h4>User</h4><pre>{p.user}</pre>
    </div>
  )
}

export default function EIPage() {
  const { courseId = '', studentId = '' } = useParams()
  const qc = useQueryClient()
  const ctxQ = useQuery({ queryKey: ['ei', courseId, studentId], queryFn: () => api.ei(courseId, studentId) })
  const course = useQuery({ queryKey: ['course', courseId], queryFn: () => api.course(courseId) })
  const [method, setMethod] = useState(false)
  const methodQ = useQuery({ queryKey: ['ei-method'], queryFn: () => api.eiMethodology(courseId, studentId), enabled: method })

  const [inputs, setInputs] = useState<EIInputs>({ objectives: '', instructor_notes: '', student_notes: '' })
  const [modelSel, setModel] = useState('')
  const [temp, setTemp] = useState(0.3)
  const [cfgSel, setCfg] = useState<WorkbookConfig | null>(null)
  const [modsSel, setMods] = useState<EIModule[] | null>(null)    // recommended (refreshed from notes)
  const [chosen, setChosen] = useState<string[] | null>(null)     // null = follow recommendation
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [out, setOut] = useState<Record<Part, string>>({ plan: '', workbook: '' })
  const [busy, setBusy] = useState<Part | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [preview, setPreview] = useState<EIPrompt | null>(null)
  const abort = useRef<AbortController | null>(null)

  const ctx = ctxQ.data
  // Until the instructor changes them, follow the server's defaults.
  const model = modelSel || ctx?.llm.default_model || ''
  const cfg = cfgSel ?? ctx?.workbook_options.default ?? null
  const mods = modsSel ?? ctx?.recommended_modules ?? null
  useEffect(() => () => abort.current?.abort(), [])

  const set = (k: keyof EIInputs) => (e: React.ChangeEvent<HTMLTextAreaElement>) => setInputs({ ...inputs, [k]: e.target.value })
  const refreshMods = () =>
    api.eiRecommend(courseId, studentId, inputs).then((r) => setMods(r.recommended_modules)).catch(() => {})
  const selectedKeys = chosen ?? (mods ?? []).map((m) => m.key)
  const toggleMod = (k: string) =>
    setChosen(selectedKeys.includes(k) ? selectedKeys.filter((x) => x !== k) : [...selectedKeys, k].slice(0, 4))

  const suggest = useMutation({
    mutationFn: () => api.eiObjectives(courseId, studentId, model, temp),
    onSuccess: (r) => setInputs((i) => ({ ...i, objectives: r.objectives })),
    onError: (e) => setErr((e as Error).message),
  })
  const promptBody = (kind: 'plan' | 'workbook') =>
    ({ ...inputs, kind, workbook_config: cfg!, module_keys: kind === 'workbook' ? chosen : null })
  const showPrompt = (kind: 'plan' | 'workbook') =>
    api.eiPrompt(courseId, studentId, promptBody(kind)).then(setPreview).catch((e) => setErr((e as Error).message))

  async function generate(part: Part) {
    setErr(null)
    setBusy(part)
    setOut((o) => ({ ...o, [part]: '' }))
    abort.current = new AbortController()
    try {
      const sid = await streamGenerate(courseId, studentId,
        { ...promptBody(part), model, temperature: temp, session_id: sessionId },
        { onDelta: (t) => setOut((o) => ({ ...o, [part]: o[part] + t })),
          onMeta: (m) => { if (m.modules) setMods(m.modules) } },
        abort.current.signal)
      setSessionId(sid)
      qc.invalidateQueries({ queryKey: ['ei', courseId, studentId] })
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setErr((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function load(id: number) {
    const s = await api.eiSession(courseId, studentId, id)
    setSessionId(s.id)
    setInputs({ objectives: s.objectives, instructor_notes: s.instructor_notes, student_notes: s.student_notes })
    setOut({ plan: s.plan_md, workbook: s.workbook_md })
    if (s.model) setModel(s.model)
    setTemp(s.temperature)
    if (s.workbook_config && Object.keys(s.workbook_config).length) setCfg({ ...ctx!.workbook_options.default, ...s.workbook_config })
    if (s.modules?.length) { setMods(s.modules); setChosen(s.modules.map((m) => m.key)) }
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  function fresh() {
    setSessionId(null)
    setOut({ plan: '', workbook: '' })
    setInputs({ objectives: '', instructor_notes: '', student_notes: '' })
    setChosen(null)
    setMods(null)
    setPreview(null)
  }
  const del = useMutation({
    mutationFn: (id: number) => api.eiDelete(courseId, studentId, id),
    onSuccess: (_, id) => { if (id === sessionId) fresh(); qc.invalidateQueries({ queryKey: ['ei', courseId, studentId] }) },
    onError: (e) => setErr((e as Error).message),
  })

  if (ctxQ.isLoading) return <p className="muted">Loading…</p>
  if (ctxQ.error) return <p className="error">{(ctxQ.error as Error).message}</p>
  if (!ctx || !cfg) return null
  const llmOff = !ctx.llm.enabled
  const studentUrl = `/courses/${encodeURIComponent(courseId)}/students/${encodeURIComponent(studentId)}`
  const extraMods = ctx.all_modules.filter((m) => !(mods ?? []).some((r) => r.key === m.key))

  return (
    <section className="ei">
      <p className="crumbs">
        <Link to="/">Courses</Link> / <Link to={`/courses/${encodeURIComponent(courseId)}`}>{course.data?.name ?? courseId}</Link> /{' '}
        <Link to={studentUrl}>{ctx.student.name}</Link> / Extra Instruction
      </p>
      <div className="page-head">
        <div>
          <h1>Extra Instruction | {ctx.student.name}</h1>
          <p className="small muted">Signals as of the export ({when(ctx.as_of)}) · Risk cluster <RiskPill risk={ctx.risk_label} /></p>
        </div>
        <div className="controls">
          {sessionId && <span className="small muted">Session #{sessionId}</span>}
          <button className="btn ghost" onClick={fresh}>New session</button>
        </div>
      </div>

      {err && <p className="error card">{err}</p>}

      <div className={`card triage ${ctx.triage.needs_intervention ? 'flagged' : ''}`}>
        <div className="card-head">
          <h3>Triage</h3>
          {ctx.triage.needs_intervention
            ? <span className="pill bad">Intervention suggested</span>
            : <span className="pill ok">No intervention suggested</span>}
        </div>
        <ul className="reasons">{ctx.triage.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        <Signals ctx={ctx} />
      </div>

      <div className="two-col">
        <div className="card">
          <h3>Session inputs</h3>
          <div className="field">
            <span className="field-head"><label htmlFor="ei-obj"><strong>Learning objectives</strong></label>
              <span className="small muted">(required for the plan)</span>
              <span className="spacer" />
              <button className="linkish" disabled={llmOff || suggest.isPending} onClick={() => suggest.mutate()}>
                {suggest.isPending ? 'Suggesting…' : 'Suggest from performance'}</button>
            </span>
            <textarea id="ei-obj" rows={5} value={inputs.objectives} onChange={set('objectives')}
                      placeholder="e.g. Trace nested loops; write functions with parameters and return values" />
          </div>
          <label className="field">
            <strong>Instructor notes</strong>
            <textarea rows={4} value={inputs.instructor_notes} onChange={set('instructor_notes')} onBlur={refreshMods}
                      placeholder="What you have observed in class or office hours" />
          </label>
          <label className="field">
            <strong>Student notes (self-report)</strong>
            <textarea rows={4} value={inputs.student_notes} onChange={set('student_notes')} onBlur={refreshMods}
                      placeholder="Goals, barriers, what the student says is hard" />
          </label>
          <p className="small muted privacy">
            The student's name, username, ID and e-mail are removed from these notes before anything is sent. Use
            <em> Preview prompt</em> to see exactly what the AI model receives.
          </p>
        </div>

        <div className="card">
          <h3>AI settings</h3>
          {llmOff ? (
            <p className="small warn-text">
              AI generation is not configured on this server. You can still preview prompts and copy them.
              {ctx.llm.disabled_reason ? <> Reason: {ctx.llm.disabled_reason}</> :
                <> An administrator can enable it with <code>LLM_PROVIDER=openai</code> and <code>OPENAI_API_KEY</code>.</>}
              {' '}Restart the backend after changing <code>.env</code>.
            </p>
          ) : ctx.llm.provider === 'fake' && (
            <p className="small warn-text">Demo mode: responses are canned text, no AI service is called.</p>
          )}
          <label className="field">
            <strong>Model</strong>
            <select value={model} onChange={(e) => setModel(e.target.value)} disabled={llmOff}>
              {ctx.llm.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className="field">
            <strong>Creativity {temp.toFixed(2)}</strong>
            <input type="range" min={0} max={0.6} step={0.05} value={temp} disabled={llmOff}
                   onChange={(e) => setTemp(Number(e.target.value))} />
            <span className="small muted">Capped at 0.6. Some models ignore this setting.</span>
          </label>
          {ctx.llm.rate_per_hour > 0 && <p className="small muted">Limit: {ctx.llm.rate_per_hour} generations per hour per user.</p>}

          <h3 className="mt">Saved sessions</h3>
          {ctx.sessions.length === 0 ? <p className="small muted">None yet. Each generated plan or workbook is saved here.</p> : (
            <ul className="sessions">
              {ctx.sessions.map((s) => (
                <li key={s.id} className={s.id === sessionId ? 'on' : ''}>
                  <button className="linkish" onClick={() => load(s.id)}>#{s.id} · {when(s.updated_at)}</button>
                  <span className="small muted"> {s.created_by} · {[s.has_plan && 'plan', s.has_workbook && 'workbook'].filter(Boolean).join(' + ')}</span>
                  <button className="linkish danger" title="Delete"
                          onClick={() => confirm(`Delete session #${s.id}?`) && del.mutate(s.id)}>delete</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* ---------------------------------------------------------------- plan */}
      <div className="card">
        <div className="card-head">
          <h3>EI lesson plan</h3>
          <div className="card-actions tight">
            <button className="btn ghost" onClick={() => showPrompt('plan')}>Preview prompt</button>
            {busy === 'plan'
              ? <button className="btn" onClick={() => abort.current?.abort()}>Stop</button>
              : <button className="btn" disabled={llmOff || !!busy || !inputs.objectives.trim()} onClick={() => generate('plan')}
                        title={!inputs.objectives.trim() ? 'Add learning objectives first' : ''}>Build EI plan</button>}
          </div>
        </div>
        <p className="small muted">A diagnostic summary, 2 to 3 goals, a time-boxed session plan, practice problems and a study guide.</p>
        {preview?.kind === 'plan' && <PromptPreview p={preview} onClose={() => setPreview(null)} />}
        <Output md={out.plan} streaming={busy === 'plan'} sessionId={sessionId} part="plan" courseId={courseId} studentId={studentId} />
      </div>

      {/* ---------------------------------------------------------------- workbook */}
      <div className="card">
        <div className="card-head">
          <h3>Student workbook</h3>
          <div className="card-actions tight">
            <button className="btn ghost" onClick={() => showPrompt('workbook')}>Preview prompt</button>
            {busy === 'workbook'
              ? <button className="btn" onClick={() => abort.current?.abort()}>Stop</button>
              : <button className="btn" disabled={llmOff || !!busy || selectedKeys.length === 0} onClick={() => generate('workbook')}>Build workbook</button>}
          </div>
        </div>

        <div className="two-col">
          <div>
            <h4>Intervention modules</h4>
            <p className="small muted">Recommended from the signals and your notes. Choose up to 4.
              {chosen && <> <button className="linkish" onClick={() => setChosen(null)}>Reset to recommended</button></>}</p>
            <ul className="modules">
              {(mods ?? []).map((m) => (
                <li key={m.key}>
                  <label className="check"><input type="checkbox" checked={selectedKeys.includes(m.key)} onChange={() => toggleMod(m.key)} />
                    <strong>{m.title}</strong></label>
                  {m.why && <p className="small muted">{m.why}</p>}
                </li>
              ))}
            </ul>
            <details className="notes">
              <summary>Other modules</summary>
              <ul className="modules">
                {extraMods.map((m) => (
                  <li key={m.key}>
                    <label className="check"><input type="checkbox" checked={selectedKeys.includes(m.key)} onChange={() => toggleMod(m.key)} />
                      {m.title}</label>
                    <p className="small muted">{m.when_to_use}</p>
                  </li>
                ))}
              </ul>
            </details>
          </div>
          <div>
            <h4>Format</h4>
            <div className="toolbar">
              <label className="small">Tone{' '}
                <select value={cfg.tone} onChange={(e) => setCfg({ ...cfg, tone: e.target.value })}>
                  {ctx.workbook_options.tones.map((t) => <option key={t}>{t}</option>)}
                </select></label>
              <label className="small">Length{' '}
                <select value={cfg.length} onChange={(e) => setCfg({ ...cfg, length: e.target.value })}>
                  {ctx.workbook_options.lengths.map((t) => <option key={t}>{t}</option>)}
                </select></label>
            </div>
            <div className="section-checks">
              {SECTIONS.map(([k, label]) => (
                <label key={k} className="check small">
                  <input type="checkbox" checked={cfg[k] as boolean} onChange={(e) => setCfg({ ...cfg, [k]: e.target.checked })} /> {label}
                </label>
              ))}
            </div>
            <label className="field">
              <span className="small">Extra instructions for the workbook</span>
              <textarea rows={2} value={cfg.custom_instructor_notes}
                        onChange={(e) => setCfg({ ...cfg, custom_instructor_notes: e.target.value })}
                        placeholder="e.g. Use Python examples; keep it to two pages" />
            </label>
          </div>
        </div>
        {preview?.kind === 'workbook' && <PromptPreview p={preview} onClose={() => setPreview(null)} />}
        <Output md={out.workbook} streaming={busy === 'workbook'} sessionId={sessionId} part="workbook" courseId={courseId} studentId={studentId} />
      </div>

      <details className="card method" open={method} onToggle={(e) => setMethod((e.target as HTMLDetailsElement).open)}>
        <summary><strong>Intervention methodology</strong> <span className="small muted">how triage and modules work</span></summary>
        {methodQ.data ? <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{methodQ.data.markdown}</ReactMarkdown></div>
          : method && <p className="muted">Loading…</p>}
      </details>
    </section>
  )
}
