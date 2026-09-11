# spark2

A from-scratch 2D ASCII "pixel" engine and runtime for Termux.
Standalone -- not Spark, doesn't import anything from it, though the
underlying idea is the same one Spark itself is built on: `WHEN` a
sensor finds something true, `DO` an action, aimed at a target.

```
python3 spark2.py
```

## What it fundamentally is

Six pieces, and nothing else is required:

1. **A grid** -- a square area, sized to fit this exact device.
2. **A pixel** -- position, a colour, a kind (its template).
3. **A rule** -- `WHEN <condition> DO <effect>`, one character's own
   list of them, checked every tick.
4. **Sensors** (the WHEN half) -- `always`, `timer`, `key`, `touch`,
   `see`, `chance`. Some of them *find* another pixel, which becomes
   the row's own target.
5. **Actions** (the DO half) -- `move`, `face`, `spawn`, `vanish`,
   `recolor`, `cycle_color`, `say`.
6. **Target vs. self** -- every action that needs one takes a
   `target`: `"self"` (whoever's running the rule) or `"it"` (whatever
   a sensor found).

## The built-in demo

Arrows/WASD move the player and turn it to face the way it moved.
**Space** spawns a "spark" pixel one square directly in front of it --
adjacent and in front of your character, literally. **c** cycles which
colour you're currently holding (shown in the status line). **e**,
while touching (or standing exactly on top of -- walking onto a spark
never blocks you) a spark you already placed, recolours *that one* to
whatever you're currently holding instead of placing a new one --
going back to edit something you made earlier.

`/` opens a real typed line -- plain text is added to the log and
shown right away; `/quit` (or plain `q`, or Ctrl-D) leaves.

## Layout

```
+----------------------------------+
|                                   |
|    (the square grid, sized to    |
|     fit this device exactly)     |
|                                   |
+-----------------------------------  <- status: tick, pixel count, held colour
| chat log line                    |
| chat log line                    |   <- the log
+-----------------------------------
| arrows/wasd move . space spawns .|   <- hint line
+-----------------------------------
```

A note on sizing: the square side is computed once, at startup, from
the real terminal size, and the *world itself* is built at exactly
that size -- not a bigger fixed world with only a small window cut
into it (an early draft's real bug: the player, placed at random
inside a much bigger world, usually landed somewhere the visible
viewport never showed). Resize your terminal before launching, not
while playing -- this first version doesn't yet re-fit a game already
in progress.

## Tests

```
python3 tests/test_spark2.py
python3 tests/test_pty.py
```

`test_spark2.py` covers the engine itself (World, Pixel, sensors,
actions, resolve_step, and the demo's own rules) directly, no terminal
needed. `test_pty.py` drives the real program through an actual
pseudo-terminal: launches, moves, spawns, cycles colour, edits a
placed pixel, chats over `/`, and quits (`/quit`, `q`, and Ctrl-D all
checked).
