import { useEffect, useRef, useState } from 'react'
import { setTheme, THEMES, useTheme } from '../theme'

/** Header button that opens a small menu of the site themes, each with its colour swatch. */
export default function ThemePicker() {
  const current = useTheme()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !box.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [open])

  const active = THEMES.find((t) => t.key === current)!
  return (
    <div className="theme-picker" ref={box}>
      <button className="btn ghost theme-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
              title="Change the site's colour theme">
        <Swatch colors={active.swatch} /> Theme
      </button>
      {open && (
        <div className="theme-menu" role="menu">
          {THEMES.map((t) => (
            <button key={t.key} role="menuitemradio" aria-checked={t.key === current}
                    className={`theme-item ${t.key === current ? 'on' : ''}`}
                    onClick={() => { setTheme(t.key); setOpen(false) }}>
              <Swatch colors={t.swatch} />
              <span><strong>{t.name}</strong><span className="small">{t.note}</span></span>
            </button>
          ))}
          <p className="small theme-foot">Saved in this browser.</p>
        </div>
      )}
    </div>
  )
}

function Swatch({ colors }: { colors: readonly string[] }) {
  return <span className="swatch" aria-hidden="true">{colors.map((c) => <i key={c} style={{ background: c }} />)}</span>
}
