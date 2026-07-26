import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Private-team Path A: fail closed.
 * Set VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY at build time.
 * Without them the leaderboard tab shows "not configured" and nothing
 * is sent to the public upstream Supabase project.
 */
const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string | undefined)?.trim();
const SUPABASE_ANON_KEY = (import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined)?.trim();

export const supabase: SupabaseClient | null =
  SUPABASE_URL && SUPABASE_ANON_KEY
    ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
        auth: {
          flowType: "pkce",
        },
      })
    : null;
