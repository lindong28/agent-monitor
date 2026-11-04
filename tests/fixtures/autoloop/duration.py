"""Deliberately faulty fixture for the marked autoloop acceptance branch only."""


def duration_seconds(value):
    """Convert an MM:SS duration to total whole seconds."""
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)
