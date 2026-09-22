import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useResumes } from '../auth/context'
import { ApplyLink, ClosedBadge, JobMeta, RemoteBadge } from '../components/job'
import { Button, Card, ErrorNote, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'

export default function JobDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { active } = useResumes()
  const { data: job, loading, waking, error, reload } = useApi((o) => api.getJob(id!, o), [id])
  const applications = useApi((o) => api.listApplications(o), [])

  const [actionError, setActionError] = useState<string | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [savedState, setSavedState] = useState<'unknown' | 'saving' | 'saved'>('unknown')
  const alreadySaved =
    savedState === 'saved' || (applications.data ?? []).some((a) => a.job.id === id)

  if (loading) return <Loading waking={waking} label="Loading job…" />
  if (error) return <ErrorNote onRetry={reload}>{error.message}</ErrorNote>
  if (!job) return null

  async function analyze() {
    if (!active || !job) return
    setAnalyzing(true)
    setActionError(null)
    try {
      const analysis = await api.createAnalysis({ resume_id: active.id, job_id: job.id })
      navigate(`/analyses/${analysis.id}`)
    } catch (e) {
      setActionError((e as Error).message)
    } finally {
      setAnalyzing(false)
    }
  }

  async function save() {
    if (!job) return
    setSavedState('saving')
    setActionError(null)
    try {
      await api.createApplication(job.id)
      setSavedState('saved')
      applications.reload()
    } catch (e) {
      if ((e as { status?: number }).status === 409) setSavedState('saved')
      else {
        setSavedState('unknown')
        setActionError((e as Error).message)
      }
    }
  }

  return (
    <>
      <p className="mb-4 text-sm">
        <Link to="/jobs" className="text-accent-700 underline">
          ← Back to jobs
        </Link>
      </p>

      <PageTitle sub={<JobMeta job={job} />}>{job.title}</PageTitle>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <RemoteBadge job={job} />
        <ClosedBadge job={job} />
        {job.level && (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs capitalize text-slate-700">
            {job.level}
          </span>
        )}
        {job.posted_at && (
          <span className="text-xs text-slate-500">
            Posted {new Date(job.posted_at).toLocaleDateString()}
          </span>
        )}
      </div>

      {job.status === 'closed' && (
        <p className="mb-4 rounded-lg border border-slate-300 bg-slate-100 p-3 text-sm text-slate-700">
          This posting is no longer listed on the company's board. The link may not accept
          applications.
        </p>
      )}

      {actionError && (
        <div className="mb-4">
          <ErrorNote>{actionError}</ErrorNote>
        </div>
      )}

      <div className="mb-6 flex flex-wrap gap-2">
        <Button onClick={analyze} loading={analyzing}>
          {analyzing ? 'Analyzing…' : 'Full analysis'}
        </Button>
        <Button variant="secondary" onClick={save} disabled={alreadySaved}>
          {alreadySaved ? 'Saved ✓' : savedState === 'saving' ? 'Saving…' : 'Save'}
        </Button>
        <ApplyLink job={job} />
      </div>

      {job.requirements.length > 0 && (
        <Card className="mb-4">
          <h2 className="text-sm font-medium text-slate-700">What they ask for</h2>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">
            {job.requirements.slice(0, 15).map((r, i) => (
              <li key={i}>{r.text}</li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <h2 className="text-sm font-medium text-slate-700">Full description</h2>
        <div className="mt-3 max-h-[32rem] overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
          {job.description_text}
        </div>
      </Card>
    </>
  )
}
