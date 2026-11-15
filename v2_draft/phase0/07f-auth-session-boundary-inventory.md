# Authentication and human-session boundary — scoped Phase 0 draft

**Status:** Current-checkout source and selected ASGI characterization. This inventories the session boundary consumed by Open WebUI and AgentRuntime, not the full OAuth/LDAP/MFA or deployed cookie/security contract. It extends [D07](07-open-webui-compatibility-manifest.md).

## Frontend consumers and wire shape

| Consumer | Request / response assumption | Producer | Test evidence and change policy |
| --- | --- | --- | --- |
| `routes/auth/+page.svelte`: `userSignIn`, `userSignUp`, `getSessionUser` | JSON email/password or signup fields; `credentials: include`. A 200 may contain a session token, MFA `next_step=verify`, or `next_step=pending`; the caller must inspect the variant. | `POST /api/v1/auths/signin`, `/signup`; `GET /api/v1/auths/` in `routers/auths.py` | Existing frontend `auths/phase0-contract.test.ts` checks selected request/variant/error behavior. Preserve the current versioned session and challenge shapes. |
| Root/app layouts, account/sidebar and channel input: `getSessionUser` | Bearer token plus `credentials: include`; receives identity, role, expiry and effective `permissions`. An HTTP JSON `detail` error is thrown as a string by the adapter. | `GET /api/v1/auths/` | Existing frontend test; backend `test_v2_phase0_auth_contract.py` covers response/cookie and selected auth-dependency behavior. |
| Root layout, sidebar and pending account: `userSignOut` | `POST /api/v1/auths/signout` with cookies; successful frontend wrapper clears session storage. | `routers/auths.py:signout` attempts per-token invalidation when Redis is configured, clears request session where possible and deletes token/other auth cookies. | Frontend test checks request and storage clear; ASGI characterization below exposes the no-Redis gap. Live browser/cross-tab behavior remains unverified. |

The route obtains a session token from bearer header, then `token` cookie, then `request.state.token`. `get_current_user` uses the same bearer-before-cookie precedence and resolves JWT user identity from the current user table. Its `get_human_user` guard admits only a JWT with `typ=session`; API-key and automation/subagent credentials cannot use the human session endpoint. `get_verified_user` separately requires a current `user` or `admin` role for ordinary verified routes. These are distinct authorization contracts: a valid human session is not an automatic document or model permission.

`is_valid_token` compares the token's `session_stamp` with the current authentication record, checks MFA state when required, and can consult Redis token/user revocation markers. Account-state lookup errors fail closed; the current Redis-error branch accepts a token and logs the error. `invalidate_token` records a per-token revocation only when Redis is available. This is current behavior to review with the Auth owner as a production availability/security decision, not proof of V2 revocation guarantees. `get_session_user` checks decoded expiry and refreshes the HTTP-only token cookie using configured SameSite/Secure values. `create_signin_response` can return pending or MFA challenge before issuing a session; completed sessions carry `typ=session` and the current stamp.

## Executed characterization

`packages/backend/tests/test_v2_phase0_auth_contract.py` includes two prior ASGI cases with identity and token decoding mocked. Two additional cases exercise the actual `get_current_user → is_valid_token → get_human_user → get_session_user` dependency chain with real signed JWTs and in-memory user/MFA record substitutions. They verify a valid session returns identity and an HTTP-only cookie, an automation token receives HTTP 403, and a stale session stamp receives HTTP 401 before user lookup. A fifth case signs out without Redis, observes the cookie deletion, then successfully reuses the same bearer JWT. This reproduces the no-Redis per-token logout gap. The tests do not use a real authentication database, Redis, OAuth, MFA challenge, browser, load balancer or deployed cookie policy.

## Remaining compatibility gate

- Capture and test real sign-in, MFA, pending, refresh, sign-out and revoked-session flows using disposable database and Redis; include bearer/cookie precedence, expiry, CSRF/origin behavior and two browser tabs.
- Decide and test the V2 per-token logout guarantee when Redis is missing or fails. The observed no-Redis behavior cannot be described as server-side token revocation; a durable revocation mechanism or an explicit fail-closed operational dependency is required before claiming immediate logout invalidation.
- Pair OAuth/LDAP/trusted-header/SCIM role and group updates with current authorization and document publication. Preserve the permission refresh contract consumed by layouts and chat.
- Freeze the V2 Auth Service principal DTO and role-revocation handoff to AgentRuntime/Revision Service. A token-carried role or cached frontend permission must not authorize a document commit.
- Record redacted golden HTTP and cookie examples and owner-reviewed error variants before Phase 0 sign-off.
