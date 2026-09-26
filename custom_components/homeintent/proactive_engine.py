"""V12 Proactive Context Intelligence orchestrator (hass-free).

Pipeline for one situation::

    DetectionSignal -> SituationStore (stable lifecycle)
      -> ContextForecast (consumes V11) -> Priority / Privacy
      -> [StandingPermission + AutoExecutionPolicy -> V10 runner]   (opt-in)
      -> Opportunity -> Attention -> CommunicationRouter
      -> PendingProposal / ActiveGoalSession -> delivery (AgentDelivery)
      -> reply / push action -> V10 runner -> GoalRun -> V11

Nothing in this module constructs a ``ServiceCallPlan``.  The only device
path is ``V10ProposalRunner``; detection, forecasting, opportunity, priority,
attention, privacy and routing are decision-only.  All HA interaction is
behind ``ProactivePorts`` so every decision is testable with an instrumented
service sink.
"""

from __future__ import annotations

import asyncio

import re
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Mapping, Protocol, cast

from .attention_policy import AttentionConfig, AttentionPolicy, AttentionStateStore, GroupedItem
from .communication_router import CommunicationRouter, RouterConfig
from .context_forecast import ContextForecastEngine, HabitEvidence
from .entities import EntitySnapshot
from .predictive_house_model import PredictiveHouseModel
from .proactive_execution import (
    ProactiveExecutionResult,
    ProactiveExecutionStatus,
    V10ProposalRunner,
)
from .proactive_messages import (
    accept_label,
    clarification_question,
    explain_record,
    full_message,
    grouped_message,
    outcome_message,
    running_message,
    proposal_label,
    situation_message,
)
from .proactive_model import (
    AnticipationResult,
    AttentionDecision,
    AttentionOutcome,
    CommunicationChannel,
    CommunicationDecision,
    ContextSnapshot,
    HistoryRecord,
    ModelReference,
    OpportunityOutcome,
    PendingProposal,
    PriorityLevel,
    PrivacyLevel,
    ProactiveSituation,
    ProposalChoice,
    ProposalState,
    ProposedGoal,
    PushActionBinding,
    RecipientContext,
    RoomPresenceResult,
    SituationEvidence,
    SituationKind,
    SituationState,
    TERMINAL_SITUATION_STATES,
    TargetState,
)
from .proactive_policy import (
    OpportunityContext,
    OpportunityPolicy,
    PriorityPolicy,
    PrivacyPolicy,
    QuietHoursPolicy,
)
from .proactive_session import (
    ProposalReply,
    ProposalStore,
    ReplyOutcome,
    classify_proposal_reply,
)
from .proactive_store import (
    GenerationalJsonFile,
    ProactiveHistoryStore,
    SCHEMA_VERSION,
    SituationStore,
)
from .room_presence import SatelliteRegistry
from .situation_detection import DetectionSignal, SituationDetector
from .standing_permission import AutoExecutionPolicy, StandingPermissionStore


PUSH_ACTION_PREFIX = "HOMEINTENT_V12_"
# The token is optional in the pattern only so that an action without device
# identity is *rejected explicitly* instead of being ignored as foreign.
_PUSH_ACTION_RE = re.compile(
    r"^HOMEINTENT_V12_(?P<choice>ACCEPT|LATER|IGNORE)_(?P<proposal>p[0-9a-f]{32})"
    r"(?:_(?P<token>t[0-9a-f]{16}))?$"
)
_PROPOSAL_KINDS = frozenset({
    SituationKind.ENTRY_LEFT_OPEN,
    SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
    SituationKind.HABIT_OPPORTUNITY,
})


@dataclass(frozen=True)
class ProactiveConfig:
    enabled: bool = False
    standing_permissions_enabled: bool = False
    entry_open_minutes: int = 15
    left_on_minutes: int = 5
    proposal_ttl: timedelta = timedelta(minutes=30)
    communication_cooldown: timedelta = timedelta(minutes=30)
    situation_max_age: Mapping[SituationKind, timedelta] = field(
        default_factory=lambda: {
            SituationKind.APPLIANCE_FINISHED: timedelta(hours=4),
            SituationKind.HABIT_OPPORTUNITY: timedelta(minutes=30),
            SituationKind.THERMAL_GOAL_AT_RISK: timedelta(hours=6),
            SituationKind.DEVICE_EFFECT_ANOMALY: timedelta(hours=1),
            SituationKind.PENDING_GOAL_REQUIRES_ATTENTION: timedelta(hours=12),
            SituationKind.TIMER_FINISHED: timedelta(minutes=5),
        }
    )
    attention: AttentionConfig = field(default_factory=AttentionConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    quiet: QuietHoursPolicy = field(default_factory=QuietHoursPolicy)
    dismiss_cooldown: timedelta = timedelta(hours=2)
    # How long an accepting reply waits for the verified effect before it
    # answers and keeps verifying in the background (F17).
    reply_budget: timedelta = timedelta(seconds=2)


@dataclass(frozen=True)
class DeliveryReceipt:
    delivered: tuple[CommunicationChannel, ...]
    origin_device_id: str | None = None
    errors: tuple[str, ...] = ()
    # Interactive push tokens actually sent, one per bound Companion device.
    push_bindings: tuple[PushActionBinding, ...] = ()


def _delivered_channel(
    planned: CommunicationChannel, receipt: DeliveryReceipt
) -> CommunicationChannel:
    """The channel the history may claim: what was actually delivered (F25).

    A notify entity cannot carry reply buttons, so a planned interactive push
    that arrived as a plain push is recorded (and explained) as a push.
    """
    if not receipt.delivered:
        return CommunicationChannel.HISTORY_ONLY
    if (
        planned is CommunicationChannel.INTERACTIVE_PUSH
        and CommunicationChannel.INTERACTIVE_PUSH not in receipt.delivered
        and CommunicationChannel.PUSH in receipt.delivered
    ):
        return CommunicationChannel.PUSH
    return planned


@dataclass(frozen=True)
class OutgoingMessage:
    """A rendered message plus its routing; carries no device action."""

    decision: CommunicationDecision
    title: str
    text: str
    priority: PriorityLevel
    proposal_id: str | None
    accept_label: str | None


class ProactivePorts(Protocol):
    """Everything the engine needs from Home Assistant, as typed callables."""

    def now(self) -> datetime: ...

    def local_now(self) -> datetime: ...

    def fresh_entities(self) -> Mapping[str, EntitySnapshot]: ...

    def nobody_home(self) -> bool | None: ...

    def recipients_for(self, situation: ProactiveSituation) -> tuple[RecipientContext, ...]: ...

    def room_for(self, person_id: str | None) -> RoomPresenceResult | None: ...

    def others_home(self, person_id: str | None) -> bool: ...

    def satellites(self) -> SatelliteRegistry: ...

    def owner_known(self, user_id: str) -> bool: ...

    def person_for(self, user_id: str | None) -> str | None: ...

    def active_goal_subjects(self) -> frozenset[str]: ...

    def habit_evidence(self, situation: ProactiveSituation) -> HabitEvidence | None: ...

    async def async_is_admin(self, user_id: str | None) -> bool: ...

    async def async_deliver(self, message: OutgoingMessage) -> DeliveryReceipt: ...

    def schedule(self, key: str, at: datetime, callback: Callable[[], Awaitable[None]]) -> None: ...

    def cancel(self, key: str) -> None: ...


@dataclass(frozen=True)
class ReplyResult:
    handled: bool
    speech: str
    clarification_ids: tuple[str, ...] = ()
    continue_conversation: bool = False


@dataclass
class EngineCounters:
    events_seen: int = 0
    events_relevant: int = 0
    evaluations: int = 0
    deliveries: int = 0
    # Every automatic V10 run is an attempt; only a verified effect counts
    # as an execution.  Rejected, unverified, stale and conflicting runs are
    # attempts without an execution.
    auto_attempts: int = 0
    auto_verified_executions: int = 0


class ProactiveContextEngine:
    def __init__(
        self,
        ports: ProactivePorts,
        runner: V10ProposalRunner,
        *,
        config: ProactiveConfig | None = None,
        predictive_house: PredictiveHouseModel | None = None,
        detector: SituationDetector | None = None,
        storage: GenerationalJsonFile | None = None,
    ) -> None:
        self.ports = ports
        self.runner = runner
        self.config = config or ProactiveConfig()
        self.detector = detector or SituationDetector()
        self.situations = SituationStore()
        self.attention_state = AttentionStateStore()
        self.proposals = ProposalStore()
        self.permissions = StandingPermissionStore()
        self.history = ProactiveHistoryStore()
        # Content-free "something persisted" notifications (Learning Center).
        self.persist_listeners: list[Callable[[], None]] = []
        self.forecast = ContextForecastEngine(predictive_house)
        self.priority_policy = PriorityPolicy()
        self.privacy_policy = PrivacyPolicy()
        self.opportunity_policy = OpportunityPolicy()
        self.attention_policy = AttentionPolicy(self.config.attention)
        self.router = CommunicationRouter(self.config.router)
        self.auto_policy = AutoExecutionPolicy()
        self.storage = storage or GenerationalJsonFile(None)
        self.counters = EngineCounters()
        self._last_anticipation: dict[str, AnticipationResult] = {}
        # Desired-state proposals per situation key (never a service plan).
        self._goals: dict[str, ProposedGoal] = {}
        self._background_executions: set[
            asyncio.Future[ProactiveExecutionResult] | asyncio.Future[None]
        ] = set()

    # ------------------------------------------------------------------ events
    async def async_observe_state(
        self,
        entity: EntitySnapshot,
        previous_state: str | None,
        entities: tuple[EntitySnapshot, ...],
        *,
        nobody_home: bool | None,
    ) -> None:
        """Hook for the existing SituationRuntime; filters before any work."""
        self.counters.events_seen += 1
        if not self.config.enabled or not self.detector.is_relevant(entity.entity_id, entity.device_class):
            return
        self.counters.events_relevant += 1
        now = self.ports.now()
        signals = self.detector.detect_state_change(
            entity, previous_state, entities=entities, now=now, nobody_home=nobody_home,
        )
        await self.async_process_signals(signals, now=now)

    async def async_process_signals(
        self, signals: tuple[DetectionSignal, ...], *, now: datetime, allow_auto: bool = True,
    ) -> None:
        changed_any = await self._expire_situations(now)
        for signal in signals:
            base_priority = self.priority_policy.decide(_probe(signal, now)).level
            privacy = self.privacy_policy.classify(_probe(signal, now)).level
            situation, changed = self.situations.apply(
                signal, now=now, priority_hint=base_priority, privacy=privacy,
            )
            if situation is None or not changed:
                continue
            changed_any = True
            if situation.state is SituationState.RESOLVED:
                await self._on_resolved(situation, now)
                continue
            if signal.proposed_goal is not None:
                self._goals[situation.dedupe_key] = signal.proposed_goal
            await self.async_evaluate(situation, now=now, allow_auto=allow_auto)
        if changed_any:
            await self.async_persist()

    async def _expire_situations(self, now: datetime) -> bool:
        """Kinds without a live resolution signal end after a bounded age."""
        before = {item.dedupe_key for item in self.situations.active()}
        if not self.situations.expire_older_than(now, self.config.situation_max_age):
            return False
        for situation in self.situations.all():
            if situation.dedupe_key in before and situation.state is SituationState.EXPIRED:
                await self._on_resolved(situation, now)
        return True

    async def _on_resolved(self, situation: ProactiveSituation, now: datetime) -> None:
        self.ports.cancel(f"check:{situation.dedupe_key}")
        self.ports.cancel(f"snooze:{situation.dedupe_key}")
        self._goals.pop(situation.dedupe_key, None)
        for proposal in self.proposals.open_for_situation(situation.situation_id, now):
            self.proposals.transition(
                proposal.proposal_id, ProposalState.CANCELLED, now=now, by="situation_resolved",
            )

    # -------------------------------------------------------------- evaluation
    def context_snapshot(self, now: datetime) -> ContextSnapshot:
        """Bounded, typed context; never a copy of Home Assistant's state machine."""
        active = self.situations.active()
        recipients: dict[str, RecipientContext] = {}
        for situation in active[:32]:
            for recipient in self.ports.recipients_for(situation):
                recipients.setdefault(recipient.user_id, recipient)
        rooms = tuple(
            room for recipient in recipients.values()
            if (room := self.ports.room_for(recipient.person_id)) is not None
        )
        local = self.ports.local_now()
        return ContextSnapshot(
            now, active, tuple(recipients.values()), self.ports.nobody_home(), rooms,
            self.ports.active_goal_subjects(),
            tuple(item.proposal_id for item in self.proposals.open_proposals(now)),
            self.attention_state.size,
            frozenset(
                user_id for user_id in recipients
                if self.config.quiet.is_quiet(user_id, local)
            ),
            tuple(
                item.model for item in self._last_anticipation.values()
            )[-16:],
        )

    async def async_evaluate(
        self, situation: ProactiveSituation, *, now: datetime, allow_auto: bool = True,
    ) -> OpportunityOutcome:
        self.counters.evaluations += 1
        anticipation = self.forecast.anticipate(
            situation, now=now, habit=self.ports.habit_evidence(situation),
            thermal_inputs=_thermal_inputs(situation),
            effect_operator=situation.evidence_value("operator_id"),
        )
        self._last_anticipation[situation.situation_id] = anticipation
        if len(self._last_anticipation) > 64:
            self._last_anticipation.pop(next(iter(self._last_anticipation)))
        priority = self.priority_policy.decide(situation)
        privacy = self.privacy_policy.classify(situation)
        recipients = self.ports.recipients_for(situation)
        local = self.ports.local_now()
        quiet_flags = [self.config.quiet.is_quiet(item.user_id, local) for item in recipients]
        if allow_auto and await self._maybe_auto_execute(situation, now=now, priority=priority.level):
            return OpportunityOutcome.HISTORY_ONLY
        minimum = {
            SituationKind.ENTRY_LEFT_OPEN: timedelta(minutes=self.config.entry_open_minutes),
            SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING: timedelta(minutes=self.config.left_on_minutes),
        }
        if (
            situation.kind is SituationKind.APPLIANCE_FINISHED
            and situation.evidence_value("source") == "power"
        ):
            # A power drop only means "finished" once it lasted (F15).
            minimum[SituationKind.APPLIANCE_FINISHED] = self.detector.config.appliance_idle_duration
        muted = bool(recipients) and all(
            self.attention_state.is_muted(item.user_id, situation.kind) for item in recipients
        )
        decision = self.opportunity_policy.decide(
            situation, priority.level,
            OpportunityContext(
                now, minimum, self.ports.active_goal_subjects(),
                self.config.communication_cooldown,
                reachable=bool(recipients),
                any_recipient_quiet=any(quiet_flags),
                all_recipients_quiet=bool(quiet_flags) and all(quiet_flags),
                muted_by_user=muted and priority.level < PriorityLevel.URGENT,
                anticipation=anticipation,
            ),
        )
        if decision.outcome is OpportunityOutcome.SUPPRESS:
            if "duration_below_threshold" in decision.reasons:
                due = situation.started_at + minimum[situation.kind]
                self._schedule_check(situation, due)
            return decision.outcome
        if decision.outcome is OpportunityOutcome.HISTORY_ONLY:
            self._record(situation, OpportunityOutcome.HISTORY_ONLY, None,
                         CommunicationChannel.HISTORY_ONLY, priority.level, privacy.level,
                         "history_only", decision.reasons, anticipation)
            return decision.outcome
        await self._communicate(
            situation, now=now, priority=priority.level, privacy=privacy.level,
            recipients=recipients, outcome=decision.outcome,
            reasons=(*decision.reasons, *priority.reasons), anticipation=anticipation,
        )
        return decision.outcome

    def _schedule_check(self, situation: ProactiveSituation, due: datetime) -> None:
        key = situation.dedupe_key

        async def _check() -> None:
            await self.async_recheck(key)

        self.ports.schedule(f"check:{key}", due, _check)

    async def async_recheck(self, dedupe_key: str, *, allow_auto: bool = True) -> None:
        """Re-evaluate against *live* HA state (duration check, snooze, restart)."""
        situation = self.situations.get(dedupe_key)
        if situation is None or situation.state in TERMINAL_SITUATION_STATES:
            return
        now = self.ports.now()
        live = self.detector.still_active(
            situation.kind, situation.subject_ids, self.ports.fresh_entities(),
            nobody_home=self.ports.nobody_home(),
        )
        if live is False:
            resolved = replace(situation, state=SituationState.RESOLVED, updated_at=now)
            self.situations.update(resolved)
            await self._on_resolved(resolved, now)
            await self.async_persist()
            return
        if live is None:
            return
        if situation.state is SituationState.SNOOZED and (
            situation.snooze_until is None or now >= situation.snooze_until
        ):
            situation = replace(
                situation, state=SituationState.ACTIVE, snooze_until=None,
                communicated_at=None, updated_at=now,
            )
            self.situations.update(situation)
        await self.async_evaluate(situation, now=now, allow_auto=allow_auto)
        await self.async_persist()

    # ------------------------------------------------------------ auto path
    async def _maybe_auto_execute(
        self, situation: ProactiveSituation, *, now: datetime, priority: PriorityLevel,
    ) -> bool:
        if not self.config.standing_permissions_enabled:
            return False
        permission = self.permissions.matching(situation, now)
        if permission is None:
            return False
        minimum = timedelta(minutes=self.config.left_on_minutes)
        if now - situation.started_at < minimum:
            self._schedule_check(situation, situation.started_at + minimum)
            return True
        entities = self.ports.fresh_entities()
        decision = self.auto_policy.evaluate(
            permission,
            enabled=self.config.standing_permissions_enabled,
            situation=situation,
            owner_known=self.ports.owner_known(permission.owner_user_id),
            entities=entities,
            nobody_home=self.ports.nobody_home(),
            now=now,
            attempts_today=self.permissions.attempts_today(permission.permission_id, now),
        )
        if not decision.allowed:
            self._record(situation, OpportunityOutcome.HISTORY_ONLY, permission.owner_user_id,
                         CommunicationChannel.HISTORY_ONLY, priority, situation.privacy_level,
                         "auto_not_allowed", decision.reasons, None)
            return False
        targets = tuple(
            item for item in self._goals.get(situation.dedupe_key, ProposedGoal((), "")).targets
            if item.entity_id in permission.entity_ids
            and item.entity_id in entities and entities[item.entity_id].state == "on"
        )
        if not targets:
            return False
        result = await self.runner.async_execute(
            ProposedGoal(targets, "Daueranweisung"),
            user_id=permission.owner_user_id,
            is_admin=await self.ports.async_is_admin(permission.owner_user_id),
            person_entity_id=self.ports.person_for(permission.owner_user_id),
            interactive_confirmed=False,
            provenance=f"standing_permission:{permission.permission_id}",
            now=now,
        )
        verified = result.status is ProactiveExecutionStatus.EXECUTED
        self.permissions.record_attempt(permission.permission_id, now, verified=verified)
        self.counters.auto_attempts += 1
        if verified:
            self.counters.auto_verified_executions += 1
        self._record(
            situation, OpportunityOutcome.HISTORY_ONLY, permission.owner_user_id,
            CommunicationChannel.HISTORY_ONLY, priority, situation.privacy_level,
            f"auto_{result.status.value}", (*decision.reasons, result.reason), None,
            run_id=result.run.run_id if result.run is not None else None,
        )
        return verified

    # ---------------------------------------------------------- communication
    async def _communicate(
        self,
        situation: ProactiveSituation,
        *,
        now: datetime,
        priority: PriorityLevel,
        privacy: PrivacyLevel,
        recipients: tuple[RecipientContext, ...],
        outcome: OpportunityOutcome,
        reasons: tuple[str, ...],
        anticipation: AnticipationResult,
    ) -> None:
        goal = self._goals.get(situation.dedupe_key) if situation.kind in _PROPOSAL_KINDS else None
        area_name = situation.evidence_value("area_name")
        statement, question = situation_message(situation, goal, area_name=area_name)
        local = self.ports.local_now()
        proposal: PendingProposal | None = None
        delivered_any = False
        satellites = self.ports.satellites()
        routed: list[tuple[RecipientContext, CommunicationDecision, AttentionDecision]] = []
        for recipient in recipients:
            attention = self.attention_policy.decide(
                self.attention_state, recipient=recipient.user_id,
                dedupe_key=situation.dedupe_key, priority=priority,
                requires_response=goal is not None, now=now,
                occurrence_id=situation.situation_id,
            )
            decision = self.router.route(
                recipient, priority=priority, privacy=privacy,
                room=self.ports.room_for(recipient.person_id), satellites=satellites,
                attention=attention,
                quiet=self.config.quiet.is_quiet(recipient.user_id, local),
                requires_response=goal is not None,
                others_home=self.ports.others_home(recipient.person_id),
                now=now,
            )
            routed.append((recipient, decision, attention))
        interactive = [
            decision for _recipient, decision, _attention in routed
            if decision.channel in {
                CommunicationChannel.VOICE, CommunicationChannel.INTERACTIVE_PUSH,
                CommunicationChannel.MULTI_CHANNEL,
            }
        ]
        if goal is not None and interactive:
            proposal = self.proposals.create(
                situation_id=situation.situation_id,
                recipient_user_ids=tuple(item.user_id for item in recipients),
                recipient_person_id=recipients[0].person_id if len(recipients) == 1 else None,
                proposed_goal=goal,
                channel=interactive[0].channel,
                privacy_level=privacy,
                subject_label=proposal_label(situation, area_name=area_name),
                question=question or statement,
                now=now,
                ttl=self.config.proposal_ttl,
            )
        for recipient, decision, attention in routed:
            if attention.outcome is AttentionOutcome.GROUP:
                self._add_to_group(recipient.user_id, situation, statement, now)
                self._record(situation, outcome, recipient.user_id, CommunicationChannel.HISTORY_ONLY,
                             priority, privacy, "grouped", (*reasons, *attention.reasons), anticipation)
                continue
            if decision.channel in {CommunicationChannel.SUPPRESS, CommunicationChannel.HISTORY_ONLY}:
                self._record(situation, outcome, recipient.user_id, decision.channel,
                             priority, privacy, decision.channel.value,
                             (*reasons, *attention.reasons, *decision.reasons), anticipation)
                continue
            ask = proposal is not None and decision.channel is not CommunicationChannel.PUSH
            text = full_message(statement, question if ask else None)
            receipt = await self.ports.async_deliver(OutgoingMessage(
                decision, _title(priority), text, priority,
                proposal.proposal_id if ask and proposal is not None else None,
                accept_label(proposal.proposed_goal) if ask and proposal is not None else None,
            ))
            if receipt.delivered:
                delivered_any = True
                self.counters.deliveries += 1
                self.attention_state.record_delivery(
                    recipient.user_id, situation.dedupe_key, now,
                    counts_for_budget=priority < PriorityLevel.URGENT,
                )
                if (
                    proposal is not None and receipt.origin_device_id is not None
                    and proposal.origin_device_id is None
                ):
                    proposal = self.proposals.bind_origin_device(
                        proposal.proposal_id, receipt.origin_device_id,
                    ) or proposal
                if proposal is not None:
                    for binding in receipt.push_bindings:
                        proposal = self.proposals.bind_push_action(
                            proposal.proposal_id, binding,
                        ) or proposal
            self._record(
                situation, outcome, recipient.user_id,
                _delivered_channel(decision.channel, receipt),
                priority, privacy,
                "delivered" if receipt.delivered else "delivery_failed",
                (*reasons, *attention.reasons, *decision.reasons, *receipt.errors), anticipation,
            )
        if not delivered_any and priority >= PriorityLevel.URGENT:
            # A safety alarm is never silently dropped because no personal
            # channel exists: fall back to the household-visible HA
            # notification and explicitly configured house speakers.
            channels = (CommunicationChannel.PUSH,) + (
                (CommunicationChannel.VOICE,)
                if self.config.router.house_speakers_configured
                and self.config.router.critical_multi_channel_enabled
                and privacy is PrivacyLevel.PUBLIC
                else ()
            )
            fallback = CommunicationDecision(
                CommunicationChannel.MULTI_CHANNEL if len(channels) > 1 else channels[0],
                channels, None, None, (), False, False, ("critical_household_fallback",),
            )
            receipt = await self.ports.async_deliver(OutgoingMessage(
                fallback, _title(priority), full_message(statement, None), priority, None, None,
            ))
            delivered_any = bool(receipt.delivered)
            self._record(
                situation, outcome, None,
                fallback.channel if receipt.delivered else CommunicationChannel.HISTORY_ONLY,
                priority, privacy, "delivered" if receipt.delivered else "delivery_failed",
                (*reasons, "critical_household_fallback", *receipt.errors), anticipation,
            )
        if delivered_any:
            self.situations.mark_communicated(situation.dedupe_key, now)
        elif proposal is not None:
            self.proposals.transition(proposal.proposal_id, ProposalState.CANCELLED,
                                      now=now, by="not_delivered")

    def _add_to_group(self, recipient: str, situation: ProactiveSituation, text: str, now: datetime) -> None:
        deadline = now + self.config.attention.group_window
        if self.attention_state.add_to_group(recipient, GroupedItem(situation.situation_id, text), deadline):
            async def _flush() -> None:
                await self.async_flush_group(recipient)

            self.ports.schedule(f"group:{recipient}", deadline, _flush)

    async def async_flush_group(self, recipient_id: str) -> None:
        items = self.attention_state.take_group(recipient_id)
        now = self.ports.now()
        live: list[str] = []
        entities = self.ports.fresh_entities()
        for item in items:
            situation = self.situations.by_id(item.situation_id)
            if situation is None or situation.state in TERMINAL_SITUATION_STATES:
                continue
            if self.detector.still_active(situation.kind, situation.subject_ids, entities,
                                          nobody_home=self.ports.nobody_home()) is False:
                continue
            live.append(item.text)
        if not live:
            return
        recipient = next(
            (candidate for situation in self.situations.active()
             for candidate in self.ports.recipients_for(situation)
             if candidate.user_id == recipient_id),
            None,
        )
        if recipient is None or not recipient.push_target_ids or recipient.push_ambiguous:
            self._record_undelivered_group(recipient_id, items, live, "group_no_push_target")
            return
        decision = CommunicationDecision(
            CommunicationChannel.PUSH, (CommunicationChannel.PUSH,), recipient_id, None,
            recipient.push_target_ids, False, False, ("grouped_digest",),
        )
        receipt = await self.ports.async_deliver(OutgoingMessage(
            decision, "HomeIntent", grouped_message(tuple(live)), PriorityLevel.INFO, None, None,
        ))
        if not receipt.delivered:
            self._record_undelivered_group(
                recipient_id, items, live, "group_delivery_failed", *receipt.errors,
            )
        if receipt.delivered:
            self.counters.deliveries += 1
            self.attention_state.record_delivery(recipient_id, f"group:{recipient_id}", now)
            for item in items:
                situation = self.situations.by_id(item.situation_id)
                if situation is not None and item.text in live:
                    self.situations.mark_communicated(situation.dedupe_key, now)
                    self._record(
                        situation, OpportunityOutcome.COMMUNICATE, recipient_id,
                        CommunicationChannel.PUSH, situation.priority_hint,
                        situation.privacy_level, "delivered", ("grouped_digest",), None,
                    )

    def _record_undelivered_group(
        self, recipient_id: str, items: tuple[GroupedItem, ...], live: list[str],
        *reasons: str,
    ) -> None:
        # A digest that cannot be sent is never dropped silently (F28).
        for item in items:
            situation = self.situations.by_id(item.situation_id)
            if situation is not None and item.text in live:
                self._record(
                    situation, OpportunityOutcome.COMMUNICATE, recipient_id,
                    CommunicationChannel.HISTORY_ONLY, situation.priority_hint,
                    situation.privacy_level, "delivery_failed", reasons, None,
                )

    # --------------------------------------------------------------- replies
    def classify_reply(self, text: str) -> ProposalReply | None:
        return classify_proposal_reply(text)

    def has_open_proposal_for(self, *, user_id: str | None, device_id: str | None) -> bool:
        eligible, _foreign = self.proposals.eligible(
            user_id=user_id, device_id=device_id, now=self.ports.now(),
        )
        return bool(eligible)

    async def async_handle_reply(
        self, text: str, *, user_id: str | None, device_id: str | None, is_admin: bool,
        other_open_questions: int = 0,
    ) -> ReplyResult | None:
        """Consume a bare reply only when it addresses exactly one proposal."""
        if not self.config.enabled:
            return None
        reply = classify_proposal_reply(text)
        if reply is None:
            return None
        now = self.ports.now()
        self.proposals.expire(now)
        resolution = self.proposals.resolve_target(user_id=user_id, device_id=device_id, now=now)
        if resolution.outcome is ReplyOutcome.NOT_ADDRESSED:
            return None
        if resolution.outcome is ReplyOutcome.WRONG_USER:
            return ReplyResult(True, "Diese Rückfrage gehört zu einem anderen Benutzer. Ich habe nichts ausgeführt.")
        if resolution.outcome is ReplyOutcome.CLARIFY or other_open_questions:
            candidates = resolution.candidates or ((resolution.proposal,) if resolution.proposal else ())
            labels = tuple(item.subject_label or item.proposed_goal.description for item in candidates)
            if other_open_questions and len(labels) == 1:
                return ReplyResult(
                    True,
                    "Es sind mehrere Fragen offen. Bitte sag genauer, was du meinst, "
                    f"zum Beispiel „{labels[0]}“.",
                    tuple(item.proposal_id for item in candidates), True,
                )
            return ReplyResult(
                True, clarification_question(labels),
                tuple(item.proposal_id for item in candidates), True,
            )
        proposal = resolution.proposal
        assert proposal is not None
        return await self.async_apply_choice(
            proposal.proposal_id, reply.choice, user_id=user_id, is_admin=is_admin,
            snooze=reply.snooze, source="voice" if device_id else "text",
        )

    async def async_select_and_apply(
        self, candidate_ids: tuple[str, ...], selection_text: str, reply_choice: ProposalChoice,
        *, user_id: str | None, is_admin: bool,
    ) -> ReplyResult | None:
        """Resolve a clarification answer ("die Garage") to exactly one proposal."""
        now = self.ports.now()
        normalized = selection_text.casefold()
        candidates = [
            proposal for pid in candidate_ids
            if (proposal := self.proposals.get(pid)) is not None
            and proposal.state is ProposalState.PENDING and proposal.expires_at > now
        ]
        ordinal = _ordinal(normalized)
        chosen: list[PendingProposal] = []
        if ordinal is not None and 0 <= ordinal < len(candidates):
            chosen = [candidates[ordinal]]
        else:
            chosen = [
                item for item in candidates
                if item.subject_label and _label_matches(item.subject_label, normalized)
            ]
        if len(chosen) != 1:
            return None
        return await self.async_apply_choice(
            chosen[0].proposal_id, reply_choice, user_id=user_id, is_admin=is_admin,
            snooze=None, source="clarification",
        )

    async def async_handle_push_action(
        self, action: str, *, user_id: str | None, device_id: str | None,
    ) -> ReplyResult | None:
        """Authorize an interactive push action; every rejection is read-only.

        Valid only when all hold: the HA user is authenticated and a
        recipient, the action carries a device token, that token was issued
        for this proposal to this user's Companion device, an event-reported
        ``device_id`` (if any) matches that device, and the proposal is still
        pending and unexpired.  Replays find the proposal resolved.
        """
        match = _PUSH_ACTION_RE.fullmatch(action)
        if match is None:
            return None
        proposal = self.proposals.get(match.group("proposal"))
        if proposal is None:
            return ReplyResult(False, "unknown_proposal")
        if proposal.state is not ProposalState.PENDING:
            return ReplyResult(False, "already_resolved")
        if proposal.expires_at <= self.ports.now():
            return ReplyResult(False, "expired")
        if user_id is None or user_id not in proposal.recipient_user_ids:
            return ReplyResult(False, "wrong_recipient")
        token = match.group("token")
        if token is None:
            return ReplyResult(False, "missing_device_identity")
        binding = next(
            (item for item in proposal.push_bindings if secrets.compare_digest(item.token, token)),
            None,
        )
        if binding is None:
            return ReplyResult(False, "unbound_device")
        if binding.user_id != user_id:
            return ReplyResult(False, "wrong_recipient")
        if device_id is not None and device_id != binding.device_id:
            return ReplyResult(False, "unexpected_device")
        choice = ProposalChoice(match.group("choice").casefold())
        return await self.async_apply_choice(
            proposal.proposal_id, choice, user_id=user_id,
            is_admin=await self.ports.async_is_admin(user_id), snooze=None, source="push",
        )

    async def async_apply_choice(
        self, proposal_id: str, choice: ProposalChoice, *, user_id: str | None,
        is_admin: bool, snooze: timedelta | None, source: str,
    ) -> ReplyResult:
        now = self.ports.now()
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.state is not ProposalState.PENDING:
            return ReplyResult(True, "Diese Rückfrage ist bereits erledigt. Ich habe nichts erneut ausgeführt.")
        if proposal.expires_at <= now:
            self.proposals.expire(now)
            return ReplyResult(True, "Diese Rückfrage ist abgelaufen. Ich habe nichts ausgeführt.")
        situation = self.situations.by_id(proposal.situation_id)
        by = f"{source}:{user_id or 'device'}"
        if choice is ProposalChoice.ACCEPT:
            claimed = self.proposals.claim_for_execution(proposal_id, now=now, by=by)
            if claimed is None:
                return ReplyResult(True, "Diese Rückfrage ist bereits erledigt. Ich habe nichts erneut ausgeführt.")
            live = (
                self.detector.still_active(
                    situation.kind, situation.subject_ids, self.ports.fresh_entities(),
                    nobody_home=self.ports.nobody_home(),
                )
                if situation is not None else False
            )
            if situation is None or situation.state in TERMINAL_SITUATION_STATES or live is not True:
                self.proposals.transition(proposal_id, ProposalState.FAILED, now=now, result="stale")
                await self.async_persist()
                return ReplyResult(True, outcome_message(proposal.proposed_goal, success=False, status="stale"))
            active_situation = situation

            async def _execute() -> ProactiveExecutionResult:
                executed = await self.runner.async_execute(
                    proposal.proposed_goal, user_id=user_id, is_admin=is_admin,
                    person_entity_id=self.ports.person_for(user_id),
                    interactive_confirmed=True,
                    provenance=f"proposal:{proposal_id}", now=now,
                )
                self._finish_execution(
                    proposal, active_situation, executed, now=now, by=by, user_id=user_id,
                )
                await self.async_persist()
                return executed

            # A garage door needs several seconds to report "closed"; the
            # spoken "Ja" must not wait for it (F17). The effect is still
            # verified and recorded; only a failure is reported afterwards.
            task = asyncio.ensure_future(_execute())
            done, _pending = await asyncio.wait(
                {task}, timeout=self.config.reply_budget.total_seconds()
            )
            if task in done:
                result = task.result()
                return ReplyResult(True, outcome_message(
                    proposal.proposed_goal,
                    success=result.status is ProactiveExecutionStatus.EXECUTED,
                    status=result.status.value,
                ))
            self._background_executions.add(task)
            task.add_done_callback(
                lambda finished: self._on_background_execution(
                    finished, proposal, active_situation, user_id,
                )
            )
            return ReplyResult(True, running_message(proposal.proposed_goal))
        if situation is not None:
            self.ports.cancel(f"check:{situation.dedupe_key}")
        if choice is ProposalChoice.LATER:
            delay = snooze or timedelta(minutes=30)
            until = now + delay
            self.proposals.transition(proposal_id, ProposalState.SNOOZED, now=now, by=by)
            if situation is not None:
                self.situations.set_state(situation.dedupe_key, SituationState.SNOOZED,
                                          now=now, snooze_until=until)
                key = situation.dedupe_key

                async def _wake() -> None:
                    await self.async_recheck(key, allow_auto=False)

                self.ports.schedule(f"snooze:{key}", until, _wake)
                self._ack(situation, "snoozed", now, user_id)
            await self.async_persist()
            minutes = max(1, int(delay.total_seconds() // 60))
            from .productivity import format_duration

            return ReplyResult(True, f"In Ordnung. Ich prüfe das in {format_duration(minutes * 60)} erneut.")
        state = ProposalState.DISMISSED if choice is ProposalChoice.IGNORE else ProposalState.REJECTED
        self.proposals.transition(proposal_id, state, now=now, by=by)
        if situation is not None:
            # Only the current occurrence is acknowledged; no preference is learned.
            self.situations.set_state(situation.dedupe_key, SituationState.ACKNOWLEDGED, now=now)
            self.attention_state.dismiss(situation.situation_id, now + self.config.dismiss_cooldown)
            self._ack(situation, choice.value, now, user_id)
        await self.async_persist()
        if choice is ProposalChoice.IGNORE:
            return ReplyResult(True, "Alles klar. Zu dieser Situation melde ich mich vorerst nicht mehr.")
        return ReplyResult(True, "In Ordnung. Ich lasse es so.")

    def _on_background_execution(
        self,
        task: "asyncio.Future[ProactiveExecutionResult]",
        proposal: PendingProposal,
        situation: ProactiveSituation,
        user_id: str | None,
    ) -> None:
        self._background_executions.discard(task)
        failed = task.cancelled() or task.exception() is not None or (
            task.result().status is not ProactiveExecutionStatus.EXECUTED
        )
        if not failed:
            return
        run = task.result().run if not task.cancelled() and task.exception() is None else None
        run_id = run.run_id if run is not None else proposal.proposal_id
        # Report the failure the user was promised (same path as other
        # unattended goal failures).
        report = self.async_report_situation(DetectionSignal(
            SituationKind.PENDING_GOAL_REQUIRES_ATTENTION,
            f"{SituationKind.PENDING_GOAL_REQUIRES_ATTENTION.value}:{run_id}",
            (), situation.area_id, True, self.ports.now(),
            (SituationEvidence("run_id", run_id),),
            situation.subject_name or "Vorschlag", owner_user_id=user_id,
        ))
        self._background_executions.add(asyncio.ensure_future(report))

    def _finish_execution(
        self, proposal: PendingProposal, situation: ProactiveSituation,
        result: ProactiveExecutionResult, *, now: datetime, by: str, user_id: str | None,
    ) -> None:
        run_id = result.run.run_id if result.run is not None else None
        executed = result.status is ProactiveExecutionStatus.EXECUTED
        self.proposals.transition(
            proposal.proposal_id,
            ProposalState.EXECUTED if executed else ProposalState.FAILED,
            now=now, by=by, run_id=run_id, result=result.status.value,
        )
        self.situations.set_state(situation.dedupe_key, SituationState.ACKNOWLEDGED, now=now)
        self._record(
            situation, OpportunityOutcome.COMMUNICATE, user_id, proposal.channel,
            situation.priority_hint, proposal.privacy_level, f"proposal_{result.status.value}",
            (result.reason,), None, run_id=run_id, acknowledgement="accepted",
        )

    def _ack(
        self, situation: ProactiveSituation, acknowledgement: str, now: datetime,
        user_id: str | None,
    ) -> None:
        self._record(
            situation, OpportunityOutcome.COMMUNICATE, user_id, CommunicationChannel.HISTORY_ONLY,
            situation.priority_hint, situation.privacy_level, "acknowledged", (),
            None, acknowledgement=acknowledgement,
        )

    # ------------------------------------------------------ external evidence
    async def async_report_situation(self, signal: DetectionSignal, *, allow_auto: bool = False) -> None:
        """Entry point for V10/V11-sourced situations (thermal risk, effect anomaly)."""
        if not self.config.enabled:
            return
        await self.async_process_signals((signal,), now=self.ports.now(), allow_auto=allow_auto)

    def record_external_communication(self, kind: SituationKind, label: str, *, owner: str) -> None:
        """History-only note for a message another authority already delivered."""
        now = self.ports.now()
        probe = ProactiveSituation(
            f"ext_{self.history.next_id(now)}", kind, (), None, now, now,
            SituationState.RESOLVED, (), (), (), PriorityLevel.INFO, PrivacyLevel.HOUSEHOLD,
            f"{kind.value}:{label}", subject_name=label,
        )
        self._record(probe, OpportunityOutcome.HISTORY_ONLY, None, CommunicationChannel.HISTORY_ONLY,
                     PriorityLevel.INFO, PrivacyLevel.HOUSEHOLD, f"delivered_by_{owner}",
                     ("announced_by_native_timer",), None)

    # ------------------------------------------------------------ history
    def _record(
        self, situation: ProactiveSituation, decision: OpportunityOutcome,
        recipient: str | None, channel: CommunicationChannel, priority: PriorityLevel,
        privacy: PrivacyLevel, result: str, reasons: tuple[str, ...],
        anticipation: AnticipationResult | None, *, run_id: str | None = None,
        acknowledgement: str | None = None,
    ) -> None:
        now = self.ports.now()
        model: ModelReference | None = anticipation.model if anticipation is not None else None
        self.history.append(HistoryRecord(
            self.history.next_id(now), situation.situation_id, situation.kind,
            situation.subject_name, decision, recipient, channel, now, priority, privacy,
            result, tuple(dict.fromkeys(reasons))[:12], acknowledgement, run_id,
            situation.evidence[:8],
            model.model_id if model is not None and model.model_id else None,
        ))

    def explain_latest(self, subject_words: tuple[str, ...], *, user_id: str | None) -> str:
        record = self.history.latest_for_subject(subject_words)
        if record is None or (
            record.privacy >= PrivacyLevel.PERSONAL
            and not record.addressed_to(user_id)
        ):
            return "Dazu habe ich in letzter Zeit keinen Hinweis gegeben."
        local = self.ports.local_now()
        local_time = record.timestamp.astimezone(local.tzinfo) if local.tzinfo else record.timestamp
        return explain_record(record, local_time=local_time)

    def history_summary(self, *, since: datetime, user_id: str | None) -> str:
        records = [
            item for item in self.history.since(since)
            if item.result == "delivered"
            and (
                item.privacy < PrivacyLevel.PERSONAL
                or item.addressed_to(user_id)
            )
        ]
        labels = tuple(dict.fromkeys(item.subject_label for item in records if item.subject_label))
        if not labels:
            return "Heute habe ich mich noch nicht proaktiv gemeldet."
        from .nlu.grounded_answer import join_german

        count = len(labels)
        noun = "Situation" if count == 1 else "Situationen"
        return f"Heute habe ich mich zu {count} {noun} gemeldet: {join_german(labels)}."

    # ---------------------------------------------------------- persistence
    def document(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "situations": self.situations.to_dict(),
            "attention": self.attention_state.to_dict(),
            "proposals": self.proposals.to_dict(),
            "permissions": self.permissions.to_dict(),
            "history": self.history.to_list(),
            "goals": {
                key: [[item.entity_id, item.desired_state, item.name] for item in goal.targets]
                for key, goal in self._goals.items()
                if self.situations.get(key) is not None
            },
        }

    async def async_persist(self) -> None:
        await self.storage.async_write(self.document())
        for listener in tuple(self.persist_listeners):
            listener()

    async def async_restore(self) -> int:
        """Restore operational state, then revalidate it against live HA state.

        No device action ever happens here: proposals are only kept pending
        when unexpired, their recipients are still valid and their situation
        is still live; auto execution is disabled for the restore pass.
        """
        document = await self.storage.async_read()
        if document is None:
            return 0
        self.situations = SituationStore.from_list(document.get("situations"))
        self.attention_state = AttentionStateStore.from_dict(document.get("attention"))
        self.proposals = ProposalStore.from_dict(document.get("proposals"))
        self.permissions = StandingPermissionStore.from_dict(document.get("permissions"))
        self.history = ProactiveHistoryStore.from_list(document.get("history"))
        goals = _goals_from(document.get("goals"))
        self._goals = goals
        now = self.ports.now()
        self.proposals.expire(now)
        await self._expire_situations(now)
        entities = self.ports.fresh_entities()
        nobody_home = self.ports.nobody_home()
        for situation in self.situations.active():
            live = self.detector.still_active(situation.kind, situation.subject_ids, entities,
                                              nobody_home=nobody_home)
            if live is False:
                resolved = replace(situation, state=SituationState.RESOLVED, updated_at=now)
                self.situations.update(resolved)
                await self._on_resolved(resolved, now)
        for proposal in self.proposals.open_proposals(now):
            situation = self.situations.by_id(proposal.situation_id)
            if (
                situation is None or situation.state in TERMINAL_SITUATION_STATES
                or not all(self.ports.owner_known(item) for item in proposal.recipient_user_ids)
            ):
                self.proposals.transition(proposal.proposal_id, ProposalState.CANCELLED,
                                          now=now, by="restart_revalidation")
        restored = 0
        for situation in self.situations.active():
            restored += 1
            if situation.state is SituationState.SNOOZED and situation.snooze_until is not None:
                key = situation.dedupe_key

                async def _wake(key: str = key) -> None:
                    await self.async_recheck(key, allow_auto=False)

                self.ports.schedule(f"snooze:{key}", max(now, situation.snooze_until), _wake)
            elif situation.state in {SituationState.ACTIVE, SituationState.DETECTED}:
                key = situation.dedupe_key

                async def _check(key: str = key) -> None:
                    await self.async_recheck(key, allow_auto=False)

                self.ports.schedule(f"check:{key}", now, _check)
        await self.async_persist()
        return restored


def _goals_from(raw: object) -> dict[str, ProposedGoal]:
    """Fail-closed restore of desired-state proposals (closed state set only)."""
    goals: dict[str, ProposedGoal] = {}
    if not isinstance(raw, Mapping):
        return goals
    for key, value in cast(Mapping[object, object], raw).items():
        if not isinstance(key, str) or not isinstance(value, list):
            continue
        targets: list[TargetState] = []
        for item in cast(list[object], value):
            if not isinstance(item, list):
                continue
            parts = cast(list[object], item)
            if (
                len(parts) == 3
                and isinstance(parts[0], str) and "." in parts[0]
                and parts[1] in {"off", "on", "closed", "open"}
                and isinstance(parts[2], str)
            ):
                targets.append(TargetState(parts[0], str(parts[1]), parts[2]))
        if targets:
            goals[key] = ProposedGoal(tuple(targets), "")
    return goals


def _probe(signal: DetectionSignal, now: datetime) -> ProactiveSituation:
    """A transient situation used only to classify a fresh signal."""
    return ProactiveSituation(
        "probe", signal.kind, signal.subject_ids, signal.area_id, signal.started_at, now,
        SituationState.DETECTED, signal.evidence, signal.persons, (),
        PriorityLevel.CRITICAL, PrivacyLevel.PUBLIC, signal.dedupe_key,
        owner_user_id=signal.owner_user_id, subject_name=signal.subject_name,
    )


def _thermal_inputs(situation: ProactiveSituation) -> tuple[float, float, float | None] | None:
    try:
        current = situation.evidence_value("current_celsius")
        target = situation.evidence_value("target_celsius")
        outdoor = situation.evidence_value("outdoor_celsius")
        if current is None or target is None:
            return None
        return float(current), float(target), float(outdoor) if outdoor else None
    except ValueError:
        return None


def _title(priority: PriorityLevel) -> str:
    return "HomeIntent: Achtung" if priority >= PriorityLevel.URGENT else "HomeIntent"


_ORDINALS = {
    "erste": 0, "ersten": 0, "erstes": 0, "zweite": 1, "zweiten": 1, "zweites": 1,
    "dritte": 2, "dritten": 2, "drittes": 2, "letzte": -1, "letzten": -1,
}


def _ordinal(text: str) -> int | None:
    for word in re.findall(r"[a-zäöüß]+", text):
        if word in _ORDINALS:
            return _ORDINALS[word]
    return None


_FUNCTION_WORDS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "im", "in",
    "am", "an", "auf", "und", "oder", "mit", "bitte", "meine", "meinen", "mein",
})


def _label_matches(label: str, normalized_reply: str) -> bool:
    from .entities import normalize_for_compare

    label_words = {
        item for item in re.split(r"[^a-z0-9]+", normalize_for_compare(label))
        if len(item) >= 3 and item not in _FUNCTION_WORDS
    }
    reply_words = {
        item for item in re.split(r"[^a-z0-9]+", normalize_for_compare(normalized_reply))
        if item and item not in _FUNCTION_WORDS
    }
    return bool(label_words & reply_words) or any(
        word.startswith(label_word) or label_word.startswith(word)
        for word in reply_words for label_word in label_words if len(word) >= 4
    )


def build_evidence(**values: str) -> tuple[SituationEvidence, ...]:
    return tuple(SituationEvidence(key, value) for key, value in values.items())


__all__ = (
    "DeliveryReceipt",
    "OutgoingMessage",
    "PUSH_ACTION_PREFIX",
    "ProactiveConfig",
    "ProactiveContextEngine",
    "ProactivePorts",
    "ReplyResult",
    "build_evidence",
)
