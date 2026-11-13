"""The required graph test for the approval interrupt.

Pause: a proposal stops the graph and nothing is written. Approve: the write happens once.
Reject: nothing is written and the audit log records the rejection. Rollback also pauses for approval.
"""
import pytest

from app.settings import get_settings
from tests.agent.fakes import say, tool_call
from tests.agent.helpers import build

pytestmark = pytest.mark.skipif(not (get_settings().data_dir / "invoices.json").exists(), reason="real data not present")

MAKE_GOOD = ("propose_make_good_invoice", {"plan_id": "C-1001", "period_start": "2025-09-01"})


async def test_proposal_pauses_the_graph_and_writes_nothing(tmp_path):
    """The graph stops at the interrupt. The sandbox records and the audit log are both empty."""
    service, _, _, store = build(tmp_path, [tool_call(*MAKE_GOOD)])
    result = await service.send("t-pause", "alice", "Make good the September gap for C-1001")

    assert result.status == "awaiting_approval"
    assert result.pending["amount"] == {"amount": "8000.00", "currency": "USD"}
    assert "8000.00" not in result.pending["summary_title"]  # the card shows the amount; the title does not repeat it
    assert result.reply.count("8000.00") == 0
    assert store.read_records("invoices") == []
    assert store.read_audit() == []
    assert (await service.pending("t-pause"))["action_kind"] == "make_good_invoice"


async def test_approve_resumes_and_writes_exactly_once(tmp_path):
    """Approve resumes the graph. One invoice is written and one 'applied' event is logged."""
    service, _, llm, store = build(tmp_path, [tool_call(*MAKE_GOOD)])
    await service.send("t-approve", "alice", "Make good September")
    llm.script.append(say("Applied the make-good for 8000.00 USD."))

    result = await service.decide("t-approve", "approve")

    assert result.status == "answered"
    assert "8000.00" in result.reply
    records = store.read_records("invoices")
    assert len(records) == 1 and records[0]["amount_invoiced"] == "8000.00"
    assert [e["event"] for e in store.read_audit()] == ["applied"]
    assert await service.pending("t-approve") is None


async def test_reject_resumes_and_writes_nothing(tmp_path):
    """Reject resumes the graph without any write. The only trace is a 'rejected' audit event."""
    service, _, llm, store = build(tmp_path, [tool_call(*MAKE_GOOD)])
    await service.send("t-reject", "alice", "Make good September")
    llm.script.append(say("Understood, no change was made."))

    result = await service.decide("t-reject", "reject")

    assert result.status == "answered"
    assert store.read_records("invoices") == []
    assert store.read_applied() == []
    assert [e["event"] for e in store.read_audit()] == ["rejected"]


async def test_rollback_also_requires_approval(tmp_path):
    """Rollback pauses first: the record stays until approval, then is removed and logged."""
    service, _, llm, store = build(tmp_path, [tool_call(*MAKE_GOOD)])
    await service.send("t-rb", "alice", "Make good September")
    llm.script += [say("Applied the make-good for 8000.00 USD.")]
    await service.decide("t-rb", "approve")

    llm.script.append(tool_call("propose_rollback", {}, "call-rb"))
    paused = await service.send("t-rb", "alice", "Roll that back")
    assert paused.status == "awaiting_approval"
    assert len(store.read_records("invoices")) == 1  # still there while the rollback waits

    llm.script.append(say("Rolled back the make-good."))
    await service.decide("t-rb", "approve")
    assert store.read_records("invoices") == []
    assert [e["event"] for e in store.read_audit()] == ["applied", "rolled_back"]


async def test_rejected_rollback_keeps_the_record(tmp_path):
    """Rejecting a rollback leaves the applied invoice in place."""
    service, _, llm, store = build(tmp_path, [tool_call(*MAKE_GOOD)])
    await service.send("t-rbr", "alice", "Make good September")
    llm.script.append(say("Applied 8000.00 USD."))
    await service.decide("t-rbr", "approve")

    llm.script += [tool_call("propose_rollback", {}, "call-rb"), say("Kept the make-good.")]
    await service.send("t-rbr", "alice", "Undo it")
    await service.decide("t-rbr", "reject")
    assert len(store.read_records("invoices")) == 1


async def test_second_proposal_in_one_batch_is_refused(tmp_path):
    """Two proposals in one model turn: the first waits, the second is refused and not stored."""
    from langchain_core.messages import AIMessage

    both = AIMessage(content="", tool_calls=[
        {"name": "propose_make_good_invoice", "args": {"plan_id": "C-1001", "period_start": "2025-09-01"}, "id": "a"},
        {"name": "propose_make_good_invoice", "args": {"plan_id": "C-1001", "period_start": "2025-11-01"}, "id": "b"},
    ])
    service, _, llm, store = build(tmp_path, [both])
    result = await service.send("t-two", "alice", "Make good Sep and Nov")
    assert result.status == "awaiting_approval"
    assert result.pending["period_start"] == "2025-09-01"
    assert store.read_records("invoices") == []
    state = (await service._graph.aget_state({"configurable": {"thread_id": "t-two"}})).values
    tool_messages = [m for m in state["messages"] if getattr(m, "name", None) == "propose_make_good_invoice"]
    assert len(tool_messages) == 2
    assert "already waiting" in tool_messages[1].content


async def test_message_while_waiting_is_refused_without_calling_the_model(tmp_path):
    """A question sent while a proposal waits is refused. The model is not called again (the script would fail)."""
    service, _, llm, _ = build(tmp_path, [tool_call(*MAKE_GOOD)])
    await service.send("t-busy", "alice", "Make good September")
    result = await service.send("t-busy", "alice", "yes")
    assert result.status == "busy"
    assert len(llm.seen) == 1  # only the proposal turn called the model


async def test_decide_without_pending_says_so(tmp_path):
    """Approving when nothing is waiting is a clean no-op."""
    service, _, _, store = build(tmp_path, [])
    result = await service.decide("t-none", "approve")
    assert result.status == "no_pending"
    assert store.read_records("invoices") == []
