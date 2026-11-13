// The approval card is where a human decides. Its two buttons must be the only way to act.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProposalCard } from "../components/ProposalCard";
import type { Proposal } from "../types";

afterEach(cleanup);

const proposal: Proposal = {
  proposal_id: "abc123",
  summary_title: "make-good for C-1001",
  action_kind: "make_good_invoice",
  plan_id: "C-1001",
  amount: { amount: "8000.00", currency: "USD" },
  target_invoice_id: null,
  period_start: "2025-09-01",
  period_end: "2025-10-01",
  reason: "Plan C-1001 has no invoice for the period.",
  calculation: ["Expected for period: 8000.00 USD", "Gap = 8000.00 USD"],
  warnings: [],
};

describe("ProposalCard", () => {
  it("shows the domain amount, period and calculation trail", () => {
    render(<ProposalCard proposal={proposal} busy={false} onApprove={() => {}} onReject={() => {}} />);
    expect(screen.getByText("8000.00 USD")).toBeTruthy();
    expect(screen.getByText("2025-09-01 to 2025-10-01")).toBeTruthy();
    expect(screen.getByText("Gap = 8000.00 USD")).toBeTruthy();
    expect(screen.getByText("Make-good invoice")).toBeTruthy();
  });

  it("offers exactly the approve and reject actions", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(<ProposalCard proposal={proposal} busy={false} onApprove={onApprove} onReject={onReject} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve this action" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("disables both actions while a request is in flight, so a double click cannot apply twice", () => {
    render(<ProposalCard proposal={proposal} busy={true} onApprove={() => {}} onReject={() => {}} />);
    expect((screen.getByRole("button", { name: "Approve this action" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
