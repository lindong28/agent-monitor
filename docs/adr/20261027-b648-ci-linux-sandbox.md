# Prepare a sandbox only on disposable Linux CI runners

Accepted 2026-10-27 after independent decision review. Scope: fresh GitHub-hosted Linux test VMs, with no restored `.runtime` cache. This does not change the product installer, user machines, provider routing or production services.

The initial Ubuntu 26.04 job downloaded Electron successfully but its startup aborted because the bundled sandbox was not root-owned with mode 4755. A subsequent job could not satisfy the same requirements using the runner's preinstalled Chrome helper. The image's build scripts also make `/opt` writable, so merely changing that helper's mode would not establish a protected installation.

Download through the project's pinned, SHA-256-checked runtime installer, then install its matching sandbox helper into a root-owned mode-0755 directory under `/run`, with root ownership and mode 4755. Select this copy with `CHROME_DEVEL_SANDBOX` for normal installation and tests. Verify ownership and permissions, then require the normal installer startup check and regression suite to succeed. Do not restore an untrusted runtime cache: the existing installer's `present` path checks its receipt rather than every extracted file.

Using Ubuntu 22.04 would avoid this preparation but introduces an already announced image-retirement dependency. Disabling sandboxing or changing global namespace policy changes the security boundary more broadly. A dedicated AppArmor rule is another privileged, distribution-specific option. The fixed helper copy confines preparation to one disposable VM and is removed with it; the product installer continues to require an administrator-provided sandbox where necessary.

At decision time, startup with the prepared helper and the complete hosted suite were unverified. CI results for each published SHA are the execution evidence; this decision record does not establish platform support on its own. Rollback is a workflow change, without business data migration.
