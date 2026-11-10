# Account selection survives navigation and reload

Accepted 2026-11-10 after independent decision review and a targeted scope correction.

The account page loses its search, filter and unsubmitted selection when its controller is recreated. A real browser check reproduced selecting one account, reloading, and returning to an empty selection. Preserve those reader choices in one origin-scoped `sessionStorage` entry owned by the account controller. The server remains authoritative for account identities, eligibility, operations and receipts.

Restore the draft after reading the account snapshot and validate each selected profile ID, workspace ID and email against that snapshot. Drop missing, disabled, ineligible or changed identities on subsequent snapshots too. Ending selection clears the selected identities and batch mode. No valid draft means no initial selection. Overview account links still locate an account without selecting it.

The lifetime is the browser page session, not permanent or cross-device persistence. Browser duplication, restoration or an opener may copy a draft into a new page session; the copied choices receive the same identity validation and fresh action confirmation. Do not promise that every new tab is empty. No credentials, device capabilities, confirmations or submitted operation payloads are stored, and restoring the draft never sends a provider request. Storage failure must leave the page usable and explain that choices cannot be retained.

URL state would disclose selected identities in copied links; localStorage and server storage add unnecessary cross-tab or cross-device sharing. The existing generic reader state only stores focus, scrolling and disclosures; extending the account controller avoids capturing unrelated sensitive form controls. One overwritten entry has no accumulating history and needs no server migration. Existing exact-scope provider confirmation is unchanged.

Related decisions: the 2026-10-30 recovery task card preserves selection during recovery; the 2026-11-05 Overview simplification keeps quota filtering separate from explicit account selection; the 2026-11-06 task-centered quota UI supports direct and SPA account-page entry without implicit selection. This change supplies the missing controller-recreation lifetime.

At decision time implementation and browser acceptance remained pending. Acceptance covers reload/navigation, explicit clearing, absent or invalid storage, inherited drafts, identity changes and removed/disabled accounts; provider calls are unnecessary for these checks. Independent review found the initial new-tab isolation claim too broad; narrowing it to browser page-session semantics resolved that finding without adding another isolation mechanism.
