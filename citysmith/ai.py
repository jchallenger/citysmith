"""Claude as a translation layer over the generator.

Claude never emits coordinates, asset UUIDs, or slab bytes. It maps a natural
language brief onto the same parameters the CLI exposes, and Python does all
the geometry deterministically. That keeps the generator independently
testable and means a bad model response can produce a boring city, never a
broken one.

Requires ``pip install anthropic`` and credentials in the environment
(``ANTHROPIC_API_KEY``, or an ``ant auth login`` profile).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .city import SIZES, CityParams
from .palette import STYLES

#: Claude's most capable model; this is a cheap structured-extraction call, so
#: effort is kept low rather than reaching for a smaller model.
MODEL = "claude-opus-5"

DEFAULT_EFFORT = "low"


class AIError(RuntimeError):
    """Raised when the model layer is unavailable or returns nothing usable."""


def _client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on install
        raise AIError(
            "The anthropic package is not installed. Run `pip install anthropic`, "
            "or use the plain CLI flags instead -- every AI feature here is optional."
        ) from exc
    try:
        return anthropic.Anthropic()
    except Exception as exc:  # pragma: no cover - depends on environment
        raise AIError(
            f"Could not create an Anthropic client: {exc}. Set ANTHROPIC_API_KEY "
            "or run `ant auth login`."
        ) from exc


#: Strict tool schema -- the model must produce exactly these fields.
_CITY_TOOL: dict[str, Any] = {
    "name": "generate_city",
    "description": (
        "Configure the procedural city generator from a natural language brief. "
        "Choose parameters that best match the description. Do not invent "
        "coordinates, asset names, or building lists -- the generator produces "
        "those deterministically from these parameters."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name for the settlement. Invent one that suits the brief.",
            },
            "size": {
                "type": "string",
                "enum": list(SIZES),
                "description": (
                    "Settlement scale. hamlet~48 tiles, village~72, town~104, "
                    "city~144, metropolis~200."
                ),
            },
            "style": {
                "type": "string",
                "enum": sorted(STYLES),
                "description": "Visual style, which selects the TaleSpire asset pack.",
            },
            "walled": {
                "type": "boolean",
                "description": "Whether the settlement has a defensive wall and gates.",
            },
            "max_floors": {
                "type": "integer",
                "enum": [1, 2, 3, 4],
                "description": "Tallest ordinary building, in storeys.",
            },
            "seed": {
                "type": "integer",
                "description": "Any integer; identical seeds reproduce identical cities.",
            },
            "site_brief": {
                "type": "string",
                "description": (
                    "One sentence describing the kind of location the party should "
                    "end up playing in, e.g. 'a dockside warehouse used by smugglers'."
                ),
            },
        },
        "required": [
            "name", "size", "style", "walled", "max_floors", "seed", "site_brief",
        ],
        "additionalProperties": False,
    },
}

_SYSTEM = (
    "You configure a procedural city generator for tabletop roleplaying games. "
    "Translate the user's brief into generator parameters. Prefer smaller "
    "settlements unless the brief implies scale -- a 'village' should not become "
    "a metropolis. Always call the generate_city tool."
)


@dataclass
class Brief:
    """A parsed natural language brief."""

    params: CityParams
    seed: int
    site_brief: str

    def describe(self) -> str:
        return (
            f"{self.params.name} -- {self.params.size}, {self.params.style}, "
            f"{'walled' if self.params.walled else 'unwalled'}, "
            f"up to {self.params.max_floors} floors (seed {self.seed})\n"
            f"  looking for: {self.site_brief}"
        )


def interpret(prompt: str, *, effort: str = DEFAULT_EFFORT) -> Brief:
    """Turn a natural language brief into generator parameters.

    ``tool_choice`` forces the call, so this is structured extraction rather
    than an open-ended conversation -- there is no agentic loop to run.
    """
    client = _client()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=_SYSTEM,
            output_config={"effort": effort},
            tools=[_CITY_TOOL],
            tool_choice={"type": "tool", "name": "generate_city"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # pragma: no cover - network dependent
        raise AIError(f"Claude request failed: {exc}") from exc

    if response.stop_reason == "refusal":
        raise AIError("Claude declined this request.")

    for block in response.content:
        if block.type == "tool_use":
            data = dict(block.input)
            return Brief(
                params=CityParams(
                    size=data["size"],
                    style=data["style"],
                    walled=bool(data["walled"]),
                    max_floors=int(data["max_floors"]),
                    name=data["name"],
                ),
                seed=int(data["seed"]),
                site_brief=data["site_brief"],
            )

    raise AIError("Claude returned no tool call; nothing to configure from.")


_FLAVOUR_SYSTEM = (
    "You are helping a game master prepare a session. Given a generated "
    "location, write short, usable prose: concrete, specific, and free of "
    "purple language. No headings, no bullet lists longer than five items."
)


def describe_site(site, city, *, effort: str = DEFAULT_EFFORT) -> str:
    """Write GM-facing prose for a selected site.

    Purely additive: the slab and the floorplan are already complete without it.
    """
    client = _client()
    rooms = ", ".join(sorted({r.purpose for r in getattr(site, "rooms", [])})) or "unknown"
    prompt = (
        f"City: {city.name} ({city.width}x{city.depth} tiles).\n"
        f"Location: {site.name}, a {site.building.kind} in {site.building.district}.\n"
        f"Footprint: {site.building.rect.w}x{site.building.rect.d} tiles, "
        f"{site.building.floors} floor(s).\n"
        f"Owner: {site.building.owner or 'unknown'}.\n"
        f"Existing hook: {site.building.hook or 'none'}.\n"
        f"Rooms: {rooms}.\n\n"
        "Write: one paragraph describing the place as the party first sees it; "
        "then three bullet points -- who is here, what is worth taking, and what "
        "goes wrong if the party lingers."
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=_FLAVOUR_SYSTEM,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # pragma: no cover - network dependent
        raise AIError(f"Claude request failed: {exc}") from exc

    if response.stop_reason == "refusal":
        raise AIError("Claude declined this request.")

    return "\n".join(b.text for b in response.content if b.type == "text").strip()
