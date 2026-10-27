"""Isolated component instances for HTTP composition tests."""
import server
from agent_monitor.collection import CollectionCoordinator


def reset_collection():
    server.COLLECTION = CollectionCoordinator(
        server.QUOTA_REFRESH, server.QUOTA_VIEW.remember_published, server.SERVER_INSTANCE_ID
    )
