import { Link, useParams } from 'react-router-dom'
import { Card, ErrorNote, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'
import type { Analysis, AnalysisFeedbackItem, Severity, SkillWeight } from '../types/api'

/** One line each, so the number means something to someone seeing it for the first time. */
const SUBSCORES: { key: keyof Analysis; label: string; explanation: string; weight: string }[] = [
  {
    key: 'skill_score',
    label: 'Skill match',
    explanation: "Share of the posting's skills that appear on your resume, weighted by how much the posting stresses each one.",
    weight: '50%',
  },
  {
    key: 'semantic_score',
    label: 'Experience match',
    explanation: 'How closely your bullet points read like the work this job asks for, beyond exact keywords.',
    weight: '35%',
  },
  {
    key: 'structure_score',
    label: 'Resume structure',
    explanation: 'How well your resume is put together: sections, action verbs, quantified results, length.',
    weight: '15%',
  },
]

const SEVERITY_ORDER: Severity[] = ['high', 'medium', 'low']
const SEVERITY_LABEL: Record<Severity, string> = {
  high: 'Do these first',
  medium: 'Worth doing',
  low: 'Nice to have',
}
const SEVERITY_STYLES: Record<Severity, string> = {
  high: 'border-red-200 bg-red-50',
  medium: 'border-amber-200 bg-amber-50',
  low: 'border-slate-200 bg-slate-50',
}

export default function AnalysisPage() {
  const { id } = useParams<{ id: string }>()
  const { data, loading, waking, error, reload } = useApi((o) => api.getAnalysis(id!, o), [id])

  if (loading) return <Loading waking={waking} label="Loading analysis…" />
  if (error) return <ErrorNote onRetry={reload}>{error.message}</ErrorNote>
  if (!data) return null

  return (
    <>
      <PageTitle sub={`Analyzed ${new Date(data.created_at).toLocaleString()}`}>
        {data.job_title ?? 'Job analysis'}
      </PageTitle>

      <Card>
        <div className="flex flex-wrap items-center gap-6">
          <div>
            <p className="text-sm font-medium text-slate-600">Match score</p>
            <p className="text-5xl font-bold text-accent-700">
              {data.final_score?.toFixed(0) ?? '—'}
              <span className="text-xl font-medium text-slate-400"> / 100</span>
            </p>
          </div>
          <p className="max-w-sm text-sm text-slate-600">
            A weighted blend of the three scores below. Treat it as a rough guide, not a verdict:
            a strong application can still come from a middling score.
          </p>
        </div>
      </Card>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        {SUBSCORES.map((sub) => {
          const value = data[sub.key] as number | null
          return (
            <Card key={sub.key}>
              <div className="flex items-baseline justify-between">
                <h2 className="text-sm font-medium text-slate-700">{sub.label}</h2>
                <span className="text-xs text-slate-400">{sub.weight}</span>
              </div>
              <p className="mt-1 text-3xl font-semibold text-slate-900">
                {value === null ? (
                  <span className="text-base font-medium text-slate-500">Not scored</span>
                ) : (
                  `${Math.round(value * 100)}%`
                )}
              </p>
              <p className="mt-2 text-xs leading-relaxed text-slate-600">{sub.explanation}</p>
              {value === null && sub.key === 'skill_score' && (
                <p className="mt-2 text-xs text-slate-500">
                  This posting named no skills we recognize, so the other scores carry the weight.
                </p>
              )}
            </Card>
          )
        })}
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <SkillList title="Skills you have" skills={data.matched_skills} tone="match" />
        <SkillList title="Skills the posting wants" skills={data.missing_skills} tone="missing" />
      </div>

      {data.weak_requirements.length > 0 && (
        <Card className="mt-4">
          <h2 className="text-sm font-medium text-slate-700">Requirements your resume doesn't cover</h2>
          <ul className="mt-3 space-y-2">
            {data.weak_requirements.map((r, i) => (
              <li key={i} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                {r.text}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Feedback items={data.feedback_items} />

      <p className="mt-6 text-sm">
        <Link to="/analyze" className="font-medium text-accent-700 underline">
          Analyze another job
        </Link>
      </p>
    </>
  )
}

function SkillList({
  title,
  skills,
  tone,
}: {
  title: string
  skills: SkillWeight[]
  tone: 'match' | 'missing'
}) {
  const styles =
    tone === 'match'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
      : 'border-slate-200 bg-slate-50 text-slate-700'
  return (
    <Card>
      <h2 className="text-sm font-medium text-slate-700">{title}</h2>
      {skills.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">None.</p>
      ) : (
        <ul className="mt-3 flex flex-wrap gap-2">
          {skills.map((s) => (
            <li
              key={s.skill}
              className={`rounded-full border px-3 py-1 text-xs ${styles}`}
              title={s.weight >= 3 ? 'Required by the posting' : undefined}
            >
              {s.skill}
              {s.weight >= 3 && <span aria-label=", required"> ★</span>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Feedback({ items }: { items: AnalysisFeedbackItem[] }) {
  return (
    <Card className="mt-4">
      <h2 className="text-sm font-medium text-slate-700">Suggestions</h2>
      <p className="mt-1 text-xs text-slate-500">
        Only add things you've genuinely done. These are prompts, not claims to copy.
      </p>
      {SEVERITY_ORDER.map((severity) => {
        const group = items.filter((i) => i.severity === severity)
        if (group.length === 0) return null
        return (
          <div key={severity} className="mt-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {SEVERITY_LABEL[severity]}
            </h3>
            <ul className="mt-2 space-y-2">
              {group.map((item, i) => (
                <li key={i} className={`rounded-lg border p-3 text-sm text-slate-800 ${SEVERITY_STYLES[severity]}`}>
                  {item.message}
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </Card>
  )
}
