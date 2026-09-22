import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useResumes } from '../auth/context'
import { ApplyLink, JobMeta, RemoteBadge, StretchBadge } from '../components/job'
import { Button, Card, ErrorNote, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'
import type { ExperienceLevel, RecommendationItem, SkillWeight } from '../types/api'

const LEVELS: ExperienceLevel[] = ['intern', 'entry', 'mid', 'senior', 'staff']

export default function Jobs() {
  // Filters live in the URL, so a refresh or a shared link keeps the same view.
  const [params, setParams] = useSearchParams()
  const remote = params.get('remote')
  const location = params.get('location') ?? ''
  const level = (params.get('level') as ExperienceLevel | null) ?? ''
  const showStretch = params.get('show_stretch') === 'true'
  const [locationDraft, setLocationDraft] = useState(location)

  const feed = useApi(
    (o) =>
      api.getRecommendations(
        {
          remote: remote === null ? undefined : remote === 'true',
          location: location || undefined,
          level: level || undefined,
          show_stretch: showStretch,
          limit: 20,
        },
        o,
      ),
    [remote, location, level, showStretch],
  )
  const saved = useApi((o) => api.listApplications(o), [])
  const savedJobIds = new Set((saved.data ?? []).map((a) => a.job.id))

  function setFilter(key: string, value: string | null) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  return (
    <>
      <PageTitle sub="Ranked against your active resume: skills you match, experience that reads like the job, and your resume's structure.">
        Jobs for you
      </PageTitle>

      <Card className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block font-medium text-slate-700">Location</span>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                setFilter('location', locationDraft.trim() || null)
              }}
            >
              <input
                value={locationDraft}
                onChange={(e) => setLocationDraft(e.target.value)}
                onBlur={() => setFilter('location', locationDraft.trim() || null)}
                placeholder="Any location"
                className="w-44 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100"
              />
            </form>
          </label>

          <Select
            label="Remote"
            value={remote ?? ''}
            onChange={(v) => setFilter('remote', v || null)}
            options={[
              { value: '', label: 'Any' },
              { value: 'true', label: 'Remote only' },
              { value: 'false', label: 'On-site only' },
            ]}
          />

          <Select
            label="Level"
            value={level}
            onChange={(v) => setFilter('level', v || null)}
            options={[{ value: '', label: 'Any' }, ...LEVELS.map((l) => ({ value: l, label: l }))]}
          />

          <label className="flex items-center gap-2 pb-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={showStretch}
              onChange={(e) => setFilter('show_stretch', e.target.checked ? 'true' : null)}
              className="h-4 w-4 rounded border-slate-300 text-accent-600 focus:ring-accent-500"
            />
            Show stretch roles
          </label>

          {(remote || location || level || showStretch) && (
            <button
              type="button"
              onClick={() => {
                setLocationDraft('')
                setParams(new URLSearchParams(), { replace: true })
              }}
              className="pb-2 text-sm text-slate-600 underline hover:text-slate-900"
            >
              Clear
            </button>
          )}
        </div>
      </Card>

      {feed.loading && <Loading waking={feed.waking} label="Finding jobs…" />}
      {feed.error && <ErrorNote onRetry={feed.reload}>{feed.error.message}</ErrorNote>}
      {feed.data && feed.data.items.length === 0 && (
        <Card>
          <p className="text-sm text-slate-700">No jobs match these filters.</p>
          <p className="mt-2 text-sm text-slate-600">
            Try clearing the filters. Jobs two or more levels above yours are hidden until you turn
            on "Show stretch roles".
          </p>
        </Card>
      )}

      <ul className="space-y-4">
        {feed.data?.items.map((item) => (
          <li key={item.job.id}>
            <JobCard item={item} saved={savedJobIds.has(item.job.id)} onSaved={saved.reload} />
          </li>
        ))}
      </ul>
    </>
  )
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <label className="text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-slate-300 px-3 py-2 text-sm capitalize focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function JobCard({
  item,
  saved,
  onSaved,
}: {
  item: RecommendationItem
  saved: boolean
  onSaved: () => void
}) {
  const { job } = item
  const navigate = useNavigate()
  const { active } = useResumes()
  const [error, setError] = useState<string | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [savingState, setSavingState] = useState<'idle' | 'saving' | 'saved'>(
    saved ? 'saved' : 'idle',
  )

  async function analyze() {
    if (!active) return
    setAnalyzing(true)
    setError(null)
    try {
      // The job is already parsed and embedded, so this only costs the feedback call.
      const analysis = await api.createAnalysis({ resume_id: active.id, job_id: job.id })
      navigate(`/analyses/${analysis.id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAnalyzing(false)
    }
  }

  async function save() {
    setSavingState('saving')
    setError(null)
    try {
      await api.createApplication(job.id)
      setSavingState('saved')
      onSaved()
    } catch (e) {
      // 409 means it's already in the tracker, which is the state the button wants anyway.
      const status = (e as { status?: number }).status
      if (status === 409) setSavingState('saved')
      else {
        setSavingState('idle')
        setError((e as Error).message)
      }
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/jobs/${job.id}`} className="text-lg font-semibold text-slate-900 hover:underline">
            {job.title}
          </Link>
          <JobMeta job={job} />
          <div className="mt-2 flex flex-wrap gap-2">
            <RemoteBadge job={job} />
            <StretchBadge levelsAbove={item.levels_above} />
            {job.level && (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs capitalize text-slate-700">
                {job.level}
              </span>
            )}
          </div>
        </div>
        <p className="text-right">
          <span className="block text-3xl font-bold text-accent-700">{Math.round(item.score)}</span>
          <span className="text-xs text-slate-500">match score</span>
        </p>
      </div>

      <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
        <SkillRow label="You match" skills={item.matched_skills} tone="match" />
        <SkillRow label="Missing" skills={item.missing_skills} tone="missing" />
      </div>

      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={analyze} loading={analyzing}>
          {analyzing ? 'Analyzing…' : 'Full analysis'}
        </Button>
        <Button variant="secondary" onClick={save} disabled={savingState === 'saved'}>
          {savingState === 'saved' ? 'Saved ✓' : savingState === 'saving' ? 'Saving…' : 'Save'}
        </Button>
        <ApplyLink job={job} variant="secondary" />
      </div>
    </Card>
  )
}

function SkillRow({
  label,
  skills,
  tone,
}: {
  label: string
  skills: SkillWeight[]
  tone: 'match' | 'missing'
}) {
  const styles =
    tone === 'match' ? 'bg-emerald-50 text-emerald-900' : 'bg-slate-100 text-slate-700'
  return (
    <p className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
      {skills.length === 0 ? (
        <span className="text-xs text-slate-400">—</span>
      ) : (
        skills.map((s) => (
          <span key={s.skill} className={`rounded-full px-2 py-0.5 text-xs ${styles}`}>
            {s.skill}
          </span>
        ))
      )}
    </p>
  )
}
