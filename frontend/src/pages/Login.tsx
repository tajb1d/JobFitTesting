import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/context'
import { Button, ErrorNote, Field } from '../components/ui'
import { supabaseConfigError } from '../lib/supabase'

export default function Login() {
  const { session, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: string } }
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(supabaseConfigError)
  const [busy, setBusy] = useState(false)

  if (session) return <Navigate to={location.state?.from ?? '/dashboard'} replace />

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(email, password)
      navigate(location.state?.from ?? '/dashboard', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Sign in" footer={<>New here? <Link className="font-medium text-accent-700 underline" to="/signup">Create an account</Link></>}>
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Field label="Email" type="email" autoComplete="email" required value={email}
               onChange={(e) => setEmail(e.target.value)} />
        <Field label="Password" type="password" autoComplete="current-password" required
               value={password} onChange={(e) => setPassword(e.target.value)} />
        <Button type="submit" loading={busy} className="w-full">Sign in</Button>
      </form>
    </AuthLayout>
  )
}

export function AuthLayout({
  title,
  children,
  footer,
}: {
  title: string
  children: React.ReactNode
  footer?: React.ReactNode
}) {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 py-10">
      <div className="mb-6 text-center">
        <p className="text-2xl font-bold text-accent-700">JobFit</p>
        <p className="mt-1 text-sm text-slate-600">
          See how your resume matches a job, and what to add.
        </p>
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="mb-4 text-lg font-semibold text-slate-900">{title}</h1>
        {children}
      </div>
      {footer && <p className="mt-4 text-center text-sm text-slate-600">{footer}</p>}
    </main>
  )
}
