import logging
import queue
import threading

import usb.core
from blinkstick import blinkstick

from agent.effects import run_effect

log = logging.getLogger(__name__)

_STOP = object()


class BlinkStickDriver:
    def __init__(self):
        self._stick = None
        self._queue = queue.Queue()
        self._cancel_event = threading.Event()
        self._led_state = {}
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    @property
    def device(self):
        return self._stick

    def find_device(self):
        self._stick = blinkstick.find_first()
        if self._stick:
            log.info("Found BlinkStick: %s", self._stick.get_serial())
        else:
            log.warning("No BlinkStick device found")
        return self._stick

    def device_info(self):
        if not self._stick:
            return {"present": False}
        return {
            "present": True,
            "serial": self._stick.get_serial(),
            "leds": 2,
        }

    def execute(self, command):
        self._cancel_event.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(command)

    def shutdown(self):
        self._cancel_event.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._stick:
            self._queue.put({
                "leds": [
                    {"index": 0, "r": 40, "g": 20, "b": 0},
                    {"index": 1, "r": 40, "g": 20, "b": 0},
                ],
                "effect": "solid",
                "params": {},
            })
        self._queue.put(_STOP)
        self._worker.join(timeout=5.0)

    def _run(self):
        while True:
            command = self._queue.get()
            if command is _STOP:
                return
            self._cancel_event.clear()
            if not self._stick:
                continue
            try:
                leds = command.get("leds", [])
                effect = command.get("effect", "solid")
                params = command.get("params", {})
                run_effect(self._stick, leds, effect, params,
                           self._cancel_event, self._led_state)
            except usb.core.USBError:
                log.exception("USB error — device may be disconnected")
                self._stick = None
            except Exception:
                log.exception("Effect execution failed")
