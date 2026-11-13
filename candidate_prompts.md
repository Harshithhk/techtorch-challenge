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

---

**Note:** Entries are appended per phase. Reflection on tool effectiveness will be added at the end of the project.
