# V2 Document Cursor Contract — Review Draft

**Status:** Proposed Phase 0 profile. This is an internal Document API/tool contract, not an Open WebUI public wire change or an implemented endpoint. It refines [D03](03-contract-catalog.md), [D03a](03a-document-tool-contracts.md) and the independent coverage rules in [D05](05-coverage-semantics.md).

## Scope and authority

A cursor continues one deterministic traversal of an immutable `(document_id, revision, source_sha256, build_id)`. The trusted Document API issues and verifies it. The caller supplies the same normalized operation scope on every page: operation, document/revision/build, sheet/range or source anchor, query/filters and sort order as applicable. The backend computes the scope digest from the validated request; the model cannot supply that digest or the authenticated principal. A cursor is neither an access token nor proof that a unit was delivered to the LLM.

Authorization is checked on every page, including replay of a previously successful cursor. The API must first authenticate and apply the existing disclosure policy before exposing document existence, cursor details or evidence. Verification does not create an index build or ingestion job. The cursor remains pinned to its build; activation of a newer build never redirects it.

## Encoding and canonical payload

The wire token is ASCII `mhv2c1.<kid>.<payload>.<tag>`. `kid` is a server-configured, non-secret key identifier containing 1–32 ASCII letters, digits, `_` or `-`; `payload` and `tag` use unpadded base64url. Decode strictly: exactly four segments, canonical base64url, at most 4096 ASCII characters before any decoding, valid UTF-8, unique JSON object keys, and the exact fields in [cursor-payload.schema.json](schemas/cursor-payload.schema.json). Reject non-finite numbers and malformed Unicode. A token over the ceiling fails as invalid without allocating an unbounded decoder buffer.

The signed bytes are ASCII `mhv2:cursor:v1\n` followed by the three literal token segments `mhv2c1.<kid>.<payload>` encoded as ASCII. `tag` is the complete 32-byte HMAC-SHA-256 over those bytes, encoded as unpadded base64url. Select the verification key by `kid`, compute the expected tag, and compare in constant time before trusting any payload field. Do not log the token, raw query or document text. The keys live in managed server configuration, outside persisted cursor data and model-visible context.

The payload JSON is serialized by the authoritative Python service using the [D03b canonical JSON profile](03b-idempotency-hash-contract.md#authorities-and-canonical-bytes). It contains:

| Field | Meaning |
|---|---|
| `v` | Literal integer `1`. |
| `principal_id`, `tenant_id` | Authenticated identity and isolation boundary at issuance. Current session must match; neither is accepted from tool arguments. |
| `operation` | One of `read_document_range`, `read_spreadsheet_range`, `search_document`. |
| `document_id`, `revision`, `source_sha256`, `build_id` | Immutable source/index binding. |
| `scope_sha256` | Digest of the complete normalized first-page semantic scope. |
| `position` | Next *unconsumed* item ordinal and optional field/codepoint offset, as below. |
| `iat`, `exp` | Integer UTC Unix seconds; `exp = iat + 900` for this profile. |

The scope digest is lowercase hex SHA-256 of ASCII `mhv2:cursor-scope:v1\n` followed by D03b canonical JSON bytes of `{ "schema": "mhv2.cursor-scope.v1", "operation": ..., "document_id": ..., "revision": ..., "source_sha256": ..., "build_id": ..., "scope": ... }`. `scope` is an operation-specific, validated object: selected sheet/anchor/range, normalized query and filters, and deterministic sort mode. Each operation owner must define its finite accepted input fields and ordering before freeze. Omitted and explicit null are distinct unless that operation schema explicitly equates them. Output budget, request ID, trace timestamp and page number are excluded because they do not change traversal. A continuation must repeat the first-page semantic scope; changed scope fails `CURSOR_SCOPE_MISMATCH`. This avoids embedding document text or arbitrarily long queries in the token.

## Position and page accounting

The traversal defines a stable total order of source units for its build. `position.item_ordinal` is zero-based and identifies the first item not fully consumed. At an item boundary, `position.fragment` is null. For an oversized item, `fragment` names the stable field being emitted and the next Unicode **codepoint** offset within that field. The field order and field identifiers are versioned by the operation schema. A fragment may be emitted only when every earlier field/item has been accounted for; advancing the offset requires actually emitting the exact slice `[old_offset, new_offset)`. A page ending inside a field retains the same ordinal. Once the final field is fully emitted, move to the next ordinal and set fragment to null. Never advance by requested limit, serialized length, or number of candidate records.

The exact complete serialized tool result, including cursor, evidence and envelope, must fit the injected character budget. A page must make progress, or return `OUTPUT_BUDGET_TOO_SMALL` without a next cursor. Empty terminal traversal returns `next_cursor: null`. A valid repeated cursor may replay the same page; clients deduplicate its anchored result and merge only matching revision/build intervals. A partial cell/row is not complete retrieval coverage until all required fields and fragments are accounted for by the [D05 proof](05-coverage-semantics.md#fragmented-units-and-coverage-accounting). This contract concerns source retrieval, not LLM-context coverage.

## TTL, rotation and build lifecycle

- Issue only with the active signing key. An old key may be `verify_only` until every cursor issued with it has expired, plus a five-minute operational margin; then remove it. The key registry records activation and last issue time. Emergency revocation may invalidate live cursors and must return a typed failure.
- Accept a cursor only when `iat <= now < exp`, `exp - iat = 900`, and `iat` is not earlier than the key's activation time or later than its last permitted issuance time. Clock synchronization is an operational prerequisite; no hidden expiry grace is granted.
- Keep a verified build readable for at least the 15-minute cursor lifetime after its last issuance, plus five minutes for in-flight requests, unless integrity, authorization or emergency retention policy requires early retirement. Record last issuance durably and serialize issuance against the collector's retirement claim; otherwise a concurrent read could issue a cursor for an already retired build. This internal retention write does not create an index or document job and must not be mistaken for a user-visible mutation. A retired or corrupt build fails explicitly; it must never continue on a replacement build.
- Key and build retention are separate. A key may verify a token whose build has retired, but that request still fails. Current authorization revocation wins over any retention promise.

The 15-minute TTL and five-minute margin are proposed R1 values; operations must confirm storage cost and deployment key management before the Phase 0 contract is frozen. A release cannot silently shorten a published TTL or delete the only verification key while live cursors remain.

## Error contract and checks

After the public endpoint's authentication/disclosure check: malformed token, unknown/revoked key, bad MAC or invalid payload yields `CURSOR_INVALID`; a valid signed token with expired time yields `CURSOR_EXPIRED`; a changed principal/tenant, operation, source identity or scope digest yields `CURSOR_SCOPE_MISMATCH`; an unavailable pinned build yields `CURSOR_BUILD_RETIRED` (or `INTEGRITY_FAILURE` for missing/corrupt committed bytes). Current permission denial follows the endpoint's existing `DOCUMENT_NOT_FOUND`/`FORBIDDEN` disclosure policy. None of these errors silently restarts at page one. The exact HTTP mapping and safe public problem body still require D07 review.

Contract tests must cover a synthetic long cell and Unicode multi-codepoint text; byte/codepoint distinction; budget shrink; exact page concatenation; replay; changed query/range/order/build/principal; revoked access; tampering; duplicate keys; malformed base64; expiry boundaries; rotation and emergency revocation; build activation/retirement; and a minimum envelope too large for the budget. Test through the actual result adapter so evidence and model-visible text describe the same emitted page. No such V2 implementation is claimed by this draft.
