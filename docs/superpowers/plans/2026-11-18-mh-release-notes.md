# Mini-Hermes Release Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show checked-in Mini-Hermes release notes once per release and remove the upstream update notice and network check from the product flow.

**Architecture:** A validated release manifest and build-supplied `MH_RELEASE_VERSION` drive one backend endpoint. The Svelte shell shows a separate Mini-Hermes modal using each user's last-seen release value. Legacy Open WebUI endpoints keep their response shape without contacting GitHub, and licensed attribution stays visible.

**Tech Stack:** FastAPI, Python standard library, pytest, Svelte, TypeScript, Vitest, GitLab CI, Docker.

**Spec:** [Tiered model routing and release notes design](../specs/2026-11-18-tiered-model-routing-release-notes-design.md). This plan covers only release notes; [tiered routing](2026-11-18-tiered-model-routing.md) can be built independently.

## Global Constraints

- Work in the supplied checkout without a worktree; leave `references/open-webui` and the upstream `packages/backend/CHANGELOG.md` unchanged.
- Preserve Open WebUI copyright/license notices and visible attribution, including protected About and release identifiers.
- Do not add a dependency or call a remote release service. `MH_RELEASE_VERSION=dev` locally; a production build requires a matching manifest entry.
- Do not deploy to production as part of this plan. Existing `/api/version/updates` and `/api/changelog` retain their response shapes for callers.

## Review Focus

- A production build with a missing or mismatched manifest version must fail in CI (Task 1 test).
- A manifest entry containing HTML or a link must be rejected rather than rendered as markup (Task 1 test).
- An old API caller must receive a compatible local response without outbound GitHub traffic (Task 2 test).
- A user who closed a release modal must not see it again for that version, but must see the next version (Task 3 test).
- The Open WebUI About attribution must remain visible after removing its update controls (Task 3 test).

---

### Task 1: Local release manifest and build contract

**Files:** Create `packages/backend/open_webui/mini_hermes/releases.json`, `packages/backend/open_webui/mini_hermes/release_manifest.py`, `scripts/check_mh_release_manifest.py`, and `packages/backend/tests/test_mh_release_manifest.py`; modify `Dockerfile` and `.gitlab-ci.yml`.

**Interfaces:** `load_release(version: str, manifest_path: Path | None = None) -> ReleaseEntry` in `release_manifest.py`, where `ReleaseEntry` has `version: str`, `released_at: date`, and `sections: dict[str, list[str]]`. `dev` returns a non-popup local entry. `validate_release_build(version: str, manifest_path: Path) -> None` raises on missing, malformed, or mismatched production entries. Manifest entries contain plain text only.

- [ ] Write tests for `dev`, a valid production entry, missing version, invalid date/section, HTML or URL text, and `MH_RELEASE_VERSION` differing from the manifest entry.
- [ ] Run `python -m pytest tests/test_mh_release_manifest.py -q` from `packages/backend`; confirm expected failures.
- [ ] Add the checked-in manifest and loader. Add `MH_RELEASE_VERSION` build argument/environment to `Dockerfile`; run `scripts/check_mh_release_manifest.py` as a GitLab test gate for `main` and release builds, requiring an explicit non-`dev` version on `main`. Pass the same version to `docker build`. Keep the local/dev default `dev`.
- [ ] Run the new test and run the checker once with `dev` and once with a deliberately mismatched production version; confirm success and failure respectively.
- [ ] Commit the manifest, validator, build wiring, and test.

### Task 2: MH release API and offline legacy endpoints

**Files:** Create `packages/backend/open_webui/mini_hermes/release_routes.py` and `packages/backend/tests/test_mh_release_routes.py`; modify `packages/backend/open_webui/main.py` and `packages/backend/open_webui/env.py`.

**Interfaces:** `GET /api/v1/mini-hermes/release` returns `{version, released_at, sections}` from `load_release(os.environ['MH_RELEASE_VERSION'])`. Keep `/api/version/updates` as `{current, latest}` with `latest == current` and `/api/changelog` as the same version-keyed object shape, populated from local MH text. No route invokes `api.github.com`.

- [ ] Write tests for exact MH response, `dev` behavior, legacy response shapes, malformed manifest failure, and no network call even when the old `ENABLE_VERSION_UPDATE_CHECK` environment flag is true.
- [ ] Run `python -m pytest tests/test_mh_release_routes.py -q`; confirm expected failures.
- [ ] Register the MH route, make the legacy endpoints local-only, and default the old update feature flag off. Do not change `/api/version` or erase upstream source notices.
- [ ] Run the route tests plus `tests/test_ci_pipeline_contract.py`; confirm pass.
- [ ] Commit API and compatibility behavior.

### Task 3: MH modal, per-user dismissal, and upstream notice removal

**Files:** Create `packages/frontend/src/lib/apis/mini-hermes/release.ts`, `packages/frontend/src/lib/apis/mini-hermes/release.test.ts`, `packages/frontend/src/lib/components/MhReleaseModal.svelte`, and `packages/frontend/src/lib/utils/mhReleaseVisibility.ts`; modify `packages/frontend/src/routes/(app)/+layout.svelte`, `packages/frontend/src/lib/components/chat/Settings/About.svelte`, `packages/frontend/src/lib/components/admin/Settings/General.svelte`, and `packages/frontend/src/lib/components/common/InterfaceSettings.svelte`. Retain the licensed `ChangelogModal.svelte` source and attribution markup; remove it from the active MH flow.

**Interfaces:** `getMhRelease(): Promise<{version: string; released_at: string; sections: Record<string, string[]>}>`; `shouldShowMhRelease(current: string, lastSeen: string | undefined, enabled: boolean): boolean`. Store the per-user last-seen value as `ui.mh_last_seen_release_version` via existing `updateUserSettings` on modal dismissal; `dev` never pops automatically. The manual “What's New” actions open the MH modal for both Free and Plus users.

- [ ] Write Vitest tests for first display, dismissal, same-version suppression, next-version display, `dev` suppression, text-only rendering contract, and no upstream update API call or release link in the active layout. Add a source assertion that the protected Open WebUI About attribution remains.
- [ ] Run `node node_modules/vitest/vitest.mjs run --passWithNoTests src/lib/apis/mini-hermes/release.test.ts` from `packages/frontend`; confirm expected failures.
- [ ] Call the MH release endpoint on authenticated layout mount. Replace the automatic upstream popup/toast with the new MH modal, wire dismissal to user settings, and route manual About/Admin actions to it. Remove upstream check controls and requests from About, Admin General, and the active layout. Remove the obsolete update-toast switch and relabel the existing What's New switch for MH notes in Interface Settings. Keep the existing `BuildVersion` and visible Open WebUI About identifier.
- [ ] Run the focused test and `node node_modules/vitest/vitest.mjs run --passWithNoTests`; confirm pass. Run `npm run check` and record any unrelated existing failures separately.
- [ ] Check no active UI path calls `/api/version/updates`, no GitHub release request is made, and `git diff --check` passes; commit the frontend behavior.
