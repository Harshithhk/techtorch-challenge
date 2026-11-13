// Read-only view of the sandbox audit trail: every applied, rolled-back and rejected action.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { AuditEvent } from "../types";

interface Props {
  userId: string;
  onAuthLost: (error: unknown) => void;
}

export function AuditLog({ userId, onAuthLost }: Props) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.audit(userId);
      setEvents(data.events);
      setError(null);
    } catch (err) {
      onAuthLost(err);
      setError(err instanceof Error ? err.message : "Could not load the audit log.");
    }
  }, [userId, onAuthLost]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="audit">
      <div className="audit-head">
        <h2>Audit log</h2>
        <button className="ghost" onClick={() => void load()}>Refresh</button>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      {events.length === 0 ? (
        <p className="muted">No actions yet. Approved changes, rollbacks and rejections appear here.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Action</th>
                <th>Plan</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {[...events].reverse().map((e) => (
                <tr key={e.event_id}>
                  <td>{new Date(e.timestamp).toLocaleString()}</td>
                  <td><span className={`pill ${e.event}`}>{e.event.replace("_", " ")}</span></td>
                  <td className="mono">{e.action_id}</td>
                  <td>{e.plan_id}</td>
                  <td>{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
