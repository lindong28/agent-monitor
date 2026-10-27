"""Per-server dashboard views; construction lives in the HTTP composition root."""
from dataclasses import dataclass

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .overview import OverviewView
    from .sessions import SessionsView
    from .network import NetworkReport


@dataclass
class ViewServices:
    overview: "OverviewView"
    sessions: "SessionsView"
    network: "NetworkReport"
