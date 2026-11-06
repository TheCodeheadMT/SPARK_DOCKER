import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

/** Confirmation dialog: remove a course from your list, or delete it from SPARK. */
export default function RemoveCourseDialog({ courseId, courseName, onClose }:
  { courseId: string; courseName: string; onClose: () => void }) {
  const qc = useQueryClient()
  const info = useQuery({ queryKey: ['removal', courseId], queryFn: () => api.removalInfo(courseId) })
  const [choice, setChoice] = useState<'hide' | 'delete' | null>(null)
  const [typed, setTyped] = useState('')
  const dialog = useRef<HTMLDivElement>(null)

  const done = () => { qc.invalidateQueries({ queryKey: ['courses'] }); onClose() }
  const hide = useMutation({ mutationFn: () => api.hideCourse(courseId), onSuccess: done })
  const del = useMutation({ mutationFn: () => api.deleteCourse(courseId, typed.trim()), onSuccess: done })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    dialog.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const d = info.data
  // Default to the only option available.
  const selected = choice ?? (d ? (d.can_hide ? 'hide' : d.can_delete ? 'delete' : null) : null)
  const busy = hide.isPending || del.isPending
  const err = (hide.error ?? del.error) as Error | null

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="remove-title" tabIndex={-1} ref={dialog}>
        <h2 id="remove-title">Remove {courseName}?</h2>
        {info.isLoading && <p className="muted">Checking what this affects…</p>}
        {info.error && <p className="error">{(info.error as Error).message}</p>}
        {d && (
          <>
            <div className="choices">
              {d.can_hide && (
                <label className={`choice ${selected === 'hide' ? 'on' : ''}`}>
                  <input type="radio" name="rm" checked={selected === 'hide'} onChange={() => setChoice('hide')} />
                  <span>
                    <strong>Remove from my courses</strong>
                    <span className="small muted">
                      The course disappears from your list and you can't open it until you restore it (from
                      "Removed courses" on this page). Nothing is deleted.
                      {d.others_with_access > 0 && ` ${plural(d.others_with_access, 'other person')} keep${d.others_with_access === 1 ? 's' : ''} access.`}
                    </span>
                  </span>
                </label>
              )}
              <label className={`choice danger ${selected === 'delete' ? 'on' : ''} ${d.can_delete ? '' : 'disabled'}`}>
                <input type="radio" name="rm" disabled={!d.can_delete} checked={selected === 'delete'} onChange={() => setChoice('delete')} />
                <span>
                  <strong>Delete from SPARK permanently</strong>
                  {d.can_delete ? (
                    <span className="small muted">Removes the course for everyone, with all of its data. This can't be undone.</span>
                  ) : <span className="small muted">{d.delete_blocked_reason}</span>}
                </span>
              </label>
            </div>

            {selected === 'delete' && d.can_delete && (
              <div className="delete-confirm">
                <p className="small"><strong>This permanently deletes:</strong></p>
                <ul className="small">
                  <li>the course data{d.impact.students != null ? ` for ${plural(d.impact.students, 'student')}` : ''} and {plural(d.impact.ingest_runs, 'update')} of history</li>
                  <li>uploaded Blackboard exports and SPARK's copies of them</li>
                  {d.impact.success_models > 0 && <li>{plural(d.impact.success_models, 'trained success model')}</li>}
                  {d.impact.ei_sessions > 0 && <li>{plural(d.impact.ei_sessions, 'saved Extra Instruction session')}</li>}
                  <li>zyBooks data and everyone's access to the course</li>
                </ul>
                {d.impact.models_reused_by.length > 0 && (
                  <p className="small warn-text">
                    {d.impact.models_reused_by.map((c) => c.name).join(', ')} {d.impact.models_reused_by.length === 1 ? 'uses' : 'use'} this
                    course's success model and will stop showing success probabilities until {d.impact.models_reused_by.length === 1 ? 'it trains' : 'they train'} or
                    reuse another.
                  </p>
                )}
                <p className="small muted">The audit log of who viewed student records is kept.</p>
                <label className="field">
                  <span className="small">Type <code>{d.course.code}</code> to confirm</span>
                  <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false}
                         aria-label="Course ID to confirm deletion" />
                </label>
              </div>
            )}
          </>
        )}
        {err && <p className="error small">{err.message}</p>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
          {selected === 'hide' && (
            <button className="btn" onClick={() => hide.mutate()} disabled={busy}>
              {hide.isPending ? 'Removing…' : 'Remove from my courses'}</button>
          )}
          {selected === 'delete' && (
            <button className="btn danger" onClick={() => del.mutate()}
                    disabled={busy || !d?.can_delete || typed.trim() !== d?.course.code}>
              {del.isPending ? 'Deleting…' : 'Delete permanently'}</button>
          )}
        </div>
      </div>
    </div>
  )
}
