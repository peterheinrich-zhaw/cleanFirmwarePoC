# Wi-Fi: SV6355 USB module (VID:PID 8065:6011)

## What it is (labels as in the provenance document)

| Fact | Class | Source |
|---|---|---|
| USB device `8065:6011`, "iComm Semiconductor sv6x/sv32WiFi/BT USB adapter" | A | `dmesg` of our own kernel |
| VBUS for the port is switched by GPIO47, active high | B (+A) | stock `fh_usb` platform data; confirmed on the board with a scope |
| Stock driver is the FMAC (cfg80211) variant: imports only `cfg80211_*`, `wiphy_*`, `usb_*` | B | symbol table of the stock `ssv6x5x.ko` |
| Every module parameter of the stock `.ko` exists in OpenIPC/ssv6x5x; the Armbian `sv6256p` tree has none of them | B/D | `.modinfo` vs upstream source |
| Stock firmware is version 13033, upstream tree expects 11204 | B/D | stock `.bin` / `ssv_firmware_version.h` |

## Decision

We use the OpenIPC/ssv6x5x tree **with its own firmware**, as one consistent pair.
Nothing from the stock image (`ssv6x5x.ko`, firmware `.bin`, `.cfg`, hostapd,
wpa_supplicant) enters the project. The stock files only serve as comparison.

**Never mix driver and firmware versions**: on a mismatch the driver executes
`BUG_ON(1)` (`ssvdevice/init.c`, unless `ignore_firmware_version=1`).

## Third-party status and open licence question

* Upstream carries `MODULE_LICENSE("Dual BSD/GPL")` / `"GPL"`, but no `LICENSE` file
  at its root; only some files have SPDX lines. Treated as: *separately labelled
  third-party component, licence unresolved*. Not vendored, not redistributed here.
* The firmware is embedded as C arrays (`include/ssv6x5x-sw*.h`) and compiled into
  the module. The module binary therefore contains the firmware blob. It is a
  build output that the user produces themselves; we do not publish it.
* `patches/ssv6x5x/` are derived works of the upstream sources and keep their
  licence; they are kept in their own directory for that reason.
* Upstream also ships ELF files with debug symbols in `image/`. They are not used
  and not copied.

## What the three patches do (against 6ff6317, tested with Linux 6.18.56)

1. `build`: `EXTRA_CFLAGS` is ignored by modern kbuild; use `ccflags-y`.
2. `fmac`: cfg80211 callback signatures (link_id, radio_idx, ...), station
   capability fields moved to `link_sta_params`, renamed HE macro.
3. `core`: `eth_hw_addr_set()`, const-correct helper, `timer_delete_sync()`,
   `timer_container_of()`, `pde_data()`.

The patches target Linux 6.18; they are not backward compatible.
Result: 44 lines added, builds without errors (`LLVM=1 ARCH=arm`), 484 KB stripped.
**Not yet tested on hardware.**

## Regulatory behaviour

`custregd` defaults to 0: the driver does not install its own permissive rules and
the kernel's world regulatory domain applies (strict: no channels 12-14,
passive scanning on some). Leave it at 0. `custregd=1` is for lab use only
and prints a caution on load. The stock RF tuning in its `ssv6x5x-wifi.cfg`
(TX power tables, calibration) is not carried over.

## Using it

```
make wifi-fetch          # clone pinned commit, apply patches
make kernel              # configured kernel with wifi.fragment (CFG80211=y, MODULES=y)
make wifi                # build/strip ssv6x5x.ko
make wifi-stage          # copy into rootfs/root, then rebuild rootfs + kernel
```

On the camera (VBUS must be on, see `lsusb`):

```
insmod /lib/modules/ssv6x5x.ko stacfgpath=/etc/ssv6x5x-wifi.cfg
dmesg | tail -60
ip link
```

Firmware is taken from the embedded array. To load from a file instead, pass
`cfgfirmwarepath=/lib/firmware/` (name from `firmware_name` in the cfg).
The default is `ssv6x5x-sw.bin` (Wi-Fi only); the stock board used the
`sw_nimble` image (Wi-Fi + BLE); not needed for a first test.
