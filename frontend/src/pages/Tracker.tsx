import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApplyLink, JobMeta } from '../components/job'
import { Card, ErrorNote, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'
import type { Application, ApplicationStatus } from '../types/api'

const STATUSES: { value: ApplicationStatus; label: string }[] = [
  { value: 'saved', label: 'Saved' },
  { value: 'applied', label: 'Applied' },
  { value: 'interviewing', label: 'Interviewing' },
  { value: 'offer', label: 'Offer' },
  { value: 'rejected', label: 'Rejected' },
]

export default function Tracker() {
  const { data, loading, waking, error, reload } = useApi((o) => api.listApplications(o), [])

  if (loading) return <Loading waking={waking} />
  if (error) return <ErrorNote onRetry={reload}>{error.message}</ErrorNote>

  const applications = data ?? []

  return (
    <>
      <PageTitle sub="Jobs you saved, and where each one stands.">Tracker</PageTitle>

      {applications.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-700">Nothing saved yet.</p>
          <p className="mt-2 text-sm text-slate-600">
            Save a job from{' '}
            <Link to="/jobs" className="font-medium text-accent-700 underline">
              Jobs for you
            </Link>{' '}
            and it will appear here.
          </p>
        </Card>
      ) : (
        STATUSES.map(({ value, label }) => {
          const group = applications.filter((a) => a.status === value)
          if (group.length === 0) return null
          return (
            <section key={value} className="mb-6">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
                {label} ({group.length})
              </h2>
              <ul className="space-y-3">
                {group.map((application) => (
                  <li key={application.id}>
                    <Row application={application} onChange={reload} />
                  </li>
                ))}
              </ul>
            </section>
          )
        })
      )}
    </>
  )
}

function Row({ application, onChange }: { application: Application; onChange: () => void }) {
  const [notes, setNotes] = useState(application.notes ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function update(changes: { status?: ApplicationStatus; notes?: string | null }) {
    setBusy(true)
    setError(null)
    try {
      await api.updateApplication(application.id, changes)
      onChange()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!confirm(`Remove ${application.job.title} from your tracker?`)) return
    setBusy(true)
    try {
      await api.deleteApplication(application.id)
      onChange()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to={`/jobs/${application.job.id}`}
            className="font-medium text-slate-900 hover:underline"
          >
            {application.job.title}
          </Link>
          <JobMeta job={application.job} />
        </div>
        <div className="flex items-center gap-2">
          <label className="sr-only" htmlFor={`status-${application.id}`}>
            Status for {application.job.title}
          </label>
          <select
            id={`status-${application.id}`}
            value={application.status}
            disabled={busy}
            onChange={(e) => update({ status: e.target.value as ApplicationStatus })}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100"
          >
            {STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <ApplyLink job={application.job} variant="secondary" size="sm" label="Apply ↗" />
        </div>
      </div>

      <textarea
        value={notes}
        disabled={busy}
        onChange={(e) => setNotes(e.target.value)}
        onBlur={() => {
          if (notes !== (application.notes ?? '')) update({ notes: notes.trim() || null })
        }}
        rows={2}
        placeholder="Notes: recruiter name, interview dates, what to prepare…"
        className="mt-3 w-full rounded-lg border border-slate-200 p-2 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100"
      />

      <div className="mt-2 flex items-center justify-between">
        <span className="text-xs text-slate-400">
          Updated {new Date(application.updated_at).toLocaleDateString()}
        </span>
        <button
          type="button"
          onClick={remove}
          disabled={busy}
          className="text-xs text-slate-500 underline hover:text-red-700"
        >
          Remove
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </Card>
  )
}
