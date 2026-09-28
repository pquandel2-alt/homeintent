"""ResponseGenerator (HomeIntent v4.2.1 plan, Section 12/Phase 6): the third
stage of the Query pipeline - ``QueryResult`` -> German response text, kept
as its own step, separate from ``QueryResult`` itself (Section 12's explicit
3-stage split: Parser -> QueryCommand -> QueryExecutor -> QueryResult ->
ResponseGenerator -> text).

WorldModelQuery wave: this is now the **live** response source for
``StateQueryParser``'s four intents (``HassStateQuery``/``HassCheckState``/
``HassExistsQuery``/``HassDeviceQuery``) - ``engine.py::_build_match_result``
calls ``respond()`` whenever ``frame.parameters["query_result"]`` is set,
before it ever reaches ``service_call.py``'s ``QUERY_INTENTS`` response
lambdas (now dead code for those three intents, kept only because
``QueryIntentSpec.response`` is a required field). The wording below is no
longer required to mirror those lambdas byte-for-byte - it is the primary
source now, not a future-caller preview.

All four ``QueryResultStatus`` values are distinguished, each with its own
noun-aware text: ``MATCHED``/``EMPTY`` via ``_respond_single``/
``_respond_exists``/``_respond_list_or_count``/``_respond_device_list``;
``TARGET_NOT_FOUND``/``AMBIGUOUS`` directly in ``respond()``. Neither of the
latter two is reachable via any sentence live today (see
``QueryExecutor._execute_plural``'s own docstring and
``StateQueryParser._parse_check_state``'s "never guess, no clarification
round-trip" precedent, Regel 4) - they stay correct and tested for a future
caller, without changing that existing refuse-early behavior.

Never touches Home Assistant - it doesn't even see a ``hass`` object, only a
``QueryResult``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..entities import format_spoken_number
from ..response_planner import (
    DialogAct,
    GermanResponseRealizer,
    QueryAnswerKind,
    QueryResponsePlan,
    ResponsePlan,
)
from .german_morphology import nominative_pronoun_for_entity
from .query_command import (
    QueryCommand,
    QueryResult,
    QueryResultStatus,
    QueryScope,
    QueryTargetKind,
)
from .semantic_state import (
    SemanticState,
    derive_semantic_state,
    evaluate_semantic_state,
    matches_semantic_state,
)

if TYPE_CHECKING:
    from .context import ConversationContext

_SEMANTIC_STATE_SPOKEN_DE = {
    SemanticState.OPEN: "geöffnet",
    SemanticState.CLOSED: "geschlossen",
    SemanticState.ON: "eingeschaltet",
    SemanticState.OFF: "ausgeschaltet",
    SemanticState.ACTIVE: "aktiv",
    SemanticState.INACTIVE: "inaktiv",
}

_SEMANTIC_STATE_SHORT_DE = {
    SemanticState.OPEN: "offen",
    SemanticState.CLOSED: "geschlossen",
    SemanticState.ON: "an",
    SemanticState.OFF: "aus",
    SemanticState.ACTIVE: "aktiv",
    SemanticState.INACTIVE: "inaktiv",
}

_DEVICE_CLASS_PLURAL_DE = {
    "window": "Fenster", "door": "Türen", "garage_door": "Garagentore", "motion": "Bewegungsmelder",
}

_DEVICE_CLASS_SINGULAR_DE = {
    "window": "Fenster", "door": "Tür", "garage_door": "Garagentor", "motion": "Bewegungsmelder",
}

_DOMAIN_PLURAL_DE = {
    "light": "Lichter", "switch": "Schalter", "fan": "Ventilatoren",
    "cover": "Rollläden", "climate": "Heizungen", "media_player": "Medienplayer",
    "vacuum": "Saugroboter", "humidifier": "Luftbefeuchter",
    "input_boolean": "Helfer", "valve": "Ventile", "lawn_mower": "Mähroboter",
}
_DOMAIN_SINGULAR_DE = {
    "light": "Licht", "switch": "Schalter", "fan": "Ventilator",
    "cover": "Rollladen", "climate": "Heizung", "media_player": "Medienplayer",
    "vacuum": "Saugroboter", "humidifier": "Luftbefeuchter",
    "input_boolean": "Helfer", "valve": "Ventil", "lawn_mower": "Mähroboter",
}


def _automation_label(automation) -> str:
    """The spoken name for one automation (V5.29) - its own ``source_text``
    (the exact sentence that created it, HomeIntent-made automations only)
    when available, since that's more recognizable to the user than an
    auto-generated ``alias``; falls back to ``alias`` for automations this
    integration didn't create (every HA automation has one, whether made by
    hand in the UI or by HomeIntent - see ``AutomationSummary``'s docstring).
    """
    # The source sentence ends with its own punctuation; the label is embedded
    # in longer answers, so a trailing "." would double up there.
    return (automation.source_text or automation.alias).rstrip(" .!?")


class ResponseGenerator:
    """Stateless, same as ``QueryExecutor`` - one shared instance is enough."""

    def respond(
        self,
        result: QueryResult,
        *,
        context: ConversationContext | None = None,
        allow_reference: bool = False,
    ) -> str:
        """Plan and realize one deterministic query response."""

        if result.command is not None and result.command.algebra is not None:
            return self._respond_algebra(result)
        if (
            result.command is not None
            and result.command.scope is QueryScope.SINGLE
            and result.command.filter.state is None
            and result.command.filter.relational is None
            and result.command.filter.relationship is None
            and len(result.entities) == 1
            and result.entities[0].domain == "sensor"
            and result.entities[0].unit is not None
        ):
            entity = result.entities[0]
            unit = entity.unit
            assert unit is not None
            spoken_unit = {
                "°C": "Grad", "°F": "Grad", "%": "Prozent",
            }.get(unit, unit)
            return f"{format_spoken_number(entity.state)} {spoken_unit}."

        return GermanResponseRealizer().realize(
            self.plan(result, context=context, allow_reference=allow_reference)
        )

    @staticmethod
    def _respond_algebra(result: QueryResult) -> str:
        """Render typed V9 results without reinterpreting the source text."""
        if result.status is QueryResultStatus.AMBIGUOUS:
            return "Mehrere gleichwertige Messwerte sind möglich. Welchen soll ich verwenden?"
        if result.status in {
            QueryResultStatus.TARGET_NOT_FOUND,
            QueryResultStatus.UNSUPPORTED,
        }:
            return "Diese Abfrage kann ich mit den bekannten Fakten nicht sicher auswerten."
        if result.groups:
            return ", ".join(f"{group.label}: {group.value}" for group in result.groups) + "."
        if isinstance(result.scalar, bool):
            return "Ja." if result.scalar else "Nein."
        if result.scalar is not None:
            return f"{format_spoken_number(result.scalar)}."
        names = tuple(
            item.friendly_name for item in result.entities
        ) or tuple(item.name for item in result.areas) or tuple(
            item.name for item in result.floors
        )
        if not names:
            return "Keine passenden Ergebnisse."
        if len(names) == 1:
            return f"{names[0]}."
        return f"{', '.join(names[:-1])} und {names[-1]}."

    def plan(
        self,
        result: QueryResult,
        *,
        context: ConversationContext | None = None,
        allow_reference: bool = False,
    ) -> ResponsePlan:
        """Convert a query result into structured, grounded response data."""

        command = result.command
        if command is None:
            # Defensive only - every QueryExecutor result carries its
            # originating command; a bare status without one can't be spoken
            # (nothing constructs QueryResult that way).
            return self._wrap(QueryResponsePlan(QueryAnswerKind.NOT_UNDERSTOOD))

        if result.status is QueryResultStatus.TARGET_NOT_FOUND:
            return self._wrap(
                QueryResponsePlan(QueryAnswerKind.NOT_FOUND, noun_plural=self._noun(result))
            )
        if result.status is QueryResultStatus.AMBIGUOUS:
            return self._wrap(
                QueryResponsePlan(QueryAnswerKind.AMBIGUOUS, noun_plural=self._noun(result))
            )

        if command.target.kind is QueryTargetKind.DEVICE:
            plan = self._respond_device_list(result)
        elif command.target.kind is QueryTargetKind.AUTOMATION:
            plan = self._respond_automation(result)
        elif command.filter.relational is not None:
            plan = self._respond_relational(result)
        elif command.filter.relationship is not None:
            plan = self._respond_relationship(result)
        elif command.scope is QueryScope.SINGLE:
            plan = self._respond_single(result)
        elif command.scope is QueryScope.EXISTS:
            plan = self._respond_exists(result)
        elif command.scope is QueryScope.ALL:
            plan = self._respond_all(result)
        elif command.scope is QueryScope.NONE:
            plan = self._respond_none(result)
        elif command.scope is QueryScope.LOCATIONS:
            plan = self._respond_locations(result)
        else:
            plan = self._respond_list_or_count(result)
        return self._with_contextual_reference(
            plan,
            result,
            context=context,
            allow_reference=allow_reference,
        )

    @staticmethod
    def _respond_relational(result: QueryResult) -> ResponsePlan:
        """Realize the truth value already computed by QueryExecutor."""
        assert result.command is not None
        comparison = result.command.filter.relational
        assert comparison is not None
        by_id = {entity.entity_id: entity for entity in result.considered_entities}
        left = by_id.get(comparison.left.entity_id)
        right = by_id.get(comparison.right.entity_id)
        if left is None or right is None:
            return ResponseGenerator._wrap(
                QueryResponsePlan(QueryAnswerKind.NOT_FOUND)
            )
        return ResponseGenerator._wrap(
            QueryResponsePlan(
                QueryAnswerKind.RELATIONAL_COMPARISON,
                names=(left.friendly_name, right.friendly_name),
                matched=result.status is QueryResultStatus.MATCHED,
            )
        )

    @staticmethod
    def _respond_relationship(result: QueryResult) -> ResponsePlan:
        return ResponseGenerator._wrap(
            QueryResponsePlan(
                QueryAnswerKind.RELATION_LIST,
                noun_plural=ResponseGenerator._noun(result),
                names=tuple(entity.friendly_name for entity in result.entities),
            )
        )

    @staticmethod
    def _wrap(query: QueryResponsePlan) -> ResponsePlan:
        return ResponsePlan(dialog_act=DialogAct.INFORM, query=query)

    @staticmethod
    def _with_contextual_reference(
        plan: ResponsePlan,
        result: QueryResult,
        *,
        context: ConversationContext | None,
        allow_reference: bool,
    ) -> ResponsePlan:
        """Use a pronoun only for a proven one-to-one typed follow-up."""

        if not allow_reference or context is None or context.focus is None:
            return plan
        if len(context.focus.candidate_entity_ids) != 1:
            return plan
        if len(context.last_entities) != 1 or len(result.entities) != 1:
            return plan
        if result.command is None or result.command.target.area is None:
            return plan
        previous_command = context.last_command
        if previous_command is None:
            return plan
        previous_query = previous_command.parameters.get("query_command")
        if not isinstance(previous_query, QueryCommand):
            return plan
        current_target = result.command.target
        previous_target = previous_query.target
        if (
            current_target.kind is not previous_target.kind
            or current_target.domain != previous_target.domain
            or current_target.device_class != previous_target.device_class
        ):
            return plan
        previous_pronoun = nominative_pronoun_for_entity(
            context.last_entities[0].friendly_name
        )
        current_pronoun = nominative_pronoun_for_entity(
            result.entities[0].friendly_name
        )
        if current_pronoun is None or current_pronoun != previous_pronoun:
            return plan
        query = plan.query
        state = result.command.filter.state
        if (
            query is None
            or query.kind not in {QueryAnswerKind.FILTERED_LIST, QueryAnswerKind.SINGLE}
            or state is None
        ):
            return plan
        realized_state = _SEMANTIC_STATE_SHORT_DE[state]
        realized_match = query.matched
        if query.kind is QueryAnswerKind.SINGLE:
            current_state = derive_semantic_state(result.entities[0])
            if current_state is not SemanticState.UNKNOWN:
                realized_state = _SEMANTIC_STATE_SHORT_DE[current_state]
                realized_match = True
        return replace(
            plan,
            query=replace(
                query,
                state=realized_state,
                matched=realized_match,
                pronoun=current_pronoun,
                deictic_location=True,
            ),
        )

    @staticmethod
    def _noun(result: QueryResult) -> str:
        assert result.command is not None
        target = result.command.target
        if target.device_class is not None:
            return _DEVICE_CLASS_PLURAL_DE.get(target.device_class, "Geräte")
        return _DOMAIN_PLURAL_DE.get(target.domain or "", "Geräte")

    @staticmethod
    def _noun_singular(result: QueryResult) -> str:
        assert result.command is not None
        target = result.command.target
        if target.device_class is not None:
            return _DEVICE_CLASS_SINGULAR_DE.get(target.device_class, "Gerät")
        return _DOMAIN_SINGULAR_DE.get(target.domain or "", "Gerät")

    def _respond_list_or_count(self, result: QueryResult) -> ResponsePlan:
        assert result.command is not None
        state = result.command.filter.state
        if state is None:
            return self._respond_list_no_state(result)
        state_word = _SEMANTIC_STATE_SPOKEN_DE[state]
        noun = self._noun(result)
        entities = result.entities
        considered = result.considered_entities
        if not entities:
            return self._wrap(
                QueryResponsePlan(
                    QueryAnswerKind.FILTERED_LIST,
                    noun_plural=noun,
                    state=state_word,
                )
            )
        if result.command.scope is QueryScope.COUNT:
            return self._wrap(
                QueryResponsePlan(
                    QueryAnswerKind.COUNT,
                    noun_plural=noun,
                    noun_singular=self._noun_singular(result),
                    names=tuple(e.friendly_name for e in entities),
                    state=state_word,
                )
            )
        if len(considered) > 1:
            matched_ids = {entity.entity_id for entity in entities}
            exception_names = tuple(
                entity.friendly_name
                for entity in considered
                if entity.entity_id not in matched_ids
            )
            if len(entities) == len(considered):
                return self._wrap(
                    QueryResponsePlan(
                        QueryAnswerKind.COMPLETE_GROUP,
                        noun_plural=noun,
                        names=tuple(e.friendly_name for e in entities),
                        state=_SEMANTIC_STATE_SHORT_DE[state],
                        considered_count=len(considered),
                    )
                )
            if len(entities) == 1:
                return self._wrap(
                    QueryResponsePlan(
                        QueryAnswerKind.ONLY_MATCH,
                        noun_plural=noun,
                        names=(entities[0].friendly_name,),
                        state=_SEMANTIC_STATE_SHORT_DE[state],
                        considered_count=len(considered),
                    )
                )
            if len(entities) == len(considered) - 1 and len(considered) >= 3:
                return self._wrap(
                    QueryResponsePlan(
                        QueryAnswerKind.ALL_BUT,
                        noun_plural=noun,
                        names=tuple(e.friendly_name for e in entities),
                        exception_names=exception_names,
                        state=_SEMANTIC_STATE_SHORT_DE[state],
                        considered_count=len(considered),
                    )
                )
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.FILTERED_LIST,
                noun_plural=noun,
                names=tuple(e.friendly_name for e in entities),
                state=state_word,
            )
        )

    def _respond_list_no_state(self, result: QueryResult) -> ResponsePlan:
        """Stateless listing ("Welche Lampen sind im Wohnzimmer?",
        ``QueryFilter.state=None`` - the user-notation's "state=ANY") - area-
        based phrasing instead of state-based, since there is no state word
        to speak."""
        assert result.command is not None
        noun = self._noun(result)
        area = result.command.target.area
        entities = result.entities
        if area is None and len(entities) == 1:
            # Without a room there is no location to list; say what the one
            # matching device is doing instead of an unfinished sentence.
            entity = entities[0]
            spoken = _SEMANTIC_STATE_SPOKEN_DE.get(derive_semantic_state(entity))
            if spoken is None:
                return self._wrap(
                    QueryResponsePlan(
                        QueryAnswerKind.UNKNOWN_SINGLE, names=(entity.friendly_name,)
                    )
                )
            return self._wrap(
                QueryResponsePlan(
                    QueryAnswerKind.SINGLE,
                    names=(entity.friendly_name,),
                    current_state=spoken,
                )
            )
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.AREA_LIST,
                noun_plural=noun,
                names=tuple(e.friendly_name for e in entities),
                area_names=(area.name,) if area is not None else (),
            )
        )

    def _respond_device_list(self, result: QueryResult) -> ResponsePlan:
        """DEVICE-scope queries (HassDeviceQuery, "welche Geräte sind im
        Büro?") - always has a resolved area (``_parse_device_query`` refuses
        without one before ever building a ``QueryCommand``).

        Follow-up wave, Phase 4: an optional ``filter.state`` ("Welche Geräte
        sind eingeschaltet im Büro?") switches to state-based phrasing,
        mirroring ``_respond_list_or_count``'s state-word branch exactly -
        same wording pattern, just for devices instead of entities."""
        assert result.command is not None
        assert result.command.target.area is not None
        area_name = result.command.target.area.name
        devices = result.devices
        state = result.command.filter.state
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.DEVICE_LIST,
                names=tuple(device.name for device in devices),
                area_names=(area_name,),
                state=_SEMANTIC_STATE_SPOKEN_DE[state] if state is not None else None,
            )
        )

    def _respond_automation(self, result: QueryResult) -> ResponsePlan:
        """AUTOMATION-scope queries (HassAutomationQuery/HassAutomationWhyQuery,
        V5.29). Three shapes: a bare "welche Automationen gibt es?" listing
        (no ``target.entity_id``), and two entity-filtered phrasings sharing
        the exact same ``QueryResult`` shape but different wording depending
        on ``command.intent`` - HassAutomationQuery's plain "wird gesteuert
        von" vs. HassAutomationWhyQuery's hedged "könnte beeinflusst werden"
        (deliberately non-committal: this only reports automations that
        *mention* the entity somewhere, see ``AutomationSummary``'s
        docstring - it is not a causation proof).
        """
        assert result.command is not None
        command = result.command
        labels = tuple(_automation_label(a) for a in result.automations)

        if command.target.entity_id is None:
            return self._wrap(
                QueryResponsePlan(QueryAnswerKind.AUTOMATION_LIST, names=labels)
            )

        entity_name = result.entities[0].friendly_name if result.entities else "das"
        causal = command.intent == "HassAutomationWhyQuery"
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.AUTOMATION_RELATION,
                names=labels,
                subject_name=entity_name,
                causal=causal,
            )
        )

    def _respond_exists(self, result: QueryResult) -> ResponsePlan:
        state = result.command.filter.state if result.command is not None else None
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.EXISTS,
                noun_plural=self._noun(result),
                noun_singular=self._noun_singular(result),
                names=tuple(entity.friendly_name for entity in result.entities),
                state=_SEMANTIC_STATE_SPOKEN_DE.get(state) if state is not None else None,
            )
        )

    def _respond_all(self, result: QueryResult) -> ResponsePlan:
        assert result.command is not None
        noun = self._noun(result)
        state = result.command.filter.state
        assert state is not None
        state_word = _SEMANTIC_STATE_SPOKEN_DE[state]
        # "Nein, nicht alle …": name the counterexamples (7.6.0).
        exceptions = tuple(
            entity.friendly_name for entity in result.entities
            if not matches_semantic_state(entity, state)
        )
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.ALL,
                noun_plural=noun,
                names=tuple(entity.friendly_name for entity in result.entities),
                state=state_word,
                matched=result.status is QueryResultStatus.MATCHED,
                exception_names=exceptions,
            )
        )

    def _respond_none(self, result: QueryResult) -> ResponsePlan:
        assert result.command is not None
        noun = self._noun(result)
        state = result.command.filter.state
        assert state is not None
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.NONE,
                noun_plural=noun,
                names=tuple(entity.friendly_name for entity in result.entities),
                state=_SEMANTIC_STATE_SPOKEN_DE[state],
                matched=result.status is QueryResultStatus.MATCHED,
            )
        )

    def _respond_locations(self, result: QueryResult) -> ResponsePlan:
        assert result.command is not None
        noun = self._noun(result)
        areas = sorted({entity.area_name for entity in result.entities if entity.area_name})
        requested = result.command.filter.state
        return self._wrap(
            QueryResponsePlan(
                QueryAnswerKind.LOCATIONS,
                noun_plural=noun,
                area_names=tuple(areas),
                state=(
                    _SEMANTIC_STATE_SPOKEN_DE[requested]
                    if requested is not None
                    else None
                ),
            )
        )

    @staticmethod
    def _respond_single(result: QueryResult) -> ResponsePlan:
        assert result.command is not None
        entity = result.entities[0]
        requested = result.command.filter.state
        if requested is None:
            current = derive_semantic_state(entity)
            if current is SemanticState.UNKNOWN:
                return ResponseGenerator._wrap(
                    QueryResponsePlan(
                        QueryAnswerKind.UNKNOWN_SINGLE,
                        names=(entity.friendly_name,),
                    )
                )
            return ResponseGenerator._wrap(
                QueryResponsePlan(
                    QueryAnswerKind.SINGLE,
                    names=(entity.friendly_name,),
                    current_state=_SEMANTIC_STATE_SPOKEN_DE[current],
                )
            )
        state_word = _SEMANTIC_STATE_SPOKEN_DE[requested]
        evaluation = evaluate_semantic_state(entity, requested)
        if evaluation is None:
            return ResponseGenerator._wrap(
                QueryResponsePlan(
                    QueryAnswerKind.UNDETERMINED_SINGLE,
                    names=(entity.friendly_name,),
                    state=state_word,
                    current_state=entity.state,
                )
            )
        return ResponseGenerator._wrap(
            QueryResponsePlan(
                QueryAnswerKind.SINGLE,
                names=(entity.friendly_name,),
                state=state_word,
                matched=evaluation,
            )
        )
