import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense, type ReactNode } from 'react'
import { api } from '../api'

// Plotly is large; it loads once, the first time any chart is on screen.
const Plot = lazy(() => import('./Plot'))

/** Fetches a server-rendered Plotly figure (spark_core plotting) and draws it. */
export default function FigureCard({ url, title, height, children, className = '' }:
  { url: string | null; title?: string; height?: number; children?: ReactNode; className?: string }) {
  const fig = useQuery({ queryKey: ['fig', url], queryFn: () => api.figureAt(url!), enabled: !!url })
  return (
    <div className={`card ${className}`}>
      {(title || children) && (
        <div className="card-head">
          {title && <h3>{title}</h3>}
          {children}
        </div>
      )}
      {!url ? null : fig.isLoading ? <p className="muted">Drawing…</p>
        : fig.error ? <p className="muted small">{(fig.error as Error).message}</p>
        : fig.data ? (
          <Suspense fallback={<p className="muted">Loading chart library…</p>}>
            <Plot figure={fig.data} height={height} />
          </Suspense>
        ) : null}
    </div>
  )
}
