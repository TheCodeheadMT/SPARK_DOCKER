import Plotly from 'plotly.js-dist-min'
import { useEffect, useRef } from 'react'
import { cssVar, useTheme } from '../theme'

// Renders a figure produced server-side by spark_core (fig.to_json()).
export default function Plot({ figure, height }: { figure: { data: unknown[]; layout: Record<string, unknown> }; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const theme = useTheme()

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const h = height ?? (figure.layout.height as number | undefined) ?? 380
    // Follow the site theme: transparent backgrounds, theme text and grid colours on every axis.
    const ink = cssVar('--plot-ink') || '#14213d', grid = cssVar('--plot-grid') || '#e3e8f0'
    const layout: Record<string, unknown> = { ...figure.layout, autosize: true, height: h,
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { ...((figure.layout.font as object) ?? {}), color: ink } }
    for (const k of Object.keys(layout)) {
      if (/^[xy]axis\d*$/.test(k) || k === 'scene') {
        const ax = { ...(layout[k] as object) } as Record<string, unknown>
        if (k === 'scene') {
          for (const a of ['xaxis', 'yaxis', 'zaxis']) ax[a] = { ...((ax[a] as object) ?? {}), gridcolor: grid, color: ink }
        } else {
          const title = ax.title && typeof ax.title === 'object' ? ax.title as Record<string, unknown> : (ax.title ? { text: ax.title } : undefined)
          Object.assign(ax, { gridcolor: grid, zerolinecolor: grid, linecolor: grid, color: ink,
            tickfont: { ...((ax.tickfont as object) ?? {}), color: ink },
            ...(title ? { title: { ...title, font: { ...((title.font as object) ?? {}), color: ink } } } : {}) })
        }
        layout[k] = ax
      }
    }
    if (Array.isArray(layout.annotations))
      layout.annotations = (layout.annotations as Record<string, unknown>[]).map((a) =>
        ({ ...a, font: { ...((a.font as object) ?? {}), color: (a.font as { color?: string })?.color ?? ink } }))
    if (layout.legend) layout.legend = { ...(layout.legend as object), bgcolor: 'rgba(0,0,0,0)', font: { color: ink } }
    Plotly.react(el, figure.data as Plotly.Data[], layout as Partial<Plotly.Layout>, { responsive: true, displaylogo: false })
    return () => {
      Plotly.purge(el)
    }
  }, [figure, height, theme])

  return <div ref={ref} className="plot" />
}
