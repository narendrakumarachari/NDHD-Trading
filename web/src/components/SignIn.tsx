import { useState, type FormEvent } from "react";
import type { Me } from "../types.js";

interface Props {
  me: Me;
  /** Why /api/auth/me failed, if it did (server down, database down). */
  loadError: string | null;
  onSignIn: (username: string, password: string) => Promise<string | null>;
  onOpenSettings: () => void;
}

/**
 * Shown instead of the dashboard until this browser is signed in, either
 * with a username and password (when the server has users) or with the API
 * key from Settings.
 */
export function SignInScreen({ me, loadError, onSignIn, onOpenSettings }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(await onSignIn(username.trim(), password));
    setPassword("");
    setBusy(false);
  }

  return (
    <div className="signin-wrap">
      <div className="modal signin-card">
        <div className="brand signin-brand">
          <div className="brand-mark">N</div>
          <span>NDHD Trading</span>
        </div>

        {me.users_enabled ? (
          <form onSubmit={submit}>
            <h3>Sign in</h3>
            <p className="desc">Use the username and password you were given.</p>
            <div className="field">
              <label htmlFor="signin-username">Username</label>
              <input
                id="signin-username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="signin-password">Password</label>
              <input
                id="signin-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {(error || loadError) && <div className="banner danger signin-error">{error || loadError}</div>}
            <div className="actions">
              <button type="button" className="btn" onClick={onOpenSettings}>
                Use API key
              </button>
              <button type="submit" className="btn primary" disabled={busy || !username.trim() || !password}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </div>
          </form>
        ) : (
          <>
            <h3>API key needed</h3>
            <p className="desc">This dashboard is locked. Open Settings and paste the dashboard API key.</p>
            {loadError && <div className="banner danger signin-error">{loadError}</div>}
            <div className="actions">
              <button className="btn primary" onClick={onOpenSettings}>
                Open Settings
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
