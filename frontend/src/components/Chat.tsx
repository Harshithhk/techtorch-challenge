// Conversation view. Loads history and any waiting proposal on mount, then sends messages and decisions.
import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatMessage, Proposal, TurnResult } from "../types";
import { ProposalCard } from "./ProposalCard";

interface Props {
  userId: string;
  onAuthLost: (error: unknown) => void;
}

export function Chat({ userId, onAuthLost }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<Proposal | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [history, waiting] = await Promise.all([api.history(userId), api.pending(userId)]);
        if (cancelled) return;
        // Do not overwrite anything the user already sent while the history was loading.
        setMessages((prev) => (prev.length > 0 ? prev : history.messages));
        setPending((prev) => prev ?? waiting.pending);
      } catch (err) {
        if (!cancelled) {
          onAuthLost(err);
          setError(err instanceof Error ? err.message : "Could not load the conversation.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [userId, onAuthLost]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  // Apply a turn result: show the reply, and keep the proposal card in step with the server.
  const apply = (result: TurnResult, userText?: string) => {
    setMessages((prev) => {
      const next = userText ? [...prev, { role: "user" as const, content: userText }] : [...prev];
      if (result.reply) next.push({ role: "assistant", content: result.reply });
      return next;
    });
    setPending(result.pending);
  };

  const run = async (action: () => Promise<TurnResult>, userText?: string) => {
    setBusy(true);
    setError(null);
    try {
      apply(await action(), userText);
    } catch (err) {
      onAuthLost(err);
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    void run(() => api.chat(userId, text), text);
  };

  return (
    <div className="chat">
      <div className="thread" role="log" aria-live="polite">
        {messages.length === 0 && !pending && (
          <div className="empty muted">
            Ask about a plan, for example “Check plan C-1001 for revenue leakage.”
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.content}
          </div>
        ))}
        {pending && (
          <ProposalCard
            proposal={pending}
            busy={busy}
            onApprove={() => void run(() => api.decide(userId, "approve"))}
            onReject={() => void run(() => api.decide(userId, "reject"))}
          />
        )}
        <div ref={endRef} />
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      <form className="composer" onSubmit={send}>
        <input
          aria-label="Message"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={pending ? "Approve or reject the proposal first" : "Ask about billing, invoices or leakage…"}
          maxLength={2000}
          disabled={busy || pending !== null}
        />
        <button type="submit" disabled={busy || !input.trim() || pending !== null}>Send</button>
      </form>
    </div>
  );
}
