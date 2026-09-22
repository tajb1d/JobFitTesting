import type { ReactNode } from 'react'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'
import { ResumesContext } from './context'
import type { ResumesValue } from './context'

/** One /resumes call shared by the route guard and the pages, instead of one per page. */
export function ResumeProvider({ children }: { children: ReactNode }) {
  const { data, loading, error, reload } = useApi((o) => api.listResumes(o), [])
  const value: ResumesValue = {
    resumes: data,
    active: data?.find((r) => r.is_active) ?? null,
    loading,
    error,
    reload,
  }
  return <ResumesContext.Provider value={value}>{children}</ResumesContext.Provider>
}
