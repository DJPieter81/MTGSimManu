"""The clause and trigger grammar (design doc 2026-09-29, section 3).

The single parse-once owner of clause structure: it reads oracle text at
LOAD, never at resolution, and produces the typed `engine.effect_spec`
model. Layers L0-L5 live in sibling modules; the closed sub-grammars live
in `engine.effect_grammar.sub`.
"""

__all__ = ["clear_caches"]


def clear_caches() -> None:
    """Clear the memo caches of every grammar module: the sub-grammars
    (`engine.effect_grammar.sub.clear_caches`) and the leaves that sit
    beside them (L0 normalize, the CR 701/702 keyword tables, the verb
    lexicon) and the L1 structure memo. The load driver calls this once,
    when the grammar pass finishes; the leaf-contract test pins that no
    module's cache is missed."""
    from engine.effect_grammar import (keywords, lexicon, normalize,
                                       structure, sub)
    sub.clear_caches()
    for leaf in (normalize, keywords, lexicon):
        leaf.clear_caches()
    structure.clear_caches()
