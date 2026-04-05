"""Archetype definitions — shared across AI modules.

Moved here from the legacy AIPlayer class. Only the enum and
deck-to-archetype mapping remain; the AIPlayer class has been
replaced by EVPlayer (ai/ev_player.py).
"""
from enum import Enum


class ArchetypeStrategy(Enum):
    AGGRO = "aggro"
    MIDRANGE = "midrange"
    CONTROL = "control"
    COMBO = "combo"
    TEMPO = "tempo"
    RAMP = "ramp"


DECK_ARCHETYPES = {
    "Boros Energy":       ArchetypeStrategy.AGGRO,
    "Jeskai Blink":       ArchetypeStrategy.TEMPO,
    "Ruby Storm":         ArchetypeStrategy.COMBO,
    "Affinity":           ArchetypeStrategy.AGGRO,
    "Eldrazi Tron":       ArchetypeStrategy.RAMP,
    "Amulet Titan":       ArchetypeStrategy.COMBO,
    "Goryo's Vengeance":  ArchetypeStrategy.COMBO,
    "Neobrand":           ArchetypeStrategy.COMBO,
    "Domain Zoo":         ArchetypeStrategy.AGGRO,
    "Living End":         ArchetypeStrategy.COMBO,
    "Belcher":            ArchetypeStrategy.COMBO,
    "Dimir Midrange":     ArchetypeStrategy.MIDRANGE,
    "Izzet Prowess":      ArchetypeStrategy.AGGRO,
    "4c Omnath":          ArchetypeStrategy.MIDRANGE,
}
