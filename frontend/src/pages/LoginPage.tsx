import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Persona } from '../api'

const MODE_ICON: Record<string, string> = {
  // Simple line icons (original artwork): a mortarboard-free "roster" and a gear-free "control panel".
  instructor: 'M4 6h16M4 12h10M4 18h7M17 14l2 2 4-4',
  admin: 'M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3zM9 12l2 2 4-4',
}

export default function LoginPage({ personas, mode }: { personas: Persona[]; mode: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const enter = async (key: string) => {
    setBusy(key)
    setError(null)
    try {
      await api.simLogin(key)
      await qc.invalidateQueries()
      navigate('/')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const local = mode === 'local'
  return (
    <section className="login">
      <div className="hero">
        <p className="hero-kicker">Student Performance Analysis &amp; Reporting Kit</p>
        <h1>SPARK</h1>
        <p className="hero-lede">See every cadet's trajectory early, find who needs help, and turn the data into a
          plan for the next Extra Instruction session.</p>
      </div>

      <h2 className="login-title">{local ? 'Choose a mode' : 'Sign in'}</h2>
      {!local && (
        <p className="muted small">These are test personas (AUTH_MODE=simulated). For just Instructor mode and Admin mode,
          set <code>AUTH_MODE=local</code> in <code>.env</code> (or remove that line) and restart the backend.</p>
      )}
      {personas.length === 0 && <p className="muted">Loading…</p>}
      <div className={local ? 'mode-grid' : 'persona-grid'}>
        {personas.map((p) => (
          <button key={p.key} className={local ? 'mode-card' : 'persona'} onClick={() => enter(p.key)} disabled={!!busy}>
            {local ? (
              <>
                <svg className="mode-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={MODE_ICON[p.key] ?? MODE_ICON.instructor} /></svg>
                <span className="mode-body">
                  <strong>{p.key === 'admin' ? 'Admin mode' : 'Instructor mode'}</strong>
                  <span className="small">{p.description}</span>
                </span>
                <span className="mode-go">{busy === p.key ? 'Opening…' : 'Enter →'}</span>
              </>
            ) : (
              <span className="persona-body">
                <strong>{p.name}</strong>
                <span className="muted small">{p.upn}</span>
                <span className="small">{p.description}</span>
                <span className="pill">{p.roles.join(', ')}</span>
              </span>
            )}
          </button>
        ))}
      </div>
      {local && <p className="small muted">No password needed. Sign out from the top bar to come back here and switch.</p>}
      {error && <p className="error">{error}</p>}
    </section>
  )
}
