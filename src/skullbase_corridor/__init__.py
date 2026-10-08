"""Open-source skull-base corridor geometry engine."""

from skullbase_corridor.domain.models import SCHEMA_VERSION
from skullbase_corridor.geometry.engine import analyze_case

__all__ = ["SCHEMA_VERSION", "analyze_case"]
__version__ = "0.2.0"
