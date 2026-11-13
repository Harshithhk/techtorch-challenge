// Shapes returned by the backend. Amounts are strings, never numbers, so no float rounding in the UI.

export interface Money {
  amount: string;
  currency: string;
}

export interface Proposal {
  proposal_id: string;
  summary_title: string;
  action_kind: "make_good_invoice" | "credit_memo" | "plan_amendment" | "rollback";
  plan_id: string;
  amount: Money | null;
  target_invoice_id: string | null;
  period_start: string | null;
  period_end: string | null;
  reason: string;
  calculation: string[];
  warnings: string[];
}

export type TurnStatus = "answered" | "awaiting_approval" | "blocked" | "busy" | "decided" | "no_pending";

export interface TurnResult {
  status: TurnStatus;
  reply: string;
  pending: Proposal | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AuditEvent {
  event_id: string;
  timestamp: string;
  event: "applied" | "rolled_back" | "rejected";
  action_id: string;
  kind: string;
  plan_id: string;
  summary: string;
}

export interface LoginResult {
  user_id: string;
  thread_id: string;
}
