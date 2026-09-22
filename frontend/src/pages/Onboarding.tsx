import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, useResumes } from '../auth/context'
import { Button, Card, ErrorNote, Field, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useAction } from '../lib/useApi'
import type { ExperienceLevel, Resume } from '../types/api'

const MAX_PDF_BYTES = 5 * 1024 * 1024 // matches the backend limit
const LEVELS: { value: ExperienceLevel; label: string }[] = [
  { value: 'intern', label: 'Internship' },
  { value: 'entry', label: 'Entry level / new grad' },
  { value: 'mid', label: 'Mid level' },
  { value: 'senior', label: 'Senior' },
  { value: 'staff', label: 'Staff or above' },
]

export default function Onboarding() {
  const { loading: resumesLoading, active, reload } = useResumes()
  const [resume, setResume] = useState<Resume | null>(null)

  if (resumesLoading) return <Loading />
  // A resume already exists (e.g. landing here by URL): skip straight to the details step.
  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      {resume || active ? (
        <DetailsStep resume={resume} onDone={reload} />
      ) : (
        <UploadStep onUploaded={setResume} />
      )}
    </main>
  )
}

function UploadStep({ onUploaded }: { onUploaded: (resume: Resume) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const upload = useAction((o, f: File) => api.uploadResume(f, o))

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return setLocalError('Choose a PDF first.')
    if (file.type !== 'application/pdf') return setLocalError('Only PDF files are accepted.')
    if (file.size > MAX_PDF_BYTES) return setLocalError('The PDF must be 5 MB or smaller.')
    setLocalError(null)
    const resume = await upload.run(file)
    if (resume) onUploaded(resume)
  }

  return (
    <>
      <PageTitle sub="We read the text to score it and match you to jobs. We never store the file itself.">
        Upload your resume
      </PageTitle>
      <Card>
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          {(localError || upload.error) && <ErrorNote>{localError ?? upload.error?.message}</ErrorNote>}
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">Resume (PDF, up to 5 MB)</span>
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-accent-50 file:px-4 file:py-2 file:text-sm file:font-medium file:text-accent-700 hover:file:bg-accent-100"
            />
          </label>
          <Button type="submit" loading={upload.loading} waking={upload.waking}>
            Upload and continue
          </Button>
          <p className="text-xs text-slate-500">
            A text-based PDF works best. Scanned or image-only resumes can't be read.
          </p>
        </form>
      </Card>
    </>
  )
}

function DetailsStep({ resume, onDone }: { resume: Resume | null; onDone: () => void }) {
  const navigate = useNavigate()
  const { session } = useAuth()
  const [roles, setRoles] = useState(resume?.suggested_roles.join(', ') ?? '')
  const [location, setLocation] = useState('')
  const [remoteOk, setRemoteOk] = useState(false)
  const [level, setLevel] = useState<ExperienceLevel>('entry')
  const save = useAction((o) =>
    api.putProfile(
      {
        target_roles: roles.split(',').map((r) => r.trim()).filter(Boolean).slice(0, 10),
        location: location.trim() || null,
        remote_ok: remoteOk,
        experience_level: level,
      },
      o,
    ),
  )

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (await save.run()) {
      onDone()
      navigate('/dashboard', { replace: true })
    }
  }

  return (
    <>
      <PageTitle sub={session ? 'Last step. You can change these later.' : undefined}>
        What are you looking for?
      </PageTitle>
      {resume && (
        <p className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
          Read <strong>{resume.filename}</strong>: {resume.bullets.length} bullet points and{' '}
          {resume.skills.length} skills. Structure score{' '}
          {Math.round((resume.structure_score ?? 0) * 100)} / 100.
        </p>
      )}
      <Card>
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          {save.error && <ErrorNote>{save.error.message}</ErrorNote>}
          <Field
            label="Target roles"
            value={roles}
            onChange={(e) => setRoles(e.target.value)}
            placeholder="Software Engineer, Data Analyst"
            hint={
              resume?.suggested_roles.length
                ? `Suggested from your resume: ${resume.suggested_roles.join(', ')}. Comma-separated, up to 10.`
                : 'Comma-separated, up to 10.'
            }
          />
          <Field
            label="Location"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="New York, NY"
          />
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={remoteOk}
              onChange={(e) => setRemoteOk(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-accent-600 focus:ring-accent-500"
            />
            I'm open to remote roles
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">Experience level</span>
            <select
              value={level}
              onChange={(e) => setLevel(e.target.value as ExperienceLevel)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100"
            >
              {LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs text-slate-500">
              Used to hide jobs far above your level in the feed.
            </span>
          </label>
          <Button type="submit" loading={save.loading} waking={save.waking}>
            Finish
          </Button>
        </form>
      </Card>
    </>
  )
}
