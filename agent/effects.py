def run_effect(stick, leds, effect, params, cancel_event, led_state):
    if effect == "off":
        _off(stick, leds, led_state)
    elif effect == "solid":
        _solid(stick, leds, led_state)
    elif effect == "pulse":
        _pulse(stick, leds, params, cancel_event, led_state)
    elif effect == "blink":
        _blink(stick, leds, params, cancel_event, led_state)
    elif effect == "morph":
        _morph(stick, leds, params, cancel_event, led_state)


def _set_led(stick, index, r, g, b, led_state):
    stick.set_color(channel=0, index=index, red=r, green=g, blue=b)
    led_state[index] = (r, g, b)


def _off(stick, leds, led_state):
    for led in leds:
        _set_led(stick, led["index"], 0, 0, 0, led_state)


def _solid(stick, leds, led_state):
    for led in leds:
        _set_led(stick, led["index"], led["r"], led["g"], led["b"], led_state)


def _pulse(stick, leds, params, cancel_event, led_state):
    duration = params.get("duration", 1000)
    steps = params.get("steps", 50)
    repeats = params.get("repeats", 0)
    step_delay = (duration / 1000) / (steps * 2)

    count = 0
    while repeats == 0 or count < repeats:
        for i in range(steps + 1):
            if cancel_event.is_set():
                return
            brightness = i / steps
            for led in leds:
                _set_led(stick, led["index"],
                         int(led["r"] * brightness),
                         int(led["g"] * brightness),
                         int(led["b"] * brightness),
                         led_state)
            cancel_event.wait(step_delay)
        for i in range(steps, -1, -1):
            if cancel_event.is_set():
                return
            brightness = i / steps
            for led in leds:
                _set_led(stick, led["index"],
                         int(led["r"] * brightness),
                         int(led["g"] * brightness),
                         int(led["b"] * brightness),
                         led_state)
            cancel_event.wait(step_delay)
        count += 1


def _blink(stick, leds, params, cancel_event, led_state):
    delay = params.get("delay", 500) / 1000
    repeats = params.get("repeats", 0)

    count = 0
    while repeats == 0 or count < repeats:
        if cancel_event.is_set():
            return
        for led in leds:
            _set_led(stick, led["index"], led["r"], led["g"], led["b"], led_state)
        if cancel_event.wait(delay):
            return
        for led in leds:
            _set_led(stick, led["index"], 0, 0, 0, led_state)
        if cancel_event.wait(delay):
            return
        count += 1


def _morph(stick, leds, params, cancel_event, led_state):
    duration = params.get("duration", 1000)
    steps = params.get("steps", 50)
    step_delay = (duration / 1000) / steps

    starts = {}
    for led in leds:
        idx = led["index"]
        starts[idx] = led_state.get(idx, (0, 0, 0))

    for i in range(1, steps + 1):
        if cancel_event.is_set():
            return
        t = i / steps
        for led in leds:
            idx = led["index"]
            sr, sg, sb = starts[idx]
            _set_led(stick, idx,
                     int(sr + (led["r"] - sr) * t),
                     int(sg + (led["g"] - sg) * t),
                     int(sb + (led["b"] - sb) * t),
                     led_state)
        cancel_event.wait(step_delay)
