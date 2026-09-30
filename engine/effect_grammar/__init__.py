"""The clause and trigger grammar (design doc 2026-09-29, section 3).

The single parse-once owner of clause structure: it reads oracle text at
LOAD, never at resolution, and produces the typed `engine.effect_spec`
model. Layers L0-L5 live in sibling modules; the closed sub-grammars live
in `engine.effect_grammar.sub`.
"""
