import { Link } from 'react-router-dom'
import { Card, ErrorNote, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'

export default function History() {
  const { data, loading, waking, error, reload } = useApi((o) => api.listAnalyses(o), [])

  if (loading) return <Loading waking={waking} />
  if (error) return <ErrorNote onRetry={reload}>{error.message}</ErrorNote>

  return (
    <>
      <PageTitle sub="Newest first.">Past analyses</PageTitle>
      {!data || data.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-600">
            No analyses yet.{' '}
            <Link to="/analyze" className="font-medium text-accent-700 underline">
              Analyze a job
            </Link>{' '}
            to see how your resume matches it.
          </p>
        </Card>
      ) : (
        <ul className="space-y-3">
          {data.map((a) => (
            <li key={a.id}>
              <Link
                to={`/analyses/${a.id}`}
                className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-accent-500"
              >
                <span>
                  <span className="block font-medium text-slate-900">
                    {a.job_title ?? 'Untitled job'}
                  </span>
                  <span className="block text-xs text-slate-500">
                    {new Date(a.created_at).toLocaleString()}
                  </span>
                </span>
                <span className="text-2xl font-semibold text-accent-700">
                  {a.final_score?.toFixed(0) ?? '—'}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
