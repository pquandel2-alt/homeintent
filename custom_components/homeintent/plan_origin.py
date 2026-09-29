"""Where a plan came from; evaluated only by the execution policies.

The language layer sets the origin (explicit command, implicit need,
inferred routine ...). ``ExecutionPolicy``/``AutoExecutionPolicy`` are the
only places that read it. A non-explicit origin is never less strict than
the same explicit command.
"""

from __future__ import annotations

from enum import Enum


class PlanOrigin(str, Enum):
    EXPLICIT_COMMAND = "explicit_command"
    IMPLICIT_NEED = "implicit_need"
    INFERRED_ROUTINE = "inferred_routine"
    PROACTIVE_PROPOSAL = "proactive_proposal"
    STANDING_PERMISSION = "standing_permission"


# Origins that act without a person answering right now.
UNATTENDED_ORIGINS = frozenset({PlanOrigin.STANDING_PERMISSION})
