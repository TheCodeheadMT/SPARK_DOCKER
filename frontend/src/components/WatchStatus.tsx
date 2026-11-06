import type { Watch } from '../api'

const WATCH_LABELS: Record<string, [string, string]> = {
  up_to_date: ['Up to date', 'ok'],
  ingested: ['Ingested', 'ok'],
  waiting: ['New export settling', 'warn'],
  ingesting: ['Ingesting', 'warn'],
  busy: ['Busy', 'warn'],
  no_gradebook: ['No gradebook export', 'warn'],
  failed: ['Failed', 'bad'],
  mount_error: ['Mount error', 'bad'],
  new: ['Not checked yet', ''],
}

export function WatchPill({ status }: { status: string }) {
  const [label, cls] = WATCH_LABELS[status] ?? [status, '']
  return <span className={`pill ${cls}`}>{label}</span>
}

export function WatchFiles({ w }: { w: Watch }) {
  const f = (k: string) => {
    const x = w.files?.[k]
    return x ? `${x.name} (${(x.size / 1024).toFixed(0)} KB, ${new Date(x.mtime * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })})` : 'none'
  }
  return <span className="small muted">gradebook {f('gradebook')} · history {f('grade_history')}{w.files?.manifest ? ' · manifest' : ''}</span>
}
