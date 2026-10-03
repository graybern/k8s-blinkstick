import logging
import queue
import threading
import time

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
        self._sequence_thread = None
        self._sequence_cancel = threading.Event()

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
        self.stop_sequence()
        self._cancel_event.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(command)

    def play_sequence(self, payload):
        self.stop_sequence()
        self._cancel_event.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        start_at = payload.get("start_at", time.time())
        beat_ms = payload.get("beat_ms", 500)
        loop = payload.get("loop", False)
        beats = payload.get("beats", [])

        if not beats:
            log.warning("play_sequence with empty beats")
            return

        self._sequence_cancel.clear()
        self._sequence_thread = threading.Thread(
            target=self._run_sequence,
            args=(start_at, beat_ms, loop, beats),
            daemon=True,
        )
        self._sequence_thread.start()
        log.info("Sequence started: %d beats at %dms, loop=%s, start_at=%.3f",
                 len(beats), beat_ms, loop, start_at)

    def stop_sequence(self):
        if self._sequence_thread and self._sequence_thread.is_alive():
            self._sequence_cancel.set()
            self._sequence_thread.join(timeout=2.0)
            log.info("Sequence stopped")
        self._sequence_thread = None

    def shutdown(self):
        self.stop_sequence()
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

    def _run_sequence(self, start_at, beat_ms, loop, beats):
        if not self._stick:
            log.warning("Sequence aborted — no device")
            return

        beat_interval = beat_ms / 1000.0
        now = time.time()

        if start_at > now:
            wait = start_at - now
            if self._sequence_cancel.wait(wait):
                return
        elif now - start_at > beat_interval:
            skip = int((now - start_at) / beat_interval)
            start_at += skip * beat_interval
            log.info("Skipped %d past beats", skip)

        beat_index = 0
        next_beat_time = start_at

        while True:
            if self._sequence_cancel.is_set():
                return

            now = time.time()
            if now < next_beat_time:
                if self._sequence_cancel.wait(next_beat_time - now):
                    return

            beat = beats[beat_index]
            try:
                leds = beat.get("leds", [])
                effect = beat.get("effect", "solid")
                params = beat.get("params", {})
                for led in leds:
                    self._stick.set_color(
                        channel=0,
                        index=led["index"],
                        red=led.get("r", 0),
                        green=led.get("g", 0),
                        blue=led.get("b", 0),
                    )
                    self._led_state[led["index"]] = (
                        led.get("r", 0), led.get("g", 0), led.get("b", 0),
                    )
            except usb.core.USBError:
                log.exception("USB error during sequence")
                self._stick = None
                return
            except Exception:
                log.exception("Beat %d execution failed", beat_index)

            beat_index += 1
            next_beat_time += beat_interval

            if beat_index >= len(beats):
                if loop:
                    beat_index = 0
                else:
                    return
