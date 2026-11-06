import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type CourseList, type CourseSort, type CourseSummary, type HiddenCourse } from '../api'
import { IngestNotes, StatusPill, timeAgo } from '../components/IngestStatus'
import RemoveCourseDialog from '../components/RemoveCourseDialog'
import UploadExports, { HowToExport } from '../components/UploadExports'

const LMS_LABEL: Record<string, string> = { bb: 'Blackboard', canvas: 'Canvas', fixture: 'Demo data' }

const SORTS: { value: CourseSort; label: string }[] = [
  { value: 'recent', label: 'Recently opened' },
  { value: 'custom', label: 'My order' },
  { value: 'name', label: 'Name' },
  { value: 'updated', label: 'Last data update' },
]
const t = (iso: string | null | undefined) => (iso ? new Date(iso).getTime() : 0)
const byName = (a: CourseSummary, b: CourseSummary) => a.name.localeCompare(b.name, undefined, { numeric: true })
const byUpdated = (a: CourseSummary, b: CourseSummary) =>
  t(b.last_success?.finished_at) - t(a.last_success?.finished_at) || byName(a, b)
// Opened courses first (most recent on top), then never-opened ones by their latest data.
const byRecent = (a: CourseSummary, b: CourseSummary) =>
  t(b.last_opened_at) - t(a.last_opened_at) || byUpdated(a, b)

function sortCourses(list: CourseSummary[], mode: CourseSort): CourseSummary[] {
  const out = [...list]
  if (mode === 'name') return out.sort(byName)
  if (mode === 'updated') return out.sort(byUpdated)
  if (mode === 'custom') {
    // Courses added since the order was saved have no position yet: show them first, newest first.
    return out.sort((a, b) => {
      const pa = a.position ?? -1, pb = b.position ?? -1
      if (pa === -1 && pb === -1) return byRecent(a, b)
      return pa - pb
    })
  }
  return out.sort(byRecent)
}

interface ArrangeProps {
  index: number; total: number; dragging: boolean
  onDragStart: () => void; onDragEnter: () => void; onDragEnd: () => void; onMove: (delta: number) => void
}

function CourseCard({ c, arrange }: { c: CourseSummary; arrange?: ArrangeProps }) {
  const qc = useQueryClient()
  const [removing, setRemoving] = useState(false)
  const archive = useMutation({
    mutationFn: () => api.archiveCourse(c.id, true),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['courses'] }),
  })
  const ingest = useMutation({
    mutationFn: () => api.ingest(c.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['courses'] }),
  })
  const r = c.last_success?.report
  if (arrange) {
    return (
      <article className={`card course-card arranging ${arrange.dragging ? 'dragging' : ''}`} draggable
               onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; arrange.onDragStart() }}
               onDragEnter={arrange.onDragEnter} onDragOver={(e) => e.preventDefault()} onDragEnd={arrange.onDragEnd}
               onDrop={(e) => e.preventDefault()}>
        <div className="card-head">
          <span className="grip" aria-hidden="true">⠿</span>
          <span className="small muted">{arrange.index + 1} of {arrange.total}</span>
        </div>
        <h2>{c.name}</h2>
        <p className="muted small"><span className={`lms lms-${c.lms}`}>{LMS_LABEL[c.lms] ?? c.lms}</span> · {c.role}</p>
        <div className="card-actions">
          <button className="btn ghost small-btn" disabled={arrange.index === 0} onClick={() => arrange.onMove(-1)}
                  aria-label={`Move ${c.name} earlier`}>← Earlier</button>
          <button className="btn ghost small-btn" disabled={arrange.index === arrange.total - 1} onClick={() => arrange.onMove(1)}
                  aria-label={`Move ${c.name} later`}>Later →</button>
        </div>
      </article>
    )
  }
  return (
    <article className="card course-card">
      <div className="card-head">
        <span className={`lms lms-${c.lms}`}>{LMS_LABEL[c.lms] ?? c.lms}</span>
        <StatusPill run={c.last_run} />
      </div>
      <h2>{c.has_data ? <Link to={`/courses/${encodeURIComponent(c.id)}`}>{c.name}</Link> : c.name}</h2>
      <p className="muted small">
        Your role: {c.role}
        {r?.students != null && <> · {r.students} students</>}
        {r?.sections?.length ? <> · sections {r.sections.join(', ')}</> : null}
      </p>
      <p className="small">Last data update: <strong>{timeAgo(c.last_success?.finished_at)}</strong>
        {c.last_opened_at && <span className="muted"> · opened {timeAgo(c.last_opened_at)}</span>}</p>
      {c.last_run?.status === 'failed' && <p className="error small">Last ingest failed: {c.last_run.error}</p>}
      <IngestNotes run={c.last_success} />
      <div className="card-actions">
        <button className="btn" onClick={() => ingest.mutate()} disabled={ingest.isPending}>
          {ingest.isPending ? 'Ingesting…' : c.has_data ? 'Ingest latest exports' : 'Ingest exports'}
        </button>
        {c.has_data && <Link className="btn ghost" to={`/courses/${encodeURIComponent(c.id)}`}>Open</Link>}
        {c.lms === 'bb' && <Link className="btn ghost" to={`/courses/${encodeURIComponent(c.id)}/data`}>Upload exports</Link>}
      </div>
      <div className="card-links">
        <button className="linkish" onClick={() => archive.mutate()} disabled={archive.isPending}
                aria-label={`Archive ${c.name}`} title="Move to Archived at the bottom of this page. It still opens.">Archive</button>
        <button className="linkish danger" onClick={() => setRemoving(true)} aria-label={`Remove ${c.name}`}>Remove</button>
      </div>
      {removing && <RemoveCourseDialog courseId={c.id} courseName={c.name} onClose={() => setRemoving(false)} />}
      {ingest.isError && <p className="error small">{(ingest.error as Error).message}</p>}
    </article>
  )
}

function AddCourse({ onClose }: { onClose?: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  return (
    <div className="card add-course">
      <div className="card-head">
        <h3>Add a Blackboard course</h3>
        {onClose && <button className="linkish" onClick={onClose}>Cancel</button>}
      </div>
      <p className="small muted">Upload the course's gradebook exports. You'll be the course's instructor in SPARK, and you can
        upload newer exports from its Data page whenever you want to refresh it.</p>
      <HowToExport />
      <UploadExports mode="new" onDone={(r) => {
        qc.invalidateQueries({ queryKey: ['courses'] })
        if (r.course_id && r.run?.status !== 'failed') navigate(`/courses/${encodeURIComponent(r.course_id)}`)
      }} />
    </div>
  )
}

function ArchivedCourses({ courses }: { courses: CourseSummary[] }) {
  const qc = useQueryClient()
  const unarchive = useMutation({
    mutationFn: (id: string) => api.archiveCourse(id, false),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['courses'] }),
  })
  if (!courses.length) return null
  return (
    <details className="card archived-courses">
      <summary><strong>Archived</strong> <span className="small muted">({courses.length}) · these still open as usual; archiving only tidies your list</span></summary>
      <ul>
        {[...courses].sort((a, b) => t(b.archived_at) - t(a.archived_at)).map((c) => (
          <li key={c.id}>
            <span>
              {c.has_data ? <Link to={`/courses/${encodeURIComponent(c.id)}`}>{c.name}</Link> : c.name}{' '}
              <span className="small muted">{LMS_LABEL[c.lms] ?? c.lms} · archived {timeAgo(c.archived_at)}</span>
            </span>
            <button className="btn ghost small-btn" disabled={unarchive.isPending} onClick={() => unarchive.mutate(c.id)}>Unarchive</button>
          </li>
        ))}
      </ul>
      {unarchive.isError && <p className="error small">{(unarchive.error as Error).message}</p>}
    </details>
  )
}

function RemovedCourses({ courses }: { courses: HiddenCourse[] }) {
  const qc = useQueryClient()
  const restore = useMutation({
    mutationFn: (id: string) => api.restoreCourse(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['courses'] }),
  })
  if (!courses.length) return null
  return (
    <details className="card removed-courses">
      <summary><strong>Removed courses</strong> <span className="small muted">({courses.length})</span></summary>
      <ul>
        {courses.map((c) => (
          <li key={c.id}>
            <span>{c.name} <span className="small muted">{LMS_LABEL[c.lms] ?? c.lms}</span></span>
            <button className="btn ghost small-btn" disabled={restore.isPending} onClick={() => restore.mutate(c.id)}>Restore</button>
          </li>
        ))}
      </ul>
      {restore.isError && <p className="error small">{(restore.error as Error).message}</p>}
    </details>
  )
}

export default function CoursesPage() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['courses'], queryFn: api.courses })
  const [adding, setAdding] = useState(false)
  const [order, setOrder] = useState<string[] | null>(null)       // non-null while arranging
  const [drag, setDrag] = useState<number | null>(null)

  const sortMode = useMutation({
    mutationFn: (mode: CourseSort) => api.setCourseSort(mode),
    onMutate: (mode) => qc.setQueryData<CourseList>(['courses'], (d) => (d ? { ...d, course_sort: mode } : d)),
    onSettled: () => qc.invalidateQueries({ queryKey: ['courses'] }),
  })
  const saveOrder = useMutation({
    mutationFn: (ids: string[]) => api.setCourseOrder(ids),
    onSuccess: () => { setOrder(null); qc.invalidateQueries({ queryKey: ['courses'] }) },
  })

  if (q.isLoading) return <p className="muted">Loading courses…</p>
  if (q.error) return <p className="error">{(q.error as Error).message}</p>
  const { courses: all, can_add_courses: canAdd, hidden_courses: hidden, course_sort: mode } = q.data!
  const active = sortCourses(all.filter((c) => !c.archived), mode)
  const archived = all.filter((c) => c.archived)
  const courses = active
  const byId = new Map(all.map((c) => [c.id, c]))
  const arranged = order ? order.map((id) => byId.get(id)).filter((c): c is CourseSummary => !!c) : null

  const move = (from: number, to: number) => setOrder((o) => {
    if (!o || to < 0 || to >= o.length || from === to) return o
    const next = [...o]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    return next
  })

  return (
    <section>
      <div className="page-head">
        <h1>Your courses</h1>
        <div className="controls">
          {active.length > 1 && !arranged && (
            <>
              <label className="small inline">Sort
                <select value={mode} onChange={(e) => sortMode.mutate(e.target.value as CourseSort)}>
                  {SORTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <button className="btn ghost" onClick={() => setOrder(active.map((c) => c.id))}>Arrange</button>
            </>
          )}
          {arranged && (
            <>
              <button className="btn ghost" onClick={() => setOrder(null)} disabled={saveOrder.isPending}>Cancel</button>
              <button className="btn" onClick={() => saveOrder.mutate(order!)} disabled={saveOrder.isPending}>
                {saveOrder.isPending ? 'Saving…' : 'Save order'}</button>
            </>
          )}
          {canAdd && all.length > 0 && !adding && !arranged && <button className="btn" onClick={() => setAdding(true)}>Add a course</button>}
        </div>
      </div>
      {arranged && <p className="small muted arrange-hint">Drag the cards, or use Earlier and Later, then Save order. Your list will
        then show <strong>My order</strong>; you can switch back to another sort at any time. Archived courses aren't included.</p>}
      {saveOrder.isError && <p className="error small">{(saveOrder.error as Error).message}</p>}
      {adding && <AddCourse onClose={() => setAdding(false)} />}
      {arranged ? (
        <div className="course-grid">
          {arranged.map((c, i) => (
            <CourseCard key={c.id} c={c} arrange={{
              index: i, total: arranged.length, dragging: drag === i,
              onDragStart: () => setDrag(i),
              onDragEnter: () => { if (drag !== null && drag !== i) { move(drag, i); setDrag(i) } },
              onDragEnd: () => setDrag(null),
              onMove: (d) => move(i, i + d),
            }} />
          ))}
        </div>
      ) : all.length === 0 ? (
        canAdd ? <AddCourse /> : (
        <div className="card empty">
          <h2>No courses assigned</h2>
          <p className="muted">Your account signed in successfully but has no course access yet. An admin can grant access.</p>
        </div>)
      ) : courses.length === 0 ? (
        <div className="card empty"><p className="muted">All your courses are archived. Open <strong>Archived</strong> below to bring one back.</p></div>
      ) : (
        <div className="course-grid">{courses.map((c) => <CourseCard key={c.id} c={c} />)}</div>
      )}
      {!arranged && <ArchivedCourses courses={archived} />}
      {!arranged && <RemovedCourses courses={hidden ?? []} />}
    </section>
  )
}
