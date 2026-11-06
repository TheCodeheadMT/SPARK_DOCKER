import { Component, type ReactNode } from 'react'

// Shows the real error instead of a blank page. A failed lazy-chunk load usually means
// SPARK was updated while this tab was open, so offer a reload.
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    const e = this.state.error
    if (!e) return this.props.children
    const stale = /dynamically imported module|Loading chunk|Importing a module script failed/i.test(e.message)
    return (
      <div className="card">
        <h2>{stale ? 'SPARK was updated' : 'Something went wrong on this page'}</h2>
        <p>{stale ? 'This tab is running an older version. Reload to continue.' : 'The error was:'}</p>
        {!stale && <pre className="error-detail">{e.message}</pre>}
        <button className="btn" onClick={() => window.location.reload()}>Reload</button>
      </div>
    )
  }
}
