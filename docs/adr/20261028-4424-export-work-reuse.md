# Reuse work within one private export and write only changed buckets

Status: accepted for implementation, 2026-10-28. This is an engineering continuation of [incremental collection](20261028-4424-incremental-collection.md); it does not change archive authority, retention, accounting, freshness or snapshot schemas.

## Bucket reconciliation

Rollup still consumes the complete prepared bucket set and reconciles it within the existing transaction. It deletes only missing keys inside effective history authority, writes new or changed buckets, and leaves identical buckets untouched. Authority permits downward corrections; non-authoritative history retains protected-field shrink rejection and the existing date/model freeze. `buckets_written` and `usage_buckets_written` now count actual inserted or updated bucket rows; unchanged rows contribute zero. Deletions remain outside those counters, and metadata timestamps still update.

No cross-database acknowledgement or dirty-bucket cursor is introduced. Complete replay after archive commit and rollup failure remains the recovery mechanism. Reconciliation remains proportional to the complete bucket set; this change reduces bucket DML rather than eliminating all database writes or scans.

## Private export validation

The public statistics writer retains complete input validation and encoding before opening the destination, canonical JSON, metadata copying, row order and its full readback return value. Export's private staging path uses the same write primitive and then performs one real statistics readback, one rollup statistics calculation and one byte digest. Its small facts remain local to that invocation. Snapshot and WAL identity are checked before and after inspection and again after fsync before publication; observable replacement or mutation and a nonempty WAL abort publication and clean staging.

Metadata assembly can use those local facts without rereading the snapshot. Public manifest validation and public generation metadata construction still read their supplied paths on every call. Transfer-digest rejection precedes statistics semantic validation. Receiving, admission, publication leases and generation GC do not receive private facts and retain their own checks. This is not a global validation cache or protection against arbitrary same-UID filesystem attacks.

## Validation and limits

Regression coverage includes unchanged bucket SQL traces, authoritative reductions and orphan removal, legacy shrink/freeze, project authority and minimum-date boundaries, transaction rollback and complete cross-database replay. Export coverage counts real operations for empty, 20-event and 200-event synthetic snapshots, reads back all entries, rejects changes during inspection and before publication, and rejects invalid statistics even after transfer and manifest digests are recomputed. Existing writer tests preserve non-private targets on invalid late input.

Five rollup loader fixtures now explicitly disable managed-account discovery and session-link reads while preserving their supplied source fixtures. A filesystem sentinel rejects real account/session paths, including a positive control against unpatched account discovery. Initial validation exposed this isolation gap; that initial run consumed local account logs and is not acceptance evidence. No live provider, network or deployment validation is claimed.

Full-history serialization and export output remain. Streaming writes, source discovery, archive layout and pricing invalidation belong to other changes. Reverting this implementation restores redundant work without a data migration; reverting code alone cannot repair data already damaged by an unrelated faulty writer.
