#!/usr/bin/env python3
"""
Promptly — a tiny, movable, always-on-top status light for macOS & Linux.

One simple control state (off / red / yellow / green) drives whatever skin you
pick. It is controlled over a tiny local HTTP server, so ANY tool that can run a
shell command or make an HTTP request can change it: Claude Code hooks, shell
scripts, CI, cron, etc.

Skins (same three states, different look):
    traffic     classic three-lamp traffic light (default)
    orb         a single breathing orb
    spinner     indeterminate spinner while busy, a check when ready
    gradient    an ambient breathing gradient bar
    tamagotchi  a little creature that works, calls you, and rests

Control examples (any of these work):
    curl -s localhost:7654/red
    curl -s 'localhost:7654/yellow?flash=1&label=Needs+your+input'
    curl -s localhost:7654/green
    curl -s localhost:7654/off
    curl -s localhost:7654/state -d '{"state":"yellow","flash":true,"label":"Running tests"}'
    curl -s localhost:7654/state          # read current state as JSON

Run:
    pip install PySide6-Essentials
    python promptly.py                      # spinner skin (default)
    python promptly.py --skin orb
    python promptly.py --skin traffic --scale 1.4
    python promptly.py --vertical --port 7654 --state green
"""

import os
import sys
import math
import json
import argparse
import threading
import subprocess
import shlex
import platform
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from PySide6.QtCore import Qt, QObject, Signal, QRectF, QPointF, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QRadialGradient, QLinearGradient, QBrush, QPen,
    QPolygonF, QAction, QActionGroup, QGuiApplication,
)
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QInputDialog, QToolTip

HOST = "127.0.0.1"
DEFAULT_PORT = 7654

# state -> which single lamp is lit ("off" lights none)
VALID_STATES = ("off", "red", "yellow", "green")
LAMPS = ("red", "yellow", "green")  # fixed top->bottom / left->right order
LAMP_COLORS = {
    "red": QColor("#ff453a"),
    "yellow": QColor("#ffd60a"),
    "green": QColor("#32d74b"),
}


def mix(a, b, t):
    """Blend two QColors: t=1 -> a, t=0 -> b."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() * t + b.red() * (1 - t)),
        int(a.green() * t + b.green() * (1 - t)),
        int(a.blue() * t + b.blue() * (1 - t)),
    )


def draw_orb(p, cx, cy, r, color, intensity):
    """A glossy sphere. color=None paints a neutral, unlit orb."""
    lit = color is not None and intensity > 0.02

    if lit:
        glow_r = r * 1.6
        glow = QRadialGradient(cx, cy, glow_r)
        gc = QColor(color)
        gc.setAlpha(int(160 * intensity))
        glow.setColorAt(0.0, gc)
        edge = QColor(color)
        edge.setAlpha(0)
        glow.setColorAt(1.0, edge)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, glow_r * 2, glow_r * 2))

    face = QRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 1.7)
    if lit:
        bright = mix(QColor("#ffffff"), color, 0.55 + 0.25 * intensity)
        midtone = mix(color, QColor(0, 0, 0), 1 - intensity)
        face.setColorAt(0.0, bright)
        face.setColorAt(0.55, color if intensity > 0.6 else midtone)
        face.setColorAt(1.0, color.darker(170))
    else:
        base = color.darker(600) if color is not None else QColor("#37373b")
        face.setColorAt(0.0, base.lighter(118))
        face.setColorAt(1.0, base)

    p.setPen(QPen(QColor(0, 0, 0, 110), 1))
    p.setBrush(QBrush(face))
    p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

    # glossy top highlight
    hl = QColor(255, 255, 255, int(70 + 60 * (intensity if lit else 0.2)))
    p.setPen(Qt.NoPen)
    p.setBrush(hl)
    p.drawEllipse(QRectF(cx - r * 0.45, cy - r * 0.62, r * 0.9, r * 0.55))


# --------------------------------------------------------------------------- #
#  Skins — each is a pure render strategy over the widget's state.
#  A skin reads w.state / w.flash / w.scale / w.vertical / w.breath() / w.phase
#  and decides geometry (size) and pixels (paint). Adding a skin = add a class
#  and list it in SKIN_LIST; nothing about the control layer changes.
# --------------------------------------------------------------------------- #
class Skin:
    name = "skin"
    label = "Skin"
    supports_orientation = False

    def size(self, w):
        """Return (width, height) in pixels for the current scale/orientation."""
        raise NotImplementedError

    def animating(self, w):
        """True if the current state needs the ~30fps ticker running."""
        return False

    def paint(self, p, w):
        raise NotImplementedError


class TrafficLightSkin(Skin):
    name = "traffic"
    label = "Traffic light"
    supports_orientation = True

    def _dims(self, w):
        lamp = round(36 * w.scale)
        gap = round(14 * w.scale)
        pad = round(16 * w.scale)
        return lamp, gap, pad

    def size(self, w):
        lamp, gap, pad = self._dims(w)
        long_side = pad * 2 + lamp * 3 + gap * 2
        short_side = pad * 2 + lamp
        return (short_side, long_side) if w.vertical else (long_side, short_side)

    def animating(self, w):
        return w.flash and w.state in LAMP_COLORS

    def paint(self, p, w):
        lamp, gap, pad = self._dims(w)
        # housing
        body = QRectF(0.5, 0.5, w.width() - 1, w.height() - 1)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(QColor(26, 26, 28, 235))
        radius = min(w.width(), w.height()) / 2.4
        p.drawRoundedRect(body, radius, radius)

        first = pad + lamp / 2
        step = lamp + gap
        for i, key in enumerate(LAMPS):
            if w.vertical:
                cx, cy = w.width() / 2, first + i * step
            else:
                cx, cy = first + i * step, w.height() / 2
            color = LAMP_COLORS[key]
            active = (w.state == key)
            intensity = w.breath() if (active and w.flash) else (1.0 if active else 0.0)
            draw_orb(p, cx, cy, lamp / 2, color, intensity)


class OrbSkin(Skin):
    name = "orb"
    label = "Orb"

    def size(self, w):
        side = round(58 * w.scale) + round(13 * w.scale) * 2
        return side, side

    def animating(self, w):
        return w.flash and w.state in LAMP_COLORS

    def paint(self, p, w):
        cx, cy = w.width() / 2, w.height() / 2
        r = min(w.width(), w.height()) / 2 - round(8 * w.scale)
        color = LAMP_COLORS.get(w.state)
        intensity = w.breath() if w.flash else 1.0
        draw_orb(p, cx, cy, r, color, intensity)


class SpinnerSkin(Skin):
    name = "spinner"
    label = "Spinner"

    def size(self, w):
        side = round(46 * w.scale) + round(15 * w.scale) * 2
        return side, side

    def animating(self, w):
        return w.state in ("red", "yellow")

    def paint(self, p, w):
        cx, cy = w.width() / 2, w.height() / 2
        d = round(46 * w.scale)
        r = d / 2
        rect = QRectF(cx - r, cy - r, d, d)
        tw = max(2, round(d * 0.13))
        state = w.state

        # housing
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(QColor(26, 26, 28, 235))
        p.drawEllipse(QRectF(0.5, 0.5, w.width() - 1, w.height() - 1))

        if state == "off":
            return

        color = LAMP_COLORS.get(state, QColor("#48484a"))

        # faint track ring
        track = QPen(QColor(255, 255, 255, 28), tw)
        track.setCapStyle(Qt.RoundCap)
        p.setPen(track)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect)

        if state == "red":  # busy -> rotating arc
            ang = (w.phase * 300) % 360
            pen = QPen(color, tw)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, int(ang * 16), int(270 * 16))
        elif state == "yellow":  # attention -> breathing dot
            rr = r * (0.26 + 0.16 * w.breath())
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(cx, cy), rr, rr)
        elif state == "green":  # ready -> static check
            pen = QPen(color, tw)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            check = QPolygonF([
                QPointF(cx - 0.42 * r, cy + 0.02 * r),
                QPointF(cx - 0.10 * r, cy + 0.34 * r),
                QPointF(cx + 0.45 * r, cy - 0.30 * r),
            ])
            p.drawPolyline(check)


class BreathingGradientSkin(Skin):
    name = "gradient"
    label = "Breathing gradient"
    supports_orientation = True

    def size(self, w):
        long_side = round(150 * w.scale)
        short_side = round(34 * w.scale)
        return (short_side, long_side) if w.vertical else (long_side, short_side)

    def animating(self, w):
        return w.state != "off"

    def paint(self, p, w):
        rect = QRectF(0.5, 0.5, w.width() - 1, w.height() - 1)
        radius = min(w.width(), w.height()) / 2
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(QColor(20, 20, 22, 235))
        p.drawRoundedRect(rect, radius, radius)

        color = LAMP_COLORS.get(w.state) or QColor("#48484a")
        lit = w.state != "off"
        b = w.breath() if lit else 0.45

        inset = round(4 * w.scale)
        inner = rect.adjusted(inset, inset, -inset, -inset)
        ir = min(inner.width(), inner.height()) / 2

        if w.vertical:
            grad = QLinearGradient(inner.left(), inner.top(), inner.left(), inner.bottom())
        else:
            grad = QLinearGradient(inner.left(), inner.top(), inner.right(), inner.top())

        edge = color.darker(230)
        peak = mix(QColor("#ffffff"), color, 0.40 + 0.45 * b)
        # a soft bright band that drifts side to side when lit
        pos = 0.5 + (0.34 * math.sin(w.phase * 1.5) if lit else 0.0)
        pos = max(0.08, min(0.92, pos))
        grad.setColorAt(0.0, edge)
        grad.setColorAt(pos, peak)
        grad.setColorAt(1.0, edge)

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(inner, ir, ir)


class TamagotchiSkin(Skin):
    name = "tamagotchi"
    label = "Tamagotchi"

    def size(self, w):
        return round(68 * w.scale), round(78 * w.scale)

    def animating(self, w):
        return w.state != "off"

    def paint(self, p, w):
        W, H = w.width(), w.height()
        t = w.phase
        state = w.state
        color = LAMP_COLORS.get(state)

        margin = round(11 * w.scale)
        bw = W - 2 * margin
        bh = H - 2 * margin

        # motion per state
        bob = 0.0
        breath_scale = 1.0
        if state == "red":          # heads-down working: gentle bob
            bob = math.sin(t * 7.0) * 2.0 * w.scale
        elif state == "yellow":     # calling you: bouncing up
            bob = -abs(math.sin(t * 6.0)) * 7.0 * w.scale
        elif state == "green":      # resting: slow breathing
            breath_scale = 1.0 + 0.03 * math.sin(t * 2.2)

        cx = W / 2
        bcy = margin + bh / 2 + bob
        sw, sh = bw * breath_scale, bh * breath_scale
        rect = QRectF(cx - sw / 2, bcy - sh / 2, sw, sh)

        # body colors (tinted by state so you still read the signal)
        if color is not None:
            body = mix(QColor("#ffffff"), color, 0.5)
            outline = color.darker(160)
            ink = QColor("#2b2b2f")
        else:
            body = QColor("#6b6b70")
            outline = QColor("#4a4a4e")
            ink = QColor("#3a3a3e")

        p.setPen(QPen(outline, max(1, round(2 * w.scale))))
        p.setBrush(body)
        p.drawRoundedRect(rect, sw * 0.42, sw * 0.42)

        # face metrics
        ex = sw * 0.22
        eyey = bcy - sh * 0.06
        eyeR = sw * 0.11
        mouthy = bcy + sh * 0.16

        pen = QPen(ink, max(2, round(2.4 * w.scale)))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)

        if state == "red":          # focused: narrowed eyes, small mouth
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(cx - ex - eyeR, eyey), QPointF(cx - ex + eyeR, eyey))
            p.drawLine(QPointF(cx + ex - eyeR, eyey), QPointF(cx + ex + eyeR, eyey))
            p.drawLine(QPointF(cx - eyeR * 0.7, mouthy), QPointF(cx + eyeR * 0.7, mouthy))
        elif state == "yellow":     # excited: wide eyes, open mouth
            p.setPen(Qt.NoPen)
            p.setBrush(ink)
            p.drawEllipse(QPointF(cx - ex, eyey), eyeR, eyeR)
            p.drawEllipse(QPointF(cx + ex, eyey), eyeR, eyeR)
            p.drawEllipse(QPointF(cx, mouthy), eyeR * 0.7, eyeR * 0.95)
        elif state == "green":      # content: happy closed eyes, smile
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            le = QRectF(cx - ex - eyeR, eyey - eyeR * 0.5, eyeR * 2, eyeR)
            re = QRectF(cx + ex - eyeR, eyey - eyeR * 0.5, eyeR * 2, eyeR)
            p.drawArc(le, int(200 * 16), int(140 * 16))
            p.drawArc(re, int(200 * 16), int(140 * 16))
            mr = QRectF(cx - eyeR * 1.3, mouthy - eyeR, eyeR * 2.6, eyeR * 1.8)
            p.drawArc(mr, int(200 * 16), int(140 * 16))
        else:                       # off: asleep, flat closed eyes
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(cx - ex - eyeR, eyey), QPointF(cx - ex + eyeR, eyey))
            p.drawLine(QPointF(cx + ex - eyeR, eyey), QPointF(cx + ex + eyeR, eyey))


SKIN_LIST = [
    TrafficLightSkin,
    OrbSkin,
    SpinnerSkin,
    BreathingGradientSkin,
    TamagotchiSkin,
]
SKINS = {cls.name: cls for cls in SKIN_LIST}


def cgs_add_window_to_all_spaces(window_number, debug=False):
    """Best-effort: use the PRIVATE CoreGraphics/SkyLight Spaces API to place a
    window on every Space, including other apps' native fullscreen Spaces — the
    one thing public AppKit (CanJoinAllSpaces etc.) cannot do. Undocumented and
    may break on macOS updates, so this is opt-in (PROMPTLY_FORCE_ALL_SPACES=1).
    Re-run periodically so freshly-created fullscreen Spaces are covered too.
    """
    import ctypes
    from ctypes import util, c_void_p, c_int, c_int32, c_long, byref, POINTER

    cf = ctypes.cdll.LoadLibrary(util.find_library("CoreFoundation"))
    try:  # the CGS* symbols live in SkyLight on modern macOS
        sky = ctypes.cdll.LoadLibrary(
            "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")
    except OSError:
        sky = ctypes.cdll.LoadLibrary(util.find_library("CoreGraphics"))

    cf.CFNumberCreate.restype = c_void_p
    cf.CFNumberCreate.argtypes = [c_void_p, c_long, c_void_p]
    cf.CFArrayCreate.restype = c_void_p
    cf.CFArrayCreate.argtypes = [c_void_p, POINTER(c_void_p), c_long, c_void_p]
    cf.CFArrayGetCount.restype = c_long
    cf.CFArrayGetCount.argtypes = [c_void_p]
    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [c_void_p]

    sky.CGSMainConnectionID.restype = c_int
    sky.CGSMainConnectionID.argtypes = []
    sky.CGSCopySpaces.restype = c_void_p
    sky.CGSCopySpaces.argtypes = [c_int, c_int]
    sky.CGSAddWindowsToSpaces.restype = None
    sky.CGSAddWindowsToSpaces.argtypes = [c_int, c_void_p, c_void_p]

    KCF_SINT32 = 3
    CGS_ALL_SPACES = 7  # current | others | user
    callbacks = c_void_p.in_dll(cf, "kCFTypeArrayCallBacks")

    cid = sky.CGSMainConnectionID()
    val = c_int32(int(window_number))
    num = cf.CFNumberCreate(None, KCF_SINT32, byref(val))
    if not num:
        return
    values = (c_void_p * 1)(num)
    win_array = cf.CFArrayCreate(None, values, 1, byref(callbacks))
    spaces = sky.CGSCopySpaces(cid, CGS_ALL_SPACES)
    try:
        if win_array and spaces:
            sky.CGSAddWindowsToSpaces(cid, win_array, spaces)
        if debug:
            cnt = cf.CFArrayGetCount(spaces) if spaces else -1
            print(f"[promptly] CGS add-to-all-spaces cid={cid} "
                  f"win#={window_number} spaces={cnt}", file=sys.stderr)
    finally:
        cf.CFRelease(num)
        if win_array:
            cf.CFRelease(win_array)
        if spaces:
            cf.CFRelease(spaces)


def tune_macos_window(win_id, on_top, force_all_spaces=False, debug=False):
    """Apply NSWindow behavior Qt doesn't expose on macOS so the panel:
      - shows on every Space / desktop and over fullscreen apps,
      - stays visible when another app is frontmost (Qt.Tool hides by default),
      - floats above normal windows when "always on top" is on.
    Uses the always-present libobjc via ctypes, so no extra dependency.
    Safe no-op on failure; call after the native window exists (showEvent).
    """
    import ctypes
    from ctypes import util, c_void_p, c_char_p, c_long, c_ulong, c_bool

    libobjc = ctypes.cdll.LoadLibrary(util.find_library("objc"))
    libobjc.sel_registerName.restype = c_void_p
    libobjc.sel_registerName.argtypes = [c_char_p]
    libobjc.objc_getClass.restype = c_void_p
    libobjc.objc_getClass.argtypes = [c_char_p]
    libobjc.object_getClassName.restype = c_char_p
    libobjc.object_getClassName.argtypes = [c_void_p]

    def send(receiver, selector, *args, argtypes=(), restype=c_void_p):
        libobjc.objc_msgSend.restype = restype
        libobjc.objc_msgSend.argtypes = [c_void_p, c_void_p, *argtypes]
        return libobjc.objc_msgSend(receiver, libobjc.sel_registerName(selector), *args)

    # Become an "accessory" app (like a menu-bar agent): no Dock icon, and —
    # crucially — its windows are allowed to float over OTHER apps' fullscreen
    # Spaces, which a "regular" app's windows cannot. Set PROMPTLY_KEEP_DOCK=1 to
    # keep the Dock icon (you then lose the over-fullscreen overlay).
    if os.environ.get("PROMPTLY_KEEP_DOCK") != "1":
        ACCESSORY = 1  # NSApplicationActivationPolicyAccessory
        nsapp = send(libobjc.objc_getClass(b"NSApplication"), b"sharedApplication")
        send(nsapp, b"setActivationPolicy:", c_long(ACCESSORY),
             argtypes=(c_long,), restype=c_bool)

    window = send(c_void_p(int(win_id)), b"window")  # NSView -> NSWindow
    if not window:
        if debug:
            print(f"[promptly] no NSWindow for winId={win_id}", file=sys.stderr)
        return

    CAN_JOIN_ALL_SPACES = 1 << 0
    FULLSCREEN_AUXILIARY = 1 << 8
    # A fullscreen app owns its own Space; CanJoinAllSpaces alone won't put us
    # there. To overlay it we must sit at a window level *above* the fullscreen
    # window — NSScreenSaverWindowLevel (1000) does it. Override via PROMPTLY_LEVEL
    # (e.g. 25 = status level, below the menu bar) if 1000 feels too aggressive.
    OVERLAY_LEVEL = int(os.environ.get("PROMPTLY_LEVEL", "1000"))
    behavior = CAN_JOIN_ALL_SPACES | FULLSCREEN_AUXILIARY
    send(window, b"setCollectionBehavior:", c_ulong(behavior),
         argtypes=(c_ulong,), restype=None)
    send(window, b"setHidesOnDeactivate:", c_bool(False),
         argtypes=(c_bool,), restype=None)
    send(window, b"setLevel:", c_long(OVERLAY_LEVEL if on_top else 0),
         argtypes=(c_long,), restype=None)

    # Setting CanJoinAllSpaces does NOT retroactively place an already-shown
    # window onto the other Spaces — macOS only re-evaluates Space membership
    # when the window is ordered in. Re-order it (without activating the app or
    # stealing focus) so it gets pulled onto whatever Space is now in front.
    # NOTE: re-ordering while another app is in fullscreen can pull us back onto
    # the desktop Space; set PROMPTLY_NO_ORDERFRONT=1 to skip this.
    if os.environ.get("PROMPTLY_NO_ORDERFRONT") != "1":
        send(window, b"orderFrontRegardless", restype=None)

    # Opt-in: the only thing that reaches other apps' fullscreen Spaces.
    if force_all_spaces:
        try:
            wnum = send(window, b"windowNumber", restype=c_long)
            cgs_add_window_to_all_spaces(wnum, debug=debug)
        except Exception as exc:
            if debug:
                print(f"[promptly] CGS force-all-spaces failed: {exc}",
                      file=sys.stderr)

    if debug:
        cls = libobjc.object_getClassName(c_void_p(window))
        now = send(window, b"collectionBehavior", restype=c_ulong)
        lvl = send(window, b"level", restype=c_long)
        hides = send(window, b"hidesOnDeactivate", restype=c_bool)
        vis = send(window, b"isVisible", restype=c_bool)
        on_space = send(window, b"isOnActiveSpace", restype=c_bool)
        nsapp = send(libobjc.objc_getClass(b"NSApplication"), b"sharedApplication")
        policy = send(nsapp, b"activationPolicy", restype=c_long)
        print(f"[promptly] class={cls!r} behavior={now} level={lvl} "
              f"hides={hides} visible={vis} onActiveSpace={on_space} policy={policy}",
              file=sys.stderr)


# --------------------------------------------------------------------------- #
#  GUI thread <- HTTP thread bridge
# --------------------------------------------------------------------------- #
class Bridge(QObject):
    """Lets the background HTTP thread push updates to the Qt GUI thread safely."""
    apply = Signal(str, bool, str)  # state, flash, label
    focus = Signal(dict)            # {app|title|cmd: ...} -> where Claude runs


# --------------------------------------------------------------------------- #
#  The widget
# --------------------------------------------------------------------------- #
class TrafficLight(QWidget):
    SIZE_PRESETS = {      # label -> scale factor (right-click > Size)
        "Small": 0.7,
        "Medium": 1.0,
        "Large": 1.4,
        "Extra large": 1.8,
    }

    def __init__(self, vertical=False, port=DEFAULT_PORT, scale=1.0, skin="spinner"):
        super().__init__()
        self.vertical = vertical
        self.port = port
        self.scale = scale
        self.skin = SKINS.get(skin, SpinnerSkin)()
        self.attached_top = False     # snap + lock to the top edge of the screen
        self.on_top = True
        # macOS: also float over other apps' fullscreen Spaces (private API).
        # On by default now; set PROMPTLY_FORCE_ALL_SPACES=0 to start without it.
        self.force_all_spaces = os.environ.get("PROMPTLY_FORCE_ALL_SPACES", "1") != "0"
        self.focus_spec = {}          # how to raise the window where Claude runs
        self.state = "off"
        self.flash = False
        self.label = ""
        self.phase = 0.0              # continuous time (s) for animated skins
        self._native_debugged = False
        self._drag_offset = None
        self._press_global = None     # to tell a click apart from a drag
        self._moved = False

        self.setWindowTitle("Promptly")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                     # floats, stays out of the dock/taskbar
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # ~30fps ticker that advances `phase`; only runs when a skin needs it.
        self._ticker = QTimer(self)
        self._ticker.setInterval(33)
        self._ticker.timeout.connect(self._on_tick)

        # macOS: Qt owns the window's collectionBehavior and re-sets it (pinning
        # us to one Space) during/after show. Re-apply our "join all Spaces"
        # setting on a slow timer so it wins the race and recovers if reset.
        self._native_timer = QTimer(self)
        self._native_timer.setInterval(1000)
        self._native_timer.timeout.connect(self._tune_native)

        self._resize_for_skin()
        self._move_to_default_corner()

    def breath(self):
        """Smooth 0.18..1.0 'breathing' value for pulsing/flashing."""
        return 0.18 + 0.82 * (0.5 - 0.5 * math.cos(self.phase * (2 * math.pi / 1.3)))

    def _on_tick(self):
        self.phase += self._ticker.interval() / 1000.0
        self.update()

    def _tune_native(self):
        # macOS only: keep us on top, across every Space, and visible while
        # another app is frontmost. Harmless / no-op elsewhere.
        if platform.system() != "Darwin":
            return
        debug = os.environ.get("PROMPTLY_DEBUG") == "1" and not self._native_debugged
        try:
            tune_macos_window(self.winId(), self.on_top,
                              force_all_spaces=self.force_all_spaces, debug=debug)
        except Exception as exc:
            if debug:
                print(f"[promptly] tune_macos_window failed: {exc}", file=sys.stderr)
        if debug:
            self._native_debugged = True

    def showEvent(self, event):
        # Qt's Cocoa backend sets its own collectionBehavior/level *after* this
        # event, which un-does ours and pins the window to one Space. Re-apply
        # immediately AND on the next runloop tick so ours is the last word.
        super().showEvent(event)
        self._tune_native()
        QTimer.singleShot(0, self._tune_native)
        if platform.system() == "Darwin" and not self._native_timer.isActive():
            self._native_timer.start()

    def _sync_animation(self):
        if self.skin.animating(self):
            if not self._ticker.isActive():
                self._ticker.start()
        else:
            self._ticker.stop()
            self.update()

    # ---- geometry ----------------------------------------------------------
    def _resize_for_skin(self):
        w, h = self.skin.size(self)
        self.setFixedSize(int(w), int(h))

    def _move_to_default_corner(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        # park it tight in the bottom-right corner, with just a small margin
        margin = 8
        x = geo.right() - self.width() - margin
        y = geo.bottom() - self.height() - margin
        self.move(x, y)

    def _reposition(self):
        """Keep the widget on-screen; if attached, snap flush to the top edge."""
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left()), geo.right() - self.width())
        y = geo.top() if self.attached_top else \
            min(max(self.y(), geo.top()), geo.bottom() - self.height())
        self.move(x, y)

    # ---- right-click options -----------------------------------------------
    def set_skin(self, name):
        if name not in SKINS:
            return
        top_left = self.pos()
        self.skin = SKINS[name]()
        self._resize_for_skin()
        self.move(top_left)
        self._reposition()
        self._sync_animation()
        self.update()

    def set_orientation(self, vertical):
        if vertical == self.vertical:
            return
        top_left = self.pos()
        self.vertical = vertical
        self._resize_for_skin()
        self.move(top_left)          # keep the corner stable, then tidy up
        self._reposition()
        self.update()

    def set_scale(self, scale):
        top_left = self.pos()
        self.scale = scale
        self._resize_for_skin()
        self.move(top_left)
        self._reposition()
        self.update()

    def set_attached(self, attached):
        self.attached_top = bool(attached)
        self._reposition()

    def set_opacity(self, value):
        self.setWindowOpacity(max(0.2, min(1.0, value)))

    def set_always_on_top(self, on):
        self.on_top = bool(on)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.on_top)
        self.show()  # re-applies window flags

    def set_force_all_spaces(self, on):
        """macOS: also float over other apps' fullscreen Spaces (private API)."""
        self.force_all_spaces = bool(on)
        self._tune_native()

    # ---- "point me to where Claude runs" -----------------------------------
    def set_focus_spec(self, spec):
        """Store how to raise Claude's window. Keys: app / title / cmd."""
        for key in ("app", "title", "cmd"):
            if key in spec:
                value = (spec[key] or "").strip()
                if value:
                    self.focus_spec[key] = value
                else:
                    self.focus_spec.pop(key, None)

    def focus_target_text(self):
        if self.focus_spec.get("cmd"):
            return "command: " + self.focus_spec["cmd"]
        if self.focus_spec.get("app"):
            return "app: " + self.focus_spec["app"]
        if self.focus_spec.get("title"):
            return "window: " + self.focus_spec["title"]
        return "not set"

    def focus_window(self):
        """Bring the window where Claude is running to the front."""
        spec = self.focus_spec
        cmd = None
        if spec.get("cmd"):
            cmd = spec["cmd"]
        elif platform.system() == "Darwin" and spec.get("app"):
            app = spec["app"].replace('"', '')
            script = 'tell application "%s" to activate' % app
            cmd = "osascript -e " + shlex.quote(script)
        elif platform.system() == "Linux" and spec.get("title"):
            t = shlex.quote(spec["title"])
            cmd = (f'wmctrl -a {t} 2>/dev/null || '
                   f'xdotool search --name {t} windowactivate 2>/dev/null')
        if not cmd:
            self._toast("Right-click → Claude window → Set target")
            return
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception:
            self._toast("Couldn't focus the window")

    def _toast(self, text):
        QToolTip.showText(self.mapToGlobal(self.rect().center()), text, self)

    def _prompt_focus_target(self):
        if platform.system() == "Darwin":
            hint = ("macOS — enter the terminal app name where Claude runs\n"
                    "(e.g. Terminal, iTerm, Visual Studio Code, Ghostty).\n"
                    "Advanced: prefix with ! for a raw shell command.")
            current = self.focus_spec.get("app", "")
        else:
            hint = ("Linux/X11 — enter part of the window title to match\n"
                    "(requires wmctrl or xdotool; Wayland may block this).\n"
                    "Advanced: prefix with ! for a raw shell command.")
            current = self.focus_spec.get("title", "")
        if self.focus_spec.get("cmd"):
            current = "!" + self.focus_spec["cmd"]
        text, ok = QInputDialog.getText(self, "Claude window target", hint, text=current)
        if not ok:
            return
        text = text.strip()
        self.focus_spec = {}
        if text.startswith("!"):
            self.set_focus_spec({"cmd": text[1:].strip()})
        elif text:
            key = "app" if platform.system() == "Darwin" else "title"
            self.set_focus_spec({key: text})

    # ---- state -------------------------------------------------------------
    def apply_state(self, state, flash, label):
        state = state if state in VALID_STATES else "off"
        self.state = state
        self.flash = bool(flash)
        self.label = label or ""
        self.setToolTip(self.label or state.capitalize())
        self._sync_animation()
        self.update()

    # ---- painting ----------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self.skin.paint(p, self)

    # ---- dragging & click --------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.frameGeometry().topLeft()
            self._moved = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is None or not (event.buttons() & Qt.LeftButton):
            return
        gp = event.globalPosition().toPoint()
        if (gp - self._press_global).manhattanLength() > 4:
            self._moved = True
        if not self._moved:
            return
        target = gp - self._drag_offset
        if self.attached_top:
            geo = QGuiApplication.primaryScreen().availableGeometry()
            x = min(max(target.x(), geo.left()), geo.right() - self.width())
            self.move(x, geo.top())
        else:
            self.move(target)
        event.accept()

    def mouseReleaseEvent(self, event):
        was_click = (event.button() == Qt.LeftButton
                     and self._drag_offset is not None and not self._moved)
        self._drag_offset = None
        self._press_global = None
        if was_click:
            self.focus_window()   # a plain left-click jumps to Claude's window

    # ---- right-click menu --------------------------------------------------
    def contextMenuEvent(self, event):
        menu = QMenu(self)

        # Skin (exclusive)
        skmenu = menu.addMenu("Skin")
        skgroup = QActionGroup(self)
        skgroup.setExclusive(True)
        for cls in SKIN_LIST:
            a = QAction(cls.label, self)
            a.setCheckable(True)
            a.setChecked(self.skin.name == cls.name)
            a.triggered.connect(lambda _c=False, n=cls.name: self.set_skin(n))
            skgroup.addAction(a)
            skmenu.addAction(a)

        # Orientation (exclusive) — only some skins use it
        omenu = menu.addMenu("Orientation")
        omenu.setEnabled(self.skin.supports_orientation)
        ogroup = QActionGroup(self)
        ogroup.setExclusive(True)
        for name, vert in (("Horizontal", False), ("Vertical", True)):
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(self.vertical == vert)
            a.triggered.connect(lambda _c=False, v=vert: self.set_orientation(v))
            ogroup.addAction(a)
            omenu.addAction(a)

        # Size (exclusive)
        smenu = menu.addMenu("Size")
        sgroup = QActionGroup(self)
        sgroup.setExclusive(True)
        for name, sc in self.SIZE_PRESETS.items():
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(abs(self.scale - sc) < 1e-6)
            a.triggered.connect(lambda _c=False, s=sc: self.set_scale(s))
            sgroup.addAction(a)
            smenu.addAction(a)

        # Opacity (exclusive)
        opmenu = menu.addMenu("Opacity")
        opgroup = QActionGroup(self)
        opgroup.setExclusive(True)
        for name, val in (("100%", 1.0), ("85%", 0.85), ("70%", 0.7), ("50%", 0.5)):
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(abs(self.windowOpacity() - val) < 1e-3)
            a.triggered.connect(lambda _c=False, v=val: self.set_opacity(v))
            opgroup.addAction(a)
            opmenu.addAction(a)

        # Attach to top bar (toggle)
        attach = QAction("Attach to top bar", self)
        attach.setCheckable(True)
        attach.setChecked(self.attached_top)
        attach.triggered.connect(lambda checked: self.set_attached(checked))
        menu.addAction(attach)

        # Always on top (toggle)
        ontop = QAction("Always on top", self)
        ontop.setCheckable(True)
        ontop.setChecked(self.on_top)
        ontop.triggered.connect(lambda checked: self.set_always_on_top(checked))
        menu.addAction(ontop)

        # Show on all Spaces & over fullscreen apps (macOS only; private Spaces API)
        if platform.system() == "Darwin":
            fs = QAction("Show on all Spaces && over fullscreen apps", self)
            fs.setCheckable(True)
            fs.setChecked(self.force_all_spaces)
            fs.triggered.connect(lambda checked: self.set_force_all_spaces(checked))
            menu.addAction(fs)

        menu.addSeparator()

        # Claude window: left-click jumps here; configure the target here
        cmenu = menu.addMenu("Claude window")
        focus_now = QAction("Focus now", self)
        focus_now.triggered.connect(self.focus_window)
        cmenu.addAction(focus_now)
        set_target = QAction("Set target…", self)
        set_target.triggered.connect(self._prompt_focus_target)
        cmenu.addAction(set_target)
        cmenu.addSeparator()
        current = QAction("Current: " + self.focus_target_text(), self)
        current.setEnabled(False)
        cmenu.addAction(current)

        menu.addSeparator()
        info = QAction(f"Control port: {self.port}", self)
        info.setEnabled(False)
        menu.addAction(info)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)
        menu.exec(event.globalPos())


# --------------------------------------------------------------------------- #
#  HTTP control server
# --------------------------------------------------------------------------- #
def make_handler(bridge):
    state_box = {"state": "off", "flash": False, "label": ""}
    focus_box = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # keep the console quiet
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _update(self, state, flash, label):
            if state not in VALID_STATES:
                self._send(400, {"error": f"unknown state '{state}'",
                                 "valid": list(VALID_STATES)})
                return
            state_box.update(state=state, flash=bool(flash), label=label or "")
            bridge.apply.emit(state, bool(flash), label or "")
            self._send(200, dict(state_box))

        def _update_focus(self, spec):
            clean = {k: v for k, v in spec.items() if k in ("app", "title", "cmd") and v}
            focus_box.clear()
            focus_box.update(clean)
            bridge.focus.emit(clean)
            self._send(200, {"focus": dict(focus_box)})

        @staticmethod
        def _truthy(v):
            return str(v).lower() in ("1", "true", "yes", "on")

        def do_GET(self):
            u = urlparse(self.path)
            path = u.path.strip("/").lower()
            q = parse_qs(u.query)
            flash = self._truthy(q.get("flash", ["0"])[0])
            label = q.get("label", [""])[0]
            if path in VALID_STATES:
                self._update(path, flash, label)
            elif path == "focus":
                self._update_focus({k: q.get(k, [""])[0] for k in ("app", "title", "cmd")})
            elif path in ("", "state", "status"):
                self._send(200, dict(state_box))
            else:
                self._send(404, {"error": "not found",
                                 "try": ["/red", "/yellow", "/green", "/off",
                                         "/state", "/focus"]})

        def do_POST(self):
            u = urlparse(self.path)
            path = u.path.strip("/").lower()
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            data = {}
            if raw:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}
            if path == "focus":
                self._update_focus(data)
                return
            state = (data.get("state") or (path if path in VALID_STATES else "")).lower()
            flash = data.get("flash", False)
            label = data.get("label", "")
            if not state:
                self._send(400, {"error": "no state given"})
                return
            self._update(state, flash, label)

    return Handler


def start_server(bridge, port):
    handler = make_handler(bridge)
    server = ThreadingHTTPServer((HOST, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="Promptly — a movable status light.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--skin", default="spinner", choices=list(SKINS),
                        help="visual style (default: spinner)")
    parser.add_argument("--vertical", action="store_true",
                        help="vertical layout (skins that support orientation)")
    parser.add_argument("--state", default="green", choices=VALID_STATES)
    parser.add_argument("--scale", type=float, default=1.0,
                        help="size multiplier (0.7 small, 1.0 medium, 1.4 large, 1.8 xl)")
    parser.add_argument("--attach-top", action="store_true",
                        help="start snapped/locked to the top edge of the screen")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    bridge = Bridge()
    widget = TrafficLight(vertical=args.vertical, port=args.port,
                          scale=args.scale, skin=args.skin)
    bridge.apply.connect(widget.apply_state)
    bridge.focus.connect(widget.set_focus_spec)
    widget.apply_state(args.state, False, "")
    if args.attach_top:
        widget.set_attached(True)
    widget.show()

    try:
        start_server(bridge, args.port)
    except OSError as exc:
        print(f"Could not start control server on {HOST}:{args.port} ({exc}).\n"
              f"Is another instance already running? Try --port <other>.", file=sys.stderr)
        return 1

    print(f"Promptly running ({args.skin}) — control server on "
          f"http://{HOST}:{args.port}")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
