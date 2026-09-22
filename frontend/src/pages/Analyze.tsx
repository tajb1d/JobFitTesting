import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useResumes } from '../auth/context'
import { Button, Card, ErrorNote, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useAction } from '../lib/useApi'

const MIN_DESCRIPTION = 100
const MAX_DESCRIPTION = 20_000

export default function Analyze() {
  const { active } = useResumes()
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const create = useAction((o, body: Parameters<typeof api.createAnalysis>[0]) =>
    api.createAnalysis(body, o),
  )

  const trimmed = text.trim()
  const looksLikeUrl = /^https?:\/\/\S+$/i.test(trimmed)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!active) return
    if (!looksLikeUrl && trimmed.length < MIN_DESCRIPTION) {
      setLocalError(
        `Paste at least ${MIN_DESCRIPTION} characters of the job description, or a Greenhouse or Lever link.`,
      )
      return
    }
    setLocalError(null)
    const analysis = await create.run(
      looksLikeUrl
        ? { resume_id: active.id, job_url: trimmed }
        : { resume_id: active.id, job_description: trimmed.slice(0, MAX_DESCRIPTION) },
    )
    if (analysis) navigate(`/analyses/${analysis.id}`)
  }

  const error = localError ?? create.error?.message
  const retryAfter = create.error?.status === 429 ? create.error.retryAfter : undefined

  return (
    <>
      <PageTitle sub="Paste a job description, or a Greenhouse or Lever job link.">
        Analyze a job
      </PageTitle>
      <Card>
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          {error && (
            <ErrorNote>
              {error}
              {retryAfter ? ` Try again in about ${Math.ceil(retryAfter / 60)} minute(s).` : ''}
            </ErrorNote>
          )}
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">
              Job description or link
            </span>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={14}
              maxLength={MAX_DESCRIPTION}
              placeholder={'Paste the full job description here…\n\nor https://boards.greenhouse.io/company/jobs/123456'}
              className="w-full rounded-lg border border-slate-300 p-3 font-mono text-xs leading-relaxed focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100"
            />
            <span className="mt-1 block text-xs text-slate-500">
              {looksLikeUrl
                ? 'Looks like a link. Greenhouse and Lever links are supported.'
                : `${trimmed.length.toLocaleString()} / ${MAX_DESCRIPTION.toLocaleString()} characters`}
            </span>
          </label>
          <Button type="submit" loading={create.loading} waking={create.waking}>
            {create.loading ? 'Analyzing…' : 'Analyze'}
          </Button>
          <p className="text-xs text-slate-500">
            Analysis compares the posting's requirements with your resume and suggests what to add.
            It never rewrites your resume or invents experience. Limited to 20 analyses per hour.
          </p>
        </form>
      </Card>
    </>
  )
}
