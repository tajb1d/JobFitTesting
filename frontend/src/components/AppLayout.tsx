import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/context'

const LINKS = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/analyze', label: 'Analyze' },
  { to: '/history', label: 'History' },
]

export default function AppLayout() {
  const { signOut } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <NavLink to="/dashboard" className="text-lg font-bold text-accent-700">
            JobFit
          </NavLink>
          <nav className="flex gap-4 text-sm">
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  isActive ? 'font-medium text-accent-700' : 'text-slate-600 hover:text-slate-900'
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
          <button
            type="button"
            onClick={async () => {
              await signOut()
              navigate('/login', { replace: true })
            }}
            className="ml-auto text-sm text-slate-600 underline hover:text-slate-900"
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  )
}
