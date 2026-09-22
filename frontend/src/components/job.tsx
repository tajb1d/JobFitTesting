import type { JobSummary } from '../types/api'

export function RemoteBadge({ job }: { job: JobSummary }) {
  if (!job.is_remote) return null
  return (
    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800">
      Remote
    </span>
  )
}

export function StretchBadge({ levelsAbove }: { levelsAbove: number | null }) {
  const above = levelsAbove ?? 0
  if (above <= 0) return null
  return (
    <span
      className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800"
      title={`This job is ${above} level${above > 1 ? 's' : ''} above yours, so its score is reduced.`}
    >
      Stretch
    </span>
  )
}

export function ClosedBadge({ job }: { job: JobSummary }) {
  if (job.status !== 'closed') return null
  return (
    <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700">
      Closed
    </span>
  )
}

export function JobMeta({ job }: { job: JobSummary }) {
  const parts = [job.company, job.location ?? 'Location not listed']
  if (job.min_years !== null) parts.push(`${job.min_years}+ yrs`)
  return <p className="text-sm text-slate-600">{parts.join(' · ')}</p>
}

/** Opens the real posting. We never submit an application (CLAUDE.md). */
export function ApplyLink({
  job,
  variant = 'primary',
  size = 'md',
  label = 'Apply on the company site ↗',
}: {
  job: JobSummary
  variant?: 'primary' | 'secondary'
  size?: 'md' | 'sm'
  label?: string
}) {
  // A variant, not overridable classes: passing a background override used to leave white
  // text on a white button, because Tailwind resolves conflicts by stylesheet order.
  const styles =
    variant === 'primary'
      ? 'bg-accent-600 text-white hover:bg-accent-700'
      : 'bg-white text-accent-700 ring-1 ring-accent-600 hover:bg-accent-50'
  const padding = size === 'sm' ? 'px-3 py-1.5' : 'px-4 py-2'
  return (
    <a
      href={job.url}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex items-center justify-center rounded-lg text-sm font-medium transition ${padding} ${styles}`}
    >
      {label}
    </a>
  )
}
