# V2 Hash and Idempotency Contract — Review Draft

**Status:** Proposed for Phase 0 review, not an implemented or frozen production protocol. This refines [D03](03-contract-catalog.md), [D03a](03a-document-tool-contracts.md) and [D04a](04a-persistence-integrity-contract.md). It does not authorize changing existing public response behavior without the compatibility/versioning decision.

## Authorities and canonical bytes

Revision Service alone normalizes supported edit operations, computes the patch hash, creates the preview, verifies approval and commits. The authenticated client displays the preview and returns its issued hash; it must not construct an approval hash from model text. Runtime, MCP and Skill components cannot claim document approval authority.

For this **proposed Python-service profile**, canonical bytes are UTF-8 encoding of a JSON object serialized by the authoritative backend with `ensure_ascii=False`, `sort_keys=True`, `separators=(',', ':')` and `allow_nan=False`. Only validated JSON-native values are accepted; reject duplicate object keys at input, non-string object keys, non-finite numbers and Unicode containing unpaired surrogates. Normalize each allowlisted operation before serialization and reject unsupported or ambiguous input. Preserve operation array order, text codepoints and the distinction between absent and explicit null where the operation schema distinguishes them. Do not apply implicit Unicode normalization or silently coerce cell values.

The serializer is part of the contract version. Numeric inputs remain subject to the per-operation format schema; do not assume `1` and `1.0` or alternative numeric representations are interchangeable. Only the authoritative backend needs to generate these hashes: other languages should treat hashes as opaque. If another implementation must independently serialize, pin its exact numeric/string behavior with cross-runtime vectors before enabling it. This profile is **not** advertised as RFC 8785/JCS and is not a license to add a dependency.

## Patch hash v1

Compute `SHA-256(domain || canonical_bytes)` and expose lowercase hexadecimal, where `domain` is ASCII `mhv2:patch:v1` followed by a single LF byte. The canonical object has exactly:

- `schema`: literal `mhv2.patch.v1`.
- `document_id`: immutable target document ID.
- `base_revision`: positive revision number of the reviewed document.
- `base_sha256`: lowercase SHA-256 of the verified base bytes.
- `operations`: ordered, validated and normalized operations bound to the target source anchors.
- `artifact_sha256`: lowercase SHA-256 of the exact staged bytes that the reviewer can inspect.

Preview metadata must retain the canonical payload or enough versioned immutable data to reproduce its exact bytes. Compute and verify again before publication; do not use object metadata alone as proof of staged bytes. Approval separately binds `user_id`, `resource_id`, `base_revision`, patch hash, approver identity and fixed expiration 15 minutes from preview creation. Retrying never extends this expiration. Commit rechecks current authorization, all approval bindings, base revision and staged checksum before the single database publication transaction.

## Idempotency fingerprint v1

The durable key scope is `(authenticated_principal_id, operation, idempotency_key)` within the V2 environment. Validate key syntax and length before creating the operation; the precise public length/character policy remains subject to API owner review. Compute a separate fingerprint over canonical JSON with the domain ASCII `mhv2:request:v1` followed by LF. The canonical envelope has exactly `schema: 'mhv2.request.v1'`, `principal_id`, `operation` and `request`, where `request` is the validated, normalized, operation-specific semantic payload. Its schema must enumerate all fields that can alter the effect, source revision, model/tool/skill version or target resource. Request IDs, retry count and trace timestamps are not effect-defining fields.

On retry, reauthenticate and reauthorize the current principal before returning any cached result. Same key and same fingerprint returns the same durable operation/result; same key and different fingerprint returns a typed conflict. In-progress and terminal results remain distinguishable. Key retention must outlive the operation's retry and reconciliation window; expiry must not allow a completed external side effect to be replayed as a new request. Do not log raw document text, access tokens or credentials. When an effect depends on a credential, bind the server-controlled credential/version identity in the fingerprint; never place its secret in a request log or public artifact.

An internal fingerprint controls Mini-Hermes deduplication only. For external calls, persist the invocation and provider idempotency key **before** dispatch. Use a provider-supported idempotency key or queryable operation status when available. If an external effect may have happened and its result is unknown, mark the invocation for reconciliation; do not automatically retry merely because the local key matches.

## Review vectors and limits

[hash-vectors-v1.json](fixtures/hash-vectors-v1.json) contains synthetic patch and request vectors with domain prefix, payload, exact canonical JSON and expected SHA-256. The vectors were generated using Node's standard cryptography API from already canonically ordered integer/string payloads and verified by Python; this does **not** prove general JavaScript/Python serialization equivalence, especially for floating-point numbers or Unicode edge cases.

Before freeze, test duplicate-key rejection, non-finite and invalid Unicode input, scalar numeric precision, both domain prefixes, operation array ordering, changed source/artifact bytes, same-key/same-request retries, same-key/changed-request conflict, concurrent execution, expired approval, revoked permission and unknown external outcomes. Verify golden vectors against the actual V2 service implementation and disposable PostgreSQL; current tests validate only the proposed vectors/profile. Public V1 preview replay currently has different behavior described in the [compatibility manifest](07-open-webui-compatibility-manifest.md).
