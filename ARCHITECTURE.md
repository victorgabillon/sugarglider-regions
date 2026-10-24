# V2-only publisher

This supersedes the 23 October dual-site handoff. The new publisher supports one
current five-component V2 snapshot, IGN evidence schema 3 / basis 2, exact bound
supplementary notices and an independently pinned operator approval. No V1 reader,
catalog adapter, combined-site assembler, migration or retention framework belongs
in this implementation. `/v2/` remains the required URL namespace.

The application verifier is the single owner of regional validation, publication
authorization and notice rendering. Pin its reviewed source commit, rather than
copying its schemas and legal strings into another repository. The host rejects
older evidence before calling its public verification path. It exposes no private
or compatibility mode. This dependency currently points to the reviewed notices
commit `5257bdc839c0a1a5a648909fd16cdf2641263af8`; application review/merges remain
separate from this draft and no merge or publication is implied by the pin.

## Preparation

Inputs are one archive and its independent SHA-256, exact evidence and operator
approval with an independent trusted digest, the canonical HTTPS `/v2/` base URL,
and a separately publisher-approved V2 policy with its independent digest. A policy
marker alone is not authority. Do not reuse the existing public policy implicitly.

Verify into a fresh temporary directory, using the pinned application verifier.
Require one advertised V2 region, all five components, exact notices and all file
hashes. Recheck the catalog against the operator-selected base URL. Place that
snapshot under `v2/`, place the exact approved V2 policy at `privacy/index.html`,
and produce a small root landing page and a deterministic file inventory. Bound
the entire result. Transfer the verified site and records into a freshly claimed
output only after every check succeeds; never replace an existing output.
Failed preparation removes its own temporary work and leaves source inputs and
any earlier prepared site alone.

The proposed policy location retains the current public URL shape. The old live
site and existing privacy bytes remain untouched until explicit cutover. The policy
file retained in this Git repository is historical live-site source, not an input
automatically approved for V2.

## Delivery and rollback

The read-only delivery check uses canonical HTTPS URLs from the verified snapshot,
rejects redirects and transformed responses, streams every file within its exact
size/hash bounds, and checks Android-origin CORS plus a single PMTiles byte range.
It has no upload, deployment or automatic retry behavior. Local HTTPS acceptance
uses only synthetic data and a temporary test certificate; unit tests use no network.

Rollback selects a previous compatible V2 archive plus its own evidence/approval
and approved policy, then uses exactly the same preparation and delivery checks.
No old catalog conversion, state migration, data merge or automatic historical
retention is needed. Never overwrite bytes under an immutable build ID. Keep any
required historical archives separately from this publisher.

## Workflow boundary

PR/push CI runs synthetic tests only. The manual preparation workflow has read-only
repository access and uploads an ordinary review artifact. It has no Pages publish
step, Pages write permission, OIDC deployment permission, release creation or live
site mutation. A separately authorized cutover must review the actual V2 artifact,
its own approved policy, delivery behavior and a compatible V2 rollback artifact.
No deployment is authorized by this PR, its merge, a source-code pin or green tests.
