import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import AppLayout from './components/AppLayout'
import ErrorBoundary from './components/ErrorBoundary'
import AnalysisPage from './pages/Analysis'
import Analyze from './pages/Analyze'
import Dashboard from './pages/Dashboard'
import History from './pages/History'
import Login from './pages/Login'
import Onboarding from './pages/Onboarding'
import Signup from './pages/Signup'
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
              <Route element={<RequireResume />}>
                <Route element={<AppLayout />}>
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/analyze" element={<Analyze />} />
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
