# Local task evidence

Runtime state belongs to the checkout's `state/` directory and may be a deployment-owned symlink. Its ownership and maintenance rules are in [architecture](../docs/public-architecture.md#persistence-and-maintenance). Do not move it into a task directory.

Developer task materials are separate from source and business data. This maintainer environment uses `~/task-artifacts/`; each task records its inputs, logs, results and continuation state in its own directory. Materials are retained for manual cleanup. Existing historical task locations stay in place and can be linked from that entry; the entry does not imply that every older artifact has been indexed.

An ignored checkout-local `.local-data` symlink may point to an existing external task root. Verify its target before use; a missing or broken link does not mean the data has moved into the checkout. Contributors may use their own external location and record it in their task notes; no maintainer path or harness is required to run this project. Do not put credentials, provider profiles or production state in the evidence directory.

For source layout and affected tests, use the [responsibility map](../docs/public-architecture.md#responsibility-map) and [contribution guide](../CONTRIBUTING.md).
