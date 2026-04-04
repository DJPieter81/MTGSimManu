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

    @property
    def cmc(self) -> int:
        return (self.generic + self.white + self.blue + self.black +
                self.red + self.green + self.colorless)

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

    def can_pay(self, cost: ManaCost) -> bool:
        """Check if this pool can pay the given mana cost."""
        # First check colored requirements
        if self.white < cost.white:
            return False
        if self.blue < cost.blue:
            return False
        if self.black < cost.black:
            return False
        if self.red < cost.red:
            return False
        if self.green < cost.green:
            return False
        if self.colorless < cost.colorless:
            return False

        # Check if remaining mana can cover generic
        remaining = (
            (self.white - cost.white) +
            (self.blue - cost.blue) +
            (self.black - cost.black) +
            (self.red - cost.red) +
            (self.green - cost.green) +
            (self.colorless - cost.colorless)
        )
        return remaining >= cost.generic

    def pay(self, cost: ManaCost) -> bool:
        """Pay a mana cost from this pool. Returns True if successful."""
        if not self.can_pay(cost):
            return False

        # Pay colored costs first
        self.white -= cost.white
        self.blue -= cost.blue
        self.black -= cost.black
        self.red -= cost.red
        self.green -= cost.green
        self.colorless -= cost.colorless

        # Pay generic with colorless first, then cheapest colored
        generic_remaining = cost.generic
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
