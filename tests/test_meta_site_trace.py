"""The showcase's card traces are the engine's own answers
(tools/meta_site/trace.py): each host of a card as the grammar typed it,
the carrier and family that take it -- or, for a host no carrier takes,
each family's strict-view and executability verdicts -- and the executor
each instruction's verb runs.

Card names are fixture carriers.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.meta_site import trace

CONTENT = Path(__file__).resolve().parents[1] / "tools" / "meta_site" / "content"


def _triggered(t):
    (h,) = [h for h in t["hosts"] if h["kind"] == "TRIGGERED"]
    return h


def test_a_trace_names_the_carriers_family_and_executor_of_a_host(card_db):
    t = trace.trace_card(card_db, "Primeval Titan")
    h = _triggered(t)
    carriers = {r["carrier"]: r["family"] for r in h["route"]["taken"]}
    assert carriers["attack"] == "attack_trigger"
    assert "enter" in carriers
    (search,) = h["specs"]
    assert search["verb"] == "SEARCH" and search["executor"]["supports"]
    assert [s["verb"] for s in search["then"]] == ["MOVE", "SHUFFLE"]
    assert all(s["executor"]["name"] and s["executor"]["supports"]
               for s in search["then"])


def test_a_host_no_carrier_takes_shows_each_familys_verdicts(card_db, monkeypatch):
    from engine import effect_carrier as ec
    monkeypatch.setattr(ec, "etb_plan", lambda face: None)
    monkeypatch.setattr(ec, "attack_plan", lambda face: None)
    t = trace.trace_card(card_db, "Primeval Titan")
    route = _triggered(t)["route"]
    assert route["taken"] == []
    assert route["carriers"] == ["enter", "attack"]
    families = [v["family"] for v in route["verdicts"]]
    assert families == list(ec.ETB_FAMILIES) + [ec.ATTACK_FAMILY]
    assert all({"strict", "executable"} <= set(v) for v in route["verdicts"])


def test_the_committed_trace_covers_the_cards_the_architecture_names(card_db):
    arch = json.loads((CONTENT / "architecture.json").read_text())
    committed = json.loads((CONTENT / "trace.json").read_text())
    named = [c["card"] for c in arch["trace"]]
    assert [c["card"] for c in committed["cards"]] == named
    assert all(card_db.get_card(n) is not None for n in named)
