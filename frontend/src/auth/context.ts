/** Contexts live apart from the providers so each component file exports only components
 *  (Vite fast refresh) and hooks can be imported without pulling in a provider. */
import type { Session } from '@supabase/supabase-js'
import { createContext, useContext } from 'react'
import type { ResumeSummary } from '../types/api'

export interface AuthValue {
  session: Session | null
  /** True until the stored session has been read, so routes don't redirect too early. */
  loading: boolean
  signIn: (email: string, password: string) => Promise<void>
  /** Resolves to true when Supabase returned a session; false means "confirm your email". */
  signUp: (email: string, password: string) => Promise<boolean>
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthValue | null>(null)

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

export interface ResumesValue {
  resumes: ResumeSummary[] | null
  active: ResumeSummary | null
  loading: boolean
  error: Error | null
  /** Call after uploading or deleting a resume. */
  reload: () => void
}

export const ResumesContext = createContext<ResumesValue | null>(null)

export function useResumes(): ResumesValue {
  const value = useContext(ResumesContext)
  if (!value) throw new Error('useResumes must be used inside ResumeProvider')
  return value
}
