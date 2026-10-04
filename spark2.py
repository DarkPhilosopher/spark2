#!/usr/bin/env python3
"""spark2 -- a from-scratch 2D ASCII "pixel" engine and runtime, standalone
(not Spark, doesn't import anything from it, though the shape is the same
idea: WHEN a sensor finds something true DO an action, target-aware).

Requested directly, across several messages narrowing down the scope:

  "what is fundamentally required to make a termux 2d ASC game...
  i need to spawn a pixel and choose when and also choose its color
  and when it is removed and when it may change color and when needs
  to be implicit by sensor triggers also let a target and its action"

  "a 2d asc area the square size of individual device and below it
  the chat log and make so i can spawn a new entity into my
  character[']s carrying which pixel is adjacent and in front of my
  character"

  "and while im holding it i can cycle color"

  "and i can go to them later and edit them if my character is on them"

    python3 spark2.py

Layout: a SQUARE ascii grid (same row and column count, as large as
fits above the chat area on THIS exact device -- recomputed from the
live terminal size every redraw, never a guessed constant) on top,
the chat log and a "/" command line below.

The built-in demo: arrows/WASD move the player and turn it to face
the way it moved. Space spawns a "spark" pixel one square directly in
front of it -- "adjacent and in front of my character," literally,
the exact same "step in my own facing direction" arithmetic move
already uses -- coloured with whatever you're currently holding.
`c` cycles which colour that is. Standing next to (or on top of,
walking onto a spark never blocks you) a spark you already placed and
pressing `e` recolours THAT one to your current colour instead --
"go to them later and edit them if my character is on them."

The engine itself (World, Pixel, sensors, actions, resolve_step) is
one small, pure, terminal-free layer -- see tests/test_spark2.py.
run() (raw keys, drawing) is deliberately thin on top of it, the same
split every other terminal program built this session uses. Chat
(typed text) and real-time movement never fight over the same
keystrokes: pressing "/" pauses raw gameplay reading for one cooked
input() line, then resumes -- the exact same trick Spark's own
engine/runner.py chat_break() uses, for the exact same reason (w/a/s/d
would otherwise be indistinguishable from someone typing them).
"""

import os
import random
import select
import shutil
import sys
import termios
import traceback
import tty

# ---------------------------------------------------------------------------
# the engine -- grid, pixels, WHEN/DO rules, sensors, actions
# ---------------------------------------------------------------------------

STEPS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
STEP_LIST = list(STEPS.values())
COLOR_CYCLE = ["red", "yellow", "green", "cyan", "blue", "magenta", "white"]


def resolve_step(name, obj, it, rng):
    """A direction name -> (dx, dy). "forward" is the actor's own last
    facing -- this one piece of arithmetic is what both "spawn in
    front of me" and "keep facing the way I last moved" actually are,
    underneath."""
    if name in STEPS:
        return STEPS[name]
    if name == "random":
        return rng.choice(STEP_LIST)
    if name == "forward":
        return obj.facing
    if it is None:
        return (0, 0)
    dx, dy = it.x - obj.x, it.y - obj.y
    if name == "away from it":
        dx, dy = -dx, -dy
    if abs(dx) >= abs(dy):
        return ((1 if dx > 0 else -1) if dx else 0, 0)
    return (0, (1 if dy > 0 else -1) if dy else 0)


def _resolve_color(obj, raw):
    """"held" means "whatever colour obj is currently holding" -- any
    other value is a literal colour name, used as-is."""
    return obj.held_color if raw == "held" else raw


class Pixel:
    def __init__(self, kind, x, y, template):
        self.kind = kind
        self.x, self.y = x, y
        self.color = template.get("color", "white")
        self.glyph = template.get("glyph", "█")   # a solid block -- a
                                                          # "pixel", not a letter
        self.role = template.get("role", "prop")
        self.rules = template.get("rules", [])
        self.facing = (0, 1)
        self.alive = True
        # What colour this pixel is currently "holding" -- separate from
        # its own visible .color -- so cycle_color/spawn/recolor with
        # color: "held" all read the one thing a player is actively
        # choosing, requested directly: "while im holding it i can
        # cycle color."
        self.held_color = template.get("held_color", COLOR_CYCLE[0])
        self.controller = None   # a single-player build for now; kept
                                  # so World.keys_for's own shape matches
                                  # Spark's exactly, ready for more later


class World:
    def __init__(self, config, seed=None):
        self.width = config["width"]
        self.height = config["height"]
        self.templates = {t["kind"]: t for t in config.get("kinds", [])}
        self.pixels = []
        self.tick = 0
        self.keys = set()
        self.message = ""
        self.rng = random.Random(seed)
        for t in config.get("kinds", []):
            for _ in range(t.get("count", 0)):
                self.spawn_somewhere(t["kind"])

    def in_bounds(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def at(self, x, y):
        return [p for p in self.pixels if p.alive and p.x == x and p.y == y]

    def empty_cell(self):
        for _ in range(200):
            x, y = self.rng.randrange(self.width), self.rng.randrange(self.height)
            if not self.at(x, y):
                return (x, y)
        return None

    def spawn(self, kind, x, y):
        template = self.templates.get(kind)
        if template is None or not self.in_bounds(x, y):
            return None
        p = Pixel(kind, x, y, template)
        self.pixels.append(p)
        return p

    def spawn_somewhere(self, kind):
        spot = self.empty_cell()
        return self.spawn(kind, *spot) if spot else None

    def remove(self, p):
        p.alive = False

    def nearest(self, obj, kind, reach):
        """The closest OTHER pixel of `kind` (or any, for "anything")
        within `reach` squares -- reach 1 doubles as "touching",
        distance 0 (standing exactly on top of it) included, since
        nothing here blocks movement onto an existing pixel."""
        best, best_d = None, None
        for p in self.pixels:
            if not p.alive or p is obj:
                continue
            if kind != "anything" and p.kind != kind:
                continue
            d = max(abs(p.x - obj.x), abs(p.y - obj.y))
            if d <= reach and (best_d is None or d < best_d):
                best, best_d = p, d
        return best

    def keys_for(self, obj):
        if obj.controller is None:
            return self.keys
        return set()   # no other controllers exist yet in this single-player build

    def check_when(self, obj, uses):
        """Every sensor in the list must pass. Returns the first Pixel
        one of them found (the row's own "it"/target), True if none
        found anybody but every sensor still passed (so a row with no
        WHEN tiles still runs its actions every tick), or False if any
        sensor failed."""
        it = None
        for use in uses:
            fn = SENSORS.get(use["sensor"])
            if fn is None:
                return False
            result = fn(obj, self, use.get("args", {}))
            if not result:
                return False
            if it is None and isinstance(result, Pixel):
                it = result
        return it if it is not None else True

    def do_all(self, obj, uses, it):
        for use in uses:
            fn = ACTIONS.get(use["action"])
            if fn is not None:
                fn(obj, self, use.get("args", {}), it)

    def step(self):
        self.tick += 1
        for p in list(self.pixels):
            if not p.alive:
                continue
            for row in p.rules:
                found = self.check_when(p, row.get("when", []))
                if not found:
                    continue
                self.do_all(p, row.get("do", []), found if isinstance(found, Pixel) else None)


# ---------------------------------------------------------------------------
# sensors (WHEN) and actions (DO) -- add a new one by writing a plain
# function and registering it; nothing else has to change to use it
# ---------------------------------------------------------------------------

SENSORS = {}
ACTIONS = {}


def sensor(name):
    def deco(fn):
        SENSORS[name] = fn
        return fn
    return deco


def action(name):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


@sensor("always")
def s_always(obj, world, a):
    return True


@sensor("timer")
def s_timer(obj, world, a):
    every = max(1, a.get("every", 4))
    return world.tick % every == 0


@sensor("key")
def s_key(obj, world, a):
    return a.get("key", "up") in world.keys_for(obj)


@sensor("touch")
def s_touch(obj, world, a):
    return world.nearest(obj, a.get("kind", "anything"), 1)


@sensor("see")
def s_see(obj, world, a):
    return world.nearest(obj, a.get("kind", "anything"), a.get("range", 6))


@sensor("chance")
def s_chance(obj, world, a):
    return world.rng.randrange(100) < a.get("percent", 25)


@action("move")
def a_move(obj, world, a, it):
    dx, dy = resolve_step(a.get("dir", "forward"), obj, it, world.rng)
    if dx or dy:
        obj.facing = (dx, dy)
        nx, ny = obj.x + dx, obj.y + dy
        if world.in_bounds(nx, ny):
            obj.x, obj.y = nx, ny


@action("face")
def a_face(obj, world, a, it):
    dx, dy = resolve_step(a.get("dir", "forward"), obj, it, world.rng)
    if dx or dy:
        obj.facing = (dx, dy)


@action("spawn")
def a_spawn(obj, world, a, it):
    """where: "forward" (one square ahead of my own facing -- "adjacent
    and in front of my character", requested in those exact words),
    "here" (my own square), or "random" (any empty cell). An optional
    color -- a literal name, or "held" for whatever I'm currently
    holding -- overrides the new pixel's own template default."""
    kind = a.get("kind")
    where = a.get("where", "forward")
    if where == "here":
        x, y = obj.x, obj.y
    elif where == "forward":
        dx, dy = obj.facing
        x, y = obj.x + dx, obj.y + dy
    else:
        spot = world.empty_cell()
        if not spot:
            return
        x, y = spot
    new = world.spawn(kind, x, y)
    if new is not None and "color" in a:
        new.color = _resolve_color(obj, a["color"])


@action("vanish")
def a_vanish(obj, world, a, it):
    victim = obj if a.get("target", "self") == "self" else it
    if victim is not None:
        world.remove(victim)


@action("recolor")
def a_recolor(obj, world, a, it):
    """color: a literal name, or "held" for whatever I'm currently
    holding -- this is the "go to them later and edit them" tile,
    aimed at `it` (whatever touch/see found) rather than self."""
    victim = obj if a.get("target", "self") == "self" else it
    if victim is not None:
        victim.color = _resolve_color(obj, a.get("color", "white"))


@action("cycle_color")
def a_cycle_color(obj, world, a, it):
    """Advances held_color to the next in COLOR_CYCLE, wrapping --
    requested directly, "while im holding it i can cycle color." What
    gets cycled is the HELD colour (what I'll place/apply next), never
    my own visible .color, which stays whatever it already was."""
    target = obj if a.get("target", "self") == "self" else it
    if target is None:
        return
    idx = COLOR_CYCLE.index(target.held_color) if target.held_color in COLOR_CYCLE else 0
    target.held_color = COLOR_CYCLE[(idx + 1) % len(COLOR_CYCLE)]


@action("say")
def a_say(obj, world, a, it):
    world.message = a.get("text", "")


# ---------------------------------------------------------------------------
# the built-in demo -- proves the whole thing end to end
# ---------------------------------------------------------------------------

# width/height are placeholders -- run() overrides both with the actual
# square side computed for this device before constructing the World,
# and spawns the player at the centre itself (count: 0 here, so nothing
# spawns it randomly first). A separate, larger fixed world with only a
# small square VIEWPORT onto it was the first draft's real mistake,
# caught by its own pty test: the player, placed at random anywhere in
# a 60x60 world, had no reason to land inside whatever small corner of
# it a phone-sized terminal could actually show, and usually didn't.
# "The square size of individual device" means the world itself is
# that size, not a bigger one with a window cut into it.
DEMO = {
    "width": 20, "height": 20,
    "kinds": [
        {"kind": "player", "glyph": "@", "color": "white", "role": "player", "count": 0,
         "rules": [
             {"when": [{"sensor": "key", "args": {"key": "up"}}],
              "do": [{"action": "move", "args": {"dir": "up"}}]},
             {"when": [{"sensor": "key", "args": {"key": "down"}}],
              "do": [{"action": "move", "args": {"dir": "down"}}]},
             {"when": [{"sensor": "key", "args": {"key": "left"}}],
              "do": [{"action": "move", "args": {"dir": "left"}}]},
             {"when": [{"sensor": "key", "args": {"key": "right"}}],
              "do": [{"action": "move", "args": {"dir": "right"}}]},
             {"when": [{"sensor": "key", "args": {"key": "space"}}],
              "do": [{"action": "spawn",
                       "args": {"kind": "spark", "where": "forward", "color": "held"}}]},
             {"when": [{"sensor": "key", "args": {"key": "c"}}],
              "do": [{"action": "cycle_color", "args": {"target": "self"}}]},
             {"when": [{"sensor": "touch", "args": {"kind": "spark"}},
                       {"sensor": "key", "args": {"key": "e"}}],
              "do": [{"action": "recolor", "args": {"target": "it", "color": "held"}}]},
         ]},
        {"kind": "spark", "glyph": "█", "color": "yellow", "role": "prop", "count": 0,
         "rules": []},
    ],
}


# ---------------------------------------------------------------------------
# chat history -- /page cuts it into fixed-size pieces and "types" one out
# a few characters at a time, overwriting the log area in place rather
# than dumping it all at once. Requested directly: "make so the chat box
# shows pages of its history one at a time with overwrite existing and
# type writer effect" -- the exact same feature already built for
# ~/termux-chat, ported over: same PAGE_SIZE/page_count/page_lines/
# reveal_step shapes, same fixed-count-not-screen-sized pagination.
# ---------------------------------------------------------------------------

PAGE_SIZE = 15
CHARS_PER_TICK = 3   # ~30 chars/second at the main loop's own 10-tick-a-second pace


def page_count(history, page_size):
    """How many pages `history` makes at `page_size` lines each. 0 for
    an empty history (there is no page 1 of nothing)."""
    if not history or page_size < 1:
        return 0
    return -(-len(history) // page_size)   # ceiling division


def page_lines(history, page_size, n):
    """Page `n` (1-indexed), or None if `n` is out of range."""
    total = page_count(history, page_size)
    if total == 0 or n < 1 or n > total:
        return None
    start = (n - 1) * page_size
    return history[start:start + page_size]


def reveal_step(lines, shown):
    """`shown` is how many characters (counting every line's own text,
    newlines free) have been revealed so far. Returns (visible_lines,
    done) -- visible_lines is `lines` with everything past `shown`
    blanked out; done is True once every character has been shown.
    Overshooting `shown` past the real total is safe -- it just reads
    as done, forever, until something resets it -- so a caller never
    has to know when to stop incrementing."""
    out = []
    remaining = shown
    for line in lines:
        if remaining >= len(line):
            out.append(line)
            remaining -= len(line)
        elif remaining > 0:
            out.append(line[:remaining])
            remaining = 0
        else:
            out.append("")
    total_chars = sum(len(line) for line in lines)
    return out, shown >= total_chars


# ---------------------------------------------------------------------------
# the terminal loop -- thin: reads keys, advances state, draws
# ---------------------------------------------------------------------------

TICK = 0.1
HIDE, SHOW = "\033[?25l", "\033[?25h"
CLEAR_HOME = "\033[H\033[2J"
COLOR_CODES = {
    "white": 37, "red": 31, "green": 32, "yellow": 33, "blue": 34,
    "magenta": 35, "cyan": 36, "grey": 90, "black": 30,
}


def _ansi(color, glyph):
    return "\033[%dm%s\033[0m" % (COLOR_CODES.get(color, 37), glyph)


class Keyboard:
    """Non-blocking single-key reads, restoring the terminal on the
    way out -- same shape as Spark's own engine/runner.py Keyboard."""
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.saved = None

    def __enter__(self):
        if sys.stdin.isatty():
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def pause(self):
        """Hand the terminal back to normal cooked, line-editing input --
        so /commands get a real text box, backspace and all, same as
        chat_break's own reasoning in Spark's engine/runner.py."""
        self.__exit__()

    def resume(self):
        self.__enter__()

    def pressed(self):
        keys = set()
        if self.saved is None:
            return keys
        while select.select([sys.stdin], [], [], 0)[0]:
            data = os.read(self.fd, 64).decode("utf-8", "replace")
            if not data:
                break
            i = 0
            while i < len(data):
                ch = data[i]
                if ch == "\x1b" and data[i + 1:i + 2] == "[":
                    keys.add(_ESCAPES.get(data[i + 2:i + 3], "escape"))
                    i += 3
                    continue
                if ch == " ":
                    keys.add("space")
                elif ch in ("\x03", "\x04") or ch.lower() == "q":
                    # q quits outright, matching Spark's own convention
                    # exactly -- not one of c/e/wasd's single letters, so
                    # claiming it here doesn't collide with anything else.
                    keys.add("quit")
                else:
                    keys.add(ch.lower())
                i += 1
        return keys


_ESCAPES = {"A": "up", "B": "down", "C": "right", "D": "left"}
_LETTER_KEYS = {"w": "up", "s": "down", "a": "left", "d": "right"}


def _translate(keys):
    """w/a/s/d answer to the same rows up/down/left/right do, the same
    way Spark's own games treat them as the arrow they stand in for."""
    out = set()
    for k in keys:
        out.add(_LETTER_KEYS.get(k, k))
    return out


def _grid_side(reserved_rows):
    """A SQUARE grid -- same row and column count -- as large as fits
    above the chat area on THIS device right now: requested directly,
    "the square size of individual device." Computed once, at startup
    (see run()) -- the WORLD itself is built at exactly this size (not
    just the drawn viewport), so unlike every other screen built this
    session, this one deliberately does NOT recompute on every redraw:
    the world's own actual dimensions would have to change along with
    it, which a live terminal resize doesn't attempt yet in this first
    version. Said plainly rather than silently: resize your terminal
    before launching, not while playing."""
    cols, rows = shutil.get_terminal_size(fallback=(80, 24))
    return max(5, min(cols, rows - reserved_rows))


def draw(world, side, history, log_h, held_color, reveal):
    out = [CLEAR_HOME]
    grid = [[" "] * side for _ in range(side)]
    # Props first, the player last, so standing on a placed pixel still
    # shows your own glyph rather than the pixel hiding you underneath it.
    ordered = sorted((p for p in world.pixels if p.alive and 0 <= p.x < side and 0 <= p.y < side),
                      key=lambda p: p.role == "player")
    for p in ordered:
        grid[p.y][p.x] = _ansi(p.color, p.glyph)
    out.extend("".join(row) for row in grid)
    out.append("tick %-6d  %d pixels  holding: %s  %s"
                % (world.tick, len(world.pixels), _ansi(held_color, "█"),
                   world.message or ""))
    out.append("-" * min(60, max(20, side)))
    # The page-status line sits right under the rule, above the log's own
    # content, not after it -- same reasoning chatshell.py's own draw()
    # gives: it's a header for the content below, not a footer to it.
    if reveal is not None:
        visible, _ = reveal_step(reveal["lines"], reveal["shown"])
        status = "-- page %d of %d --" % (reveal["page"], reveal["total"])
    else:
        visible = history
        status = "%d line%s logged -- /page to look back" % (
            len(history), "" if len(history) == 1 else "s")
    out.append(status)
    shown = visible[-log_h:] if len(visible) > log_h else visible
    shown = shown + [""] * (log_h - len(shown))
    out.extend(shown)
    out.append("arrows/wasd move . space spawns . c cycles colour . e edits . / chat . q quits")
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def parse_command(text):
    text = text.strip()
    if not text:
        return ("empty", None)
    if not text.startswith("/"):
        return ("say", text)
    parts = text[1:].split(None, 1)
    word = (parts[0] if parts else "").lower()
    rest = parts[1] if len(parts) > 1 else ""
    if word == "page":
        return ("page", rest.strip())
    if word in ("quit", "q"):
        return ("quit", None)
    return ("unknown", word)


def chat_break(keyboard, history, reveal_box):
    """Pressed "/": a real cooked line (or several -- stays open until
    a blank one), the same shape as Spark's own chat_break, for the
    same reason -- w/a/s/d moving the player while someone's trying to
    type them would otherwise be unavoidable. The prompt itself is a
    plain "> ", not a "/" -- unlike Spark's own terminal chat (which
    has no concept of plain chat text at all, everything typed there
    is a command), spark2's is genuinely either, so parse_command's own
    "/" means command, anything else is said" rule needs an actual
    typed "/" to go with it, the same as chatshell.py's own prompt.

    `reveal_box` is a one-item list ([None] or [{"lines","shown","page",
    "total"}]) rather than a plain variable, so /page can actually hand
    a new reveal back to run()'s own loop -- the typewriter itself plays
    out there, tick by tick, not inside this blocking cooked-input
    sub-loop, so the game (and its own animation) keeps going the whole
    time you're reading an old page."""
    keyboard.pause()
    sys.stdout.write(SHOW)
    try:
        while True:
            try:
                said = input("\n> ")
            except EOFError:
                said = "/quit"
            kind, arg = parse_command(said)
            if kind == "empty":
                break
            if kind == "quit":
                return True
            if kind == "say":
                history.append("you: " + arg)
                reveal_box[0] = None   # a new message always snaps back to "now"
            elif kind == "page":
                total = page_count(history, PAGE_SIZE)
                try:
                    n = int(arg) if arg else total
                except ValueError:
                    print("page number needs to be a plain number")
                    continue
                lines = page_lines(history, PAGE_SIZE, n)
                if lines is None:
                    print("nothing logged yet" if total == 0 else
                          "no page %d -- there are %d" % (n, total))
                    continue
                reveal_box[0] = {"lines": lines, "shown": 0, "page": n, "total": total}
                break   # close chat -- go watch it type itself out in the live view
            else:
                history.append("no such command: /" + arg)
    finally:
        sys.stdout.write(HIDE)
        keyboard.resume()
    return False


LOG_H = 6


def run():
    # +4 reserved: the status line, the rule, the page-status line, the hint line
    side = _grid_side(LOG_H + 4)
    config = dict(DEMO, width=side, height=side)
    world = World(config)
    player = world.spawn("player", side // 2, side // 2)
    history = ["arrows/wasd move, space spawns a pixel right in front of you",
               "c cycles the colour you're holding, e edits one you're on/next to",
               "type / any time to chat, /page [n] to look back"]
    reveal_box = [None]   # [None] or [{"lines","shown","page","total"}] -- see chat_break
    with Keyboard() as keyboard:
        sys.stdout.write(HIDE)
        try:
            while True:
                held = player.held_color if player is not None else COLOR_CYCLE[0]
                try:
                    draw(world, side, history, LOG_H, held, reveal_box[0])
                except Exception:
                    print(CLEAR_HOME)
                    print(traceback.format_exc())
                keys = keyboard.pressed()
                if "quit" in keys:
                    break
                if "/" in keys:
                    if chat_break(keyboard, history, reveal_box):
                        break
                    continue
                if reveal_box[0] is not None:
                    reveal_box[0]["shown"] += CHARS_PER_TICK
                world.keys = _translate(keys)
                try:
                    world.step()
                except Exception:
                    history.append("something went wrong -- " + repr(sys.exc_info()[1]))
                    history.extend(traceback.format_exc().rstrip("\n").split("\n"))
        except KeyboardInterrupt:
            pass
        finally:
            sys.stdout.write(SHOW)
            sys.stdout.write("\n")
            sys.stdout.flush()
    print("bye")


if __name__ == "__main__":
    run()
