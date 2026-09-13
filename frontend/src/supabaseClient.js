import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || "";
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || "";

export const isSupabaseConfigured = Boolean(
  supabaseUrl && 
  supabaseAnonKey && 
  !supabaseUrl.includes("xxxxxxxxxxxx")
);

// Initialize client only if valid URL/Key is configured, otherwise provide safe stub
export const supabase = isSupabaseConfigured
  ? createClient(supabaseUrl, supabaseAnonKey)
  : null;

// Call this from "Sign in with Google" button
export async function signInWithGoogle() {
  if (!isSupabaseConfigured || !supabase) {
    console.warn("Supabase not configured. Using local dev session.");
    return { error: { message: "Supabase credentials not set in .env. Please use Guest Dev Mode for local testing." } };
  }
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: {
      redirectTo: window.location.origin,
    },
  });
  if (error) console.error("Google Auth error:", error.message);
  return { error };
}

export async function signOut() {
  localStorage.removeItem("astrid_guest_user");
  if (supabase) {
    await supabase.auth.signOut();
  }
}

export async function getSession() {
  // 1. Check active Supabase session if configured
  if (supabase) {
    try {
      const { data } = await supabase.auth.getSession();
      if (data?.session) return data.session;
    } catch (err) {
      console.warn("Error getting Supabase session:", err);
    }
  }

  // 2. Check local dev/guest session fallback
  const guestUser = localStorage.getItem("astrid_guest_user");
  if (guestUser) {
    try {
      return JSON.parse(guestUser);
    } catch (e) {
      return null;
    }
  }

  return null;
}

export function setGuestSession(email = "dev.guest@astrid.ai") {
  const guestSession = {
    access_token: "dev-guest-token-astrid-local",
    user: {
      id: "00000000-0000-0000-0000-000000000001",
      email: email,
      user_metadata: {
        full_name: "Local Dev User",
        avatar_url: "https://api.dicebear.com/7.x/bottts/svg?seed=AstridDev",
      },
    },
  };
  localStorage.setItem("astrid_guest_user", JSON.stringify(guestSession));
  return guestSession;
}
