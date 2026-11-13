"""Stage 3: nested functions are their own symbols, apart from the top-level ones."""

from scrinium.infrastructure.code_structure import NESTED, parse_symbols

GAME = """\
export function start() {
  const scareAdult = (npc) => {
    if (npc.alert > 2) flee(npc)
  }
  function tick() {
    const step = () => scareAdult(first)
    step()
  }
  tick()
}
"""


def test_nested_functions_are_kept_with_their_outer_name() -> None:
    symbols = {s.qualified_name: s for s in parse_symbols("src/main.ts", GAME)}
    assert symbols["start"].kind == "function"
    assert {name: s.kind for name, s in symbols.items() if name != "start"} == {
        "start.scareAdult": NESTED,
        "start.tick": NESTED,
        "start.tick.step": NESTED,
    }
    assert symbols["start.scareAdult"].calls == ("flee",)
    assert (symbols["start.tick.step"].start_line, symbols["start.tick.step"].end_line) == (6, 6)
    # The top-level one still sees every call inside it, as before.
    assert set(symbols["start"].calls) == {"flee", "scareAdult", "step", "tick"}
