"""P34.3 controlled data foundation.

The package contains persistence and strict logical contracts only.  CRUD,
DDL execution, public routing, and runtime capability wiring are introduced
behind later P34.3 gates.
"""

from omnibase.controlled_data.identifiers import (
    column_identifier,
    index_identifier,
    table_identifier,
)
from omnibase.controlled_data.types import validate_type_spec

__all__ = [
    "column_identifier",
    "index_identifier",
    "table_identifier",
    "validate_type_spec",
]
