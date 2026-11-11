"""Display projection of a Codex earned-reset observation, never a redemption."""
import math


def normalize(value, *, wire=False):
    if not isinstance(value, dict):
        return None
    count = value.get("availableCount" if wire else "available_count")
    if type(count) is not int or count < 0:
        return None
    rows = value.get("credits")
    details = None
    if isinstance(rows, list):
        details = []
        for row in rows:
            if not isinstance(row, dict) or (wire and row.get("status") != "available"):
                continue
            key = "expiresAt" if wire else "expires_at"
            detail = {}
            if key in row:
                expiry = row[key]
                if expiry is None or (type(expiry) in (int, float) and math.isfinite(expiry)):
                    detail["expires_at"] = expiry
            details.append(detail)
    return {"available_count": count, "credits": details}
