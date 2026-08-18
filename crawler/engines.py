"""Engine displacement, read off the model name.

FIPE has no field for it: the displacement is written into the model name
("UNO EVOLUTION 1.4 Fire Flex 8V 5p") and nowhere else, so the only way to
classify a model is to read its own name back.

Pure functions, no database access — the table of engine types lives in
``crawler.models`` and is filled from what these functions return.

Three values are not displacements and are negative on purpose, so that one
numeric column can hold the whole classification:

* ``ELECTRIC`` (-1) — nothing to measure in liters;
* ``HYBRID`` (-2) — only for the hybrid whose name carries no displacement;
  a hybrid that does say "1.5" is a 1.5, and is classified as one;
* ``UNKNOWN`` (0) — combustion, but the name never said how big. It is what
  the filter hides, since "unknown" is not something anyone searches for.
"""

import re
from decimal import Decimal

from crawler.models import FuelType

ELECTRIC = Decimal("-1.0")
HYBRID = Decimal("-2.0")
UNKNOWN = Decimal("0.0")

DESCRIPTIONS = {
    ELECTRIC: "Elétrico",
    HYBRID: "Híbrido",
    UNKNOWN: "Não informado",
}

# Liters, the modern notation: "1.4", and the odd "1,4". The two lookarounds are
# what keep it from biting a chunk out of a longer number — the truck "9.170"
# has no reading left once neither side may touch a digit. They are digit-only
# on purpose: "Ed.2.0" is a 2.0, and a dot to the left means nothing.
_LITERS = re.compile(r"(?<!\d)([0-9])[.,]([0-9])(?!\d)")

# Cubic centimeters, the old notation FIPE still uses on 80s/90s cars: the
# "Gol 1000 Mi" and the "Logus GLS 2000" are a 1.0 and a 2.0. Only four digits
# ending in "00", and never right after a letter or a hyphen — "F-4000" and
# "D-20" are model names, not engines.
_CC = re.compile(r"(?<![\w-])([1-9])([0-9])00(?![\d])")

# Read off the name when the model has no stored years yet — an interrupted
# sweep leaves those behind, and they should still classify.
_ELECTRIC_IN_NAME = re.compile(r"el[ée]tric", re.IGNORECASE)
_HYBRID_IN_NAME = re.compile(r"h[íi]br[íi]d|hybrid", re.IGNORECASE)


def parse_displacement(name):
    """The engine size in liters, or ``None`` when the name does not say it.

    A name that lists several ("Premio CS 1.6/ 1.5/ 1.3 2p") keeps the first,
    which is the one FIPE writes as the headline.
    """
    match = _LITERS.search(name)
    if match:
        return Decimal(f"{match.group(1)}.{match.group(2)}")
    match = _CC.search(name)
    if match:
        return Decimal(f"{match.group(1)}.{match.group(2)}")
    return None


def classify(name, fuel_types=()):
    """The engine type of one model, as the single number stored for it.

    ``fuel_types`` are the fuel codes of the model's stored years, which is the
    reliable half: FIPE marks most electrics with "(Elétrico)" in the name, but
    not all of them — "Dolphin Mini GL" is as electric as "Dolphin Mini GS
    (Elétrico)". The name is only the fallback for a model with no years yet.
    """
    fuels = set(fuel_types)
    if fuels == {FuelType.ELECTRIC} or (not fuels and _ELECTRIC_IN_NAME.search(name)):
        return ELECTRIC

    displacement = parse_displacement(name)
    if displacement is not None:
        return displacement

    if FuelType.HYBRID in fuels or _HYBRID_IN_NAME.search(name):
        return HYBRID
    return UNKNOWN


def describe(value):
    """The label of an engine type: the displacement itself, or the name of the
    negative code — the whole reason those are negative and not another column.
    """
    value = Decimal(value)
    if value in DESCRIPTIONS:
        return DESCRIPTIONS[value]
    return f"{value:.1f}"
