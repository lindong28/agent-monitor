"""Raw model counts and indexed lookup, maintained for every archive writer."""


def initialize(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_model_counts'").fetchone()
    conn.execute("CREATE INDEX IF NOT EXISTS usage_events_model ON usage_events(json_quote(json_extract(entry, '$.model')))")
    conn.execute("CREATE TABLE IF NOT EXISTS usage_model_counts (model TEXT PRIMARY KEY, event_count INTEGER NOT NULL)")
    if not exists:
        conn.execute("INSERT INTO usage_model_counts SELECT json_quote(json_extract(entry, '$.model')), count(*) FROM usage_events GROUP BY json_quote(json_extract(entry, '$.model'))")
    # Separate triggers stay active when an older writer updates raw facts.
    for operation in ("INSERT", "UPDATE", "DELETE"):
        old = "" if operation == "INSERT" else """
            UPDATE usage_model_counts SET event_count=event_count-1
                WHERE model=json_quote(json_extract(OLD.entry, '$.model'));
            DELETE FROM usage_model_counts WHERE model=json_quote(json_extract(OLD.entry, '$.model')) AND event_count=0;
        """
        new = "" if operation == "DELETE" else """
            INSERT INTO usage_model_counts VALUES (json_quote(json_extract(NEW.entry, '$.model')), 1)
                ON CONFLICT(model) DO UPDATE SET event_count=event_count+1;
        """
        conn.execute("CREATE TRIGGER IF NOT EXISTS usage_events_models_%s AFTER %s ON usage_events BEGIN %s %s END" % (operation.lower(), operation, old, new))
    return not exists
