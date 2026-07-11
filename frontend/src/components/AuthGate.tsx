import { useState, useEffect, type FormEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Lock, Loader2 } from "lucide-react";
import { authApi } from "../lib/api";

/** Renders children always (the SPA shell is public) and overlays a login modal
 *  whenever the API requires a session — either on initial status check or when
 *  any request comes back 401 (dispatched as "powarr:unauthorized"). */
export default function AuthGate({ children }: { children: ReactNode }) {
  const [locked, setLocked] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const { data: status } = useQuery({
    queryKey: ["auth-status"],
    queryFn: authApi.status,
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    const handler = () => setLocked(true);
    window.addEventListener("powarr:unauthorized", handler);
    return () => window.removeEventListener("powarr:unauthorized", handler);
  }, []);

  useEffect(() => {
    if (status && status.enabled && !status.authenticated && !status.bypassed) {
      setLocked(true);
    }
  }, [status]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await authApi.login(username, password, totp || undefined);
      window.location.reload(); // fresh session — reload everything cleanly
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  };

  return (
    <>
      {children}
      {locked && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center">
          <form
            onSubmit={handleSubmit}
            className="bg-surface-raised border border-purple-900/40 rounded-2xl p-8 w-80 shadow-2xl"
          >
            <div className="flex items-center gap-2 mb-6">
              <Lock size={18} className="text-brand-light" />
              <h2 className="text-lg font-bold text-white">Sign in to Powarr</h2>
            </div>

            <label className="text-xs text-slate-400 mb-1 block">Username</label>
            <input
              autoFocus
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              className="w-full mb-3 bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
            />

            <label className="text-xs text-slate-400 mb-1 block">Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full mb-3 bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
            />

            {status?.totp_enabled && (
              <>
                <label className="text-xs text-slate-400 mb-1 block">Authenticator Code</label>
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder="123 456"
                  value={totp}
                  onChange={e => setTotp(e.target.value)}
                  className="w-full mb-3 bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white placeholder:text-slate-600"
                />
              </>
            )}

            {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

            <button
              type="submit"
              disabled={submitting || !username || !password}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-brand text-surface font-semibold hover:bg-brand-light text-sm transition-colors disabled:opacity-50"
            >
              {submitting && <Loader2 size={14} className="animate-spin" />}
              Sign In
            </button>
          </form>
        </div>
      )}
    </>
  );
}
