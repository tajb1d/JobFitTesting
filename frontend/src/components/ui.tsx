import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { WAKING_AFTER_MS } from '../lib/api'

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-slate-600" role="status">
      <span
        aria-hidden
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-accent-600"
      />
      {label}
    </span>
  )
}

/** Loading state that explains a slow first request (plan §12: Render's free tier sleeps). */
export function Loading({ waking, label = 'Loading…' }: { waking?: boolean; label?: string }) {
  return (
    <div className="py-10 text-center">
      <Spinner label={waking ? 'Waking up the server…' : label} />
      {waking && (
        <p className="mt-2 text-sm text-slate-500">
          The free server sleeps when idle. This can take up to a minute.
        </p>
      )}
    </div>
  )
}

export function ErrorNote({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <p>{children}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="mt-2 font-medium underline">
          Try again
        </button>
      )}
    </div>
  )
}

export function Button({
  variant = 'primary',
  loading,
  waking,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary'
  loading?: boolean
  waking?: boolean
}) {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60'
  const styles =
    variant === 'primary'
      ? 'bg-accent-600 text-white hover:bg-accent-700'
      : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
  return (
    <button {...props} disabled={props.disabled ?? loading} className={`${base} ${styles} ${props.className ?? ''}`}>
      {loading && (
        <span
          aria-hidden
          className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
        />
      )}
      {loading && waking ? 'Waking up the server…' : children}
    </button>
  )
}

export function Field({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      <input
        {...props}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100"
      />
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      {children}
    </section>
  )
}

export function PageTitle({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="text-2xl font-semibold text-slate-900">{children}</h1>
      {sub && <p className="mt-1 text-sm text-slate-600">{sub}</p>}
    </header>
  )
}

export const wakingAfterSeconds = WAKING_AFTER_MS / 1000
