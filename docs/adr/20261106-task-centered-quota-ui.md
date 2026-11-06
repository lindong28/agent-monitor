# Task-centered quota UI

The user approved restructuring Overview around reading usage and quotas, refreshing a selected account and finding the next step for exceptions. This supersedes the presentation placement in 20261105-8e42-overview-simplification; account identity, backend operations and restrictions keep their existing authority.

Overview contains the comparison and a short machine-update summary. Collection owns machine/service quota diagnostics and automatic-refresh enrollment. Accounts is the complete account-management task, including query, login, maintenance, batch sending, receipts and recovery. Both client navigation and direct page loads initialize the configured runtime before the account controller. The account controller retains its page lifetime, operation polling and abort behavior.

An identity-bound Overview action navigates using a unique managed profile ID. The refresh task focuses that profile's refresh button after its local snapshot loads; it neither submits a provider request nor changes batch selection. This adds a deliberate action step while reusing the existing device pairing, one-time egress confirmation, revision binding and Agent Monitor route. No second provider driver or direct upstream path is introduced. Non-unique and unmatched observed identities do not receive a managed action.

State remains separate from historical receipts. Machine pauses are not represented as individual account failures. Short account restriction markers retain their detailed evidence in existing account details. Batch sending is secondary and individual sending remains available in account details. No persisted data migration is required.
