import type { Session } from '@supabase/supabase-js'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { setUnauthorizedHandler } from '../lib/api'
import { supabase } from '../lib/supabase'
import { AuthContext } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next))
    return () => data.subscription.unsubscribe()
  }, [])

  // A 401 from the API means the token is no longer good: drop it so the guards redirect.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      void supabase.auth.signOut()
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw new Error(error.message)
  }, [])

  const signUp = useCallback(async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signUp({ email, password })
    if (error) throw new Error(error.message)
    return data.session !== null // null when the project requires email confirmation
  }, [])

  const signOut = useCallback(async () => {
    await supabase.auth.signOut()
  }, [])

  const value = useMemo(
    () => ({ session, loading, signIn, signUp, signOut }),
    [session, loading, signIn, signUp, signOut],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
