#!/usr/bin/env python3
"""Check spark2.py's engine -- World, Pixel, sensors, actions, and the
built-in demo's own rules. Everything here is a plain function/method
of the objects involved, no terminal needed -- see tests/test_pty.py
for the real interactive loop.

    python3 tests/test_spark2.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import spark2 as s2                                              # noqa: E402

passed = failed = 0


def check(name, condition, extra=""):
    global passed, failed
    if condition:
        passed += 1
        print("  ok   " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  -> " + str(extra) if extra else ""))


def game(hero_rules=None, spark_rules=None, width=10, height=10):
    return {
        "width": width, "height": height,
        "kinds": [
            {"kind": "hero", "glyph": "@", "color": "white", "role": "player",
             "count": 0, "rules": hero_rules or []},
            {"kind": "spark", "glyph": "█", "color": "yellow", "role": "prop",
             "count": 0, "rules": spark_rules or []},
        ],
    }


print("resolve_step: forward is my own facing; toward/away from it read it.x/it.y")
hero = s2.Pixel("hero", 5, 5, {})
hero.facing = (1, 0)
check("forward is my own facing, whatever it is", s2.resolve_step("forward", hero, None, None) == (1, 0))
check("a plain compass name ignores facing entirely",
      s2.resolve_step("up", hero, None, None) == (0, -1))
it = s2.Pixel("spark", 8, 5, {})
check("toward it steps along the bigger axis", s2.resolve_step("toward it", hero, it, None) == (1, 0))
check("away from it is the exact opposite",
      s2.resolve_step("away from it", hero, it, None) == (-1, 0))
check("toward it with no it at all is a no-op step",
      s2.resolve_step("toward it", hero, None, None) == (0, 0))

print("\nWorld/Pixel basics: spawning, bounds, removal")
w = s2.World(game(), seed=1)
p = w.spawn("spark", 3, 3)
check("spawning a registered kind in bounds works", p is not None and (p.x, p.y) == (3, 3))
check("spawning an unregistered kind does nothing", w.spawn("nosuchkind", 0, 0) is None)
check("spawning out of bounds does nothing", w.spawn("spark", 99, 99) is None)
w.remove(p)
check("remove marks it not-alive, doesn't delete it outright", p in w.pixels and not p.alive)
check("at() only ever returns living pixels", w.at(3, 3) == [])

print("\nnearest: reach 1 doubles as \"touching\", including standing exactly on top of it")
w = s2.World(game())
hero = w.spawn("hero", 5, 5)
spark = w.spawn("spark", 5, 5)   # same cell -- nothing here blocks that
check("distance 0 (same cell) is within reach 1", w.nearest(hero, "spark", 1) is spark)
spark.x, spark.y = 7, 5
check("out of reach returns nothing", w.nearest(hero, "spark", 1) is None)
check("but is found at a wider reach", w.nearest(hero, "spark", 5) is spark)
check("wrong kind is never matched", w.nearest(hero, "wall", 5) is None)
check("\"anything\" matches regardless of kind", w.nearest(hero, "anything", 5) is spark)
w_alone = s2.World(game())
lonely = w_alone.spawn("spark", 4, 4)
check("a pixel never counts as its own nearest neighbour",
      w_alone.nearest(lonely, "anything", 5) is None)

print("\nmove/face: turns to face the way it moved, walking onto another pixel is fine")
w = s2.World(game([{"when": [], "do": [{"action": "move", "args": {"dir": "right"}}]}]))
hero = w.spawn("hero", 5, 5)
w.spawn("spark", 6, 5)   # directly in the hero's path
w.step()
check("moved right", (hero.x, hero.y) == (6, 5), (hero.x, hero.y))
check("facing updated to match", hero.facing == (1, 0))
check("walking onto an existing pixel is not blocked -- \"go to them later\" needs this",
      any(p.x == 6 and p.y == 5 and p.kind == "spark" for p in w.pixels))

w2 = s2.World(game())
hero2 = s2.Pixel("hero", 0, 0, {})
before = (hero2.x, hero2.y)
s2.ACTIONS["move"](hero2, w2, {"dir": "up"}, None)   # up from (0,0) is off the grid
check("moving off the edge of the world does nothing", (hero2.x, hero2.y) == before)

print("\nspawn: \"where\" controls the exact spot -- forward is \"adjacent and in front\"")
w = s2.World(game())
hero = w.spawn("hero", 5, 5)
hero.facing = (0, -1)   # facing up
s2.ACTIONS["spawn"](hero, w, {"kind": "spark", "where": "forward"}, None)
new = next(p for p in w.pixels if p.kind == "spark")
check("spawned exactly one square in front, in my own facing direction",
      (new.x, new.y) == (5, 4), (new.x, new.y))
s2.ACTIONS["spawn"](hero, w, {"kind": "spark", "where": "here"}, None)
check("\"here\" spawns on my own exact square",
      any(p.x == 5 and p.y == 5 and p.kind == "spark" for p in w.pixels))

print("\nheld colour: cycling, and spawning/recolouring with color: \"held\"")
hero = s2.Pixel("hero", 0, 0, {})
check("starts on the first colour in the cycle", hero.held_color == s2.COLOR_CYCLE[0])
s2.ACTIONS["cycle_color"](hero, s2.World(game()), {"target": "self"}, None)
check("cycling advances to the next one", hero.held_color == s2.COLOR_CYCLE[1])
for _ in range(len(s2.COLOR_CYCLE) - 1):
    s2.ACTIONS["cycle_color"](hero, s2.World(game()), {"target": "self"}, None)
check("cycling all the way around wraps back to the first", hero.held_color == s2.COLOR_CYCLE[0])

w = s2.World(game())
hero = w.spawn("hero", 5, 5)
hero.held_color = "magenta"
s2.ACTIONS["spawn"](hero, w, {"kind": "spark", "where": "here", "color": "held"}, None)
placed = next(p for p in w.pixels if p.kind == "spark")
check("a spawned pixel picks up my currently-held colour when asked to",
      placed.color == "magenta", placed.color)
check("its own held_color is untouched by mine -- these are two separate things",
      placed.held_color == s2.COLOR_CYCLE[0], placed.held_color)

print("\nrecolor (\"edit them later\"): aimed at it, using color: \"held\"")
w = s2.World(game())
hero = w.spawn("hero", 5, 5)
hero.held_color = "green"
spark = w.spawn("spark", 5, 5)
spark.color = "yellow"
s2.ACTIONS["recolor"](hero, w, {"target": "it", "color": "held"}, spark)
check("the OTHER pixel (it) changed colour, not me", spark.color == "green" and hero.color == "white",
      (spark.color, hero.color))

print("\nthe demo's own rules, run end to end through a real World.step()")
# DEMO's own player has count: 0 -- run() spawns it itself, explicitly,
# at the grid's centre, once the actual square side is known (see
# _grid_side's own docstring for why); mirrored here rather than
# relying on count-based auto-spawn, which would place it randomly.
w = s2.World(s2.DEMO, seed=1)
hero = w.spawn("player", 10, 10)
hero.x, hero.y = 10, 10
hero.facing = (1, 0)
w.keys = {"space"}
w.step()
check("pressing space in the demo spawns a spark right in front of the player",
      any(p.kind == "spark" and (p.x, p.y) == (11, 10) for p in w.pixels), w.pixels)
w.keys = {"c"}
before_held = hero.held_color
w.step()
check("pressing c in the demo cycles the held colour",
      hero.held_color != before_held, (before_held, hero.held_color))
placed = next(p for p in w.pixels if p.kind == "spark")
placed.x, placed.y = hero.x, hero.y   # stand exactly on it
w.keys = {"e"}
w.step()
check("pressing e while touching a placed spark recolours it to my held colour",
      placed.color == hero.held_color, (placed.color, hero.held_color))

print("\nparse_command / chat: plain text vs. a command, blank is its own thing")
check("blank input", s2.parse_command("   ") == ("empty", None))
check("plain text is said, not a command", s2.parse_command("hello") == ("say", "hello"))
check("/quit and /q both quit",
      s2.parse_command("/quit") == ("quit", None) and s2.parse_command("/q") == ("quit", None))
check("an unrecognised command names itself", s2.parse_command("/nonsense") == ("unknown", "nonsense"))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
