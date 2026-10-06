"""7.9.2 Teil B: reports, consumption, summaries, vacation and habits.

One entry point for the new abilities so that ``conversation.py`` stays an
orchestrator. Read-only answers need no "Ja"; everything that writes or
creates something lasting goes through the established preview and "Ja"
(automation previews via ``AutomationController``).
"""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from datetime import datetime, timedelta

from ..energy_query import async_answer as async_energy_answer
from ..event_summary import (
    SummaryEvent,
    history_entities,
    last_absence,
    parse_summary_query,
    render_rest,
    render_summary,
    states_of,
    summarize,
)
from ..execution_trace import TRACE_DATA_KEY
from ..history_query import async_read_state_rows
from ..automation_ownership import HOUSEHOLD_OWNER
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..execution_trace import actor_hash
from ..habit_suggestions import (
    MIN_DAYS,
    WINDOW_DAYS,
    HabitStore,
    TimedHabit,
    describe_habit,
    discover_habits,
    observations_from_trace,
    parse_habit_request,
)
from ..missing_part import MissingPart, PartRequest
from ..nlu.action_model import ActionModel, ActionType
from ..nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from ..nlu.automation_model import AutomationModel, TriggerModel, TriggerTarget, TriggerType, render_automation_tree
from ..nlu.condition_model import ConditionModel, ConditionNode, ConditionType
from ..nlu.action_model import NotificationRecipientKind
from ..nlu.automation_validator import validate_automation
from ..notification_target import NotificationTargetResolver
from ..security_control import conversation_user_id, user_is_admin
from ..service_call import ServiceCallPlan
from ..service_executor import async_execute_service_plan
from ..turn_outcome import TurnOutcomeKind, report_outcome
from ..vacation import (
    VacationRecord,
    VacationStore,
    build_plan,
    describe_plan,
    parse_vacation_request,
    plan_configs,
    validate_plan,
)
from ..energy_query import (
    CONF_ENERGY_PRICE,
    async_energy_dashboard_price,
    current_power_answer,
    parse_energy_query,
    samples_reader,
)
from ..entities import EntitySnapshot


class InsightsController:
    """Health reports (B3), consumption (B5), summaries (B1), vacation (B4)
    and habit suggestions (B2)."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        runtime: Any,
        automations: Any,
        entities: Callable[[], list[EntitySnapshot]] | None = None,
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._runtime = runtime
        self._automations = automations
        self._entities_of = entities or (lambda: [])
        # Events of a summary not yet spoken, per conversation (B1).
        self._rest: dict[str, tuple[SummaryEvent, ...]] = {}

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    def _answer(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse, text: str
    ) -> conversation.ConversationResult:
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech(text)
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    async def async_handle_reading(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
        active_dialog: Any = None,
    ) -> conversation.ConversationResult | None:
        """Read-only answers - before the situation view, which would answer
        "während ich weg war" with the current state."""
        if active_dialog is not None:
            return None
        rest = self._rest.pop(user_input.conversation_id or "", ())
        if rest and _asks_more(user_input.text):  # "Was noch?" after a summary (B1)
            self._rest[user_input.conversation_id or ""] = tuple(rest[5:])
            return self._answer(user_input, response, render_rest(rest))
        task = self._runtime.dialog_manager.active(user_input.conversation_id)
        if task is not None and task.kind is DialogTaskKind.VACATION_CONFIRMATION:
            handled = await self._async_vacation_answer(user_input, response, task, entities)
            if handled is not None:
                return handled
        if task is not None and task.kind is DialogTaskKind.HABIT_OFFER:
            handled = self._habit_answer(user_input, response, task, entities)
            if handled is not None:
                return handled
        vacation = parse_vacation_request(user_input.text, dt_util.now().date())
        if vacation is not None:  # "Ich bin bis Sonntag weg" (B4)
            return await self._async_vacation(user_input, response, vacation, entities)
        habit = parse_habit_request(user_input.text)
        if habit is not None:  # "Welche Gewohnheiten hast du erkannt?" (B2)
            answered = self._habit_request(user_input, response, habit, entities)
            if answered is not None:
                return answered
        summary = parse_summary_query(user_input.text, dt_util.now())
        if summary is not None:  # "Was war los, während ich weg war?" (B1)
            return self._answer(user_input, response, await self._async_summary(user_input, summary, entities))
        power = current_power_answer(user_input.text, entities)
        if power is not None:  # "… gerade?" stays the power in W (B5)
            return self._answer(user_input, response, power)
        energy = parse_energy_query(user_input.text, dt_util.now())
        if energy is not None:  # "Wie viel Strom hat … heute verbraucht?" (B5)
            options: dict[str, object] = dict(self.entry.options)
            if not options.get(CONF_ENERGY_PRICE):
                # Without the option the energy dashboard's fixed price.
                price = await async_energy_dashboard_price(self.hass)
                if price is not None:
                    options[CONF_ENERGY_PRICE] = price
            text = await async_energy_answer(energy, entities, samples_reader(self.hass), options)
            return self._answer(user_input, response, text)
        return None

    async def async_handle(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Requests that set something up (preview and "Ja") - behind the
        language/safety gate."""
        report = self._automations.handle_health_report(user_input, response, entities)
        if report is not None:  # "Sag mir jeden Sonntag, welche Batterien …" (B3)
            return report
        return None


    async def _async_summary(
        self, user_input: conversation.ConversationInput, summary: Any, entities: list[EntitySnapshot]
    ) -> str:
        now = dt_util.now()
        actor = conversation_user_id(user_input)
        is_admin = await user_is_admin(self.hass, user_input)
        contexts = getattr(self._runtime, "user_contexts", None)
        binding = contexts.resolve_current_person(actor) if contexts is not None else None
        person = binding.person_entity_id if binding is not None else None
        start, end, label = summary.start, summary.end, summary.label
        if summary.away:
            if person is None:
                return (
                    "Ich weiß nicht, welche Person du bist, also auch nicht, wann du weg warst. "
                    "Frag zum Beispiel: „Was ist heute passiert?“"
                )
            rows = await async_read_state_rows(self.hass, [person], now - timedelta(days=7), now)
            if rows is None:
                return _NO_RECORDER
            absence = last_absence([(moment, state) for moment, state, _ in rows.get(person, [])], now)
            if absence is None:
                return "Laut Verlauf warst du in den letzten 7 Tagen nicht weg."
            start, end = absence
            label = f"Während du weg warst ({start:%H:%M} bis {end:%H:%M} Uhr)"
        assert start is not None and end is not None
        ids = history_entities(entities, self.hass)
        rows = await async_read_state_rows(self.hass, ids, start, end)
        if rows is None:
            return _NO_RECORDER
        runs = await self._automation_runs(start, end)
        names = {
            state.entity_id: str(state.attributes.get("friendly_name") or state.entity_id)
            for state in states_of(self.hass, "person")
        }
        events = summarize(
            {key: [(moment, state) for moment, state, _ in value] for key, value in rows.items()},
            entities, start, end,
            away=summary.away, speaker_person=person, speaker_is_admin=is_admin,
            automation_runs=runs, executions=self._executions(start, end), person_names=names,
        )
        text, rest = render_summary(events, label)
        if rest:
            self._rest[user_input.conversation_id or ""] = rest
        return text

    async def _automation_runs(self, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
        """Monitors and automations that ran in the window (their own run
        record, ``last_triggered``)."""
        automations = [state.entity_id for state in states_of(self.hass, "automation")]
        rows = await async_read_state_rows(self.hass, automations, start, end, attributes=True) or {}
        runs: dict[tuple[str, str], tuple[datetime, str]] = {}
        for entity_id, items in rows.items():
            for _moment, _state, attributes in items:
                raw = attributes.get("last_triggered")
                moment = raw if isinstance(raw, datetime) else _parse_time(raw)
                if moment is None or not start <= moment <= end:
                    continue
                name = str(attributes.get("friendly_name") or entity_id)
                runs[(entity_id, moment.isoformat())] = (moment, name)
        return sorted(runs.values())

    def _executions(self, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
        """What HomeIntent executed unattended (its trace)."""
        data = getattr(self.hass, "data", None)
        runtime = data.get(TRACE_DATA_KEY) if isinstance(data, dict) else None
        store = getattr(runtime, "store", None)
        found: list[tuple[datetime, str]] = []
        for record in store.recent() if store is not None else ():
            if record.user_present or not record.executed:
                continue
            moment = record.time
            if not start <= moment <= end:
                continue
            names = [record.names.get(target, target) for target in record.targets]
            found.append((moment, "HomeIntent hat " + ", ".join(names) + " geschaltet"))
        return found


    # --- B4 vacation -----------------------------------------------------------

    def _result(self, user_input: conversation.ConversationInput, response: intent.IntentResponse, text: str) -> conversation.ConversationResult:
        response.async_set_speech(text)
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    def _recipients(self, user_input: conversation.ConversationInput, entities: list[EntitySnapshot]) -> tuple[str, ...]:
        """The confirmed household's push devices, else the speaker's own."""
        resolver = NotificationTargetResolver.from_options(
            self.entry.options, getattr(self._runtime, "user_contexts", None)
        )
        for kind in (NotificationRecipientKind.HOUSEHOLD, NotificationRecipientKind.CURRENT_USER):
            resolution = resolver.resolve(kind, conversation_user_id(user_input))
            if resolution.resolved and resolution.entity_ids:
                return tuple(resolution.entity_ids)
        return ()

    async def _async_vacation(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        request: Any, entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        now = dt_util.now()
        store = _vacation_store(self.hass)
        record = store.load()
        if record is not None and datetime.fromisoformat(record.end) <= now:
            store.save(None)  # the end automation already took everything back
            record = None
        manager = self._runtime.dialog_manager
        if request.action == "status":
            return self._answer(user_input, response, _vacation_status(record))
        if request.action == "end":
            if record is None:
                return self._result(user_input, response, "Der Urlaubsmodus ist nicht aktiv.")
            helper = f" und schalte „{record.helper}“ aus" if record.helper else ""
            manager.create(
                user_input.conversation_id, "vacation", DialogTaskKind.VACATION_CONFIRMATION,
                DialogPriority.CONFIRMATION, reason="Das Ende des Urlaubsmodus wartet auf Ja.",
                requested_by_user_id=conversation_user_id(user_input), payload=("end", record),
            )
            return self._result(
                user_input, response,
                f"Ich beende den Urlaubsmodus: Ich lösche seine {len(record.automation_ids)} Automationen{helper}. "
                "Soll ich?",
            )
        if record is not None:
            return self._result(
                user_input, response,
                f"Der Urlaubsmodus läuft schon bis {datetime.fromisoformat(record.end):%d.%m. %H:%M} Uhr. "
                "Sag „Urlaub vorbei“, um ihn zu beenden.",
            )
        if request.end is None:
            question = "Bis wann seid ihr weg? Sag zum Beispiel: „bis Sonntag“."
            manager.create(
                user_input.conversation_id, "monitor-part", DialogTaskKind.MONITOR_PART,
                DialogPriority.FOLLOWUP, reason="Eine Rückfrage nach dem Enddatum ist offen.",
                requested_by_user_id=conversation_user_id(user_input),
                payload=PartRequest(MissingPart.DATE, question, original_text=user_input.text),
            )
            return self._result(user_input, response, question)
        if request.end < now.date():
            return self._result(user_input, response, "Dieses Datum liegt in der Vergangenheit. Bis wann seid ihr weg?")
        if (request.end - now.date()).days > 30:
            return self._result(user_input, response, "Den Urlaubsmodus richte ich für höchstens 30 Tage ein.")
        habits = discover_habits(observations_from_trace(self._trace_records()), now, entities)
        plan = build_plan(request, now, entities, habits, self._recipients(user_input, entities))
        preview = describe_plan(plan, request.end_spoken)
        if not plan.recipients:
            preview = preview.replace(
                "Urlaubsmodus bis", "Benachrichtigen kann ich niemanden (kein bestätigtes Push-Ziel). Urlaubsmodus bis", 1
            )
        manager.create(
            user_input.conversation_id, "vacation", DialogTaskKind.VACATION_CONFIRMATION,
            DialogPriority.CONFIRMATION, reason="Der Urlaubsmodus wartet auf ausdrückliche Bestätigung.",
            requested_by_user_id=conversation_user_id(user_input), payload=("start", plan),
        )
        return self._result(user_input, response, preview)

    async def _async_vacation_answer(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        task: Any, entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        if task.requested_by_user_id not in {None, conversation_user_id(user_input)}:
            return None
        manager = self._runtime.dialog_manager
        reply = _yes_no(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            if len(user_input.text.split()) > 3:
                manager.cancel(user_input.conversation_id, task.task_id)
                return None
            return self._result(user_input, response, "Bitte antworte mit Ja oder Nein.")
        manager.cancel(user_input.conversation_id, task.task_id)
        if reply is ConfirmationReply.NO:
            return self._result(user_input, response, "In Ordnung, ich ändere nichts.")
        action, payload = task.payload
        store = _vacation_store(self.hass)
        executor = self._automations._automation_store()
        if action == "end":
            for automation_id in payload.automation_ids:
                try:
                    await executor.async_delete_automation(automation_id)
                except ValueError:
                    pass  # already gone (end automation or by hand)
            if payload.helper_id:
                await self._switch_helper(user_input, entities, payload.helper_id, "turn_off")
            store.save(None)
            report_outcome(TurnOutcomeKind.EXECUTED)
            return self._result(user_input, response, "Der Urlaubsmodus ist beendet; alles ist zurückgenommen.")
        plan = payload
        configs, _end_id = plan_configs(plan, dt_util.now().date())
        problem = validate_plan(configs, entities)
        if problem is not None:
            return self._result(user_input, response, f"Das richte ich nicht ein ({problem}). Ich habe nichts angelegt.")
        created: list[str] = []
        try:
            for automation_id, config in configs:
                await executor.async_create_automation(
                    dict(config), automation_id=automation_id, owner_user_id=HOUSEHOLD_OWNER
                )
                created.append(automation_id)
        except Exception as err:  # noqa: BLE001 - roll back what was created
            for automation_id in created:
                try:
                    await executor.async_delete_automation(automation_id)
                except Exception:  # noqa: BLE001
                    pass
            return self._result(user_input, response, f"Der Urlaubsmodus konnte nicht eingerichtet werden: {err}")
        if plan.helper is not None:
            await self._switch_helper(user_input, entities, plan.helper, "turn_on")
        store.save(VacationRecord(
            end=plan.end.isoformat(), automation_ids=created, helper=plan.helper_name,
            lights=[light.name for light in plan.lights], watched=len(plan.watched),
            created_by=conversation_user_id(user_input), helper_id=plan.helper,
        ))
        report_outcome(TurnOutcomeKind.EXECUTED)
        return self._result(user_input, response, f"Der Urlaubsmodus läuft bis {plan.end:%d.%m. %H:%M} Uhr.")

    async def _switch_helper(
        self, user_input: conversation.ConversationInput, entities: list[EntitySnapshot], entity_id: str, service: str
    ) -> None:
        """The helper is switched through the one write path."""
        await async_execute_service_plan(
            self.hass, ServiceCallPlan("input_boolean", service, entity_id), entities, self.entry.options,
            is_admin=await user_is_admin(self.hass, user_input), user_id=conversation_user_id(user_input),
            confirmed=True,
        )

    def _trace_records(self) -> tuple[Any, ...]:
        data = getattr(self.hass, "data", None)
        runtime = data.get(TRACE_DATA_KEY) if isinstance(data, dict) else None
        store = getattr(runtime, "store", None)
        return tuple(store.recent()) if store is not None else ()

    # --- B2 habits --------------------------------------------------------------

    def _habits_of(self, user_input: conversation.ConversationInput, entities: list[EntitySnapshot]) -> list[TimedHabit]:
        user_id = conversation_user_id(user_input)
        if user_id is None:
            return []
        mine = actor_hash(user_id)
        return [
            habit for habit in discover_habits(observations_from_trace(self._trace_records()), dt_util.now(), entities)
            if habit.actor == mine
        ]

    def _habit_request(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        request: str, entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        user_id = conversation_user_id(user_input)
        if user_id is None:
            return self._result(user_input, response, "Gewohnheiten erkenne ich nur für angemeldete Personen.")
        store = _habit_store(self.hass)
        actor = actor_hash(user_id)
        if request in {"off", "push", "voice"}:
            store.set_mode(actor, request)
            return self._result(user_input, response, {
                "off": "In Ordnung, ich schlage dir keine Automationen mehr vor.",
                "push": "In Ordnung, neue Vorschläge schicke ich dir per Push.",
                "voice": "In Ordnung, neue Vorschläge sage ich dir wieder direkt.",
            }[request])
        habits = self._habits_of(user_input, entities)
        if request == "list":
            if not habits:
                if getattr(self._runtime, "learned_models", None) is not None:
                    return None  # the V11 routine habits keep their own answer
                return self._answer(user_input, response, (
                    "Ich habe noch keine Gewohnheit erkannt. Dafür brauche ich dieselbe Handlung "
                    f"um eine ähnliche Uhrzeit an mindestens {MIN_DAYS} von {WINDOW_DAYS} Tagen."
                ))
            return self._answer(user_input, response, " ".join(describe_habit(habit, entities) for habit in habits[:5]))
        fresh = [habit for habit in habits if not store.offered(habit.habit_id)]
        if not fresh:
            return self._answer(user_input, response, "Gerade habe ich keinen neuen Vorschlag.")
        return self._offer_habit(user_input, response, fresh[0], entities)

    def _offer_habit(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        habit: TimedHabit, entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        self._runtime.dialog_manager.create(
            user_input.conversation_id, "habit-offer", DialogTaskKind.HABIT_OFFER, DialogPriority.CONFIRMATION,
            reason="Ein Automationsvorschlag wartet auf Ja.",
            requested_by_user_id=conversation_user_id(user_input), payload=habit,
        )
        _habit_store(self.hass).mark(habit.habit_id, "offered")
        return self._result(
            user_input, response, f"{describe_habit(habit, entities)} Soll ich das automatisch machen?"
        )

    def offer_after_turn(self, user_input: conversation.ConversationInput, outcomes: Any) -> str | None:
        """After a successful command: a habit that now qualifies is offered
        once - as a question appended to this reply (or as push)."""
        user_id = conversation_user_id(user_input)
        kinds = getattr(outcomes, "kinds", ())
        # A command ran (its effect may still be reporting); nothing refused.
        ran = {TurnOutcomeKind.EXECUTED, TurnOutcomeKind.UNCONFIRMED}
        if user_id is None or not kinds or any(kind not in ran for kind in kinds):
            return None
        manager = self._runtime.dialog_manager
        if manager.has_open_question(user_input.conversation_id):
            return None  # never during another question
        store = _habit_store(self.hass)
        actor = actor_hash(user_id)
        mode = store.mode(actor)
        if mode == "off":
            return None
        if not self._trace_records():
            return None
        entities = self._entities_of()
        fresh = [habit for habit in self._habits_of(user_input, entities) if not store.offered(habit.habit_id)]
        if not fresh:
            return None
        habit = fresh[0]
        text = f"{describe_habit(habit, entities)} Soll ich das automatisch machen?"
        if mode == "push":
            # Pushed once; "Hast du Vorschläge für Automationen?" sets it up.
            store.mark(habit.habit_id, "pushed")
            self.hass.async_create_background_task(
                self._async_push(user_input, f"HomeIntent-Vorschlag: {describe_habit(habit, entities)} "
                                 "Frag mich „Hast du Vorschläge für Automationen?“, um sie einzurichten."),
                "homeintent_habit_push",
            )
            return None
        manager.create(
            user_input.conversation_id, "habit-offer", DialogTaskKind.HABIT_OFFER, DialogPriority.CONFIRMATION,
            reason="Ein Automationsvorschlag wartet auf Ja.", requested_by_user_id=user_id, payload=habit,
        )
        store.mark(habit.habit_id, "offered")
        return text

    async def _async_push(self, user_input: conversation.ConversationInput, message: str) -> None:
        """The suggestion as push to the speaker's own device (agent delivery)."""
        from ..agent_delivery import AgentDelivery
        from ..notification_request import NotificationRequest, async_deliver_notification_request

        await async_deliver_notification_request(
            NotificationRequest(NotificationRecipientKind.CURRENT_USER, message),
            resolver=NotificationTargetResolver.from_options(
                self.entry.options, getattr(self._runtime, "user_contexts", None)
            ),
            delivery=AgentDelivery(self.hass),
            user_id=conversation_user_id(user_input),
        )

    def _habit_answer(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        task: Any, entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        if task.requested_by_user_id not in {None, conversation_user_id(user_input)}:
            return None
        manager = self._runtime.dialog_manager
        reply = _yes_no(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            if len(user_input.text.split()) > 3:
                manager.cancel(user_input.conversation_id, task.task_id)
                return None
            return self._result(user_input, response, "Bitte antworte mit Ja oder Nein.")
        manager.cancel(user_input.conversation_id, task.task_id)
        habit: TimedHabit = task.payload
        if reply is ConfirmationReply.NO:
            _habit_store(self.hass).mark(habit.habit_id, "rejected")
            return self._result(user_input, response, "In Ordnung, das schlage ich nicht mehr vor.")
        _habit_store(self.hass).mark(habit.habit_id, "accepted")
        target = (
            TriggerTarget(entity_id=habit.targets[0]) if len(habit.targets) == 1
            else TriggerTarget(entity_ids=habit.targets)
        )
        action_type = {"turn_on": ActionType.TURN_ON, "turn_off": ActionType.TURN_OFF,
                       "open_cover": ActionType.TURN_ON, "close_cover": ActionType.TURN_OFF}.get(habit.service)
        action = (
            ActionModel(type=action_type, target=target) if action_type is not None
            else ActionModel(type=ActionType.REGISTERED_SERVICE, target=target,
                             service_domain=habit.domain, service_name=habit.service)
        )
        conditions = (
            (ConditionNode(condition=ConditionModel(
                type=ConditionType.WEEKDAY, weekdays=("mon", "tue", "wed", "thu", "fri"))),)
            if habit.workdays_only else ()
        )
        model = AutomationModel(
            triggers=(TriggerModel(type=TriggerType.TIME, time_hour=habit.hour, time_minute=habit.minute),),
            conditions=conditions, actions=(action,), source_text=describe_habit(habit, entities),
        )
        # The ordinary automation preview, with its own "Ja".
        return self._automations.handle_match_result(
            user_input, response,
            _match_result(model, render_automation_tree(model), validate_automation(model)), entities,
        )


def _asks_more(text: str) -> bool:
    """"Was noch?", "Und weiter?", "Mehr", "Sonst noch was?" (B1)."""
    words = [word.casefold().strip(",.;:!?") for word in text.split()]
    return len(words) <= 4 and (bool(set(words) & {"weiter", "mehr"}) or "noch" in words)
_NO_RECORDER = (
    "Dafür brauche ich den Verlauf von Home Assistant (Recorder); er ist gerade nicht verfügbar. "
    "Ich kann dir nur den jetzigen Zustand sagen."
)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- B4 vacation -------------------------------------------------------------


def _vacation_store(hass: Any) -> VacationStore:
    return VacationStore(hass.config.path("homeintent_vacation.json"))


def _habit_store(hass: Any) -> HabitStore:
    return HabitStore(hass.config.path("homeintent_habit_suggestions.json"))


def _yes_no(text: str) -> ConfirmationReply:
    return classify_confirmation_reply(text)


def _vacation_status(record: VacationRecord | None) -> str:
    if record is None:
        return "Der Urlaubsmodus ist aus."
    parts = []
    if record.watched:
        parts.append(f"Türen, Fenster und Bewegung ({record.watched} Melder) melde ich sofort")
    if record.lights:
        parts.append("Anwesenheit simuliere ich mit " + ", ".join(record.lights))
    if record.helper:
        parts.append(f"„{record.helper}“ ist an")
    detail = ("; ".join(parts) + ".") if parts else ""
    return f"Der Urlaubsmodus läuft bis {datetime.fromisoformat(record.end):%d.%m. um %H:%M} Uhr. {detail}".strip()


def _match_result(model: AutomationModel, text: str, error: Any) -> Any:
    from ..engine import AutomationMatchResult

    return AutomationMatchResult(model, text, error)
