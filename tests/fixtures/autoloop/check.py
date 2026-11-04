"""Protected acceptance check; the repair agent may only edit duration.py."""
from duration import duration_seconds

for value, expected in [("00:05", 5), ("01:30", 90), ("02:00", 120), ("12:59", 779)]:
    actual = duration_seconds(value)
    assert actual == expected, f"{value}: expected {expected} seconds, got {actual}"
print("Autoloop acceptance fixture: all four MM:SS cases passed")
