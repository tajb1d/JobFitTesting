import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/context'
import { Button, ErrorNote, Field } from '../components/ui'
import { supabaseConfigError } from '../lib/supabase'
import { AuthLayout } from './Login'

const MIN_PASSWORD = 8

export default function Signup() {
  const { session, signUp } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(supabaseConfigError)
  const [confirmSent, setConfirmSent] = useState(false)
  const [busy, setBusy] = useState(false)

  if (session) return <Navigate to="/onboarding" replace />

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (password.length < MIN_PASSWORD) {
      setError(`Use at least ${MIN_PASSWORD} characters for your password.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      // false means this Supabase project requires email confirmation before signing in.
      if (await signUp(email, password)) navigate('/onboarding', { replace: true })
      else setConfirmSent(true)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (confirmSent) {
    return (
      <AuthLayout title="Check your email">
        <p className="text-sm text-slate-700">
          We sent a confirmation link to <strong>{email}</strong>. Open it, then sign in.
        </p>
        <Link to="/login" className="mt-4 inline-block text-sm font-medium text-accent-700 underline">
          Go to sign in
        </Link>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title="Create your account" footer={<>Already have one? <Link className="font-medium text-accent-700 underline" to="/login">Sign in</Link></>}>
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Field label="Email" type="email" autoComplete="email" required value={email}
               onChange={(e) => setEmail(e.target.value)} />
        <Field label="Password" type="password" autoComplete="new-password" required
               minLength={MIN_PASSWORD} hint={`At least ${MIN_PASSWORD} characters.`}
               value={password} onChange={(e) => setPassword(e.target.value)} />
        <Button type="submit" loading={busy} className="w-full">Create account</Button>
      </form>
    </AuthLayout>
  )
}
