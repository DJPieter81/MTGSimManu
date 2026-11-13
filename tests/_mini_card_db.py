"""A small MTGJSON-shaped card database for load-path tests.

`load_mini_db(tmp_path, entries)` writes `entries` (an MTGJSON ``data``
mapping: name -> list of face entries) to a file and loads it through the
real `CardDatabase.load`, without the auto-discovery of the full pool and
without the too-small-DB merge recovery. The cards are fixture carriers:
their names exist only in this file and in the tests that build them.
"""
from __future__ import annotations

import json


def _face(name, types, text, *, supertypes=(), subtypes=(), loyalty=None,
          mana="{2}", power=None, toughness=None, keywords=()):
    e = {"name": name, "text": text, "types": list(types),
         "type": " ".join(list(supertypes) + list(types)),
         "supertypes": list(supertypes), "subtypes": list(subtypes),
         "manaCost": mana, "manaValue": 2, "colors": [],
         "colorIdentity": [], "keywords": list(keywords),
         "legalities": {"modern": "Legal"}}
    if loyalty is not None:
        e["loyalty"] = str(loyalty)
    if power is not None:
        e["power"], e["toughness"] = str(power), str(toughness)
    return e


WALKER_TEXT = ("[+1]: Draw a card.\n[−2]: Destroy target creature.\n"
               "[−7]: You get an emblem with \"Creatures you control get "
               "+1/+1.\"")
BACK_WALKER_TEXT = ("[+1]: Each player mills two cards.\n[−3]: Exile target "
                    "nonland permanent.")


def mini_entries():
    """A front-face planeswalker, a transforming creature whose back face
    is a planeswalker, a creature with a keyword, and an instant."""
    return {
        "Fixture Sage": [_face(
            "Fixture Sage", ["Planeswalker"], WALKER_TEXT,
            supertypes=("Legendary",), subtypes=("Fixture",), loyalty=4)],
        "Fixture Adept // Fixture Ascended": [
            _face("Fixture Adept", ["Creature"],
                  "{T}: Draw a card, then discard a card. If there are five "
                  "or more cards in your graveyard, exile Fixture Adept, "
                  "then return him to the battlefield transformed.",
                  supertypes=("Legendary",), subtypes=("Human",),
                  power=0, toughness=2),
            _face("Fixture Ascended", ["Planeswalker"], BACK_WALKER_TEXT,
                  supertypes=("Legendary",), subtypes=("Fixture",),
                  loyalty=5, mana="")],
        "Fixture Drake": [_face(
            "Fixture Drake", ["Creature"],
            "Flying\nWhen this creature enters, draw a card.",
            subtypes=("Drake",), power=2, toughness=2,
            keywords=("Flying",))],
        "Fixture Bolt": [_face(
            "Fixture Bolt", ["Instant"],
            "Fixture Bolt deals 3 damage to any target.", mana="{R}")],
    }


def load_mini_db(tmp_path, entries=None, before_load=None):
    """A CardDatabase holding only `entries` (default `mini_entries()`),
    loaded through `CardDatabase.load`. `before_load(db)` runs on the
    empty database, before the load (to patch instance methods)."""
    from engine.card_database import CardDatabase
    path = tmp_path / "mini_atomic.json"
    path.write_text(json.dumps({"data": entries or mini_entries()}))
    db = CardDatabase.__new__(CardDatabase)
    db.cards, db._raw_data, db._effects_cache = {}, {}, {}
    db._automerge_attempted = True      # a fixture is small on purpose
    if before_load is not None:
        before_load(db)
    db.load(str(path))
    return db
