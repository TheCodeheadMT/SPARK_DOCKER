import { useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api, ApiError } from './api'
import ErrorBoundary from './components/ErrorBoundary'
import ThemePicker from './components/ThemePicker'
import AdminPage from './pages/AdminPage'
import AnalyticsPage from './pages/AnalyticsPage'
import CoursePage from './pages/CoursePage'
import CoursesPage from './pages/CoursesPage'
import DataPage from './pages/DataPage'
import DirectorPage from './pages/DirectorPage'
import LoginPage from './pages/LoginPage'
import StudentPage from './pages/StudentPage'
// The EI page pulls in the markdown renderer; load it only when opened.
const EIPage = lazy(() => import('./pages/EIPage'))

export default function App() {
  const cfg = useQuery({ queryKey: ['auth-config'], queryFn: api.authConfig })
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const qc = useQueryClient()
  const location = useLocation()

  if (me.isLoading || cfg.isLoading) return <div className="center muted">Loading…</div>
  const signedOut = me.error instanceof ApiError && me.error.status === 401
  const user = me.data?.user

  const signOut = async () => {
    try { await api.logout() } catch { /* already signed out */ }
    qc.clear()
    // Full reload of the start page: it fetches the sign-in options again and leaves no course or
    // student data from the previous session in memory.
    window.location.replace('/login')
  }

  return (
    <div className="app">
      {cfg.data?.mode === 'simulated' && (
        <div className="sim-banner" role="status">
          Simulated Microsoft 365 sign-in ({cfg.data.env}). Not for real student data outside approved systems.
        </div>
      )}
      {cfg.data?.mode === 'local' && (
        <div className="sim-banner" role="status">
          Local mode: no sign-in. Anyone who can open this address can see student data, so run it only on your own
          computer or a private network.
        </div>
      )}
      <header className="topbar">
        <Link to="/" className="brand">
          <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" style={{ fill: 'var(--brand-tile)' }}/><path d="M18 4 8 18h7l-2 10 11-15h-7z" style={{ fill: 'var(--brand-bolt)' }}/></svg>
          <span>SPARK</span><small>BB Edition</small>
        </Link>
        <div className="who">
          <ThemePicker />
          {user && (
            <>
              {user.is_admin && <Link to="/admin" className="admin-link">Admin</Link>}
              {cfg.data?.mode === 'local' ? (
                <>
                  <span className="pill">{user.is_admin ? 'Admin mode' : 'Instructor mode'}</span>
                  <button className="btn ghost" onClick={signOut}>Sign out</button>
                </>
              ) : (
                <>
                  <span className="who-name">{user.name}</span>
                  <span className="pill">{user.roles.join(', ')}</span>
                  <button className="btn ghost" onClick={signOut}>Sign out</button>
                </>
              )}
            </>
          )}
        </div>
      </header>
      <main className="content">
        <ErrorBoundary key={location.pathname}>
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage personas={cfg.data?.personas ?? []} mode={cfg.data?.mode ?? ''} />} />
          {signedOut || !user ? (
            <Route path="*" element={<Navigate to="/login" replace />} />
          ) : (
            <>
              <Route path="/" element={<CoursesPage />} />
              <Route path="/courses/:courseId" element={<CoursePage />} />
              <Route path="/courses/:courseId/analytics" element={<AnalyticsPage />} />
              <Route path="/courses/:courseId/director" element={<DirectorPage />} />
              <Route path="/courses/:courseId/data" element={<DataPage />} />
              {user.is_admin && <Route path="/admin" element={<AdminPage />} />}
              <Route path="/courses/:courseId/students/:studentId" element={<StudentPage />} />
              <Route path="/courses/:courseId/students/:studentId/ei" element={<Suspense fallback={<p className="muted">Loading…</p>}><EIPage /></Suspense>} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </>
          )}
        </Routes>
        </ErrorBoundary>
      </main>
    </div>
  )
}
