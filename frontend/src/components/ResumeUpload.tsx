import { useRef, useState } from 'react'
import { Button, ErrorNote } from './ui'
import { api } from '../lib/api'
import { useAction } from '../lib/useApi'
import type { Resume } from '../types/api'

export const MAX_PDF_BYTES = 5 * 1024 * 1024 // matches the backend limit

/** Shared by onboarding and settings: pick a PDF, validate it here, upload it. */
export default function ResumeUpload({
  label = 'Resume (PDF, up to 5 MB)',
  buttonText = 'Upload',
  onUploaded,
}: {
  label?: string
  buttonText?: string
  onUploaded: (resume: Resume) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const upload = useAction((o, f: File) => api.uploadResume(f, o))

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return setLocalError('Choose a PDF first.')
    if (file.type !== 'application/pdf') return setLocalError('Only PDF files are accepted.')
    if (file.size > MAX_PDF_BYTES) return setLocalError('The PDF must be 5 MB or smaller.')
    setLocalError(null)
    const resume = await upload.run(file)
    if (resume) {
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      onUploaded(resume)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3" noValidate>
      {(localError || upload.error) && <ErrorNote>{localError ?? upload.error?.message}</ErrorNote>}
      <label className="block">
        <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full text-sm text-slate-600 file:mr-3 file:cursor-pointer file:rounded-lg file:border-0 file:bg-accent-50 file:px-4 file:py-2 file:text-sm file:font-medium file:text-accent-700 hover:file:bg-accent-100"
        />
      </label>
      <Button type="submit" loading={upload.loading} waking={upload.waking}>
        {buttonText}
      </Button>
      <p className="text-xs text-slate-500">
        A text-based PDF works best. Scanned or image-only resumes can't be read. The new resume
        becomes your active one.
      </p>
    </form>
  )
}
