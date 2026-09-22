import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY

/** Set once at startup so a missing .env fails loudly instead of as a confusing 401 later. */
export const supabaseConfigError =
  !url || !publishableKey
    ? 'Supabase is not configured. Copy frontend/.env.example to frontend/.env and fill in VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY.'
    : null

export const supabase = createClient(url ?? 'http://localhost', publishableKey ?? 'missing-key', {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
})
