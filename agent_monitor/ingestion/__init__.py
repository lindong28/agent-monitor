"""Default ingestion composition; callers may construct isolated instances."""
from .project_identity import ProjectIdentityStore
from .usage_loader import UsageLoader

IDENTITY_STORE = ProjectIdentityStore()
USAGE_LOADER = UsageLoader(IDENTITY_STORE)
load_all_entries = USAGE_LOADER.load_all_entries
