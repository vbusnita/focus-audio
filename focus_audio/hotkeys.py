"""Optional global hotkeys (macOS). Requires pynput + Accessibility permission."""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Set


# Minimum gap between accepted hotkey actions (stops key-repeat multi-fire).
DEBOUNCE_S = 0.45


def start_hotkey_listener(on_action: Callable[[str], None]) -> None:
    """
    Ctrl+Shift+Space → toggle
    Ctrl+Shift+R     → restart
    Ctrl+Shift+.     → skip
    Ctrl+Shift+B     → rebrief
    Ctrl+Shift+M     → mode toggle

    Fires **once per chord** (edge-triggered + debounced). Holding the combo
    or OS key-repeat must not stack multiple play/pause actions.
    """
    try:
        from pynput import keyboard
    except ImportError as e:
        raise RuntimeError(
            "pynput not installed. pip install pynput  (or run without hotkeys)"
        ) from e

    current: Set[object] = set()
    state = {
        "latched": False,  # True while a chord is considered held
        "last_fire_at": 0.0,
        "last_action": None,  # type: Optional[str]
    }
    lock = threading.Lock()

    def normalize(key) -> str:
        if key in (
            keyboard.Key.ctrl,
            keyboard.Key.ctrl_l,
            keyboard.Key.ctrl_r,
        ):
            return "ctrl"
        if key in (
            keyboard.Key.shift,
            keyboard.Key.shift_l,
            keyboard.Key.shift_r,
        ):
            return "shift"
        if key == keyboard.Key.space:
            return "space"
        try:
            if hasattr(key, "char") and key.char:
                return key.char.lower()
        except AttributeError:
            pass
        name = getattr(key, "name", None)
        if isinstance(name, str):
            return name.lower()
        return str(key)

    def active_action() -> Optional[str]:
        parts = {normalize(k) for k in current}
        if "ctrl" not in parts or "shift" not in parts:
            return None
        has_period = (
            "." in parts
            or "period" in parts
            or any(getattr(k, "char", None) == "." for k in current)
        )
        if "space" in parts:
            return "toggle"
        if "r" in parts:
            return "restart"
        if has_period:
            return "skip"
        if "b" in parts:
            return "rebrief"
        if "m" in parts:
            return "mode"
        return None

    def maybe_fire() -> None:
        action = active_action()
        now = time.monotonic()
        with lock:
            if action is None:
                state["latched"] = False
                return
            # Still holding the same chord — ignore key-repeat / extra modifiers.
            if state["latched"]:
                return
            if (
                now - float(state["last_fire_at"]) < DEBOUNCE_S
                and action == state["last_action"]
            ):
                return
            state["latched"] = True
            state["last_fire_at"] = now
            state["last_action"] = action
        try:
            on_action(action)
        except Exception as e:  # noqa: BLE001 — never kill the listener
            print(f"focus-audio hotkey action error: {e}", flush=True)

    def on_press(key) -> None:
        current.add(key)
        maybe_fire()

    def on_release(key) -> None:
        current.discard(key)
        if active_action() is None:
            with lock:
                state["latched"] = False

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
    start_hotkey_listener._listener = listener  # type: ignore[attr-defined]
