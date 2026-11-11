# Latest quota plan replaces earlier plan records

On 2026-11-11 the maintainer clarified that an account's plan can change and the latest queried record should replace the older record. This supersedes the comparison and dual-source Web presentation in [20261105-655f2](20261105-655f2-quota-plan-sources.md); provider-specific tier names and source attribution remain applicable.

Use the existing observation-time selection: plan, percentages, source and timestamp come from the same selected quota reading. Both upgrades and downgrades replace the previous displayed plan; an older receipt cannot replace a newer observation. The plan cell, search and observation details use that selected plan without comparing it to local login metadata. When no reading plan is available, retain the existing login-metadata fallback and its unknown update time. Historical and unverified records retain their existing states.

Removing only the main-row marker would leave the older plan competing in details and search, so all three consumers follow the selected record. Snapshot raw fields remain compatible, credential files are not rewritten, and no provider calls, schedules, authentication or persistence behavior change. Local source rollback needs no data migration.
