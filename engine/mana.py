"""
MTG Mana System
Handles mana costs, mana pools, color identity, and mana payment.
Supports all 5 colors (W, U, B, R, G) plus colorless (C) and generic.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import re


class Color(Enum):
    WHITE = "W"
    BLUE = "U"
    BLACK = "B"
    RED = "R"
    GREEN = "G"
    COLORLESS = "C"


COLORS = [Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN]
COLOR_CHARS = {c.value for c in COLORS}


@dataclass
class ManaCost:
    """Represents a mana cost like {2}{W}{W} or {3}{R}."""
    generic: int = 0
    white: int = 0
    blue: int = 0
    black: int = 0
    red: int = 0
    green: int = 0
    colorless: int = 0  # true colorless (C), not generic
    # Phyrexian pips in this cost (CR 107.4f), colour char -> count.
    # A {B/P} pip is ALSO counted in `black` above, so `cmc`, `colors`
    # and every existing consumer are unchanged; this field records the
    # extra permission the pip grants: the controller may pay 2 life
    # INSTEAD of one mana of that specific colour.  The colour is
    # load-bearing — waiving an arbitrary pip of a {U}{G/P} cost would
    # wrongly excuse the blue requirement — so the counts are kept per
    # colour rather than as a bare total.
    # Populated once by `parse_mana_cost_mtgjson` at DB load; because it
    # lives on ManaCost, every alternative cost parsed through that
    # function (dash, escape, warp, spectacle, flashback, suspend)
    # inherits Phyrexian support with no extra plumbing.
    phyrexian: Dict[str, int] = field(default_factory=dict)
    # Hybrid pips (CR 107.4e), one entry per pip: the tuple of ways the
    # pip may be paid.  A colour/colour pip {R/G} is ("R", "G"); a
    # two-brid pip {2/W} is ("W", "2") — a digit option means "that much
    # GENERIC mana instead of the colour".  A hybrid pip is NOT generic
    # mana: it needs a source of one of its colours (or the digit
    # alternative), and a generic cost reduction (CR 601.2f) never
    # touches it.  Modelling these as generic — the state before
    # 2026-09-07 — let a {R/W}{R/W}{R/W} creature be cast off Islands
    # and let two "cost {1} less" permanents make {1}{R/G} free.
    # Like `phyrexian`, this lives on the cost so every alternative cost
    # parsed by `parse_mana_cost_mtgjson` carries it; `colors` and
    # `to_dict` are deliberately unchanged so existing consumers keep
    # their behaviour — the payment/feasibility solvers read `hybrid`
    # explicitly.
    hybrid: List[Tuple[str, ...]] = field(default_factory=list)

    @staticmethod
    def hybrid_pip_value(pip: Tuple[str, ...]) -> int:
        """Mana value of one hybrid pip: 1, or the digit of a two-brid
        pip (CR 202.3e — {2/W} contributes 2)."""
        for option in pip:
            if option.isdigit():
                return int(option)
        return 1

    @property
    def cmc(self) -> int:
        return (self.generic + self.white + self.blue + self.black +
                self.red + self.green + self.colorless
                + sum(self.hybrid_pip_value(p) for p in self.hybrid))

    @property
    def min_mana(self) -> int:
        """The least mana that can pay this cost: mana value, minus the
        surplus of every two-brid pip paid with its colour ({2/W} is
        mana value 2 but one Plains pays it).  The quantity gate in
        `can_cast` starts from this, never from `cmc`."""
        return self.cmc - sum(self.hybrid_pip_value(p) - 1 for p in self.hybrid)

    @property
    def non_generic_pips(self) -> int:
        """Pips that no generic cost reduction can touch (CR 601.2f):
        every coloured, colourless and hybrid pip.  The floor under
        every reduction arithmetic."""
        return (self.white + self.blue + self.black + self.red
                + self.green + self.colorless + len(self.hybrid))

    @property
    def colors(self) -> List[Color]:
        result = []
        if self.white > 0: result.append(Color.WHITE)
        if self.blue > 0: result.append(Color.BLUE)
        if self.black > 0: result.append(Color.BLACK)
        if self.red > 0: result.append(Color.RED)
        if self.green > 0: result.append(Color.GREEN)
        return result

    def to_dict(self) -> Dict[str, int]:
        return {
            "W": self.white, "U": self.blue, "B": self.black,
            "R": self.red, "G": self.green, "C": self.colorless,
            "generic": self.generic
        }

    @staticmethod
    def parse(cost_str: str) -> "ManaCost":
        """Parse a mana cost string like '2WW', '3R', 'WUBRG', '0'."""
        if not cost_str or cost_str == "0":
            return ManaCost()

        cost = ManaCost()
        i = 0
        while i < len(cost_str):
            ch = cost_str[i]
            if ch.isdigit():
                num = ""
                while i < len(cost_str) and cost_str[i].isdigit():
                    num += cost_str[i]
                    i += 1
                cost.generic += int(num)
                continue
            elif ch == "W":
                cost.white += 1
            elif ch == "U":
                cost.blue += 1
            elif ch == "B":
                cost.black += 1
            elif ch == "R":
                cost.red += 1
            elif ch == "G":
                cost.green += 1
            elif ch == "C":
                cost.colorless += 1
            i += 1
        return cost

    def __str__(self) -> str:
        parts = []
        if self.generic > 0:
            parts.append(str(self.generic))
        parts.extend(["W"] * self.white)
        parts.extend(["U"] * self.blue)
        parts.extend(["B"] * self.black)
        parts.extend(["R"] * self.red)
        parts.extend(["G"] * self.green)
        parts.extend(["C"] * self.colorless)
        parts.extend("{" + "/".join(p) + "}" for p in self.hybrid)
        return "".join(parts) if parts else "0"


@dataclass
class ManaPool:
    """Represents a player's current mana pool."""
    white: int = 0
    blue: int = 0
    black: int = 0
    red: int = 0
    green: int = 0
    colorless: int = 0

    def add(self, color: str, amount: int = 1):
        if color == "W":
            self.white += amount
        elif color == "U":
            self.blue += amount
        elif color == "B":
            self.black += amount
        elif color == "R":
            self.red += amount
        elif color == "G":
            self.green += amount
        elif color == "C":
            self.colorless += amount

    def total(self) -> int:
        return self.white + self.blue + self.black + self.red + self.green + self.colorless

    def get(self, color: str) -> int:
        mapping = {"W": self.white, "U": self.blue, "B": self.black,
                   "R": self.red, "G": self.green, "C": self.colorless}
        return mapping.get(color, 0)

    def remove(self, color: str, amount: int = 1):
        current = self.get(color)
        if current < amount:
            raise ValueError(f"Not enough {color} mana: have {current}, need {amount}")
        self.add(color, -amount)

    def _payment_plan(self, cost: ManaCost):
        """How this pool would pay `cost`, or None if it cannot.

        Returns (hybrid_assignments, extra_generic): one colour char per
        hybrid pip paid with a colour (None when its digit alternative is
        used), and the generic mana those digit alternatives add.  Fixed
        colour pips are settled first; hybrid pips then take a colour
        that is still available, scarcest pip first (MRV), preferring
        the colour with the most spare mana so later pips keep their
        options.
        """
        avail = {"W": self.white - cost.white, "U": self.blue - cost.blue,
                 "B": self.black - cost.black, "R": self.red - cost.red,
                 "G": self.green - cost.green,
                 "C": self.colorless - cost.colorless}
        if any(v < 0 for v in avail.values()):
            return None
        assignments = [None] * len(cost.hybrid)
        extra_generic = 0
        pending = list(range(len(cost.hybrid)))

        def _choices(i):
            return [o for o in cost.hybrid[i]
                    if not o.isdigit() and avail.get(o, 0) > 0]

        while pending:
            pending.sort(key=lambda i: len(_choices(i)))
            i = pending.pop(0)
            choices = _choices(i)
            if choices:
                colour = max(choices, key=lambda o: avail[o])
                avail[colour] -= 1
                assignments[i] = colour
                continue
            digits = [o for o in cost.hybrid[i] if o.isdigit()]
            if not digits:
                return None
            extra_generic += int(digits[0])
        if sum(avail.values()) < cost.generic + extra_generic:
            return None
        return assignments, extra_generic

    def can_pay(self, cost: ManaCost) -> bool:
        """Check if this pool can pay the given mana cost."""
        return self._payment_plan(cost) is not None

    def pay(self, cost: ManaCost) -> bool:
        """Pay a mana cost from this pool. Returns True if successful."""
        plan = self._payment_plan(cost)
        if plan is None:
            return False
        hybrid_assignments, extra_generic = plan

        # Pay colored costs first
        self.white -= cost.white
        self.blue -= cost.blue
        self.black -= cost.black
        self.red -= cost.red
        self.green -= cost.green
        self.colorless -= cost.colorless
        # Then the hybrid pips paid with a colour (CR 107.4e)
        for colour in hybrid_assignments:
            if colour is not None:
                self.add(colour, -1)

        # Pay generic with colorless first, then cheapest colored
        generic_remaining = cost.generic + extra_generic
        # Pay with colorless first
        pay_from_colorless = min(self.colorless, generic_remaining)
        # Actually colorless already subtracted above, use remaining
        # Pay generic from remaining colored mana (least valuable first)
        if generic_remaining > 0:
            for attr in ["colorless", "green", "red", "black", "blue", "white"]:
                available = getattr(self, attr)
                pay = min(available, generic_remaining)
                setattr(self, attr, available - pay)
                generic_remaining -= pay
                if generic_remaining <= 0:
                    break

        return True

    def empty(self):
        """Empty the mana pool (happens at end of each step/phase)."""
        self.white = 0
        self.blue = 0
        self.black = 0
        self.red = 0
        self.green = 0
        self.colorless = 0

    def copy(self) -> "ManaPool":
        return ManaPool(self.white, self.blue, self.black,
                        self.red, self.green, self.colorless)

    def __str__(self) -> str:
        parts = []
        if self.white: parts.append(f"{self.white}W")
        if self.blue: parts.append(f"{self.blue}U")
        if self.black: parts.append(f"{self.black}B")
        if self.red: parts.append(f"{self.red}R")
        if self.green: parts.append(f"{self.green}G")
        if self.colorless: parts.append(f"{self.colorless}C")
        return ", ".join(parts) if parts else "empty"
