# Effective authorization writer inventory — Phase 0

**Status:** Source audit of the current checkout, pending contract-owner review. This records the V1 PostgreSQL authorization gate and one additional move-path regression. It is not the V2 authority protocol or a staging sign-off.

## Authorization inputs used by document publication

`commit_preview` takes the shared authorization gate, reads `User.role` from PostgreSQL under that gate, and calls `accessible_document` for write access. `has_document_access` allows an active admin, the document owner, a direct `mh_document` grant, or a grant/ownership inherited through the folder ancestry. Group grants include inherited group membership. Consequently, publication depends on the following mutable inputs, not just the direct grant row.

| Input | Current-checkout writer | Gate in the writer transaction | Remaining proof |
| --- | --- | --- | --- |
| `User.role`, including pending/deactivation | `Users.update_user_role_by_id`, `Users.update_user_by_id` when `role` is supplied, `Users.delete_user_by_id`; OAuth and SCIM call these methods | Exclusive for role write and final user delete; group removal during delete has its own exclusive transaction | Live authentication, OAuth/SCIM callbacks and deletion failure/retry semantics |
| `AccessGrant` for `mh_document` / `mh_folder` | `AccessGrants.grant_access`, `revoke_access`, `revoke_all_access`, `set_access_control`, `set_access_grants`, `replace_access_grants` | Exclusive | Every external caller and grants written outside these methods, including DB roles and direct SQL |
| Group membership and parent hierarchy | `Groups.set_group_user_ids_by_id`, `update_group_by_id`, `delete_group_by_id`, `delete_all_groups`, `remove_user_from_all_groups`, `sync_groups_by_group_names`, `add_users_to_group`, `remove_users_from_group` | `hierarchy_transaction` takes exclusive authorization gate before hierarchy lock | Concurrent external identity sync, process restart and lock order with every caller |
| Document folder edge | `move_document` changes `Document.folder_id` | Exclusive; now re-reads live role under gate | Concurrent move/revocation and live route test |
| Document and folder owner/topology | `create_document`, personal root and `create_folder` set owner/parent on creation. Source search found no live reassignment of `Document.owner_id`, `Folder.owner_id`, or `Folder.parent_id` | No gate on creation; these paths do not revoke an existing document's inherited rights | V2 schema and future reassignment/migration paths must be gated before publication |

`Groups.create_groups_by_group_names` inserts empty groups without the hierarchy gate. With no membership or grants in the same operation, this does not change current effective document permission. It must use the protocol if extended to attach members, grants or a parent in one transaction. Deletions of `AccessGrant` in model, tool and skill management are filtered to their own resource types; group deletion removes group grants inside `hierarchy_transaction`.

`UserStatus` updates only emoji/message/expiration fields, not account role. The direct assignment `user.role = determined_role` in OAuth follows `Users.update_user_role_by_id` and updates the in-memory user; the database role write uses the gated method. SCIM active-state updates pass a role through `Users.update_user_by_id`. These observations are source-level and do not prove that external identity systems participate in the database transaction.

## Newly identified stale-role path

The Mini-Hermes move route passes `is_admin=user.role == 'admin'` to `move_document`. Before this audit, `move_document` acquired the exclusive gate but used that earlier flag to authorize the document and destination folder. A PostgreSQL regression first reproduced a move after an admin demotion committed, despite the actor no longer having write access. The method now reads current role under the gate, denies inactive/missing users and reduces the passed admin flag to `role == 'admin'`. It expires ORM state before document and folder checks. The test is sequential after demotion; a separate concurrent barrier test and live authentication path remain required.

## Contract requirements before freeze

1. Keep a single PostgreSQL `READ COMMITTED` transaction for authorization and publication; acquire the shared gate before fresh authority reads and hold it through commit. Every writer of any listed input takes the exclusive gate before mutation and holds it through its commit.
2. Freeze the lock order across auth gate, document, preview/approval and group hierarchy. Do not allow a helper commit to release a gate before the protected write. Any new owner/topology mutation must join this inventory before code lands.
3. Restrict database roles so application writers cannot bypass the protocol through direct SQL. Define the external identity and emergency-administration path; fail closed when current authority cannot be established.
4. Add executable tests for both serialization orders of role, direct/inherited grant, group and document move changes; include role changes during a move, unavailable authority and retry. Test with real PostgreSQL transactions, live route/auth integration and configured object storage before a staging gate.

## Source and evidence boundary

- Authority reader: `packages/backend/open_webui/mini_hermes/library.py` and `revision_service.py`.
- Gate and writers: `packages/backend/open_webui/internal/db.py`, `models/access_grants.py`, `models/groups.py`, `models/users.py`, `mini_hermes/library.py`, `utils/oauth.py`, `routers/scim.py`.
- Race tests: `packages/backend/tests/test_resource_indexing_postgres.py`; prior reproduced races and limitations are in [D04b](04b-authorization-publication-race.md).
- Search covered Python runtime source under `packages/backend/open_webui` for ORM writes to `User`, `Group`, `GroupMember`, `AccessGrant`, `Document.folder_id` and folder owner/parent. Migrations contain historical direct document updates and are outside this runtime gate. Other languages, database procedures, operational SQL, plugins and future V2 writers are not covered by this search.
