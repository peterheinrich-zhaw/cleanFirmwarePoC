# Actuators and board I/O

Pan/tilt motors, IR-cut filter, white and IR LEDs and the reset key of the
FH8626V200 camera board, and how our kernel drives them.

## GPIO and pad map

The GPIO numbers come from the stock `productinfo/hardware.txt`. The pads and
their functions come from the `fh_pinctrl` table in the stock bootloader:
entries `{name, 0, packed}` with pad in bits 7:0, register offset in bits 15:8
and function in bits 23:20. All of these pads are switched to function 1
(GPIO); pad register value `0x01001000`, no pulls. Every line was checked on
the board with devmem before the drivers were written.

| Element | GPIO (port, bit) | Pad | Default function of the pad | Result on the board |
|---|---|---|---|---|
| Pan motor, coils 1–4 (stock `motorH`) | 0–3 (porta0 0–3) | 20–23 | ARM JTAG | works |
| Tilt motor, coils 1–4 (stock `motorV`) | 37, 38, 41, 42 (porta1 5, 6, 9, 10) | 13–16 | I2C1 / UART2 | works |
| IR-cut, filter out (night) (`ircut_pos`) | 43 (porta1 11) | 18 | PWM0 | works |
| IR-cut, filter in (day) (`ircut_neg`) | 4 (porta0 4) | 24 | ARM JTAG TMS | works |
| White LED | 39 (porta1 7) | 11 | UART1_TX | works, active high |
| IR LED | 40 (porta1 8) | 12 | UART1_RX | works, active high |
| Reset key | 44 (porta1 12) | 19 | PWM1 | reads 1, and 0 when pressed |
| Light sensor (`lightSensor` 26) | 26 | 38 | SADC_XAIN0 | **not fitted on this board** |

Stock motor settings (`domeJson.cfg`): `hmaxstep 8100`, `vmaxstep 1140`,
`hspeed 900`, `vspeed 600`. `data/isvp_motor_local` holds 2025 / 660, which
looks like the last position. Pads 13–16 and 20–23 also offer a hardware
stepper unit (functions STM_H_0..3 / STM_V_0..3, with the H/V names swapped
against `hardware.txt`). The stock firmware does not use it and neither do we.

## Drivers (patches/linux 0027–0030)

**Steppers** (`gpio-stepper`, `/sys/class/stepper/{pan,tilt}/`):

- Half-steps from an hrtimer, with a linear ramp from 200 half-steps/s over
  32 ticks.
- The coils are switched off 20 ms after the last step.
- Positions and speeds are always in half-steps.

| Attribute | Meaning |
|---|---|
| `target` | Write N to move to N, or +N/-N to move relative. Clamped to 0..max_steps; the move runs in the background. |
| `position` | Current position. Writable only while idle, to restore a saved position without moving. |
| `moving` | 1 while the motor runs. Can be waited on with poll(); notified at the end of a move. |
| `stop` | Decelerate and stop. |
| `home` | Run max_steps towards 0 against the end stop, then set 0. |
| `speed` | Half-steps/s, up to 4000. |
| `max_steps` | Travel range. |
| `mode` | `half` (default) or `full` (two-coil). |

**IR-cut** (`gpio-ircut`, `/sys/devices/platform/ircut/mode`): writing `day`
or `night` sends a 150 ms pulse on the matching H-bridge input, then both
inputs go low. The driver switches to day at probe.

**LEDs**: `gpio-leds`, `/sys/class/leds/{white,ir}/brightness`.

**Reset key**: `gpio-keys`, `KEY_RESTART` on `/dev/input/event0`, 20 ms
debounce.

## Results on the board

- **Motor timing.** Stepping from the shell with devmem was uneven, because
  each step started separate processes. Stepping from the hrtimer is smooth.
- **Half-step mode** runs smoothly enough, so it stays the default.
- **Speed.** We keep the stock speeds of 900 (pan) and 600 (tilt); the 1500/s
  test was faster than wanted.
- **IR-cut.** The first guess was swapped. A pulse on GPIO4 puts the filter in
  (day); GPIO43 takes it out (night).
- **Light sensor.** None is fitted. Day/night has to be decided from the image,
  for example from the AE gain and exposure.

## cam-http and boot

`/etc/init.d/rcS` (started in the background by `/init`):
- starts `cam-http` (log in `/tmp/cam-http.log`)
- homes both axes, then moves them to the centre

cam-http endpoints, besides `/stream` and `/snapshot.jpg`:

| Path | Function |
|---|---|
| `/` | Page from `/usr/share/cam-http/index.html`: stream, pan/tilt pad (hold to move, release to stop), centre, home, Auto/Day/Night |
| `/status` | JSON: positions, targets, ranges, day/night state, exposure, gain, light |
| `/ptz?pan=N&tilt=N` | Move to N (absolute) or by ±N (relative), in half-steps |
| `/ptz?stop=1`, `home=1`, `center=1` | Stop both axes, home both, or centre both |
| `/ir?mode=auto\|day\|night` | Day/night control |

**Day/night.** There is no light sensor, so the decision uses
light = exposure lines × analogue gain / 32, read from the sensor subdevice.
Its maximum is about 1080 × 16 = 17280.

- **Night:** light ≥ 8000 (`-N`) for 5 s. IR-cut out, IR LED on, saturation 0
  (the new `V4L2_CID_SATURATION` of fh8626-camera).
- **Day:** light ≤ 600 (`-D`) for 10 s.
- **Hold time:** at least 30 s between two switches.
- **IR feedback:** the IR LED lights the scene in night mode. If the camera
  falls back to night within 120 s after switching to day, the day threshold
  is halved (minimum 50).
- **Only while streaming:** decisions run only while the camera streams.
  The thresholds are first guesses; `/status` shows the live value for
  tuning.
