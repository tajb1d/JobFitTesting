import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import AppLayout from './components/AppLayout'
import ErrorBoundary from './components/ErrorBoundary'
import AnalysisPage from './pages/Analysis'
import Analyze from './pages/Analyze'
import Dashboard from './pages/Dashboard'
import History from './pages/History'
import JobDetail from './pages/JobDetail'
import Jobs from './pages/Jobs'
import Login from './pages/Login'
import Onboarding from './pages/Onboarding'
import Settings from './pages/Settings'
import Signup from './pages/Signup'
import Tracker from './pages/Tracker'
import { RequireAuth, RequireResume } from './routes/ProtectedRoute'

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />

            <Route element={<RequireAuth />}>
              <Route path="/onboarding" element={<Onboarding />} />
              <Route element={<AppLayout />}>
                {/* Reachable without a resume, so an account can always be deleted. */}
                <Route path="/settings" element={<Settings />} />
                <Route element={<RequireResume />}>
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/jobs" element={<Jobs />} />
                  <Route path="/jobs/:id" element={<JobDetail />} />
                  <Route path="/analyze" element={<Analyze />} />
                  <Route path="/tracker" element={<Tracker />} />
                  <Route path="/analyses/:id" element={<AnalysisPage />} />
                  <Route path="/history" element={<History />} />
                </Route>
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
