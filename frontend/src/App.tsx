// Top-level view: login until a user id is set, then chat or the audit log.
import { useCallback, useState } from "react";
import { api, ApiError } from "./api";
import { AuditLog } from "./components/AuditLog";
import { Chat } from "./components/Chat";
import { Login } from "./components/Login";

const STORAGE_KEY = "rla.userId"; // remembered per browser only, for convenience; not security

function readStoredUser(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // storage can be blocked (private windows); the app still works without it
  }
}

function writeStoredUser(value: string | null): void {
  try {
    if (value) localStorage.setItem(STORAGE_KEY, value);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore: storage unavailable */
  }
}

export function App() {
  const [userId, setUserId] = useState<string | null>(readStoredUser);
  const [view, setView] = useState<"chat" | "audit">("chat");

  const signIn = async (candidate: string) => {
    const result = await api.login(candidate);
    writeStoredUser(result.user_id);
    setUserId(result.user_id);
  };

  // Stable callbacks: child effects depend on them, so a new identity per render would refetch.
  const signOut = useCallback(() => {
    writeStoredUser(null);
    setUserId(null);
  }, []);

  // A 401 from any call means the stored user is no longer known to the server: sign out.
  const onAuthLost = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) signOut();
    },
    [signOut],
  );

  if (!userId) return <Login onSignIn={signIn} />;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Revenue Leakage Agent</div>
        <nav className="tabs" aria-label="Main">
          <button className={view === "chat" ? "tab active" : "tab"} onClick={() => setView("chat")}>Chat</button>
          <button className={view === "audit" ? "tab active" : "tab"} onClick={() => setView("audit")}>Audit log</button>
        </nav>
        <div className="who">
          <span className="user">{userId}</span>
          <button className="ghost" onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main className="main">
        {view === "chat" ? <Chat userId={userId} onAuthLost={onAuthLost} /> : <AuditLog userId={userId} onAuthLost={onAuthLost} />}
      </main>
    </div>
  );
}
