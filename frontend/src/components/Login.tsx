// Dummy login: any user id of safe characters. The server creates the user and their thread on first use.
import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "../api";

interface Props {
  onSignIn: (userId: string) => Promise<void>;
}

const USER_ID = /^[A-Za-z0-9_.@-]{1,64}$/;

export function Login({ onSignIn }: Props) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = value.trim();
    if (!USER_ID.test(candidate)) {
      setError("Use 1 to 64 letters, digits, dots, dashes, underscores or @.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSignIn(candidate);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <h1>Revenue Leakage Agent</h1>
        <p className="muted">Enter a user id to open your conversation. Your chat history is kept under that id.</p>
        <label htmlFor="user-id">User id</label>
        <input
          id="user-id"
          autoComplete="username"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. analyst-01"
          maxLength={64}
        />
        {error && <div className="error" role="alert">{error}</div>}
        <button type="submit" disabled={busy || !value.trim()}>{busy ? "Signing in…" : "Continue"}</button>
      </form>
    </div>
  );
}
