// Thin client for the backend. The user id is sent as X-User-Id; the server maps it to a thread.
import type { AuditEvent, ChatMessage, LoginResult, Proposal, TurnResult } from "./types";

const BASE_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  userId?: string;
}

async function request<T>(path: string, { method = "GET", body, userId }: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (userId) headers["X-User-Id"] = userId;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the backend running?");
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, describeError(data));
  }
  return data as T;
}

// FastAPI returns validation errors as a list, and domain refusals as a string. Show both readably.
function describeError(data: { detail?: unknown }): string {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((item: { msg?: string }) => item.msg ?? "invalid input").join("; ");
  }
  return "Request failed";
}

export const api = {
  login: (userId: string) => request<LoginResult>("/auth/login", { method: "POST", body: { user_id: userId } }),
  chat: (userId: string, message: string) =>
    request<TurnResult>("/chat", { method: "POST", body: { message }, userId }),
  decide: (userId: string, decision: "approve" | "reject") =>
    request<TurnResult>("/chat/resume", { method: "POST", body: { decision }, userId }),
  history: (userId: string) => request<{ messages: ChatMessage[] }>("/chat/history", { userId }),
  pending: (userId: string) => request<{ pending: Proposal | null }>("/chat/pending", { userId }),
  audit: (userId: string) => request<{ events: AuditEvent[] }>("/audit-log", { userId }),
};
