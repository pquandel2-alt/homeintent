"""RelativeTimeCommandParser (Wave 13, "Relative-Zeit-Automationen"): turns a
single-clause spoken command with a relative-time offset ("Fahre in 5 Minuten
die Wohnzimmer Rolllade auf 30 Prozent") into the offset (in seconds) plus the
already-parsed action(s) to run once that offset has elapsed.

Top-level, hassil-aware - sibling of ``automation_trigger_parser.py``/
``automation_action_parser.py``, not part of ``nlu/`` (see ``nlu/frame.py``'s
docstring). Its own separately-compiled ``Intents`` grammar lives under
``intents/de/relative_time/`` (one YAML file, one intent,
``HassRelativeTimeCommand`` - see that file's own comment for the three
sentence shapes and why this needs a dedicated grammar/entry point rather
than reusing ``match_automation()``: the reported bug sentence has no comma,
so ``automation_sentence_split.py``'s trigger/action split can never apply to
it).

Deliberately thin: no target/entity resolution of its own. The captured
``{command_text}``/``{command_prefix}`` are just reassembled into a plain
command string and handed to the caller-supplied ``AutomationActionParser``
instance (composition, not a second action grammar - Regel 6, same pattern
``automation_action_parser.py``'s own ``_parse_wait`` uses for
``AutomationConditionParser``). ``{amount}``/``{action_temporal_unit}`` reuse
``automation_action_parser.py``'s ``_seconds_from_amount_unit`` verbatim
(same Regel 6 reasoning, see ``delay_action.yaml``'s own comment for why this
vocabulary is already isolated from ``parsers.py``'s ``TemporalParser``).
"""

from __future__ import annotations

from pathlib import Path

from hassil import Intents, RangeSlotList, RangeType, TextSlotList, WildcardSlotList

from .hassil_compat import recognize_aligned
from .automation_action_parser import AutomationActionParser, _seconds_from_amount_unit
from .nlu.action_model import ActionGroup, ActionModel
from .nlu.lexicon import _ACTION_TEMPORAL_UNIT_SLOT_LIST
from .nlu.parser import ParseContext

RELATIVE_TIME_DIR = Path(__file__).parent / "intents" / "de" / "relative_time"


class RelativeTimeCommandParser:
    """Wraps the single ``intents/de/relative_time/relative_time_command.yaml``
    grammar (one ``Intents`` object, one intent, ``HassRelativeTimeCommand``).
    ``parse()`` is the sole public entry point - returns ``(actions,
    offset_seconds)`` on a match, ``None`` on anything unparseable (never a
    partial guess, Regel 4).
    """

    def __init__(self, intents: Intents, action_parser: AutomationActionParser) -> None:
        self._intents = intents
        self._action_parser = action_parser
        self._slot_lists = {
            "amount": RangeSlotList(name="amount", start=1, stop=59, step=1, type=RangeType.NUMBER),
            "amount_2": RangeSlotList(name="amount_2", start=1, stop=59, step=1, type=RangeType.NUMBER),
            "action_temporal_unit": _ACTION_TEMPORAL_UNIT_SLOT_LIST,
            "action_temporal_unit_2": TextSlotList.from_tuples(
                [
                    ("Sekunden", "seconds"), ("Sekunde", "seconds"),
                    ("Minuten", "minutes"), ("Minute", "minutes"),
                    ("Stunden", "hours"), ("Stunde", "hours"),
                ],
                name="action_temporal_unit_2",
            ),
            "command_prefix": WildcardSlotList(name="command_prefix"),
            "command_text": WildcardSlotList(name="command_text"),
        }

    def parse(
        self, text: str, context: ParseContext
    ) -> tuple[tuple["ActionModel | ActionGroup", ...], int] | None:
        decomposed = self.decompose(text)
        if decomposed is None:
            return None
        command_text, offset_seconds = decomposed

        actions = self._action_parser.parse(command_text, context)
        if not actions:
            return None
        return actions, offset_seconds

    def decompose(self, text: str) -> tuple[str, int] | None:
        """Extract timing and reconstruct the untimed command.

        Kept separate from action parsing so the engine can pass the same
        command through its broader direct-command understanding when the
        automation-specific grammar does not cover an otherwise valid
        action phrasing (for example the semantic "halb runter" form).
        """
        result = recognize_aligned(text, self._intents, slot_lists=self._slot_lists, language="de")
        if result is None or result.intent is None or result.intent.name != "HassRelativeTimeCommand":
            return None

        amount_slot = result.entities.get("amount")
        unit_slot = result.entities.get("action_temporal_unit")
        if amount_slot is None or unit_slot is None:
            return None  # grammar requires both - structurally unreachable, defense only
        offset_seconds = _seconds_from_amount_unit(int(amount_slot.value), str(unit_slot.value))
        amount_2_slot = result.entities.get("amount_2")
        unit_2_slot = result.entities.get("action_temporal_unit_2")
        if (amount_2_slot is None) != (unit_2_slot is None):
            return None
        if amount_2_slot is not None and unit_2_slot is not None:
            offset_seconds += _seconds_from_amount_unit(
                int(amount_2_slot.value), str(unit_2_slot.value)
            )
        command_text_slot = result.entities.get("command_text")
        if command_text_slot is None:
            return None  # grammar requires {command_text} in every sentence shape - defense only
        command_text = str(command_text_slot.value)

        command_prefix_slot = result.entities.get("command_prefix")
        if command_prefix_slot is not None:
            command_text = f"{command_prefix_slot.value} {command_text}"
        return command_text, offset_seconds
