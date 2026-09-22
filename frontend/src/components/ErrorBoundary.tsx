import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

/** Last resort: a render crash shows a message instead of a blank page. */
export default class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled error', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="mx-auto max-w-md px-4 py-16 text-center">
        <h1 className="text-xl font-semibold text-slate-900">Something went wrong</h1>
        <p className="mt-2 text-sm text-slate-600">{this.state.error.message}</p>
        <button
          type="button"
          onClick={() => window.location.assign('/dashboard')}
          className="mt-4 rounded-lg bg-accent-600 px-4 py-2 text-sm font-medium text-white"
        >
          Back to dashboard
        </button>
      </main>
    )
  }
}
