"""7.9.2 Teil B: reports, consumption, summaries, vacation and habits.

One entry point for the new abilities so that ``conversation.py`` stays an
orchestrator. Read-only answers need no "Ja"; everything that writes or
creates something lasting goes through the established preview and "Ja"
(automation previews via ``AutomationController``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any, Awaitable, Callable

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from datetime import datetime, timedelta

from ..energy_query import async_answer as async_energy_answer
from ..event_summary import SummaryEvent, parse_summary_query, render_rest
from ..automation_ownership import turn_is_shared
from ..house_report import (
    ReportRecord,
    ReportRequest,
    build_config,
    describe_request,
    new_report_id,
    parse_report_request,
    parse_report_show,
    render_weekly,
    validate_report_config,
)
from ..const import CONF_SHARE_HOUSEHOLD_LOCATION
from ..media import now_playing, parse_media_request, resolve_media
from ..presence_query import HABIT_DAYS, PersonState, answer_presence, arrivals, parse_presence_query
from ..report_runtime import async_report_store, async_summary_text, async_weekly_facts
from ..execution_trace import TRACE_DATA_KEY
from ..history_query import async_read_forecast, async_read_state_rows
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
    names_helper,
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
from ..entities import EntitySnapshot, normalize_for_compare
from ..weather import (
    WeatherClause,
    WeatherGuard,
    WeatherTurn,
    answer_weather,
    begin_weather_turn,
    build_guard,
    guard_holds_now,
    needs_hourly,
    parse_weather_clause,
    parse_weather_query,
    resume_weather_turn,
    weather_entities,
    weather_turn,
)


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
        devices: Any = None,
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._runtime = runtime
        self._automations = automations
        self._entities_of = entities or (lambda: [])
        # Events of a summary not yet spoken, per conversation (B1).
        self._rest: dict[str, tuple[SummaryEvent, ...]] = {}
        self._rerun: Callable[[conversation.ConversationInput], Awaitable[conversation.ConversationResult]] | None = None
        self._rerunning = False
        self._area_id: str | None = None
        self.devices: Any = devices  # the device controller (the one write path)

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    def begin_turn(self, conversation_id: str | None) -> None:
        """A new turn: the conversation's open rain clause, if any (B1/B4)."""
        resume_weather_turn(conversation_id)

    def _answer(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse, text: str
    ) -> conversation.ConversationResult:
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech(text)
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    async def _async_habits(self) -> HabitStore:
        """The habit store, read from disk in the executor on first use."""
        store = _habit_store(self.hass)
        if store.data is None:
            await self.hass.async_add_executor_job(store.load)
        return store

    async def _async_flush_habits(self) -> None:
        store = _habit_store(self.hass)
        if store.dirty:
            await self.hass.async_add_executor_job(store.save)

    async def async_handle_reading(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
        active_dialog: Any = None,
        rerun: Callable[[conversation.ConversationInput], Awaitable[conversation.ConversationResult]] | None = None,
        open_task: bool = False,
        area_id: str | None = None,
    ) -> conversation.ConversationResult | None:
        """Read-only answers - before the situation view, which would answer
        "während ich weg war" with the current state."""
        if active_dialog is not None:
            return None
        self._rerun = rerun
        if (
            not open_task and not self._rerunning and weather_turn() is not None
            and parse_weather_clause(user_input.text) is None
        ):
            # A fresh, unrelated sentence ends an unfinished rain clause.
            begin_weather_turn(None, user_input.conversation_id)
        self._area_id = area_id
        await self._async_habits()
        try:
            weather = await self._async_weather(user_input, response, entities)
            if weather is not None:
                return weather
            return await self._async_reading(user_input, response, entities)
        finally:
            await self._async_flush_habits()

    # --- 7.9.3 B1/B4 weather and rain --------------------------------------------

    async def _async_forecast(self, entity_id: str, kind: str) -> list[dict[str, Any]] | None:
        """``weather.get_forecasts`` through the one read adapter."""
        return await async_read_forecast(self.hass, entity_id, kind)

    async def _async_weather(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        now = dt_util.now()
        if weather_turn() is None:
            clause = parse_weather_clause(user_input.text)
            if clause is not None:
                return await self._async_rain_rule(user_input, response, clause, entities)
        query = parse_weather_query(user_input.text, now)
        if query is None or _names_a_place_or_device(user_input.text, entities):
            return None
        weathers = weather_entities(entities)
        if not weathers:
            if query.topic == "temperature":
                return None  # the established temperature answers stay
            return self._answer(
                user_input, response,
                "Ich habe keine Wettervorhersage in Home Assistant (keine Wetter-Entität). Raten möchte ich nicht.",
            )
        chosen = [entity for entity in weathers if normalize_for_compare(entity.friendly_name) in
                  normalize_for_compare(user_input.text)]
        if len(chosen) != 1 and len(weathers) > 1:
            listed = " oder ".join(f"„{entity.friendly_name}“" for entity in weathers)
            question = f"Welche Wettervorhersage meinst du: {listed}?"
            self._runtime.dialog_manager.create(
                user_input.conversation_id, "monitor-part", DialogTaskKind.MONITOR_PART, DialogPriority.SELECTION,
                reason="Mehrere Wettervorhersagen.", requested_by_user_id=conversation_user_id(user_input),
                payload=PartRequest(MissingPart.SOURCE, question, original_text=user_input.text,
                                    choices=tuple(entity.friendly_name for entity in weathers)),
            )
            return self._result(user_input, response, question)
        entity = chosen[0] if len(chosen) == 1 else weathers[0]
        forecast: list[dict[str, Any]] | None = None
        if query.days:
            forecast = await self._async_forecast(entity.entity_id, "hourly" if needs_hourly(query) else "daily")
        return self._answer(user_input, response, answer_weather(query, entity, forecast, now))

    async def _async_rain_rule(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        clause: WeatherClause, entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """A request with a rain clause: the rest becomes the automation (or
        the command), the clause its trigger or condition."""
        rest = clause.rest
        if not rest or self._rerun is None:
            return None
        notify = _notifies(rest)
        kinds = clause.kinds
        if clause.role == "trigger" and notify and kinds == ("rain_soon",):
            kinds = ("rain_today",)  # a message about rain: once a day, at a set time
        guard, problem = build_guard(kinds, entities)
        if guard is None:
            assert problem is not None
            if problem.startswith("Welche"):
                return self._result(user_input, response, problem)
            self._runtime.dialog_manager.create(
                user_input.conversation_id, "weather-offer", DialogTaskKind.REPORT_CONFIRMATION,
                DialogPriority.CONFIRMATION, reason="Angebot ohne Regenbedingung.",
                requested_by_user_id=conversation_user_id(user_input), payload=("without", rest),
            )
            return self._result(
                user_input, response,
                f"{problem} Die Bedingung „{clause.spoken}“ kann ich deshalb nicht prüfen. "
                f"Soll ich „{rest.rstrip('.!?')}“ ohne diese Bedingung einrichten?",
            )
        if clause.role == "guard":
            if _scheduled(rest):
                begin_weather_turn(WeatherTurn(user_input.text, "guard", guard), user_input.conversation_id)
                return await self._async_rerun(replace(user_input, text=rest))
            holds, reason = await self._async_guard_now(guard, entities)
            if not holds:
                return self._result(
                    user_input, response, f"{reason}; deshalb führe ich „{rest.rstrip('.!?')}“ jetzt nicht aus."
                )
            return await self._rerun(replace(user_input, text=rest))
        clock = _CLOCK_RE.search(rest) or _CLOCK_RE.search(user_input.text)
        if notify and "rain_today" in guard.kinds and clock is None:
            question = "Um wie viel Uhr soll ich in der Vorhersage nachsehen? Sag zum Beispiel: „um 7 Uhr“."
            self._runtime.dialog_manager.create(
                user_input.conversation_id, "monitor-part", DialogTaskKind.MONITOR_PART, DialogPriority.FOLLOWUP,
                reason="Eine Rückfrage nach der Uhrzeit ist offen.", requested_by_user_id=conversation_user_id(user_input),
                payload=PartRequest(MissingPart.CLOCK, question, original_text=user_input.text),
            )
            return self._result(user_input, response, question)
        body = _CLOCK_RE.sub("", rest).strip(" ,") if clock is not None else rest
        when = f"um {int(clock.group('hour'))}:{int(clock.group('minute') or 0):02d} Uhr" if clock else "um 0:00 Uhr"
        # The rest is read as a daily automation; its trigger is replaced by
        # the rain trigger at the preview (``weather.attach``).
        begin_weather_turn(WeatherTurn(user_input.text, "trigger", guard), user_input.conversation_id)
        return await self._async_rerun(replace(user_input, text=f"Jeden Tag {when} {body[:1].lower()}{body[1:]}"))

    async def _async_rerun(self, again: conversation.ConversationInput) -> conversation.ConversationResult:
        """The rest of a request read again, with the rain clause kept."""
        assert self._rerun is not None
        self._rerunning = True
        try:
            return await self._rerun(again)
        finally:
            self._rerunning = False

    async def _async_guard_now(self, guard: WeatherGuard, entities: list[EntitySnapshot]) -> tuple[bool, str]:
        hourly = daily = None
        if guard.weather_entity_id is not None and "rain_soon" in guard.kinds:
            hourly = await self._async_forecast(guard.weather_entity_id, "hourly")
        if guard.weather_entity_id is not None and {"no_rain_today", "rain_today"} & set(guard.kinds):
            daily = await self._async_forecast(guard.weather_entity_id, "daily")
        states = {entity.entity_id: entity.state for entity in entities}
        held: dict[str, float] = {}
        now = dt_util.utcnow()
        for entity_id in (guard.rain_sensor_id, guard.rain_amount_id):
            state = self.hass.states.get(entity_id) if entity_id else None
            changed = getattr(state, "last_changed", None)
            if entity_id and changed is not None:
                held[entity_id] = (now - changed).total_seconds()
        return guard_holds_now(guard, states, held, hourly, daily)

    async def _async_reading(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        rest = self._rest.pop(user_input.conversation_id or "", ())
        if rest and _asks_more(user_input.text):  # "Was noch?" after a summary (B1)
            self._rest[user_input.conversation_id or ""] = tuple(rest[5:])
            return self._answer(user_input, response, render_rest(rest, dt_util.now().date()))
        task = self._runtime.dialog_manager.active(user_input.conversation_id)
        if task is not None and task.kind is DialogTaskKind.VACATION_CONFIRMATION:
            handled = await self._async_vacation_answer(user_input, response, task, entities)
            if handled is not None:
                return handled
        if task is not None and task.kind is DialogTaskKind.REPORT_CONFIRMATION and (
            isinstance(task.payload, tuple) and task.payload[:1] == ("without",)
        ):
            # "Soll ich das ohne die Regenbedingung einrichten?" (7.9.3 B4)
            if task.requested_by_user_id in {None, conversation_user_id(user_input)}:
                reply = _yes_no(user_input.text)
                if reply is ConfirmationReply.UNCLEAR and len(user_input.text.split()) <= 3:
                    return self._result(user_input, response, "Bitte antworte mit Ja oder Nein.")
                self._runtime.dialog_manager.cancel(user_input.conversation_id, task.task_id)
                if reply is ConfirmationReply.NO:
                    return self._result(user_input, response, "In Ordnung, ich richte nichts ein.")
                if reply is ConfirmationReply.YES and self._rerun is not None:
                    return await self._rerun(replace(user_input, text=task.payload[1]))
        elif task is not None and task.kind is DialogTaskKind.REPORT_CONFIRMATION:
            handled = await self._async_report_answer(user_input, response, task, entities)
            if handled is not None:
                return handled
        if parse_report_show(user_input.text):  # "Zeig mir den Haus-Bericht" (B6)
            return self._answer(user_input, response, await self._async_report_show(user_input, entities))
        report = parse_report_request(user_input.text)
        if report is not None:  # "Schick mir jeden Sonntag … einen Haus-Bericht" (A6/B6)
            return await self._async_report(user_input, response, report, entities)
        if task is not None and task.kind is DialogTaskKind.HABIT_OFFER:
            handled = self._habit_answer(user_input, response, task, entities)
            if handled is not None:
                return handled
        vacation = parse_vacation_request(user_input.text, dt_util.now().date())
        if vacation is not None:  # "Ich bin bis Sonntag weg" (B4)
            handled = await self._async_vacation(user_input, response, vacation, entities)
            if handled is not None:
                return handled
        habit = parse_habit_request(user_input.text)
        if habit is not None:  # "Welche Gewohnheiten hast du erkannt?" (B2)
            answered = self._habit_request(user_input, response, habit, entities)
            if answered is not None:
                return answered
        media = await self._async_media(user_input, response, entities)
        if media is not None:  # "Pause", "Spiel Bayern 3 in der Küche", "Was läuft gerade?" (7.9.3 B3)
            return media
        presence = await self._async_presence(user_input, response, entities)
        if presence is not None:  # "Wo ist Anna?" (7.9.3 B2)
            return presence
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
        actor = conversation_user_id(user_input)
        is_admin = await user_is_admin(self.hass, user_input)
        contexts = getattr(self._runtime, "user_contexts", None)
        binding = contexts.resolve_current_person(actor) if contexts is not None else None
        person = binding.person_entity_id if binding is not None else None
        if summary.away and person is None:
            return (
                "Ich weiß nicht, welche Person du bist, also auch nicht, wann du weg warst. "
                "Frag zum Beispiel: „Was ist heute passiert?“"
            )
        text, rest = await async_summary_text(
            self.hass, entities, summary, person=person, is_admin=is_admin, now=dt_util.now(),
            read_rows=async_read_state_rows,
        )
        if rest:
            self._rest[user_input.conversation_id or ""] = rest
        return text

    # --- 7.9.3 B3 music and media ---------------------------------------------

    async def _async_media(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        request = parse_media_request(user_input.text)
        if request is None or self.devices is None:
            return None
        if request.op == "query":
            return self._answer(user_input, response, now_playing(request, entities, self._area_id))
        resolved = resolve_media(request, entities, satellite_area_id=self._area_id)
        if resolved.plan is None:
            if resolved.question:
                return self._result(user_input, response, resolved.spoken)
            return self._answer(user_input, response, resolved.spoken)
        # The one write path: policy, service_executor, tone and effect wait.
        from ..engine import MatchResult

        return await self.devices.async_handle_match_result(
            user_input, response, MatchResult(plan=resolved.plan, response_text=resolved.spoken), entities
        )

    # --- 7.9.3 B2 where is someone ----------------------------------------------

    async def _async_presence(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Only released (exposed) persons; ``last_changed`` from the state."""
        tz_now = dt_util.now()
        people: list[PersonState] = []
        for entity in entities:
            if entity.domain != "person":
                continue
            state = self.hass.states.get(entity.entity_id) if hasattr(self.hass, "states") else None
            changed = getattr(state, "last_changed", None)
            people.append(PersonState(
                entity.entity_id, entity.friendly_name, entity.state,
                dt_util.as_local(changed) if changed is not None else None,
            ))
        if not people:
            return None
        query = parse_presence_query(user_input.text, [person.name for person in people])
        if query is None or query.kind == "is_home" or (
            query.kind == "who_home" and _words_of(user_input.text)[:1] == ["wer"]
        ):
            return None  # "Wer ist zuhause?", "Ist Anna zuhause?" keep their established answer
        contexts = getattr(self._runtime, "user_contexts", None)
        actor = conversation_user_id(user_input)
        binding = contexts.resolve_current_person(actor) if contexts is not None and actor else None
        history: dict[str, list[datetime]] | None = None
        if query.kind in {"arrived", "will_arrive"}:
            ids = [person.entity_id for person in people]
            rows = await async_read_state_rows(self.hass, ids, tz_now - timedelta(days=HABIT_DAYS), tz_now)
            if rows is not None:
                history = {key: arrivals([(moment, state) for moment, state, _ in value]) for key, value in rows.items()}
        text = answer_presence(
            query, people, tz_now,
            speaker_person=binding.person_entity_id if binding is not None else None,
            is_admin=await user_is_admin(self.hass, user_input),
            share=bool(self.entry.options.get(CONF_SHARE_HOUSEHOLD_LOCATION, False)),
            arrival_history=history,
        )
        return self._answer(user_input, response, text)

    # --- 7.9.3 A6/B6 pushed reports ----------------------------------------------

    async def _async_report(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse,
        request: ReportRequest, entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Preview of a pushed report; created only after "Ja"."""
        user_id = conversation_user_id(user_input)
        shared = turn_is_shared(self.hass, self.entry.options, user_input)
        if user_id is None and not shared:
            return self._result(user_input, response, "Berichte richte ich nur für angemeldete Personen ein.")
        if not await self._automations.async_may_create(user_input):
            return self._result(user_input, response, "Das Einrichten ist nur für Administratoren erlaubt.")
        person = None
        if request.kind == "arrival":
            contexts = getattr(self._runtime, "user_contexts", None)
            binding = contexts.resolve_current_person(user_id) if contexts is not None and user_id else None
            person = binding.person_entity_id if binding is not None else None
            if person is None:
                return self._result(
                    user_input, response,
                    "Ich weiß nicht, welche Person du bist, also auch nicht, wann du heimkommst. "
                    "Ein Administrator kann dich unter HomeIntent mit deiner Person verknüpfen.",
                )
        if request.kind == "weekly" and request.hour is None:
            question = "Um wie viel Uhr soll ich dir den Haus-Bericht schicken? Sag zum Beispiel: „um 18 Uhr“."
            self._runtime.dialog_manager.create(
                user_input.conversation_id, "monitor-part", DialogTaskKind.MONITOR_PART, DialogPriority.FOLLOWUP,
                reason="Eine Rückfrage nach der Uhrzeit ist offen.", requested_by_user_id=user_id,
                payload=PartRequest(MissingPart.CLOCK, question, original_text=user_input.text),
            )
            return self._result(user_input, response, question)
        labels = {item.entity_id: item.friendly_name for item in entities}
        resolver = NotificationTargetResolver.from_options(
            self.entry.options, getattr(self._runtime, "user_contexts", None),
            label_for=lambda target_id: labels.get(target_id, ""),
        )
        kind = NotificationRecipientKind.HOUSEHOLD if shared else NotificationRecipientKind.CURRENT_USER
        resolution = resolver.resolve(kind, user_id)
        if not resolution.resolved:
            return self._result(
                user_input, response,
                "Ich kenne kein bestätigtes Push-Ziel dafür; ohne gebundenes Gerät richte ich keinen Bericht ein.",
            )
        labels = [target.label or target.target_id for target in resolution.targets]
        recipient = "euch per Push" if shared else "dir an „" + "“ und „".join(labels) + "“"
        example = ""
        if request.kind == "weekly":
            facts = await async_weekly_facts(self.hass, entities, dt_util.now())
            example = render_weekly(facts)[0].split(" Details:")[0].rstrip(".")
        preview = describe_request(request, recipient, example)
        if shared:
            preview = preview.replace(" Soll ich", " Er gehört dem ganzen Haushalt (gemeinsam). Soll ich", 1)
        self._runtime.dialog_manager.create(
            user_input.conversation_id, "report", DialogTaskKind.REPORT_CONFIRMATION, DialogPriority.CONFIRMATION,
            reason="Ein Bericht per Push wartet auf ausdrückliche Bestätigung.", requested_by_user_id=user_id,
            payload=(request, new_report_id(), shared, person),
        )
        return self._result(user_input, response, preview)

    async def _async_report_answer(
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
            return self._result(user_input, response, "In Ordnung, ich richte nichts ein.")
        request, report_id, shared, person = task.payload
        config = build_config(report_id, request, person)
        problem = validate_report_config(config, entities)
        if problem is not None:
            return self._result(user_input, response, f"Das richte ich nicht ein ({problem}). Ich habe nichts angelegt.")
        owner = HOUSEHOLD_OWNER if shared else conversation_user_id(user_input)
        store = await async_report_store(self.hass)
        store.records[report_id] = ReportRecord(
            report_id, request.kind, conversation_user_id(user_input), shared, person,
        )
        try:
            automation_id = await self._automations._automation_store().async_create_automation(
                config, owner_user_id=owner
            )
        except Exception as err:  # noqa: BLE001 - nothing half-created stays
            store.records.pop(report_id, None)
            return self._result(user_input, response, f"Der Bericht konnte nicht eingerichtet werden: {err}")
        store.records[report_id].automation_id = automation_id
        await self.hass.async_add_executor_job(store.save)
        report_outcome(TurnOutcomeKind.EXECUTED)
        return self._result(user_input, response, "Eingerichtet." if request.kind == "arrival" else (
            "Eingerichtet. Die Details eines Berichts sage ich dir auf „Zeig mir den Haus-Bericht“."
        ))

    async def _async_report_show(
        self, user_input: conversation.ConversationInput, entities: list[EntitySnapshot]
    ) -> str:
        """The full house report: the last one sent, else as of now (read-only)."""
        user_id = conversation_user_id(user_input)
        store = await async_report_store(self.hass)
        mine = [
            record for record in store.records.values()
            if record.kind == "weekly" and record.last_full and (record.shared or record.owner_user_id == user_id)
        ]
        if mine:
            latest = max(mine, key=lambda record: record.last_sent)
            sent = datetime.fromisoformat(latest.last_sent)
            return f"Der letzte Haus-Bericht vom {sent:%d.%m.} um {sent:%H:%M} Uhr: " + latest.last_full.removeprefix(
                "Haus-Bericht: "
            )
        facts = await async_weekly_facts(self.hass, entities, dt_util.now())
        full = render_weekly(facts)[1]
        return "Einen Haus-Bericht habe ich dir noch nicht geschickt. Nach heutigem Stand: " + full.removeprefix(
            "Haus-Bericht: "
        )

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
    ) -> conversation.ConversationResult | None:
        now = dt_util.now()
        store = _vacation_store(self.hass)
        record = await self.hass.async_add_executor_job(store.load)
        if record is not None and datetime.fromisoformat(record.end) <= now:
            # the end automation already took everything back
            await self.hass.async_add_executor_job(store.save, None)
            record = None
        manager = self._runtime.dialog_manager
        if request.action == "status":
            return self._answer(user_input, response, _vacation_status(record))
        if request.action in {"end", "return"}:
            if record is None:
                if request.action == "return" or names_helper(user_input.text):
                    # "Ich bin wieder zuhause" without a vacation mode, or
                    # "Schalte den Urlaubsmodus aus" (the helper device):
                    # read on as before (7.9.3 A3).
                    return None
                return self._result(
                    user_input, response, "Der Urlaubsmodus ist nicht aktiv; ich habe nichts geändert."
                )
            helper = f" und schalte „{record.helper}“ aus" if record.helper else ""
            manager.create(
                user_input.conversation_id, "vacation", DialogTaskKind.VACATION_CONFIRMATION,
                DialogPriority.CONFIRMATION, reason="Das Ende des Urlaubsmodus wartet auf Ja.",
                requested_by_user_id=conversation_user_id(user_input), payload=("end", record),
            )
            welcome = (
                f"Willkommen zurück! Der Urlaubsmodus läuft noch bis "
                f"{datetime.fromisoformat(record.end):%d.%m.}. Soll ich ihn beenden? "
                if request.action == "return" else ""
            )
            return self._result(
                user_input, response,
                f"{welcome}Ich beende den Urlaubsmodus: Ich lösche seine {len(record.automation_ids)} "
                f"Automationen{helper}. Soll ich?",
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
            await self.hass.async_add_executor_job(store.save, None)
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
        await self.hass.async_add_executor_job(store.save, VacationRecord(
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

    async def async_offer_after_turn(self, user_input: conversation.ConversationInput, outcomes: Any) -> str | None:
        """After a successful command: a habit that now qualifies is offered
        once - as a question appended to this reply (or as push)."""
        if conversation_user_id(user_input) is None or not getattr(outcomes, "kinds", ()):
            return None
        await self._async_habits()
        try:
            return self._offer_after_turn(user_input, outcomes)
        finally:
            await self._async_flush_habits()

    def _offer_after_turn(self, user_input: conversation.ConversationInput, outcomes: Any) -> str | None:
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


_LOGGER = logging.getLogger(__name__)
_CLOCK_RE = re.compile(r"\bum\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?:uhr)?\b", re.IGNORECASE)
_NOTIFY_WORDS = frozenset({
    "bescheid", "nachricht", "push", "benachrichtige", "benachrichtigung", "informiere", "warne", "melde",
    "erinnere", "schick", "schicke", "sende",
})
_SCHEDULE_WORDS = frozenset({
    "jeden", "jede", "jedes", "taeglich", "morgens", "abends", "mittags", "nachts", "werktags", "wochentags",
    "montags", "dienstags", "mittwochs", "donnerstags", "freitags", "samstags", "sonntags", "immer", "wenn",
    "sobald", "sonnenaufgang", "sonnenuntergang", "uhr",
})


def _words_of(text: str) -> list[str]:
    return "".join(char if char.isalnum() else " " for char in normalize_for_compare(text)).split()


def _notifies(text: str) -> bool:
    words = set(_words_of(text))
    return bool(words & _NOTIFY_WORDS) and not words & {"oeffne", "schliesse", "fahr", "fahre", "schalte", "mach"}


def _scheduled(text: str) -> bool:
    """A time or event in the rest: an automation, not a command now."""
    return bool(set(_words_of(text)) & _SCHEDULE_WORDS) or _CLOCK_RE.search(text) is not None


def _names_a_place_or_device(text: str, entities: list[EntitySnapshot]) -> bool:
    """"Wie warm wird es im Büro?" is no weather question."""
    words = set(_words_of(text))
    for entity in entities:
        if entity.domain == "weather":
            continue
        area = normalize_for_compare(entity.area_name or "")
        if area and area not in {"garten", "terrasse"} and area in words:
            return True
    return False


def _asks_more(text: str) -> bool:
    """"Was noch?", "Und weiter?", "Mehr", "Sonst noch was?" (B1)."""
    words = [word.casefold().strip(",.;:!?") for word in text.split()]
    return len(words) <= 4 and (bool(set(words) & {"weiter", "mehr"}) or "noch" in words)


# --- B4 vacation -------------------------------------------------------------


def _vacation_store(hass: Any) -> VacationStore:
    return VacationStore(hass.config.path("homeintent_vacation.json"))


_HABIT_STORE_KEY = "homeintent_habit_store"


def _habit_store(hass: Any) -> HabitStore:
    """The one habit store of this Home Assistant (loaded in the executor
    by ``InsightsController._async_habits``)."""
    data = hass.data if isinstance(getattr(hass, "data", None), dict) else {}
    store = data.get(_HABIT_STORE_KEY)
    if not isinstance(store, HabitStore):
        store = HabitStore(hass.config.path("homeintent_habit_suggestions.json"))
        data[_HABIT_STORE_KEY] = store
    return store


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
