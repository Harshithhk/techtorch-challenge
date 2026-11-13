// End-to-end in the browser (API mocked): ask, see the proposal, approve, and see the outcome.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  api: {
    history: vi.fn(),
    pending: vi.fn(),
    chat: vi.fn(),
    decide: vi.fn(),
  },
  ApiError: class extends Error {},
}));

import { api } from "../api";
import { Chat } from "../components/Chat";
import type { Proposal } from "../types";

const proposal: Proposal = {
  proposal_id: "p1",
  summary_title: "make-good for C-1001",
  action_kind: "make_good_invoice",
  plan_id: "C-1001",
  amount: { amount: "8000.00", currency: "USD" },
  target_invoice_id: null,
  period_start: "2025-09-01",
  period_end: "2025-10-01",
  reason: "No invoice for September.",
  calculation: ["Gap = 8000.00 USD"],
  warnings: [],
};

beforeEach(() => {
  vi.mocked(api.history).mockResolvedValue({ messages: [] });
  vi.mocked(api.pending).mockResolvedValue({ pending: null });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Chat approval flow", () => {
  it("shows the proposal after a message, and approving calls the decision endpoint once", async () => {
    vi.mocked(api.chat).mockResolvedValue({
      status: "awaiting_approval",
      reply: "I've drafted a proposal: make-good for C-1001.",
      pending: proposal,
    });
    vi.mocked(api.decide).mockResolvedValue({ status: "answered", reply: "Applied the make-good.", pending: null });

    render(<Chat userId="analyst-01" onAuthLost={() => {}} />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Make good September for C-1001" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("button", { name: "Approve this action" })).toBeTruthy();
    expect(api.chat).toHaveBeenCalledWith("analyst-01", "Make good September for C-1001");

    fireEvent.click(screen.getByRole("button", { name: "Approve this action" }));
    await waitFor(() => expect(screen.getByText("Applied the make-good.")).toBeTruthy());
    expect(api.decide).toHaveBeenCalledTimes(1);
    expect(api.decide).toHaveBeenCalledWith("analyst-01", "approve");
    expect(screen.queryByRole("button", { name: "Approve this action" })).toBeNull();
  });

  it("reject sends the reject decision and shows no proposal afterwards", async () => {
    vi.mocked(api.pending).mockResolvedValue({ pending: proposal });
    vi.mocked(api.decide).mockResolvedValue({ status: "answered", reply: "No change made.", pending: null });

    render(<Chat userId="analyst-01" onAuthLost={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() => expect(screen.getByText("No change made.")).toBeTruthy());
    expect(api.decide).toHaveBeenCalledWith("analyst-01", "reject");
  });
});
