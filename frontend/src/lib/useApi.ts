import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

interface State<T> {
  data: T | null
  loading: boolean
  /** The request is slow enough that the backend is probably waking up (plan §12). */
  waking: boolean
  error: ApiError | null
}

/** Runs a GET-style call on mount, with loading, waking and error states. `deps` re-runs it. */
export function useApi<T>(
  call: (options: { onWaking: () => void; signal: AbortSignal }) => Promise<T>,
  deps: unknown[] = [],
): State<T> & { reload: () => void } {
  const [state, setState] = useState<State<T>>({
    data: null,
    loading: true,
    waking: false,
    error: null,
  })
  const [nonce, setNonce] = useState(0)
  // Keep the newest closure without re-running the fetch when its identity changes. Assigned
  // in an effect declared before the fetching one, so it's current by the time that runs.
  const callRef = useRef(call)
  useEffect(() => {
    callRef.current = call
  })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, waking: false, error: null })
    callRef
      .current({
        signal: controller.signal,
        onWaking: () => setState((s) => (s.loading ? { ...s, waking: true } : s)),
      })
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, loading: false, waking: false, error: null })
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted || (e as Error).name === 'AbortError') return
        const error = e instanceof ApiError ? e : new ApiError(0, (e as Error).message)
        setState({ data: null, loading: false, waking: false, error })
      })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { ...state, reload: useCallback(() => setNonce((n) => n + 1), []) }
}

/** The submit-side counterpart: tracks one action's loading, waking and error state. */
export function useAction<Args extends unknown[], T>(
  action: (options: { onWaking: () => void }, ...args: Args) => Promise<T>,
) {
  const [loading, setLoading] = useState(false)
  const [waking, setWaking] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const actionRef = useRef(action)
  useEffect(() => {
    actionRef.current = action
  })

  const run = useCallback(async (...args: Args): Promise<T | undefined> => {
    setLoading(true)
    setWaking(false)
    setError(null)
    try {
      return await actionRef.current({ onWaking: () => setWaking(true) }, ...args)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, (e as Error).message))
      return undefined
    } finally {
      setLoading(false)
      setWaking(false)
    }
  }, [])

  return { run, loading, waking, error, setError }
}
