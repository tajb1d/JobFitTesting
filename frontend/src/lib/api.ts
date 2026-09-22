/** The only place that talks to the backend: attaches the Supabase token, turns errors into
 *  ApiError, and signals the "waking up" state from plan §12. */

import { supabase } from './supabase'
import type {
  Analysis,
  AnalysisCreate,
  AnalysisSummary,
  Me,
  Profile,
  ProfileUpdate,
  Resume,
  ResumeSummary,
} from '../types/api'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

/** Render's free tier sleeps; a request slower than this is probably a cold start (plan §12). */
export const WAKING_AFTER_MS = 3000

export class ApiError extends Error {
  readonly status: number
  /** Seconds to wait, from Retry-After on a 429. */
  readonly retryAfter?: number

  constructor(status: number, message: string, retryAfter?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}

/** Set by AuthProvider so a 401 can send the user to /login from anywhere. */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

interface RequestOptions {
  method?: string
  body?: unknown
  /** Called if the request is still running after WAKING_AFTER_MS. */
  onWaking?: () => void
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { data } = await supabase.auth.getSession()
  const headers: Record<string, string> = {}
  if (data.session) headers.Authorization = `Bearer ${data.session.access_token}`

  let init: RequestInit = { method: options.method ?? 'GET', headers, signal: options.signal }
  if (options.body instanceof FormData) {
    init.body = options.body // the browser sets the multipart boundary
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init = { ...init, body: JSON.stringify(options.body) }
  }

  const waking = options.onWaking ? setTimeout(options.onWaking, WAKING_AFTER_MS) : undefined
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, init)
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, "We couldn't reach the server. Check your connection and try again.")
  } finally {
    clearTimeout(waking)
  }

  if (response.status === 401) {
    onUnauthorized?.()
    throw new ApiError(401, 'Your session has expired. Please sign in again.')
  }
  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const retryAfter = Number(response.headers.get('Retry-After')) || undefined
    throw new ApiError(response.status, detailOf(payload, response.status), retryAfter)
  }
  return payload as T
}

/** FastAPI sends `detail` as a string, or as a list of field errors for a 422. */
function detailOf(payload: unknown, status: number): string {
  const detail = (payload as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string } | undefined
    if (first?.msg) return first.msg.replace(/^Value error, /, '')
  }
  return `Something went wrong (HTTP ${status}). Please try again.`
}

type Opts = Pick<RequestOptions, 'onWaking' | 'signal'>

export const api = {
  getMe: (o?: Opts) => request<Me>('/api/v1/me', o),
  putProfile: (body: ProfileUpdate, o?: Opts) =>
    request<Profile>('/api/v1/profile', { ...o, method: 'PUT', body }),

  listResumes: (o?: Opts) => request<ResumeSummary[]>('/api/v1/resumes', o),
  getResume: (id: string, o?: Opts) => request<Resume>(`/api/v1/resumes/${id}`, o),
  uploadResume: (file: File, o?: Opts) => {
    const form = new FormData()
    form.append('file', file)
    return request<Resume>('/api/v1/resumes', { ...o, method: 'POST', body: form })
  },

  createAnalysis: (body: AnalysisCreate, o?: Opts) =>
    request<Analysis>('/api/v1/analyses', { ...o, method: 'POST', body }),
  listAnalyses: (o?: Opts) => request<AnalysisSummary[]>('/api/v1/analyses', o),
  getAnalysis: (id: string, o?: Opts) => request<Analysis>(`/api/v1/analyses/${id}`, o),
}
