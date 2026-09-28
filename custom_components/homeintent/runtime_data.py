"""Typed config-entry owned runtime state."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from typing import Any
import asyncio
from collections.abc import Callable

from .engine import NluEngine
from .audit_log import AuditTrail
from .nlu.context import ConversationContextStore
from .memory import MemoryStore
from .situation import RoutineStatistics, SituationEvaluator
from .dialog_manager import DialogManager
from .effect_monitor import EffectMonitor
from .execution_trace import TraceRuntime
from .adapters import AdapterEvidence
from .goal_run import GoalRunStore
from .monitor_goal import MonitorGoalRuntime, MonitorGoalStore
from .profiles import ProfileStore
from .user_context import UserContextStore
from .execution_coordinator import ExecutionCoordinator
from .experience_store import ExperienceStore
from .learning_manager import LearningManager
from .learning_policy import LearningPolicy
from .model_registry import ModelRegistry
from .predictive_house_model import PredictiveHouseModel
from .thermal_tracker import ThermalExperienceTracker
from .thermal_deadline import PendingThermalCheckpointStore
from .learning_center import LearningCenterRevision


@dataclass
class HomeIntentRuntimeData:
    """State shared by platforms belonging to one integration entry."""

    engine: NluEngine = field(default_factory=NluEngine)
    context_store: ConversationContextStore = field(
        default_factory=ConversationContextStore
    )
    audit_trail: AuditTrail = field(default_factory=AuditTrail)
    proactive_agent: Any | None = None
    situation_runtime: Any | None = None
    proactive_context: Any | None = None
    memory: MemoryStore | None = None
    situation_evaluator: SituationEvaluator = field(default_factory=SituationEvaluator)
    routine_statistics: dict[str, RoutineStatistics] = field(default_factory=dict)
    dialog_manager: DialogManager = field(default_factory=DialogManager)
    document_index: Any | None = None
    effect_monitor: EffectMonitor = field(default_factory=EffectMonitor)
    adapter_evidence: deque[AdapterEvidence] = field(
        default_factory=lambda: deque(maxlen=512)
    )
    adapter_runtime: Any | None = None
    native_timer: Any | None = None
    user_contexts: UserContextStore | None = None
    profiles: ProfileStore | None = None
    goal_runs: GoalRunStore | None = None
    monitor_goals: MonitorGoalStore | None = None
    monitor_runtime: MonitorGoalRuntime | None = None
    execution_coordinator: ExecutionCoordinator = field(default_factory=ExecutionCoordinator)
    learning_policy: LearningPolicy | None = None
    experiences: ExperienceStore | None = None
    learned_models: ModelRegistry | None = None
    predictive_house: PredictiveHouseModel | None = None
    learning_manager: LearningManager | None = None
    thermal_tracker: ThermalExperienceTracker | None = None
    thermal_checkpoints: PendingThermalCheckpointStore | None = None
    learning_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    remove_learning_listener: Callable[[], None] | None = None
    remove_proactive_listener: Callable[[], None] | None = None
    # 7.3.2: ring buffer of executions and HA automation/script runs.
    trace: TraceRuntime | None = None
    stop_trace: Callable[[], Any] | None = None
    # 7.1 Learning Center: runtime-only change counter and bounded audit.
    learning_center_revision: LearningCenterRevision = field(
        default_factory=LearningCenterRevision
    )
    learning_center_audit: Any | None = None

    def __post_init__(self) -> None:
        self.context_store.bind_dialog_listener(self._synchronize_dialog)

    def _synchronize_dialog(self, conversation_id: str, pending: object | None) -> None:
        kind = getattr(pending, "kind", None)
        payload = getattr(pending, "payload", None)
        self.dialog_manager.synchronize_pending_payload(
            conversation_id,
            getattr(kind, "name", None),
            payload,
        )
