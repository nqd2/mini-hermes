# Mini-Hermes V2 — Phase 0 Architecture Lock Pack

**Status: LOCKED / ACCEPTED — CONTRACT-FROZEN.**
Baseline: user-provided *Mini-Hermes V2 — Master Roadmap*.
Repository reference: local checkout of `nqd2/mini-hermes`, branch `feature/agent-runtime-production-refinement`, commit `3d1eeedfeed8124706bf666d4da5b0380e7ff9c1`.

## Contents (nine Phase 0 deliverables)

| ID | Artifact | Role | State |
|---|---|---|---|
| D01 | `01-adr-v2.md` | V2 architecture decision | Locked / Accepted |
| D02 | `02-architecture-spec.md` | Ownership and flows (8 components incl. Provider Gateway) | Locked / Accepted |
| D03 | `03-contract-catalog.md` | Internal/API/tool/event/cursor contracts | Locked / Accepted |
| D04 | `04-data-model-spec.md` | New DB schema (49 tables `mh_*`) & publication rules | Locked / Accepted |
| D05 | `05-coverage-semantics.md` | Semantics and proof conditions | Locked / Accepted |
| D06 | `06-format-capability-matrix.md` | Mandatory R1 formats and edit allowlists (PDF read-only) | Locked / Accepted |
| D07 | `07-open-webui-compatibility-manifest.md` | Frontend-facing contract audit & zero-Ollama elimination | Locked / Accepted |
| D08 | `08-test-plan.md` | Fixture/test and verification gates | Locked / Accepted |
| D09 | `09-agent-work-breakdown.md` | Task DAG (P0-01 to P0-15 and P1-01 to P6-06) | Locked / Accepted |

`fixtures/xlsx-f05-synthetic.xlsx` is a **synthetic** regression fixture (3 sheets, multiline cells, merged range, large cell, formulas; no original respondent data). `fixtures/xlsx-f01-f03-hidden-missing-cache.xlsx` adds a hidden sheet and an uncached formula. Checksums are listed in `fixtures/SHA256SUMS.txt`. `master-roadmap-source.md` is the supplied baseline; `phase0-status.json` is machine-readable review state.

## Grounding and uncertainty

**Directly verified in repository documents:** `AGENTS.md` / ADR-001 / ADR-002 require one AgentRuntime production path, Open WebUI ownership, fresh PostgreSQL/SQLAlchemy async baseline, strict revision approval, event persistence/replay, request budget limits, no production reset without separate approval. README confirms Office/PDF workflows and the Open WebUI integrated frontend/backend layout.

**Proposals, not established code behavior:** New tool names/DTOs, new schema, coverage proof schema, XLSX canonical model, read-only indexes, particular editing allowlists, new API version namespace and task decomposition. These require owner review and contract tests.

**Unverified:** Full source/API consumer inventory, live SSE and Socket.IO wire behavior, the remaining runtime characterization scenarios, original XLSX fixture, V2 tests for the supplemental fixture, V2 PostgreSQL bootstrap/backup/restore and staging gates. Selected V1 runtime/resource PostgreSQL tests have run on a disposable container; they do not prove V2 readiness. The after-check authorization revocation race was reproduced and mitigated for tested current-checkout writers; full writer, live-auth and V2 integration proof remains open. Do not mark Phase 0 completed using this package alone.

V2 parser/coverage tests against those fixtures are a Phase 2 implementation gate. They do not block Phase 0 on code that the roadmap schedules for Phase 2; Phase 0 must instead freeze the fixture plan and intended contracts.

## Gate before Phase 1

- [ ] D01 reviewed/accepted by architecture owner; no contradiction with ADR-001/002.
- [ ] D02-D06 owner signatures; concrete typed schemas checked against implementation.
- [ ] D07 full live frontend API/SSE inventory + tests, not just route categories.
- [ ] D08 characterization tests **written and executed**; fixtures under test source control.
- [ ] D09 owners assigned; shared module ownership and contracts frozen.
- [ ] Verified commit SHA recorded and `git diff --check` run in actual checkout.

## Source references

- `AGENTS.md`: https://github.com/nqd2/mini-hermes/blob/feature/agent-runtime-production-refinement/AGENTS.md
- `ADR-001`: https://github.com/nqd2/mini-hermes/blob/feature/agent-runtime-production-refinement/docs/adr/ADR-001-agent-runtime-production-path.md
- `ADR-002`: https://github.com/nqd2/mini-hermes/blob/feature/agent-runtime-production-refinement/docs/adr/ADR-002-agent-runtime-contract-domain-boundaries.md
- `README.md`: https://github.com/nqd2/mini-hermes/blob/feature/agent-runtime-production-refinement/README.md

No files in the user's GitHub repository, production storage, or database were changed.
