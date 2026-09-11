#!/usr/bin/env python3
"""Drive the real program through an actual pseudo-terminal -- the pure
engine is covered by tests/test_spark2.py, this covers the thin loop on
top of it: does it actually launch, draw a square grid, move the player,
spawn/cycle/edit, chat over "/", and quit cleanly.

    python3 tests/test_pty.py
"""

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "spark2.py"

passed = failed = 0


def check(name, condition, extra=""):
    global passed, failed
    if condition:
        passed += 1
        print("  ok   " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  -> " + str(extra) if extra else ""))


class Session:
    def __init__(self, rows=30, cols=60):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.proc = subprocess.Popen(
            [sys.executable, str(PROGRAM)],
            stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        select.select([self.master], [], [], 1.0)

    def send(self, data, wait=0.2):
        os.write(self.master, data)
        time.sleep(wait)

    def drain(self, timeout=0.5):
        out = b""
        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if self.master not in ready:
                continue
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        return out.decode("utf-8", "replace")

    def close(self):
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        os.close(self.master)


def wait_ready(s, needle="tick", timeout=3.0):
    """Retries drain() until real drawn output (the status line, by
    default) actually shows up, instead of hoping a single fixed-length
    drain was long enough. Matters specifically for a keystroke sent
    right after startup: Keyboard.__enter__'s tty.setcbreak() defaults
    to TCSAFLUSH, which DISCARDS any input typed before the mode switch
    actually takes effect -- under real system load, the gap between
    the process starting and its first real frame (import, World
    construction, entering cbreak) can run longer than a short fixed
    drain covers, and a keystroke sent into that gap is not just
    answered late, it can be silently lost outright. Waiting for
    genuine proof the program is already past that point, however long
    it actually takes (up to `timeout`), is what actually closes the
    race, the same lesson Spark's own tests/check_menu.py had to learn
    for its own Session class."""
    out = ""
    end = time.time() + timeout
    while time.time() < end:
        out += s.drain(0.1)
        if needle in out:
            return out
    return out


print("launches and draws a square grid on a real terminal")
s = Session()
out = wait_ready(s)
check("draws right away, no crash", len(out) > 100, len(out))
check("the player's own glyph is on screen", "@" in out, out[:200])
check("the hint line mentions the controls",
      "spawns" in out and "cycles" in out, out[-300:])

print("\nmoving turns the player and actually changes position")
s.send(b"\x1b[C")   # right arrow
out = s.drain(0.3)
check("still drawing after a move, no crash", "@" in out, out[-200:])

print("\nspace spawns a pixel; c cycles the held colour")
s.send(b" ")
out = s.drain(0.3)
check("the status line still shows a tick count and pixel count",
      "tick" in out and "pixels" in out, out[-200:])
s.send(b"c")
out = s.drain(0.3)
check("cycling colour doesn't crash anything", "holding" in out, out[-200:])

print("\n/ opens a real cooked chat line, stays open, a blank line closes it")
s.send(b"/")
s.drain(0.2)
s.send(b"hello from the pty\r")
out = s.drain(0.4)
check("the chat message shows up", "hello from the pty" in out, out[-300:])
s.send(b"\r")   # blank line closes chat
back = s.drain(0.4)
check("back in the running game afterward", "tick" in back, back[-300:])

print("\n/quit leaves cleanly")
s.send(b"/")
s.drain(0.2)
s.send(b"/quit\r")
s.close()
check("the process actually exited, code 0", s.proc.returncode == 0, s.proc.returncode)

print("\nordinary q also quits without needing /quit")
s2 = Session()
wait_ready(s2)
s2.send(b"q")
s2.close()
check("q exits cleanly", s2.proc.returncode == 0, s2.proc.returncode)

print("\nCtrl-D quits too")
s3 = Session()
wait_ready(s3)
s3.send(b"\x04")
s3.close()
check("Ctrl-D exits cleanly", s3.proc.returncode == 0, s3.proc.returncode)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
