"""Sovereign Sockets — the immutable boundary between public Tool and proprietary Brain.

This package exports the abstract interfaces (Protocols/ABCs) that define the only
sanctioned crossing points between the open-core Tool and the gitignored proprietary
Brain. No implementation lives here — only contracts.

VDS map:
- VDS 00000: BrainInterface, ConfidenceProvider  (reasoning + confidence)
- VDS 40000: RedTeamInterface                    (red-team gate)
- VDS 50000: ElevatorInterface                   (Floor<->Surface crossing + wipe)
- VDS 90000: VoidArchiveInterface                (Pool of Tears)
- Sovereign Visage: VisageInterface              (affective HUD)
- Sovereign Archive: ArchiveInterface            (R2 disaster recovery)
"""
from __future__ import annotations

from .brain_interface import BrainInterface, BrainResponse
from .confidence_provider import ConfidenceProvider
from .elevator_interface import ElevatorInterface
from .redteam_interface import RedTeamInterface, RedTeamVerdict
from .void_archive import VoidArchiveInterface, VoidSocket
from .visage_interface import VisageInterface, AffectSignal, FaceFrame, VisageState
from .archive_interface import ArchiveInterface, ArchiveEntry, MerkleProof, RestoreResult

__all__ = [
    "BrainInterface",
    "BrainResponse",
    "ConfidenceProvider",
    "ElevatorInterface",
    "RedTeamInterface",
    "RedTeamVerdict",
    "VoidArchiveInterface",
    "VoidSocket",
    "VisageInterface",
    "AffectSignal",
    "FaceFrame",
    "VisageState",
    "ArchiveInterface",
    "ArchiveEntry",
    "MerkleProof",
    "RestoreResult",
]
