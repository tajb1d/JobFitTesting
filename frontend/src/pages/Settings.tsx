import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, useResumes } from '../auth/context'
import ResumeUpload from '../components/ResumeUpload'
import { Button, Card, ErrorNote, Loading, PageTitle } from '../components/ui'
import { api } from '../lib/api'
import { useAction } from '../lib/useApi'

const CONFIRM_WORD = 'DELETE'
// Mirrors max_resumes_per_user in backend/app/config.py; the backend rejects extras with 409.
const MAX_RESUMES = 10

export default function Settings() {
  const { resumes, loading, error, reload } = useResumes()

  return (
    <>
      <PageTitle>Settings</PageTitle>

      <Card className="mb-4">
        <h2 className="text-sm font-medium text-slate-700">Your resumes</h2>
        <p className="mt-1 text-xs text-slate-500">
          The active resume is the one used for the job feed and new analyses.
        </p>
        {loading && <Loading />}
        {error && <ErrorNote onRetry={reload}>{error.message}</ErrorNote>}
        {resumes && resumes.length === 0 && (
          <p className="mt-3 text-sm text-slate-600">No resumes yet.</p>
        )}
        <ul className="mt-3 divide-y divide-slate-100">
          {resumes?.map((resume) => (
            <ResumeRow key={resume.id} resume={resume} onChange={reload} />
          ))}
        </ul>
      </Card>

      <Card className="mb-4">
        <h2 className="text-sm font-medium text-slate-700">Add another resume</h2>
        <p className="mt-1 mb-3 text-xs text-slate-500">
          Keep several versions and switch between them. Up to {MAX_RESUMES} in total
          ({resumes ? MAX_RESUMES - resumes.length : MAX_RESUMES} left).
        </p>
        {resumes && resumes.length >= MAX_RESUMES ? (
          <p className="text-sm text-slate-600">
            You've reached the limit. Delete one above to add another.
          </p>
        ) : (
          <ResumeUpload label="Another resume (PDF, up to 5 MB)" onUploaded={reload} />
        )}
      </Card>

      <DangerZone />
    </>
  )
}

function ResumeRow({
  resume,
  onChange,
}: {
  resume: { id: string; filename: string; is_active: boolean; structure_score: number | null; created_at: string }
  onChange: () => void
}) {
  const activate = useAction((o) => api.setActiveResume(resume.id, o))
  const remove = useAction((o) => api.deleteResume(resume.id, o))
  const error = activate.error ?? remove.error

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">
            {resume.filename}
            {resume.is_active && (
              <span className="ml-2 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800">
                Active
              </span>
            )}
          </p>
          <p className="text-xs text-slate-500">
            Uploaded {new Date(resume.created_at).toLocaleDateString()} · structure{' '}
            {Math.round((resume.structure_score ?? 0) * 100)}/100
          </p>
        </div>
        <div className="flex gap-2">
          {!resume.is_active && (
            <Button
              variant="secondary"
              loading={activate.loading}
              onClick={async () => {
                if (await activate.run()) onChange()
              }}
            >
              Use this one
            </Button>
          )}
          <button
            type="button"
            disabled={remove.loading}
            onClick={async () => {
              if (!confirm(`Delete ${resume.filename}? Its analyses are deleted too.`)) return
              await remove.run()
              onChange()
            }}
            className="text-sm text-slate-500 underline hover:text-red-700"
          >
            Delete
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error.message}
        </p>
      )}
    </li>
  )
}

function DangerZone() {
  const { signOut } = useAuth()
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const del = useAction((o) => api.deleteAccount(o))

  return (
    <Card className="border-red-200">
      <h2 className="text-sm font-medium text-red-800">Delete account</h2>
      <p className="mt-1 text-sm text-slate-600">
        Removes your profile, resumes, analyses and tracker, then your login. This can't be undone.
      </p>
      {del.error && (
        <div className="mt-3">
          <ErrorNote>{del.error.message}</ErrorNote>
        </div>
      )}
      {!confirming ? (
        <Button
          variant="secondary"
          className="mt-3 border-red-300 text-red-700 hover:bg-red-50"
          onClick={() => setConfirming(true)}
        >
          Delete my account
        </Button>
      ) : (
        <div className="mt-3 space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-slate-700">
              Type {CONFIRM_WORD} to confirm
            </span>
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              className="w-40 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-100"
            />
          </label>
          <div className="flex gap-2">
            <Button
              className="bg-red-600 hover:bg-red-700"
              disabled={typed !== CONFIRM_WORD}
              loading={del.loading}
              waking={del.waking}
              onClick={async () => {
                if (typed !== CONFIRM_WORD) return
                if (del.error) return
                await del.run()
                await signOut()
                navigate('/login', { replace: true })
              }}
            >
              Delete everything
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}
