import { Link } from 'react-router-dom'
import { useResumes } from '../auth/context'
import { Card, ErrorNote, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'
import type { Severity } from '../types/api'

const SEVERITY_STYLES: Record<Severity, string> = {
  high: 'border-red-200 bg-red-50 text-red-900',
  medium: 'border-amber-200 bg-amber-50 text-amber-900',
  low: 'border-slate-200 bg-slate-50 text-slate-700',
}

export default function Dashboard() {
  const { active } = useResumes()
  const resumeId = active?.id
  const { data, loading, waking, error, reload } = useApi(
    (o) => api.getResume(resumeId!, o),
    [resumeId],
  )

  if (loading) return <Loading waking={waking} />
  if (error) return <ErrorNote onRetry={reload}>{error.message}</ErrorNote>
  if (!data) return null

  const score = Math.round((data.structure_score ?? 0) * 100)
  const failed = data.structure_checks.filter((c) => !c.passed)

  return (
    <>
      <PageTitle sub={`From ${data.filename}. This score is about your resume alone, not any one job.`}>
        Your resume
      </PageTitle>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card className="sm:col-span-1">
          <p className="text-sm font-medium text-slate-600">Structure score</p>
          <p className="mt-1 text-4xl font-bold text-accent-700">
            {score}
            <span className="text-lg font-medium text-slate-400"> / 100</span>
          </p>
          <p className="mt-2 text-xs text-slate-500">
            {data.structure_checks.filter((c) => c.passed).length} of {data.structure_checks.length}{' '}
            checks passed
          </p>
        </Card>

        <Card className="sm:col-span-2">
          <h2 className="text-sm font-medium text-slate-600">What to fix first</h2>
          {data.general_feedback.length === 0 ? (
            <p className="mt-2 text-sm text-slate-700">
              Every check passed. Run an analysis against a job to see role-specific gaps.
            </p>
          ) : (
            <ul className="mt-3 space-y-2">
              {data.general_feedback.map((item, i) => (
                <li key={i} className={`rounded-lg border p-3 text-sm ${SEVERITY_STYLES[item.severity]}`}>
                  {item.message}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {failed.length > 0 && (
        <Card className="mt-4">
          <h2 className="text-sm font-medium text-slate-600">Checks not met</h2>
          <ul className="mt-3 divide-y divide-slate-100">
            {failed.map((check) => (
              <li key={check.id} className="flex items-start justify-between gap-4 py-2 text-sm">
                <span>
                  <span className="font-medium text-slate-800">{check.label}</span>
                  <span className="block text-slate-600">{check.detail}</span>
                </span>
                <span className="shrink-0 text-slate-400">0 / {check.max_points}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card className="mt-4">
        <h2 className="text-sm font-medium text-slate-600">Next</h2>
        <div className="mt-3 flex flex-wrap gap-3">
          <Link
            to="/analyze"
            className="rounded-lg bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700"
          >
            Analyze a job
          </Link>
          <Link
            to="/history"
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Past analyses
          </Link>
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Skills we found: {data.skills.slice(0, 12).map((s) => s.canonical).join(', ') || 'none yet'}
        </p>
      </Card>
    </>
  )
}
