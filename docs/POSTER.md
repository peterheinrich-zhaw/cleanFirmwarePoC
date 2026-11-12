# Open firmware for an IoT camera, reverse-engineered with AI

## Relevance
IoT devices ship with opaque vendor firmware built on outdated components. Replacing it:
- restores **supply-chain control**: we know and can audit every line that runs;
- makes **validation** possible on maintained, documented software;
- **extends device life**: hardware the manufacturer stopped supporting stays usable and secure instead of becoming e-waste.

## Goal
Reverse engineering was manual and slow, and did not scale to the many different IoT devices. We test whether AI can do it at scale. Case study: a pan/tilt Wi-Fi camera (Fullhan FH8626V200 SoC, SC2336 sensor). The AI disassembles, emulates and writes the drivers; a human runs the board tests (human in the loop).

## Results

| Component | Vendor firmware | Our firmware |
|---|---|---|
| Bootloader | U-Boot 2010.06 | U-Boot 2026.07 |
| Kernel | Linux 4.9.129 | Linux 6.18.56 + 31 patches |
| Camera | 8 binary modules, RTOS on co-processor, ~20 closed libraries, 5.8 MB media server | One V4L2 driver: auto exposure and white balance, MJPEG at 30 fps |
| ISP tuning | Vendor tables | Own values from bisection, no vendor data |
| Motors, IR | Binary module, custom ioctls | Standard sysfs, gpio-leds, gpio-keys |
| Application | Closed media server | BusyBox, HTTP stream with pan/tilt and day/night |

All of it fits in 6 MB of flash and uses standard Linux interfaces.

## Limitations
- One device only; proof of concept.
- No H.264 (MJPEG only), no audio.
- Basic application.
- A human still flashes, observes and reports.

## Outlook
Replace the human with **hardware in the loop**: automated flashing, power cycling, serial console, and camera and motor sensing. AI can then reverse-engineer devices without human intervention.
