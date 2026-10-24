This is synthetic basis metadata, not a genuine publication request or approval.
Archive and legal-record pins describe tiny fake test contents. Component files,
build identities, notices, evidence and explicitly test-only decisions are rebuilt
in a temporary directory by `tests/synthetic.py`. No actual IGN archives, regional
PMTiles, PBF, HGT or routing data is present or downloaded.

The current contract requires the inventory's exact upstream filenames, legal
URLs and notice terms. Keeping those identifiers exercises the real verifier;
they are never fetched by these tests. Full notice text comes from the pinned
application's code-owned registry. Only the test context substitutes the map
descriptor to recognize its 131-byte synthetic PMTiles file. Production exposes
no registry substitution or synthetic approval mode.
