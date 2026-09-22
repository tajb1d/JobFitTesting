import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth, useResumes } from '../auth/context'
import { ResumeProvider } from '../auth/ResumeProvider'
import { ErrorNote, Loading } from '../components/ui'

/** Signed out → /login, remembering where they were going (plan §12). */
export function RequireAuth() {
  const { session, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Loading />
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return (
    <ResumeProvider>
      <Outlet />
    </ResumeProvider>
  )
}

/** Signed in but no resume yet → /onboarding. Wraps everything except /onboarding itself. */
export function RequireResume() {
  const { active, loading, error, reload } = useResumes()
  if (loading) return <Loading />
  if (error) return <ErrorNote onRetry={reload}>{error.message}</ErrorNote>
  if (!active) return <Navigate to="/onboarding" replace />
  return <Outlet />
}
