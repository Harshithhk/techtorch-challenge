// The approval card. Shows the domain's evidence and offers the only two ways to act on a proposal.
import type { Proposal } from "../types";

interface Props {
  proposal: Proposal;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}

const KIND_LABEL: Record<Proposal["action_kind"], string> = {
  make_good_invoice: "Make-good invoice",
  credit_memo: "Credit memo",
  plan_amendment: "Plan amendment",
  rollback: "Rollback",
};

export function ProposalCard({ proposal, busy, onApprove, onReject }: Props) {
  const period = proposal.period_start
    ? `${proposal.period_start} to ${proposal.period_end ?? "open"}`
    : null;

  return (
    <section className="proposal" aria-label="Proposed action">
      <div className="proposal-head">
        <span className="kind">{KIND_LABEL[proposal.action_kind]}</span>
        <span className="awaiting">Waiting for your decision</span>
      </div>
      <h3>{proposal.summary_title}</h3>
      <dl className="facts">
        <dt>Plan</dt>
        <dd>{proposal.plan_id}</dd>
        {proposal.amount && (
          <>
            <dt>Amount</dt>
            <dd className="money">{proposal.amount.amount} {proposal.amount.currency}</dd>
          </>
        )}
        {proposal.target_invoice_id && (
          <>
            <dt>Invoice</dt>
            <dd>{proposal.target_invoice_id}</dd>
          </>
        )}
        {period && (
          <>
            <dt>Period</dt>
            <dd>{period}</dd>
          </>
        )}
      </dl>
      <p className="reason">{proposal.reason}</p>
      {proposal.calculation.length > 0 && (
        <details open>
          <summary>How this was calculated</summary>
          <ol className="calc">
            {proposal.calculation.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ol>
        </details>
      )}
      {proposal.warnings.map((w, i) => (
        <div className="warning" key={i}>{w}</div>
      ))}
      <div className="actions">
        <button className="approve" onClick={onApprove} disabled={busy}>Approve this action</button>
        <button className="reject" onClick={onReject} disabled={busy}>Reject</button>
      </div>
      <p className="muted small">Nothing is written until you approve. Reject writes nothing.</p>
    </section>
  );
}
