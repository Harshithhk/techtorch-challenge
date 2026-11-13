# Candidate AI Tool Usage Log

**Instructions for Candidates:**
Please document all prompts you use with AI tools during this challenge. This helps us understand your problem-solving approach and AI tool utilization skills.

---

## AI Tool Usage Log

### Prompt 1
**Tool Used:** Claude Code (Anthropic CLI, plan mode)
**Context:** Read the challenge PDF and the four JSON files, then produce a phased build plan for the revenue-leakage agent before any code is written.
**Prompt:**
```
Enter plan mode. Do not write any code until I approve the plan.
Read docs/AI Engineer Technical Challenge – The Revenue Leakage Agent .pdf first ...
read the schemas in /data directory as assumptions to verify against the real data.
Goal: Build a stateful conversational "financial detective" agent for revenue leakage ...
Plan and build it in phases. ... Only interrupt me on critical cases.
We should not precompute any findings or store any computed values.
```
**Result:** Plan file with architecture, PDF-vs-brief conflict table, schema findings from the real data (no schema files exist; fields read from the JSON), and a 7-phase plan with tests per phase.
**Follow-up:** Answered four clarifying questions (Anthropic Claude as LLM; React + Vite UI; configurable as-of date defaulting to today; docker-compose Postgres). Approved the plan.

### Prompt 2
**Tool Used:** Claude Code
**Context:** Phase 0 scaffold.
**Prompt:** "Exited plan mode, proceed with Phase 0 of the approved plan."
**Result:** uv backend project, `app/settings.py` (pydantic-settings), `.env.example`, `.gitignore`, `docker-compose.yml` (Postgres 16 on host port 5433), settings tests passing, Postgres connection verified.
**Follow-up:** Host port 5432 was already held by a local Homebrew Postgres, so the compose mapping moved to 5433.

### Prompt 3
**Tool Used:** Claude Code
**Context:** Phase 1 (domain core): money, FX, schedule, detectors, proposals, tests. Commit after each module.
**Prompt:** "Proceed to phase 1. Make sure you commit after each phase or after completing meaningful modules."
**Result:** Pure Decimal domain with 62 tests. Scenario tests check the four real JSON files against hand-worked expectations. Findings are computed live, nothing is stored. Four commits in total.
**Follow-up:** Fixed three test-side mistakes (a wrong expected id, a fixture with one gap instead of three, a weak assertion). The domain code itself did not need changes.

### Prompt 4
**Tool Used:** Claude Code
**Context:** Phases 2 to 6 (storage, tools, agent graph and guardrails, API and observability, UI), run without a confirmation stop between phases.
**Prompt:** "proceed till phase 6 without my confirmation. Write Phase completion summaries after each phase completion."
**Result:** Storage with atomic sandbox writes and Postgres users and checkpointer; tools with stale-ledger protection and a registry that cannot reach write functions; a LangGraph graph with the approval interrupt, injection and off-topic guard, and numeric grounding; a FastAPI app with PII-masked Langfuse; a React chat UI with an approval card. 158 backend tests and 7 frontend tests.
**Follow-up:** Running against the real Claude models and Postgres found six defects, all fixed with tests. Among them: the classifier flagged legitimate undo requests as injection, the grounding check mistook plan ids for figures, and replies leaked model thinking blocks. The full live conversation (investigate, follow-up, make-good, approve, rollback, refusals) was run against the server.

### Prompt 5
**Tool Used:** Claude Code
**Context:** Reviewing the classifier after the live run.
**Prompt:** Not a separate prompt. The rule "injection means bypassing approval, not asking for changes" was written into the classifier prompt, with examples, and checked against five sentences on the live model.
**Result:** Undo and rollback requests are now on topic and not flagged. Override attempts are still refused.

---

**Note:** Entries are appended per phase. Reflection on tool effectiveness will be added at the end of the project.
