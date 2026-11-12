# Camera: initial findings from the stock firmware

Status: analysis only, no code yet. Every fact here is class **B** (analysis of
the stock firmware, see the provenance classes in
[Initial_Bringup_Linux_to_Shell.md](Initial_Bringup_Linux_to_Shell.md)) unless
marked otherwise. Source: `orig_firmware/rootfs_booted.tar.gz`, mainly
`mnt/flash/`. Nothing below has been observed on our own kernel yet.

## The product

| Fact | Source |
|---|---|
| SoC FH8626V200, model `ZS2031-T`, Tuya cloud firmware `V1.0.14-TY-260410` | `productinfo/hardware.txt`, `customCfg.txt`, `data/Version.txt` |
| SDK app built 2026-04-09 (`/home/du/share/version/20260409/sdk_app_fuhan/...`) | string in `sdk_app` |
| Sensor: **SmartSens SC2336** (`SC2336P ver20240229`), 1920x1080 over MIPI | `productinfo/sensor_info`, `autorun.sh` (`DEVICEID=sc2336`), `sdk_app` strings |
| `sdk_app` also supports GC1084 (720p) and MIS2032 (1080p) as alternative sensors | `sdk_app` strings, `mediaserver/*_attr.hex` |
| Pan/tilt dome: two 4-wire stepper motors, IR-cut filter, IR and white LEDs | `hardware.txt`, `motor_gpio.ko` |

Board GPIOs from `hardware.txt` (vendor numbering, still to be verified on the board):

| Function | GPIO |
|---|---|
| Sensor reset (from `autorun.sh`, see the load order below) | 13 |
| IR-cut + / - | 43 / 4 |
| IR LED, white LED | 40, 39 |
| Light sensor (day/night) | 26 |
| Speaker amplifier enable | 27 |
| Reset key | 44 |
| Motor H (4 phases) | 0, 1, 2, 3 |
| Motor V (4 phases) | 37, 38, 41, 42 |

## Software stack of the stock firmware

The stock kernel is **4.9.129** (vermagic of all modules). `mnt/flash/` holds three parts:

- `driver/driver/`: kernel modules and the ARC firmware
- `driver/libs/`: user-space MPI libraries
- `Server/mediaserver/`: `sdk_app` (the media pipeline) plus the sensor tuning files

`Server/LINUX/hiapp` is the Tuya/cloud application. It is not relevant to the camera.

### Load order (`Server/script/autorun.sh`)

```
insmod vmm.ko mmz=anonymous,0,0xa2600000,26M anony=1   # DDR < 64M branch (taken)
set_gpio 13 0                                          # sensor reset asserted
insmod xbus_rpc.ko fn=rtthread_arc.bin fa=0xbff80000   # load + start the ARC core
set_gpio 13 1                                          # sensor reset released
insmod media_process.ko
insmod isp.ko
insmod enc.ko jpeg.ko bgm.ko nna.ko
insmod extdrv/motor_gpio.ko
echo adv_vpu        > /proc/driver/media_process
echo cap_0_2304_1296 > /proc/driver/vpu
echo buf_0_1        > /proc/driver/vpu
sdk_app &
```

The vendor bootargs, which `autorun.sh` writes for the `iot_device` case, contain
`mem=38M` and
`mtdparts=spi0.0:256k(uboot),64k(uboot-env),1536k(kernel),832k(rootfs),2304k(driver),832k(cfg),-(appfs)`.
Linux therefore owns 0xA0000000 to 0xA2600000, and the MMZ (media memory pool)
takes the 26 MiB above it, up to 0xA3C00000. That leaves 4 MiB at the top of the
64 MiB, presumably for the ARC.

### Kernel modules

All modules are ARM, built for 4.9.129, and **not stripped**, so they are good
Ghidra targets.

| Module | Licence | Role | Depends on |
|---|---|---|---|
| `vmm.ko` | GPL | MMZ allocator for physically contiguous media buffers | — |
| `xbus_rpc.ko` | GPL | Loads `rtthread_arc.bin`, kicks and resets the ARC, mailbox/IRQ RPC (`xbus_*`, `vbus_mesg_*`), `get_vmm_range` | vendor kernel: `fh_pmu_arxc_kickoff/reset`, `fh_pmu_arxc_*_INT_RAWSTAT` |
| `media_process.ko` | Proprietary | Common media core: object/bind graph, buffer pools, clocks, chip info, **encoder rate control** (`fh_rc_*`), H.264/H.265 slice/NAL writers | `xbus_rpc` (`vbus_mesg_*`), vendor `fh_clk_*`, `fh_pmu_get_reg`, `fh_get_chipid` |
| `isp.ko` | Proprietary | **ISP + MIPI CSI receiver + VPU** (scaler, crop, OSD, masks, sub-streams): `isp_*`, `Mipi_*`, `mipiWrap_*`, `vpu_*`; `/dev/isp` | `media_process` |
| `enc.ko` | Proprietary | H.264/H.265 encoder hardware ("hhe") | `media_process` |
| `jpeg.ko`, `bgm.ko`, `nna.ko` | Proprietary | JPEG, background model (motion), neural-net accelerator (`model/persondet.nbg`) | `media_process` |
| `motor_gpio.ko` | GPL | Pan/tilt steppers | vendor `fh_gpio_set` |

None of them loads on our 6.18 kernel as it stands. Apart from the API changes
between 4.9 and 6.18, they import vendor kernel symbols that do not exist
upstream: `fh_clk_*`, `fh_pmu_*`, `fh_irq_create_mapping`, `fh_setscheduler`,
`fh_get_chipid` and `fh_gpio_set`.

### The ARC core (`rtthread_arc.bin`, 226 KiB)

- RT-Thread image, built on 2023-03-24, `[softcore] version: V1.0.0 3.0.0(build 000)`. The first word is `0x00000121`, and the image is loaded to `0xbff80000` by `xbus_rpc.ko`. It speaks an RPC protocol over `xbus`/`vbus`, with RX threads `xbus-rx%02d` and handlers registered in `rpc_register_table`.
- Its strings point to two jobs:
  - **Video-encoder "softcore"** work: the thread names `enc_fae0`, `enc_fae1`, `enc_fhe` and `v_venc`, plus `arc_sw_kick`.
  - **Audio DSP**: WebRTC AECM (echo cancellation), AGC and NS.
- **None of its strings mention the ISP, the sensor, MIPI, AE, AWB or I2C.**

So the earlier assumption that the ARC does the image processing does not hold
on the evidence so far. The ARC seems to be a co-processor for the encoder and
for audio. The image pipeline (sensor → MIPI → ISP → VPU) is driven from the ARM
side. This is only an inference from strings and still has to be confirmed in
Ghidra. However, `media_process.ko`, and through it `isp.ko`, depends on
`xbus_rpc`. With the stock modules, the ARC must therefore be running before the
ISP comes up.

The address `0xbff80000` lies outside the 64 MiB DRAM window at 0xA0000000 that
our DTS describes. It is not yet known whether this is ARC-local memory, a bus
alias of the free top 4 MiB of DRAM, or something else.

### User space: where the sensor driver lives

`sdk_app` is dynamically linked against uClibc and the MPI libraries in
`driver/libs/`:

- `libisp`/`libispcore`, the ISP API and the 3A algorithms, run on the ARM
- `libmipi`, plus `libdsp`, `libdbi`, `libadvapi_*`, `libvmm` and `libaudio_codec`

All of the libraries report `V1.0.0 3.0.0(build 000)`.

- **The SC2336 driver is compiled into `sdk_app` itself.** It covers the register tables (`SC2336P ver20240229`), `Sensor_Read`/`Sensor_Write` over **`/dev/i2c-0`**, and registration with the ISP through `API_ISP_SensorRegCb`. The ISP core then calls back into it through hooks: `isp_core_set_sensor_intt`, `_gain`, `_fmt`, `_frame_height`, `_mirror_flip` and `isp_core_set_mipi_lane_num_to_sensor`.
- The sensor clock comes from the SoC: `API_ISP_SetCisClk(CIS_CLK_36M)`, which uses `/dev/fh_clk_miscdev`.
- `libmipi` only opens `/dev/isp`, `mmap`s it and makes ioctls. The MIPI receiver itself is part of `isp.ko`.
- ISP init sequence, from the strings and `log/assert.txt`:
  1. `API_ISP_MemInit`
  2. `API_ISP_GetBinAddr`
  3. `API_ISP_SensorRegCb`
  4. `API_ISP_SetCisClk`
  5. `API_ISP_SetSensorFmt`
  6. `API_ISP_SensorInit`
  7. `API_ISP_Init`
  8. `API_ISP_LoadIspParam` (the `.hex` file)
  9. `API_ISP_Run`

  `FH_VPSS_SetViAttr` uses 1920x1080.

### Sensor tuning files (`Server/mediaserver/*_attr.hex`)

These are 4984-byte ISP parameter blobs, one day and one night variant per
sensor, loaded with `API_ISP_LoadIspParam`. The SC2336 day and night files
differ in 2396 bytes. Header words seen so far:

- 0x0780 and 0x0438, which is 1920x1080
- 0x1194 = 4500 and 0x04B0 = 1200. These may be the line length and frame
  length (HTS/VTS); that is a guess, unverified.

The format is the ISP's internal parameter layout. They are tuning data (black
level, gamma, CCM, NR and so on), not sensor register settings.

## What our tree is missing for the camera

Our DTS currently has only the CPU, GIC, timer, PMU, UART, GPIO and USB. Nothing
for the camera path is described yet:

- an **I2C controller** for bus 0, which the sensor sits on
- **clocks**: the CIS/MCLK 36 MHz output, and the ISP, VPU and MIPI clocks
- the **ISP/MIPI/VPU** register blocks and their interrupts. The GIC routing of
  SoC interrupts is itself still unverified (see the UART node).
- **reserved memory** for the media buffers and for the ARC
- pinmux for the sensor (MCLK, reset GPIO 13, I2C pins). The stock firmware uses
  a `/proc/driver/pinctrl` interface from the vendor kernel.

## Milestone 1 facts from the stock kernel

The stock kernel (`uImage` at flash 0x50000: XZ-compressed zImage, `Linux
version 4.9.129 ... #41 Wed Aug 7 19:33:38 CST 2024`) has no device tree. It is
a board-file kernel, so its static tables were decoded directly. All of this is
class B and not yet confirmed on the board.

Stock IRQ numbers are GIC interrupt IDs. UART0 is 62 there, and our DTS has
`GIC_SPI 30` for it, so SPI = ID − 32.

**Peripheral map** (`struct resource` tables, MMIO size 0x4000 each unless noted):

| Device | Base | IRQ (stock → `GIC_SPI`) |
|---|---|---|
| `fh_i2c.0` (I2C0, sensor bus) | 0xF0200000 | 43 → 11 |
| `fh_i2c.1` | 0xF0B00000 | 44 → 12 |
| `fh_i2c.2` | 0xF0100000 | 78 → 46 |
| `FH_GPIO.0` / `.1` | 0xF0300000 / 0xF4000000 | 58 → 26 / 72 → 40 |
| `fh_pwm` | 0xF0400000 | 68 → 36 |
| `fh_spi.0` / `.1`, `fh_spi_slave` | 0xF0500000 / 0xF0600000 / 0xF0640000 | 60 / 53 / 70 |
| `ttyS0` / `ttyS1` / `ttyS2` | 0xF0700000 / 0xF0800000 / 0xF1300000 | 62 / 63 / 73 |
| `fh_mci.0` / `.1` (SD/SDIO) | 0xE2000000 / 0xE2200000 | 74 / 75 |
| `fh_usb` | 0xE0700000 (1 MiB) | 71 |
| `fh_gmac` | 0xE0600000 | 76 |
| `fh_axi_dmac` | 0xE0300000 | 55 |
| `fh_aes`, `fh_efuse`, `fh_rtc`, `fh_sadc`, `fh_wdt` | 0xE8200000, 0xF1600000, 0xF1500000, 0xF1200000, 0xF0D00000 | 48, –, 65, 52, 34 |

The I2C controller is very likely a Synopsys DesignWare part, because the stock
kernel carries the `controller timed out` message of `i2c-designware`. Reading
`IC_COMP_TYPE` (+0xFC) on the board should return 0x44570140 if so.

**Clocks** (`fh_clks` table; register offsets relative to the PMU at 0xF0000000):

| Clock | Parent(s) | Divider | Gate |
|---|---|---|---|
| `osc_clk` | — (24 MHz) | — | — |
| `pll_cpu_pclk` | `osc_clk` | PLL regs 0x14 / 0x64[11:8] | — |
| `cpu_div15_clk` | `pll_cpu_pclk` | 0x38[31:24] | — |
| `i2c0_clk` | `cpu_div15_clk` | 0x34[21:16] | 0x1C bit 12 |
| `cis_clk_out` (sensor MCLK) | mux `osc_clk` / `pll_cpu_pclk` | 0x28[23:16] | 0x1C bit 23 |
| `mipi_dphy_clk` | `osc_clk` | — | 0x5C bit 6 |
| `mipi_cfg_clk` | `pll_cpu_pclk` | 0x28[13:8] | 0x5C bit 4 |
| `mipic_pclk_gate`, `mipi_wrap_gate` | — | — | 0x20 bit 4, 0x20 bit 29 |
| `pix_clk` | `pll_cpu_pclk` | 0x2C[27:24] | 0x20 bit 23 |
| `isp_aclk` | `pll_cpu_pclk` | 0x24[13:8] | — |

Each `fh_clks` entry is a composite of mux, divider and gate. The stock clock
code shows how each part works:

- **Divider:** rate = parent / (field + 1).
- **Mux:** the field value is the parent index. `cis_clk_out` uses PMU+0x0C
  bit 4: 0 = `osc_clk`, 1 = `pll_cpu_pclk`.
- **Gate:** the bit set to 1 means the clock is off (confirmed on the board,
  see below).
- **PLLs:** rate = 24 MHz · M / (N + 1) / (P + 1) / post. The fields are:
  - in the configuration register: M = [6:0], N = [12:8], P = [21:16] for the
    `pclk` output or [29:24] for `rclk`
  - in the second register: the post-divider field, where 0, 8, 12, 13, 14 and
    15 mean 1, 2, 4, 8, 16 and 32
  - CPU PLL: configuration at PMU+0x14, post-divider at PMU+0x64[11:8]
    (`pclk`) and [15:12] (`rclk`)
  - DDR PLL: configuration at 0x10, post-divider at 0x18

Rates with the register values read on our board:

| Clock | Rate |
|---|---|
| `pll_cpu_pclk` | 1500 MHz |
| `pll_cpu_rclk` | 1000 MHz |
| `pll_ddr` | 606 MHz |
| `cpu_div15_clk` | 100 MHz |
| `uart0_clk` | 16.667 MHz (matches the DTS value that works) |
| `i2c0_clk` | **50 MHz**, not the 100 MHz in our DTS, so SCL runs at about half the requested rate |

From `pll_cpu_pclk`, the stock `CIS_CLK_36M` can only be approximated:
1500 / 42 = 35.7 MHz. On our board, PMU+0x0C reads 0x00000001: bit 4 = 0, so
`cis_clk_out` comes from `osc_clk`. With divider 0 that gives **MCLK = 24 MHz**,
and the SC2336 answers at that rate. Bit 0 (the `arm_clk`/`ahb_clk` selector)
is 1, i.e. those two are on the PLL side.

The full table (70 clocks) is in the stock kernel at 0xC03CFEB0, as an array of
pointers to entries of 0x4C bytes:

| Offset | Field |
|---|---|
| +0x00 | name |
| +0x08 | flags |
| +0x10 / +0x14 | parents |
| +0x20 | fixed pre-divider |
| +0x28 / +0x2C | divider register / mask |
| +0x30 / +0x34 | gate register / mask |
| +0x40 / +0x44 | selector register / mask |
| +0x48 | default rate |

**Pinmux.** Pad registers sit at PMU + 0x80 + 4·pad. The stock kernel uses the
literal `0xfe090080`, its static mapping of PMU + 0x80. Function numbers come
from the `fh_pinctrl` table:

| Pad | Register | Function 0 (default) | Function 1 |
|---|---|---|---|
| 3 / 4 | 0xF000008C / 0xF0000090 | UART0_TX / UART0_RX | GPIO33 / GPIO34 |
| 5 | 0xF0000094 | **I2C0_SCL** | GPIO35 |
| 6 | 0xF0000098 | **I2C0_SDA** | GPIO36 |
| 7 | 0xF000009C | **SENSOR_CLK** | GPIO12 |
| 8 | 0xF00000A0 | **GPIO13** (sensor reset) | — |
| 42–47 | 0xF0000128 – 0xF000013C | MIPI_DP0/DN0, CKN/CKP, DP1/DN1 | GPIO51–56 |

So the sensor link is **2-lane MIPI**. Every sensor function is the pads'
function 0.

The pad register layout, from the stock `fh_pinctrl` mux setter:

| Bits | Meaning |
|---|---|
| 27:24 | Function select |
| 20 / 16 | Pull-up / pull-down |
| 6:4 | Drive strength |
| 12, 8, 28 | Always set by the stock driver; bit 12 is cleared on MIPI pads |

Each stock pin table entry is one word. Bits 7:0 are the pad number, 15:8 the
register offset, and 23:20 the function. Bits 27:26 give the pull (01 = up,
10 = down), 31:29 the drive strength, and bit 25 becomes register bit 28. The
I2C0 pads use pull-up and drive 0. SENSOR_CLK uses no pull and drive 4.

## Milestone 1 observations on our kernel (class A)

With patches 0007/0008 and `cam-probe show`:

- I2C0 at 0xF0200000 is a **Synopsys DesignWare I2C**, and `i2c-designware`
  probes it as `i2c-0`.
  - `IC_COMP_TYPE` = 0x44570140
  - `IC_COMP_VERSION` = 0x3132312A ("121*", i.e. 1.21a)
  - `IC_COMP_PARAM_1` = 0x000F0FAA: 32-bit APB, fast mode (400 kHz) at most,
    16-entry TX and RX FIFOs, no DMA interface
- Clock gates in PMU+0x1C: **0 = clock on**. Bit 12 (i2c0_clk) is 0 after
  boot. Setting it to 1 makes every I2C0 register read 0, so the gate also cuts
  the bus clock. Bit 23 (cis_clk_out, the sensor MCLK) is 1 after boot, so MCLK
  is off until something clears it.
- After reset, pads 5, 6 and 7 are in **function 1 (GPIO)**, not I2C0/SENSOR_CLK
  (pad registers 0x01101000 and 0x01001000). In that state the controller takes
  the command into its FIFO but never starts a transfer, and every `i2c-dev`
  access ends in `controller timed out`. Muxing the pads to function 0 with the
  stock settings (`cam-probe pins`) fixes this. Since patches 0009/0010 the
  DTS does this with `pinctrl-single`: I2C0 claims pads 5/6 and the pin
  controller hogs pads 7/8 for the sensor. Verified on the board.
- The I2C0 interrupt is GIC ID 43 (`GIC_SPI 11`), as in the stock kernel;
  confirmed with `cam-probe irq`.
- With pinmux, MCLK gate open, the divider left at 0x28[23:16] = 0 and
  GPIO13 reset released, the sensor answers at **7-bit address 0x30**, and its
  chip ID (0x3107/0x3108) is **0xCB 0x3A**, i.e. SC2336. No other address on
  bus 0 answers. Milestone 1 is reached. The MCLK frequency itself is still
  unmeasured.
- Since patches 0011–0015 the kernel does all of this itself:
  - The PMU clock driver provides `cis_clk_out` (24 MHz).
  - The SC2336 driver switches MCLK on and releases reset at probe, checks the
    chip ID (`SC2336 found, chip id 0xcb3a`), and powers the sensor down again
    1 s later.
  - It registers a V4L2 subdevice with 1920x1080 RAW10. The Bayer order is
    unverified.
  - Verified on the board.
- Patch 0016 adds the mode table and the sensor controls (class C):
  - Taken from the GPL-2.0 Rockchip `sc2336.c` (rockchip-linux/kernel
    develop-6.1, © 2022 Rockchip): 1920x1080 RAW10 at 30 fps, 2 MIPI lanes at
    405 Mbit/s per lane, 24 MHz MCLK, BGGR.
  - Its HTS 0x8CA (×2 = 4500) and VTS 0x4B0 (1200) match the header words of
    the stock `.hex` files.
  - Probe and control setup are verified on the board.
  - Streaming is untested until there is a MIPI receiver driver to start it.

## MIPI receiver (class B, from `isp.ko` and `libmipi.so`)

The MIPI receiver is driven by the `Mipi_*`/`mipi*` functions of `isp.ko`, and
`isp.ko` maps its register blocks at fixed addresses (`mipi_driver_init`):

| Block | Base | Size | Identification |
|---|---|---|---|
| CSI-2 host | 0xF1100000 | 0x200 | **Synopsys DesignWare MIPI CSI-2 host** (see below) |
| D-PHY settings | 0xF1700000 | 0x200 | Fullhan-specific D-PHY configuration |
| MIPI wrap | 0xF1000000 | 0x40 | Glue to the ISP. Its interrupt is stock ID 50 = `GIC_SPI 18` |
| PMU | 0xF0000000 | 0x3000 | +0x4C bits 13/14 (probably wrap/controller resets), +0x2108 bit 4 |

The CSI-2 host accesses match the register map of the newer DesignWare CSI-2
host versions, the same one the Raspberry Pi 5 driver uses in
`rp1-cfe/dphy.c`:

| Offset | Register |
|---|---|
| 0x00 | VERSION |
| 0x04 | N_LANES |
| 0x08 | CSI2_RESETN |
| 0x0C | INT_ST_MAIN |
| 0x40 | PHY_SHUTDOWNZ |
| 0x44 | DPHY_RSTZ |
| 0x48 | PHY_RX |
| 0x4C | PHY_STOPSTATE |
| 0xE0, 0xF0, 0x100, 0x110, 0x120, 0x130 | error status (PHY fatal, packet fatal, frame fatal, PHY, packet, line) |
| following word of each | the matching mask |

User space (`libmipi.so`, `mipi_init(cfg)`) passes a six-word `cfg` through two
ioctls on `/dev/isp`. The kernel functions then program the hardware.

`0x40066955` → `mipiWrap_Init`:

1. PMU+0x4C &= ~BIT(13)
2. wrap+0x00 = `cfg[1]` bits 0/1 → bits 0/1, `cfg[2]` → bit 2, `cfg[3]` → [17:16],
   `cfg[4]` → [13:12], OR 0x06800000
3. wrap+0x10 = 1, wait 2 µs, wrap+0x10 = 0
4. bits 25/26 adjusted by bit 0
5. wrap+0x04 = 0x79, wrap+0x08 = 1

`0x40036954` → `mipiCtrl_Init` (frequency code `cfg[0]`, lanes `cfg[5]`, which may be 1, 2 or 4):

1. PMU+0x2108 |= BIT(4), PMU+0x4C &= ~BIT(14)
2. CSI-2: PHY_SHUTDOWNZ = 1, DPHY_RSTZ = 1, RESETN = 0xFFFFFFFF
3. D-PHY: +0x04 = 1, +0x30…+0x40 = 10000, +0x20 = 0x01140114, +0x24 = 0x01010101,
   +0x28 = frequency code in each of the four bytes (one per lane)
4. CSI-2: N_LANES = lanes − 1, all six masks = 0xFFFFFFFF

Frequency codes (`astMipiFreq`, Mbit/s per lane):

| Code | Range | Code | Range |
|---|---|---|---|
| 0x0F | 80–99 | 0x09 | 200–299 |
| 0x0D | 100–119 | 0x08 | 300–499 |
| 0x0C | 120–149 | 0x07 | 500–1299 |
| 0x0B | 150–199 | 0x06 | 1300–1499 |

The wrap interrupt handler reads wrap+0x0C, writes the value back to clear
it, and counts bits 0, 3, 4, 5 and 6.

**Stock SC2336P setting** (`sdk_app`, next to `"SC2336P ver20240229"`): `cfg = { 7,
0, 1, 0, 0, 2 }`, i.e. code 7 (500–1299 Mbit/s), wrap bit 2 set, 2 lanes, so
wrap+0x00 ends up as 0x04800004.
- The stock SC2336P table runs the link faster than the Rockchip SC2336 table
  (405 Mbit/s, which is code 0x08).
- Whether the "P" variant needs different sensor settings is open. The chip ID
  0xCB3A is the same.

Clocks used (`isp.ko` names; gates from the clock table):

| Clock | Gate | Boot state |
|---|---|---|
| `mipi_dphy_clk` | 0x5C bit 6 | off |
| `mipi_cfg_clk` (100 MHz) | 0x5C bit 4 | off |
| `mipic_pclk_gate` | 0x20 bit 4 | on |
| `mipi_wrap_gate` | 0x20 bit 29 | off |

`isp_init_clk` only checks that the clocks are enabled; who enables them is
still open.

### Observed on our board (class A)

- With the three MIPI clocks on and the PMU+0x4C/0x2108 bits set, the CSI-2 host
  reads VERSION **0x3133302A ("130*", DesignWare CSI-2 host 1.30a)**. N_LANES
  resets to 3 (4 lanes).
- `cam-probe stream` runs the stock init with code 8 and 2 lanes, writes the
  Rockchip SC2336 table to our SC2336P, and sets 0x0100 = 1:
  - PHY_RX = 0x00030000: bit 17, the clock lane is in high-speed mode
  - PHY_STOPSTATE alternates between 0x00010003 and 0x00000000: only data
    lanes 0/1 are used, and they go idle between lines
  - all six error status registers and INT_ST_MAIN read 0
  - wrap+0x0C = 1, wrap+0x34 = 0x0B
- So the sensor streams over 2 lanes at 405 Mbit/s, and the receiver detects
  no errors.
- With the six masks cleared to 0 the error registers still read 0, so the
  link is clean whichever way the mask bits work.
- wrap+0x0C bit 0 is **not** a per-frame event. Writing 1 clears it, and it
  stays 0 for at least 100 ms while the sensor streams. It is a one-time event;
  possibly the wrap stalls because nothing (the ISP) takes the data. Its meaning
  is open.

## Media blocks and the ISP raw dump (class B)

`media_process.ko` has a per-chip table of the media register blocks (three
variants with the same addresses):

| Base | Assumed block |
|---|---|
| 0xE8400000 | ISP (confirmed by `isp_driver_init`, 1 MiB mapping) |
| 0xE7000000, 0xE7400000 | encoder |
| 0xE8300000, 0xE8700000 | probably the VPU (scaler/output) |
| 0xE8600000 | unknown (JPEG or BGM) |

The ISP interrupt is `media_get_irq_offset() + 7`. The `0xFDx00000` values next
to the bases are probably the stock kernel's static virtual mappings.

**Raw dump.** `API_ISP_GetRaw*` (`libisp.so`) uses two ioctls on `/dev/isp`:
`0x4010693D` stores a 16-byte buffer description (base, frame count, frame
size, tap point) and `0x8014690E` returns dump info. `handle_isp_interrupt`
then programs the ISP (offsets from 0xE8400000), one frame per ISP interrupt:

| Register | Function |
|---|---|
| +0x28 bit 26 | dump enable; cleared after the last frame |
| +0x120 [4:0] | tap point |
| +0x124 | DMA address: base + frame size × index |
| +0x128 | line stride in bytes, rounded up to 8 |
| +0x130 [11:0] | ISP input width − 1 |
| +0x138 [11:0] | width − 1 later in the pipeline |

Stride per tap point:

| Tap point | Stride basis | Format |
|---|---|---|
| **0 and 1** | **(+0x130 + 1) × 10 bits** | **10-bit raw at the ISP input, i.e. the sensor data** |
| 2 | (+0x138 + 1) × 10 bits | 10-bit |
| 3–5 | (+0x138 + 1) × 12 bits | 12-bit |
| 6 | (+0x138 + 1) × 36 bits | 3 × 12-bit (RGB) |

After a dump, user space sets ISP+0x100 bit 0 (`isp_core_get_dump_vi_en`)
and waits two frame times.

For the SC2336 at tap point 0: stride 2400 bytes, frame size 2400 × 1080
(about 2.5 MiB).

**First test on our board (class A, `cam-probe raw`):**

Setup: `pix_clk` and `adapt_hclk` on; the stock system reset bits (+0xF0
[5:4] = 1, +0xF4 [1:0] = 2); input size +0x130 = +0x144 = 0x0437077F; the
dump registers above; then +0x100 = 4, +0x34 = 5, +0x100 |= 1 (as
`isp_startup_Isp_PkgEnable` does).

What it showed:
- **Full frames arrive at the ISP input:** +0x1468 = 0x001FA400 = 1920 × 1080
  pixels, and +0x1460[31:16] = 0x780 = 1920. +0x1460[15:0] changes slightly
  between readings (0x1740 → 0x173F).
- **The dump DMA writes only the start of the buffer:** the markers at +1 MiB
  and at the end of the frame stay untouched. The buffer was zeroed and read
  through an uncached mapping (`memcat`).
- ISP+0x04 (interrupt status) stays 0, and +0x28 reads 0x0618C200.
- The dump stops just under 32 KiB (data up to +31744, zero from +32000), about
  13 lines. It is **not** rewritten in later frames: the first 4 KiB, once
  cleared, stay 0.
- The ISP itself keeps running: +0x114 changes all the time (0xB6B, 0x374,
  0x5CE …, probably a position counter), so there is no back-pressure from the
  missing VPU.
- Other ISP registers after the test: +0x000 = 0x5A542093 (ID?), +0x08/+0x0C =
  0 (interrupt enables?), +0x104/+0x108 = 0xFEDCBA98/0x76543210, +0x14C =
  0x01DF027F (a 640x480 window).

**Second round** (stock clock rates, valid ISP DMA addresses at +0xFC/+0x524/+0x4EC):
- +0x524 was 0 and +0x4EC was 0x00100000 before the fix, but the running stock
  firmware has the same values. So they are not invalid addresses, and the
  change that matters was most likely the stock clock rates (`isp_aclk` 50 MHz,
  `pix_clk` 100 MHz instead of 214 MHz).
- +0xFC is read-only (writes do not stick). The stock value 0xA3790000 was
  probably a current DMA address, not a setting.
- With these changes the dump DMA no longer stalls once and for all: it starts
  again in **every frame**.
- But each frame still writes only **exactly 31744 bytes (0x7C00)**, about 13.2
  lines. This does not depend on the start address (also 4 KiB before a 32 KiB
  boundary) and not on `arc_clk`. It is a byte or frame-end limit inside the ISP,
  not an address window.
- The "VPU default bypass" (`vpu_set_default_bypass`: ISP+0x30 bit 0 set, bit 19
  cleared; the VPU's register base in `chip_fh8626v200` is the ISP base
  0xE8400000) changes nothing.
- Reading 0xE8300000 hangs both the stock system and ours. That block is not the
  VPU.
- **Tap point test** (same stream, only tap point and stride changed):

  | Tap point | Written per frame | Lines |
  |---|---|---|
  | 0 (stride 2400) | 31744 bytes | ≈ 13.2 |
  | 3 (stride 2880) | 21248 bytes | ≈ 7.4 |
  | 6 (stride 8640) | 0 | 0 |

  The further back in the pipeline the tap point, the fewer lines arrive. This
  is **back-pressure**: the ISP output (to the VPU) is not taken, so the pipeline
  stops once its line buffers are full and starts again at the next frame. The
  counter at +0x114 measures the input side and keeps running.
### VPU (class B, from `isp.ko`)

The VPU registers are inside the ISP block: the VPU base in `chip_fh8626v200`
(+28) is 0xE8400000.

| Offset | Function |
|---|---|
| +0x30 | control: bits 20/21/22 enable channels 0/1/2; bits 9/12 channel 0/1 mode; bits 0/19 default bypass; dither bits 7, 8, 10, 11, 14, 15 |
| +0xE4 [21:20] | RGB input select (`vpu_rgb_vi_sel`) |
| +0xF00 | base setting 0x888 (`vpu_init_cfg`) |
| +0xF04 / +0xF0C | channel 0 input width (luma \| chroma) / height (luma \| chroma) |
| +0xF08 / +0xF10 | channel 0 output width − 1 / height − 1 |
| +0xF14…+0xF1C, +0xF20…+0xF28 | channel 0 start phases x/y (0 at 1:1) |
| +0xF2C/+0xF30, +0xF34/+0xF38 | channel 0 step x/y = ((in << 16) + out/32) / (out/16), 0x100000 at 1:1 |
| +0x1200 / +0x1204 | channel 0 offset / size (w − 1) \| (h − 1) << 16 |
| +0x1350 / +0x1354 / +0x1358 | channel 0 Y address / C address / Y stride \| C stride << 16 |
| +0x135C…+0x1364, +0x1368…+0x136C | the same for channels 1 and 2 |
| +0x1F40, +0x2140, +0x2340, +0x2540 | channel 0 scaler coefficient banks of 128 words each (channel 1 at +0x800: +0x2740…; channel 2 at +0x1000: +0x2F40…); probably write-only, reading this area hangs the bus |

Further details:
- **Coefficient format** (`vpu_scaler_table`, 16 levels of 512 bytes): 2 words
  per phase, 64 phases. The taps are 10-bit signed values at bits 0/10/20 of the
  first word, plus one in the second word, in Q8 (256 = 1.0). Each phase sums
  to 256. Level 15 phase 0 = `0x00040000, 0` (identity).
- **Per frame** (`vpu_kickoff_by_isp`, called through a hook at ISP frame start)
  the stock driver writes the channel addresses (`vpu_set_chn_output`) and
  enables the channel (`vpu_set_chn_enable` → +0x30 bit 20). `vpu_submit_frame`,
  `vpu_run_frame_ctrl` and `vpu_send_frame` are pure bookkeeping.
- **On the board:** channel 0 configured without scaler and without coefficients
  → the buffer stays 0.
- With identity coefficients and markers in the buffer, nothing is written
  either: the ISP back end (demosaic → RGB → YUV) is not running, which matches
  tap point 6 delivering 0 lines.

### ISP back end (stock defaults, class B, private)

`isp_startup_Ispf_Init` consists of 170 constant register writes plus a gamma
table copy (128 words at +0x690). `SetIspPicSize` writes sizes, line lengths and
windows. The list was extracted by static analysis into
`orig_firmware/derived/isp-init.regs` (vendor data, private). Our own linear
gamma curve replaces the stock table.

With this list applied (`cam-probe ispinit`, inside `raw`/`vpu`), on the board:
- **raw dump:** about 2/3 of the frame instead of 13 lines. The first lines look
  like an image, the rest is noise with a repetitive structure, and the last
  third stays 0.
- **VPU channel 0:** for the first time, data: exactly **128 bytes** of 0xFF at
  the start of the Y plane and of the C plane, then nothing.
- So the pipeline runs considerably further, but something still stalls. One
  suspect is a module with a DDR reference buffer (3D noise reduction?) without
  an address. In stock, +0xFC = 0xA3790000 is a running DDR address.
- +0x28 = 0x0008DB94 (the value `Ispf_Init` sets) instead of 0x0218C200: no
  change.
- **Comparison with stock** (`cam-probe ispcmp`, 1912 registers): many module
  blocks that `Ispf_Init` initialises are 0 in stock at runtime (+0x400…+0x414,
  +0x498…+0x4C4, +0x5B0…+0x600, +0xAC8…+0xB3C, +0xE00…). `cam-probe ispstock`
  writes the stock runtime values (except status, statistics DMA, dump and VPU).
  Result: the VPU still writes only 128 bytes, and the DMA idle flags +0x14/+0x18
  stay 0.
- **VPU channel 0 writes its first 128 bytes per plane again in every frame,**
  then stops. In stock, channel 0 runs exclusively in loopbuf mode to the
  encoder, while channels 1/2 write to DDR (864x480 etc.). Hypothesis: channel 0
  goes through the encoder adapter, which waits for the encoder after the first
  burst. Test: `CHN=1 cam-probe vpu`.
- **Channel 1 behaves exactly like channel 0** (128 bytes per plane per frame).
  So it is not channel-specific; the loopbuf hypothesis is out.
- `ispstock` (stock runtime values for ~800 registers) makes the raw dump worse:
  0 bytes per frame instead of about 2/3 of the frame with `ispinit` alone. The
  snapshot contains states that only hold together with the running stock
  driver.

### Raw path: bit scan (`cam-probe bitscan`, `dumpext`)

With `ISPSTOCK=0` (only `ispinit`) and a running stream, the dump writes **0
bytes per frame** after the first frames (baseline).
- Toggling a bit of +0x28, +0x30 or +0x100 and restoring it **now and then**
  restarts the pipeline. It then runs a fairly constant 1871872 bytes (≈ 780
  lines) and stops again.
- This happens for bits that do not even exist (+0x28 bit 31 reads back
  unchanged), so it is the reconfiguration itself, not a particular bit.
- Constantly rewriting the same values (statistics addresses, dump address,
  +0x28) has no effect.

Interpretation: the ISP is probably reset now and then by the reconfiguration,
runs until its downstream path (VPU, which does not run) stalls it again, and
does not recover by itself. A complete raw frame therefore probably needs a
running VPU path, or the per-frame servicing of the stock interrupt handler
(`handle_isp_interrupt`, `vpu_hook_interrupt`, `vpu_hw_reset`), which has not
been replicated.

### ISP interrupt (class A unless noted)

- `isp_interrupt` (stock) reads the status at ISP+0x04, acknowledges by
  writing it back, and calls `handle_isp_interrupt`.
- The enable mask is ISP+0x08 (write-only, reads 0); `isp_startup_Ispp_Init`
  sets it to 0x07FFFFFF (class B). `cam-probe` had not set it, so +0x04 always
  read 0.
- Interrupt: ID 39 = `GIC_SPI 7` (ISPENDR1 bit 7 with the status pending); the
  MIPI wrap is ID 50 = `GIC_SPI 18` (bit 18).
- With mask 0x07FFFFFF and no service: +0x04 = 0x0403000C, constant (bits 2,
  3, 16, 17, 26); no bits 0/1. Acknowledging from a shell loop does not help.
- **Kernel test driver** `fh8626-isp-bringup` (acknowledge, count, switch the
  statistics buffer, re-arm the dump; sysfs):
  - **Bit 2 re-triggers immediately** after each acknowledgement (about 146,000
    interrupts/s) while the driver switches the statistics buffer on bits 2/3.
  - Bits 3, 16, 17 and 26 come together at about 140/s.
  - Bits 0/1 never come; the raw dump stays at 0.
  - With bit 2 masked (rate-based storm protection): bits 3, 16 and 17 come
    once per frame (about 30/s).
- **Meaning of the status bits** (trace strings in the branches of the stock
  `handle_isp_interrupt`):

  | Bit | Event | Stock action |
  |---|---|---|
  | 17 | ISPF START | `vpu_hook_interrupt` |
  | 16 | ISPP START | |
  | 1 | ISPF END | dump re-arm, `vpu_hook_interrupt` |
  | 0 | ISPP END | `triBuf_switch_in`, `ispp_set_stat_addr`, `ispp_get_stat` |
  | 3 | `pipe_conflict` | counter; in a certain mode `vpu_hook_reset` / `init_isp` |
  | 2 | probably ISPP overflow | counter |

  (`triBuf_*` is purely software index handling for the statistics buffers.)
- **On our board:** ISPF/ISPP START every frame, but **never END**, and instead a
  `pipe_conflict` every frame. The back end does not finish because its output
  (the VPU) does not take the frame.
- **Per-frame VPU kickoff in the driver** (on ISPF START: write the channel
  addresses, switch the enable bit +0x30 off and on, as in `vpu_kickoff_by_isp`):
  it runs, but changes nothing. The VPU still writes 128 bytes per plane, with
  no ISPF END.
- **State:** the missing piece is in the VPU initialisation or its per-frame
  servicing (`vpu_hook_interrupt`, 510 instructions; `vpu_hw_reset`; the
  complete channel setup). Reconstructing these by hand has reached its limit.

### Emulation of the stock modules (`scripts/stockemu.py`)

To get the complete register sequence without guessing, the stock modules
(`media_process.ko`, `isp.ko`) and the user library `libdsp.so` run in an ARM
emulator (Unicorn):
- The modules are linked against each other, and the kernel API and libc are
  Python stubs.
- `ioctl` jumps straight into the kernel handlers (`fh81_isp_ioctl`,
  `media_control`).
- `ioremap` returns the physical address, and every access to the media
  register blocks is logged.
- Reads start from the stock runtime values (`isp-stock-runtime.txt`), so that
  read-modify-write sequences come out right.
- `buffer_malloc` allocates from 0xA3800000 (our reserved area), so the
  addresses can be used on the board directly.
- Stock binaries in `work/stock/` (git-ignored), extracted from
  `orig_firmware/rootfs_booted.tar.gz`.

Scenario `vpss`, as in `sdk_app`: `FH_SYS_Init` → `FH_VPSS_SetViAttr`
(1920x1080) → `FH_VPSS_Enable` → `FH_VPSS_SetChnAttr(0, 1920x1080)` →
`FH_VPSS_SetVOMode(0, 1)` → `FH_VPSS_OpenChn(0)`. All calls succeed. Findings:
- `vpu_drv_open` (opening `/dev/isp`) sets the VPU limits; without it
  `SetChnAttr` fails.
- vomode 0 = BLK (not allowed for channel 0), **1 = SCAN** (linear NV12),
  2–4 = TILE192/224/256, 5 = RGB888.
- `FH_SYS_Init` creates channel 0 for at most 1920x1088 and channel 1 for at
  most 720x576, and writes the scaler coefficients of all channels.
- **`FH_VPSS_OpenChn` sets the DDR addresses of the VPU auxiliary units and
  only then enables them:** CPY (+0x1278, stride +0x127C), BGM (+0x1224,
  +0x1228, +0x1220 = 2), SAD (+0x12E4, +0x12E8, level +0x12A0). On our board
  bits 17/18 (CPY/BGM) were set in +0x30 with no addresses, which is the
  most likely cause of the 128-byte stall.
- Final state: +0x30 = 0x0096DF83, +0xE8 = 0. Buffers: channel 0 Y 0xA3816000,
  C 0xA3A14000; BGM 0xA3E10000, CPY 0xA3E28000, SAD 0xA3E2EC00. The reserved
  area was enlarged to 8 MiB for this.
- The replay list (668 writes; coefficients only for channel 0) is in
  `orig_firmware/derived/vpu-init.regs`; `cam-probe vpustock` applies it.
- **Per frame** (scenario `frames`: the stock `isp_interrupt` with the status
  events one after the other, after the VPSS bring-up):
  - ISPF START (0x20000): `isp_driver_pipline_pause` (+0x100 = 0, +0x34 = 4).
  - ISPF END (0x2): channel 0 enable and addresses (double buffering between
    two Y/C buffers), CPY/BGM/SAD outputs rotating, **SAD read address
    +0x12DC/+0x12E0** and **YC mean outputs +0xED4…+0xEE0**
    (`vpu_set_mean_addr`).
  - ISPP END (0x1): `ispp_set_stat_addr`.
- `FH_VPSS_SetViAttr` switches the mean statistics on, but their addresses
  are only set at ISPF END. On our board ISPF END never comes, so the mean and
  SAD-read DMA use address 0: a likely chicken-and-egg stall. `vpustock` now
  sets these registers once before the ISP input starts.
- On the board with the replay (in stock order: VPU before the ISP input,
  interrupt mask restored after the replay's +0x08 = 0x1E00): interrupts and
  per-frame kickoff run, but still `pipe_conflict` every frame, no ISPF END,
  and the VPU writes 128 bytes.
- Also with the mean and SAD-read addresses set: no change. But **+0x18 (DMU
  idle) = 0x3A000** instead of 0x7FFFF: the stuck DMA units are in the ISP.
- **ISP emulation** (scenario `isp`: `libisp.so` + `libispcore.so`,
  `API_ISP_MemInit(1920, 1080)` → `GetBinAddr` → `SensorRegCb` (stub sensor;
  the "get vi attr" callback returns 1920x1080) → `SensorInit` →
  `SetSensorFmt` → `Init` → `LoadIspParam(sc2336_mipi_attr.hex)` →
  `SetCisClk(36 MHz)`):
  - all calls succeed
  - one DDR buffer "isp" of 3 MiB
  - `libispcore` maps the ISP registers through `/dev/mem`
  - **DDR address registers of the ISP that we never set:** **+0x0F8** (buffer
    base; write-only, +0xFC reads the running address back), +0x2F4/+0x2FC,
    +0x644/+0x654, and +0x4EC/+0x524 (statistics)
  - +0x28 ends at **0x0008DB80** (the stock runtime value 0x0218C200 only
    comes from later tuning)
  - the init itself switches the ISP input on (`vi_pol`, `PkgEnable`)
- **Scenario `full`** in stock order: `FH_SYS_Init` → ISP init → VPSS channel
  0 (NV12). 14 calls, 1731 writes; replay list in
  `orig_firmware/derived/stock-init.regs` (absolute addresses).
  - Buffers: `vpu-sys` 0xA3000000, channel 0 0xA3016000 (C at 0xA3214000),
    channel 1 0xA3632000, ISP 0xA3770000.
  - The reserved area is 16 MiB at 0xA3000000; the raw dump moved to
    0xA3B00000. `cam-probe stock` applies the replay.

**Register dump of the running stock firmware** (class A, `scripts/cam-regdump.sh`
on the stock system while streaming; reading 0xE830xxxx crashed it):

PMU:

| Register | Stock | Note |
|---|---|---|
| 0x1C | 0x80040012 | `arc_clk` (bit 22), `cis_clk_out`, `jpeg_adapt_clk` on |
| 0x20 | 0x40404000 | `jpg_hclk_gate` (bit 31), `pix_clk`, `mipi_wrap`, `adapt_hclk` on |
| 0x24 | 0x04091D00 | `isp_aclk` = 1500 / 30 = **50 MHz** (ours after boot: 214 MHz) |
| 0x2C | 0x0E141063 | `pix_clk` = 1500 / 15 = **100 MHz** (ours after boot: 214 MHz) |
| 0x40–0x4C | 0xFFFFFFFF | reset registers all 1 at runtime: the bits `mipiWrap_Init`/`mipiCtrl_Init` clear are probably self-clearing reset pulses |
| 0x5C | 0x00060406 | `veu_hclk`, `veu_adapt`, `nn`, MIPI clocks on |

MIPI:
- D-PHY +0x28 = 0x07070707 (code 7)
- wrap +0x20 = 0x039B0780 (1920 wide, plus a counter); wrap +0x38 = 0x09600101 (0x960 = 2400 = bytes per RAW10 line)

ISP:
- **+0xFC = 0xA3790000**: a DDR address in the stock media memory
- +0xD0 = 0x0437077F, +0xF0 = 9, +0xF4 = 0x01010003
- +0x100 = 0 (bit 0 is probably self-clearing)
- +0x128 = 0x500

The statistics DMA addresses (`ispp_set_stat_addr`) are at ISP **+0x524 (base)** and
**+0x4EC (base + 0x2030)**, with 0x60C8 bytes per buffer; they are not in the dump.

A further stock dump of ISP +0x300…+0x1FFC (non-zero values only) has **no
other DDR address**. It contains a gamma-like table (+0x690…+0x890), window
and limit values, and the VPU part inside the ISP block:
- +0x1034/+0x106C = 0xF010EB10 (`vpu_set_logo_rgb2yuv`)
- +0x1204 = 0x0437077F (VPU input 1920x1080)
- channel sizes such as +0x120C = 0x01DF035F (864x480) and +0x1358 =
  0x03900780 (1920x912)

Reading the ISP block above +0x2000 rebooted the stock system.

**Line buffer partition tables (+0x80…+0xB0).** In the stock runtime dump
they differ from the `Ispp_Init` constants our replay writes (stock +0x80 =
0x60502010, +0x90 = 0x10301010, +0xA0…+0xB0 = 0x50482820 0x69686058
0xA8987870 0xE8E0C8B8 0x00FFF8F0). No ARM code of the stock system writes
these values: only `Ispp_Init` (libispcore) and `change_dmu_isp` (isp.ko,
0x34302010 …, after `test_dmu` sees +0x18 bits 10:9 = 3) write the
registers, and the ARC image does not contain them either. Writing the stock
values while our replay runs stops the VPU completely (no marker
overwritten). `DMU=1 cam-probe stock` applies them inside the replay instead,
`DMU=2` the `change_dmu_isp` values. Result: `DMU=1` changes nothing (same 754
lines), `DMU=2` stops the VPU. The tables are not the cause.

**The 754-line limit is `isp_aclk` (class A).** With the sensor test pattern
(SC2336 0x4501 bit 3) the first 26 lines are correct colour bars. From then
on every line holds the bars compressed into 1388 pixels, shifted by
1920 − 1388 = 532 pixels per line (diagonal stripes). Every source line loses
its last 532 pixels, and the VPU, counting 1920 pixels per line, runs out of
data after about 26 · 1920 + 1054 · 1388 bytes ≈ 780 lines. The raw dump
(≈ 780 lines in the bit scan) shows the same thing. 1388 pixels is exactly
what a unit processing one pixel per cycle at 50 MHz manages in one sensor
line (HTS 2250 at 81 MHz = 27.8 µs). A FIFO absorbs the deficit for the first
26 lines. Doubling VTS did not help because the line rate stays the same.
Setting `isp_aclk` to 1500/15 = 100 MHz (PMU+0x24[13:8] = 0x0E) while the
stream ran gave complete frames: 1080 lines, C plane to the end, straight
bars, ISPP END (bit 0) every frame. The stock value of 50 MHz reads the same
on the running stock system; why it is enough there is open.
`isp_clocks()` now sets 100 MHz (`ACLK_DIV`).

**First image (class A).** With `isp_aclk` at 100 MHz from the start,
`cam-probe stock` delivers the complete scene as 1920x1080 NV12 at 0xA3016000
(C at 0xA3214000). It is overexposed, because nothing controls the sensor
exposure yet (on stock, AE runs in user space from the ISP statistics). The
setup still relies on the 1731 replayed writes of the emulated stock
bring-up.

To use it without the stock stack we still need: the ISP clocks and resets,
the minimum ISP input setup (input size, enable) so that it takes frames from
the MIPI wrap and raises frame interrupts, and a DDR area that Linux does not
use.

### Luma lost again (2026-11-02, solved 2026-11-03)

**Cause: the per-frame VPU kickoff of the bring-up driver.** With bit 17
(ISPF START) masked, the driver does nothing on frame start (in `stock`,
`stat_addr` and `dump_addr` are 0, so the kickoff is the only bit-17 action):

| Run (`ISPMASK=7fdfffb`) | Result |
|---|---|
| scene | Y 0xEF…0xFF (overexposed, no AE yet), PASS, 0 kickoffs |
| `PATTERN=1` ramp | bars Y 11 11 11 241 249 252 254 255 (x = 120…1800 in steps of 240; the ramp starts at x = 668) |

On 2026-10-31 the bit-2 storm had masked bit 17 by accident, which is why the
first image worked. Switching the channel enable (+0x30 bit 20 + channel) off
and on during every frame breaks luma behind tap 5; stock evidently doesn't
need it for continuous capture (the frames update without it). `cam-probe
stock` now defaults to `ISPMASK=7fdffeb` (bit 17 off, bits 2 and 4 off
because both storm; bit 4, status 0x10, storms since this run too).
Consequence for the driver: no kickoff, set the channel enable once; the
kickoff code in patch 0018 is dropped when the bring-up moves into the
kernel driver.

The notes below are the investigation as it stood before the cause was found.

**Symptom.** The same replay (`stock-init.regs` and `cam-probe stock` are
unchanged since the first image) now gives a black scene:
- every Y byte is 0 and every C byte 0x80
- with `PATTERN=1`, Y is 0x0B where the input is 0 and 0 everywhere else

On 2026-10-31 the same test pattern gave Y 0x0B and 0xEC…0xFF in the bright
part (frames `nv12-tp.bin`, `nv12-aclk.bin` of that session). So the luma path
worked then. Something in the procedure differs, not the register list.

**Measured (class A):**
- **The test pattern** (SC2336 0x4501 bit 3) is a horizontal ramp, not colour
  bars:
  - raw 0 up to x = 668
  - then rising about 1 per pixel, reaching 1023 at x = 1692
  - saturated after that
  - every line the same

  The "colour bars" above were a misreading.
- **Dump format:** taps 0–5 write little-endian packed samples
  (`raw2pnm.py --packing le`).
- **Tap points** (the ramp at x = 600 / 700 / 1000 / 1400 / 1800):

  | Tap | Values |
  |---|---|
  | 2 (10 bit) | 0 / 31 / 331 / 733 / 1023 |
  | 3, 4 (12 bit) | 0 / 124 / 1324 / 2928 / 4092 |
  | 5 | 7 / 978 / 2848 / 4095 / 3908 (bent like a gamma curve; Bayer rows differ) |

  The raw path up to tap 5 is correct, and luma is lost after it.
- **Tap 6** (RGB, 3 × 12 bit by the stock stride rule) does not decode as
  expected: it is flat for 14 pixels, then noise with a period of about
  900 pixels. Either the format is different or the data is broken. Its stride
  needs `ONELINE=1`: stride 0, one line, since a full frame (9.3 MiB) does not
  fit below the end of DRAM. Stride 0 is verified with tap 3.
- **ISP soft reset** (`isp_reset`/`isp_core_reset` of `isp.ko`, class B, verified
  on the board): +0x40 bit 0 and bit 1, done when the same bits are set in
  +0x44, which happens at once.
  - It brings every register back to its reset value except table RAM. Of
    437 changed registers, the 129 gamma words +0x690…+0x890 stay; they hold
    random contents at power-on.
  - The scaler coefficients (+0x1F40…) are not readable.
- **Bus hangs** came from reconfiguring while the units of the previous run
  were still running: `cam-probe stop` only stops the sensor. `isp_quiesce`
  now pauses the ISP input (+0x100 = 0, +0x34 = 4), switches off the VPU units
  in +0x30, stops the kickoff and waits 100 ms before every new setup.
- **The RGB→YCbCr matrix is not the cause:**
  - +0xA60…+0xA74 is the stock table `RGB2YCbCr` of libispcore (BT.601, Q10,
    306/601/116)
  - its Y row is the same in stock, the replay and our runs
- **Ruled out, no effect on luma:**
  - stock runtime values for single blocks: 0x1C4, 0x250…0x26C, 0x278…0x29C,
    0x2C0…0x2C8, 0x400…0x414, 0x50C…0x55C, 0x618…0x66C, 0xA64…0xA70,
    0xAB0…0xB48, 0xC2C…0xC74, 0x28, 0xE0, 0xF0/0xF4, 0x14C, 0x2AC…0x2BC,
    0x2D0, 0xB4C…0xC28
  - the 17 non-zero registers that `ispstock` writes and the replay does not
  - skipping replay groups 29, 30 (in parts), 32 and 34–36
  - stock runtime values for all of 0x1C0…0xEBC (except DMA addresses): black
- **Effects that were seen:**
  - 0xE00…0xE2C with stock values lifts Y by about 96 (an offset, not the
    fix)
  - 0x570/0x574 and 0x590…0x600 with stock values remove the chroma
- **Previous session:** on 2026-10-31 the interrupt storm masked status
  `0x602000c`, and with it bit 17, so the per-frame VPU kickoff of the
  bring-up driver never ran. Now the storm hits bit 4, and the kickoff runs.
  The 16:05 test also ran the replay at `isp_aclk` = 50 MHz and switched to
  100 MHz live afterwards.

**Next experiments.** The prime suspect is the per-frame VPU kickoff (the
driver writes the channel addresses and toggles +0x30 bit 20 on ISPF START).
Run with the room lit:

```
echo "== no kickoff, scene"; ISPMASK=7fdfffb cam-probe stock /tmp/k1.bin 2>&1 | grep -E '^(result|stock: Y|stock: kick)'
echo "== no kickoff, ramp";  PATTERN=1 ISPMASK=7fdfffb cam-probe stock /dev/null 2>&1 | grep -E '^(result|bars Y)'
```

`ISPMASK=7fdfffb` is the default mask without bit 17, so there is no kickoff.
If the scene is still black, repeat the 2026-10-31 sequence exactly: replay at
50 MHz, then the clock raised live.

```
PATTERN=1 ACLK_DIV=0x1d KEEP=1 cam-probe stock /dev/null > /dev/null 2>&1
v=$(devmem 0xf0000024); devmem 0xf0000024 32 $(( (v & ~(0x3f << 8)) | (0x0e << 8) ))
usleep 300000; for x in 120 600 840 1080 1320 1560 1800; do printf '%s ' $(devmem $((0xa3016000 + 540*1920 + x)) 8); done; echo
cam-probe stop
```

On 2026-10-31 the ramp part read about 0xEC…0xFF. If neither run brings luma
back, compare the full register state of a working and a broken run with
`cam-probe ispdump`.

**`cam-probe` tools added for this:**

| Tool | Purpose |
|---|---|
| `groups` | lists the replay groups |
| `SKIP="G G:FROM-TO"` | leaves out replay writes |
| `snap` / `ispreset` | saves the reset values / tests the soft reset |
| `RESET=0` | skips the soft reset before the replay |
| `RESTORE=1` | writes the `snap` values instead of the soft reset |
| `STOCKSET="ranges"` | stock runtime values after the replay |
| `POKE="addr=val"` | single writes after the replay |
| `RAWOUT=FILE TAP=0..6 ONELINE=1` | pipeline dumps |
| `ispdump FILE` | all readable ISP registers |
| `result: PASS/BLACK/FAIL` and `bars Y/U/V` | summary line at the end of each run |

`/tmp` lives in RAM: remove old `*.bin` files before larger runs (a full
`/tmp` truncated dumps silently).

### Replay bisect, single-group skip (2026-11-03)

`ISPMASK=7fdffeb SKIP=$g PATTERN=1 cam-probe stock`, g = 1…75 (numbers as
listed by `cam-probe groups`). Reference bars Y: 11 11 11 241 249 252 254 255.
Only four groups make a difference:

| Group | Function | Skipped |
|---|---|---|
| 29 | isp_startup_Ispp_Init (343) | image correct, but ISPF END 44 instead of ~100, pipe_conflict 0 |
| 30 | isp_startup_Ispf_Init (299) | bars 96 96 96 149 … 160: black level +85, contrast compressed (like the earlier 0xE00 finding) |
| 32 | SetIspPicSize (22) | image correct, but no ISPF END at all |
| 39 | isp_startup_Isp_PkgEnable (3) | nothing runs (Y 0, no interrupts) |

All others are within ±1. That does **not** mean they are unnecessary:
- **Duplicates:** the VPU setups appear up to four times (FH_SYS_Init,
  SetViAttr, SetChnAttr, SetVOMode), so a skipped copy is covered by the next.
- **Coarse metric:** after gamma the ramp saturates from x ≈ 840, so LSC,
  CNR, NR3D and colour show nothing here. They need the scene.
- **Table RAM:** soft reset doesn't clear table RAM (gamma, scaler
  coefficients group 22), so a skip only shows the table of the previous run.
  Only a skip right after power-on tells us whether a table is needed.

Index ranges in group 30: 1–299, gamma 169–297 (+0x690–0x890), +0xE00–0xE2C
86–97.

### Replay bisect, combined skip (2026-11-03)

Group sets (numbers from `cam-probe groups`):

```
A="24 25 26 27 28 31 33 34 35 36 37 38"        # ISP side outside 29/30/32/39
B="1 2 3 4 5 6 7 8 9 41 42 65 66 67 68 69 70 71 72 73 74 75"  # VPU extras, CPY/BGM/SAD
C5="10 11 12 14 15 16 18 19 20 43 44 45"       # duplicate channel setups
E="52 53 54 55 56 57"                          # SetChnAttr, repeated by SetVOMode
G30="30:1-85 30:98-133 30:166-168 30:298-299"  # no visible effect in Ispf_Init
```

**Result:** with `SKIP="$A $B $C5 $E $G30"` the scene is correct. It's the
same overexposed room as with the full replay (Y mean 246 vs 249, p1 233 vs
236). That leaves **1105 of 1731 writes**:

| Writes | What |
|---|---|
| 512 | scaler coefficients, group 22 (table RAM, see below) |
| 343 | Ispp_Init, group 29 (not split yet) |
| 173 | Ispf_Init: gamma 129, +0xE00–0xE2C 12, +0x590–0x620 32 |
| 22 | SetIspPicSize, group 32 |
| ~55 | VPU: vi size (40), ch0 from SetVOMode, ch1/2 sizes, +0x30 writes, reg init 23, PkgEnable 39 |

**Order matters:**
- **The +0x30 writes:**
  - FH_SYS_Init 13/17/21 are named vpu_chn_enable, but they clear bits 20/21/22
    and so switch channels 0–2 off.
  - Groups 5 and 7 leave all three channels on.
  - If 13, 17 and 21 are all skipped, the pipeline starts (group 39) with three
    unconfigured channels on. The VPU then stalls for good, and the later +0x30
    writes don't help (bit-16 storm, no ISPF END).
  - A single skip only leaves one channel on, and that one is still configured.
- **Channels 1/2:**
  - These stay off but still need valid sizes and scaler steps (352×288, step
    0x5745d1, groups 46–51).
  - Without them the VPU stalls as well.

**Open:**
- **The reduced set costs throughput:** ISPF END ~62 instead of ~100 in 5 s,
  and pipe_conflict ~380 instead of ~20.
  - C5 alone already gives 171.
  - So one of the early channel setups affects timing, probably ch0 before the
    pipeline start (10–12).
- **No colour,** even with the full replay: U/V is 128 everywhere (frames
  `full.bin`/`min.bin` in data5.tar). It's unclear whether that comes from the
  overexposure or from the chroma path.
- **Group 22:** whether the scaler coefficients are needed can only be shown
  by a skip directly after power-on.

**Update (same day):**
- **The throughput loss is solved.** It came from the late SetViAttr
  duplicates (43–51): the early ch1/2 setup in FH_SYS_Init (14–20) is needed
  *before* the pipeline start, and its later copies are redundant.
  `C6="43 44 45 46 47 48 49 50 51"` replaces C5. With
  `M="$A $B $C6 $E $G30"`: ISPF END 111–115, pipe_conflict 14.
- **Group 29 (Ispp_Init)** is structured as follows:

  | Writes | Contents | Needed? |
  |---|---|---|
  | 1 | +0x08 = 0x07ffffff, interrupt enable | Yes. Without it ISPF END is ~42 and pipe_conflict 0 (interrupt delivery, not the image). |
  | 2–16 | +0x80–0xB8, curve segment boundaries | No (ramp unchanged) |
  | 17–23 | +0x494, +0x13C–0x154, +0x578/+0x57C | No (ramp unchanged) |
  | 24–343 | four interleaved 80-word tables (+0x1A40, +0x1B80, +0x1CC0 identical 12-bit curves; +0x1E00 a 10-bit curve) | No (ramp unchanged) |

- **The luma curve is very steep:** Y goes 11 → 219 between raw 0 and raw ≈ 32
  (x = 668…700), while tap 5 there is only 978/4095.
  - Candidate: +0xE00 is 0x40 after init but **0x10** in the stock runtime
    dump (gain 4 → 1 in 4.4?).
  - Also different at runtime: +0xE08 0x00cc011c → 0x03ff0080, and +0x590
    0x0d → 0x02.
  - The other runtime values of these blocks are 0, probably because the
    registers are write-only.

### Black level, exposure: first usable image (2026-11-03)

**Base set:** `M2="$A $B $C6 $E $G30 29:2-343"` (sets as above). PATTERN
ramp, real scene, `ISPMASK=7fdffeb`.

**Black level (class A):**
- **The pedestal is never subtracted.** With the lens covered:

  | Tap | Value |
  |---|---|
  | 0 | 66 (10 bit) |
  | 2 | 65 (10 bit) |
  | 3/4 | 264/276 (12 bit) |
  | 5 | 1065 (12 bit, after gamma) |

  Through the steep tone curve this becomes Y ≈ 230, which is why every scene
  looked "overexposed". The ramp (true 0) didn't show it.
- **The BLC registers are +0x13C/+0x140,** each two 16-bit halves (four Bayer
  channels):
  - libispcore `isp_core_set_blc_init_cfg` writes them.
  - `blc_ctrl_run` rewrites them per frame from the total gain.
  - The replay writes 0 there (group 29, writes 18–21).
  - The stock runtime dump also reads 0, which is unexplained.
- **Unit: 10 bit raw.**
  - 0x108 shifts the start of the ramp by about 264 raw (black up to x ≈ 920).
  - 0x42 shifts it by exactly 66 (668 → 734).
- Our value: `POKE="0xe840013c=0x00420042 0xe8400140=0x00420042"`. The driver
  will need one value per gain step (to be measured).

**Tone curve:**
- **The jump stays steep** (raw +26 above black → Y 213). Neither of these is
  the cause:
  - gamma table +0x690–0x88C: linear and x^0.45 tables written by us only
    move the black floor (33 → 62)
  - stock runtime values of 0x200–0xE7C, set block by block (STOCKSET S1–S5,
    statistics addresses excluded):
    - S2 (0x300–0x68C) undoes the BLC, so stock does its black level
      differently in that range, and triggers the bit-25 storm.
    - S5 is just the contrast +0xE00.
- **+0xE00 is a contrast around the midpoint:** 0x40 = 1.0, and 0x10
  compresses to 99…160.
- **Tap 6 is not 3×12 bit YCbCr,** and selecting it disturbs the main path
  (Y 250 over the whole line).

**Exposure sweep** (`KEEP=1`, then {0x3e00..02} = lines × 16 over I2C,
0x3e08 = 0x03): Y drops gradually.

| Lines | Row 540 (8 samples) |
|---|---|
| 1194 | 248…232 |
| 150 | 162…237 |
| 60 | 67…231 |
| 10 | 31…159 |

- **At 60 lines the room is clearly recognisable with gradation** (frame
  `x60.bin`, data11.tar).
- So the ISP chain has a high gain, and AE has to choose short exposures; the
  sensor table's 1194 lines with coarse gain 0x1f is far too much.
- Horizontal bands in single frames: `memcat` reads while the VPU writes the
  next frame.

**Still no colour:**
- U/V = 128 in the whole image. The only exception is a garbage strip in the
  bottom rows.
- The WB gains +0x570/+0x574 (30:24–25) are in the skipped part, but the full
  replay had no colour either.

### Colour: first colour image (2026-11-04)

**+0xE0C bits 0 and 8 switch the chroma path off.**
- Init writes +0xE0C = 0x00010101. Per frame, the stock `cie_ctrl` writes
  0x00010000 (emulator, scenario `isprun`). The stock runtime dump reads
  0x00000000.
- With the bits set, U/V = 128 everywhere: the grey images above.
- The earlier full-replay colour tests left the bits set or stalled under
  BLC + KEEP, so they showed nothing.

**With the bits cleared on the reduced replay M2, the frame is noise.**
- Y neighbour |dx| ≈ 68 (x60.bin: 9). Every bit is random, including the
  MSB.
- The noise differs from run to run (correlation 0.00), so it isn't a buffer
  being read. Only 32×32 block means still follow the scene a little.
- It looks like an unclipped high gain on sensor noise.
- Ruled out as the cause:
  - the identity CCM, +0x2C = 0x80
  - the per-frame `cie_ctrl` values (+0xE04/E08, +0xA64–A70)
  - the ISP DMA buffer addresses (group 27: +0x2E4–0x654, +0xAA4), plus
    group 35

**Group 29 (`isp_startup_Ispp_Init`) is needed for colour.**

Tested with BLC 0x42, 60 lines exposure, gain 0x03, and +0xE0C = 0x00010000:

| Skipped | Y \|dx\| | U / V mean | Result |
|---|---|---|---|
| M2 (A B C6 E G30 29:2–343) | 68 | 130 / 137 | noise |
| A B C6 E 29:2–343 (group 30 complete) | 68 | 130 / 137 | noise |
| A B C6 E (groups 29 and 30 complete) | 1.9 | 90 / 141 | **colour image** |
| A only | 2.0 | 91 / 141 | colour image |

- First correct colour image (`n29.bin`, `na.bin`, data13.tar).
- It has a strong yellow cast: U ≈ 90 and V ≈ 141 across the whole scene,
  because nothing does white balance yet.
- On the grey path, group 29 had seemed skippable. The curve tables in it
  (+0x80–0xB8, +0x1A40–0x1E00) evidently belong to the chroma path.

**Correction: only the curve tables count, and they persist across runs.**
- The ISP soft reset before each replay doesn't clear table RAM.
- So once a run has written the tables, later runs that skip them still have
  them. That's why skipping any single part of group 29 looked harmless.

**Tone curve tables +0x1A40 / +0x1B80 / +0x1CC0 / +0x1E00.**
- Each table is 80 words, two entries per word (low half first), so 160
  entries.
- The first three tables are 12 bit (R, G, B?). The vendor curve is nearly
  linear: 0, 20, 40, … 4072.
- The fourth table is the same curve in 10 bit.
- Results:
  - All zero: black frame.
  - **Our own linear tables** (entry k = k·4095/159, or k·1023/159): clean
    image.
- Group 29 can then be skipped completely (29:2–343).

**+0x28 holds the Bayer phase (bits 3–4).**

Bisect from the good setup "M2 without the G30 skip", skipping one piece of
group 30 at a time:
- Only skipping 30:298 (+0x28 = 0x0008DB94) breaks the image. Its reset value
  is 0.
- Everything else in G30 can stay skipped:
  - WB gains at reset: the colour gets more neutral
  - CCM, CSC, NR blocks, +0x2C, +0x664–66C
- The CSC values the vendor writes are plain BT.601 in 1/1024 units
  (306/601/117, −173/−339/512, 512/−429/−83). They aren't needed, because the
  reset values work.

Test: M2 + linear tables + +0x28 = 0x0008DB80 | v (`b0` … `b1c`):

| bits 4:3 | v | Colour |
|---|---|---|
| 0 | 0x00, 0x04 | cyan: R and B swapped |
| 1 | 0x08, 0x0C | magenta: G swapped with R/B |
| 2 | 0x10, 0x14 | **correct** (yellowish without AWB) |
| 3 | 0x18, 0x1C | magenta |

Notes:
- The vendor init ends with 0x0008DB80 (phase 0), and the stock runtime has
  0x0218C200 (also phase 0).
- The `Ispf_Init` value 0x0008DB94 (phase 2) only works for us by accident
  of the replay order: group 37 (0x…80) is skipped.
- Our image is rotated by 180° compared with stock. The sensor table in use
  doesn't set the mirror/flip the stock app sets, which shifts the colour
  filter pattern by one pixel in x and y.
- Bit 2 has no visible effect.
- Single-bit clears of bits 7–19 (from 0x…80, i.e. phase 0) left the colour
  cast unchanged, except bit 9 (magenta) and bit 12 (stronger). Still to be
  re-checked with phase 2.

**+0x28 needs only bits 4, 9 and 12: 0x00001210.**
- Clearing bit 9 gives magenta (probably part of the phase setting).
- Clearing bit 12 makes the colours swing wildly.
- Bits 7, 8, 11, 14, 15 and 19 have no visible effect.
- `m1210` looks like `min`.

**White-balance gains +0x570/+0x574 (0x200 = 1.0, phase 2):**

| Field | Channel | Test (field = 0) |
|---|---|---|
| +0x570 [15:0] | B | U drops (yellow) |
| +0x570 [31:16] | G | magenta, Y drops |
| +0x574 [15:0] | G | magenta |
| +0x574 [31:16] | R | V drops to 0 (cyan) |

- Read with the vendor init value, that is B 1.45 and R 1.68.
- Gains at reset are probably 1.0.
- The `min` frame with gains 1.0 averages about R 151, G 174, B 98
  (greenish-yellow).

Manual WB under the office fluorescent light, 30 lines exposure, gain 0x03
(frame means over mid-tones; WB gains are linear, but the output passes the
gamma curve, exponent ≈ 0.46):

| R | B | R / G / B mean | Result |
|---|---|---|---|
| 1.0 | 1.0 | 151 / 174 / 98 | yellow-green |
| 1.0 | 2.0 | 158 / 193 / 150 | green (`wb20.bin`) |
| 1.5 | 3.5 | 200 / 180 / 164 | slightly pink, nearly neutral (`wb35.bin`) |

- So R ≈ 1.3 and B ≈ 3.5 suit this light.
- Colour reproduction with only our own values is plausible: skin, black
  hoodie, white ceiling.

**Minimal colour setup so far:**
- the M2 replay set
- BLC +0x13C/+0x140 = 0x00420042
- +0xE0C = 0x00010000 (chroma on)
- our linear tone tables
- +0x28 = 0x00001210
- WB +0x570 = 0x02000700, +0x574 = 0x029a0200 (R 1.3, B 3.5; for the
  office light)
- (2026-11-10) +0x590 = 0x0D in the replay, +0xE00 = 0x40,
  +0x600 = 0x100; identity scaler filter (group 22); gamma table x^1.7 of our own instead of the vendor
  curve

None of these values come from vendor tuning tables.

**Remaining vendor parts of group 30 (2026-11-09).**

Each test uses the minimal setup, with mean Y over 25 points:

| Change | Ymean | nz | Result |
|---|---|---|---|
| ref (vendor gamma) | 179 | 12 | PASS |
| own gamma x^1.0 (30:169–296 skipped) | 182 | 7 | PASS |
| own gamma x^1.5 | 161 | 9 | PASS |
| own gamma x^1.7 | 158 | 12 | PASS, natural image (`g17.bin`) |
| own gamma x^2.0 | 151 | 20 | PASS |
| skip 30:86–97 (+0xE00–0xE2C) | 145 | 6 | PASS |
| skip 30:134–165 (+0x590–0x620) | 0 | 0 | **FAIL**, storm status 0x2010008 |

Gamma table +0x690–0x88C:
- 256 entries of 16 bit, two per word, low half first.
- +0x890 = 0x00010007 belongs to the block; it was kept in these tests.
- The vendor curve is convex (≈ x^1.7, 0.31 at the midpoint), i.e. it
  darkens. Our x^0.45 made the image far too bright (`gam.bin`).
- Generic power curves of our own work; x^1.5–1.7 looks natural.

Contrast block +0xE00–0xE2C: needed after all, see below. The PASS above
only meant "no stall"; Ymean hid that the range was squashed.

+0x590–0x620 is the DRC block:
- The per-frame writes in the emulator come from `setSideBoundaryAndSatParam`,
  `setGradCoordinate`, `setBrightParam` and `setContrastParam`.
- Without it the pipeline stalls. It still has to be split.

**Contrast and colour gain (2026-11-10).**

DRC block +0x590:
- It has to be written **before** the pipeline start, so it stays in the
  replay (30:134). Written after the start, the ISP runs slowly (ISPF END
  ~20–47 instead of ~400) and the image is grey.
- With +0x590 = 0 the pipeline stalls (storm 0x2010008).
- 0x0D reads back as 0x0C, so bit 0 is write-only or self-clearing.
- The rest of the block (+0x594–0x620) isn't needed for speed.

Flat image (Y 95–160, weak colour, also straight after a cold boot):
- Cause: the contrast block 30:86–97 was skipped.
- +0xE00 = 0x40 (contrast 1.0) alone restores the full Y range 0–255
  (`e10.bin`).
- +0xE04 = 0x00800000, +0xE10 = 0x00ff0000 and +0xE08 = 0x00cc011c make no
  measurable difference.

Colour gain +0x600:
- With all of +0x594–0x620 skipped, U = V = 128 exactly (no colour at all).
- Bisected to +0x600, lower field. The vendor value is 0x010000cd; the
  upper half (0x0100) makes no difference.
- Works when written after the start.

Sweep of the lower field (mean |U−128|, |V−128| over 36 samples):

| +0x600 | \|U\| | \|V\| | Result |
|---|---|---|---|
| 0 | 0 | 0 | no colour |
| 0x20 | 0.3 | 0.3 | |
| 0x40 | 0.7 | 0.6 | |
| 0x80 | 1.0 | 0.8 | |
| 0xcd | 1.9 | 1.9 | |
| 0xff | 2.1 | 1.8 | |
| 0x100 | 1.9 | 1.7 | |
| 0x1ff | 16.3 | 6.7 | broken: false colour on edges, Y noise ×3 (`s1ff.bin`) |
| 0x3ff | 19.3 | 9.2 | broken |

- So it is a gain that rises with the value up to about 0xcd–0x100.
- 0x1ff and above give false colour on edges, perhaps a sign bit or an
  overflow.
- With the whole vendor block replayed, the colour is somewhat stronger
  (|U| 3.6, |V| 2.6, `drc.bin`, natural colours). Another register in the
  block adds to the gain.
- With +0x600 = 0xcd set by us in every run, ×10 values |U| / |V|:
  - none 18/15, repeated 18/16 (this is the scatter)
  - whole block 23/30
  - +0x594–0x5AC 18/15
  - +0x5B0–0x5C4 15/13
  - +0x5C8–0x5EC 1/0 (kills colour, darkens the image)
  - +0x5F0–0x5FC 28/23
  - +0x618–0x620 21/18
- The boost comes from +0x5F0–0x5FC (vendor values, meaning unknown) and
  is only about 1.5×.
- Decision: not used. We set +0x600 = 0x100 instead. The image with only
  our own values is plausible but pale (`own.bin`).

**Scaler coefficients, group 22 (2026-11-10, after a cold boot).**

Layout:
- 512 writes in four banks of 0x200 bytes (+0x1F40, +0x2140, +0x2340,
  +0x2540).
- Each bank holds 64 phases of two words.
- Word 0 has tap 0 in [9:0] and tap 1 in [19:10]; word 1 has tap 2. Taps
  are signed 10-bit values, 0x100 = 1.0, and the taps of each phase sum to
  0x100.

| Run | Table | Result |
|---|---|---|
| `sk22` | not written (power-on RAM) | storm status 0x2810000, regular white lines across the image (`sk22.bin`) |
| `id22` | our identity filter: 0x00040000 / 0 in every phase | clean image (`id22.bin`) |
| `ref22` | vendor | clean image |

- The table is needed even at 1:1.
- Our identity filter (nearest-neighbour) replaces the vendor table. For
  real downscaling we would need a filter of our own (e.g. a computed
  windowed sinc), later.

**Own bring-up without vendor values (2026-11-10): step 1 done.**

The list:
- `scripts/cam-own-regs.py` computes the complete register list.
- `overlay-static/etc/cam-own.regs` is its output: 1044 writes.
- `cam-probe own` runs it the same way as `stock` (SKIP/POKE work on its
  groups).
- Sizes, strides, scale steps and addresses come from 1920x1080 (VPU
  channel height 1088). The settings are the neutral values found above.

Tested on the board with the list mounted over `/etc/stock-init.regs`:

| Run | Result |
|---|---|
| first list (no channel 1/2 setup) | no frame, storm 0x10008, buffer untouched |
| + early +0x30 = 0x0086df83 | still no frame (storm 0x2010008) |
| vendor replay without groups 14–21 (channel 1/2) | no frame |
| vendor replay without groups 13/17/21 (+0x30 writes) | no frame |
| vendor replay without groups 10–12 (early channel 0) or 23 (`vpu_reg_init`) | PASS |
| own list + channel 1/2 at 352x288 | PASS, picture with colour (`c352.bin`) |
| own list + channel 1/2 at 640x360 | PASS, same picture (`c640.bin`) |

VPU channels 1 and 2 are unused, but they need a valid size and scale step
before the ISP start:
- The setup has to be written while the channel is switched on in +0x30:
  0x00e6df83, set channel 1, 0x00c6df83, set channel 2, 0x0086df83.
- Writing only the final value is not enough.
- The size is free; the scale step is (input size << 20) / output size,
  rounded. This formula reproduces the vendor values 0x5745d1/0x3c71c7
  exactly.

Interrupt count:
- The own runs report ISPF END 54 and no storm.
- The replay runs reported about 410 (or 810 with the identity filter
  written after the start), always together with a storm on status bit 5
  (0x20).
- 54 ISPF END in the roughly 1.8 s the probe waits matches 30 fps, so the
  higher counts were probably inflated by the bit 5 storm. To be confirmed
  with a timed count in the kernel driver.

Still open, but not blocking step 2:
- `isp_pic_size_misc` (19 line buffer values of SetIspPicSize) is only
  partly understood (0x960 = RAW10 stride 2400, 0x280 = 640, 0x258 = 600).
  A test without it is still to be done.
- The +0x30 bits are only known as far as the bisect shows (bit 20 =
  channel 0 on, bits 21/22 used while channels 1/2 are set up).

### Kernel driver `fh8626-camera` (step 2, 2026-11-10)

Buffer switching was tested before writing the driver. The `own` setup was
running; then +0x1350/+0x1354 were rewritten to a second buffer and back:
- The VPU follows the new address at once: the new buffer is written and
  the old one is no longer touched, in both directions.
- The channel stays enabled; no kickoff.
- The second buffer holds a complete frame (`swb.bin`).
- Timed count: 61 ISPF END in 2 s, so 30 fps.

The driver (patches/linux, `drivers/media/platform/fullhan/fh8626-camera.c`):
- One video node with a fixed format: 1920x1088 NV12.
- The SC2336 is bound through the DT graph; its subdevice node takes
  exposure and gain.
- PMU access through the syscon (`fullhan,pmu`).
- Buffers come from the 16 MiB area, now a no-map `shared-dma-pool`.
- At start: CSI-2/wrap/D-PHY init, sensor streaming, ISP soft reset, the
  `cam-own.regs` sequence (computed in C, gamma as a table from
  `scripts/cam-own-regs.py --gamma-c`), then channel 0 on the first
  buffer.
- Only ISPF END is enabled. On each one the driver completes the current
  buffer and points the channel at the next queued one; without a free
  buffer the frame is dropped.
- The bring-up helper node is still in the DT, disabled.

First board test (`cam-grab -e 30 -g 32`):
- 60 frames, then 300 frames with 3 buffers: 30.00 fps, sequence without
  gaps, 0 dropped.
- Complete frame with full chroma (`k1.bin`): Y 0–255, |U−128| 2.3,
  |V−128| 1.8.
- The 4th buffer failed at first: the no-map pool allocates power-of-two
  blocks (3 MiB → 4 MiB), and the 64 KiB scratch area for the mean/SAD
  outputs had split the first block. The scratch area now comes from
  `dma_alloc_pages()` outside the pool, so 4 buffers fit.

Auto exposure (2026-11-11), with the trace from `cam-grab -a 1` (once per
second: mean Y, exposure lines, gain with 32 = 1x):
- From exposure 31 / gain 33: mean Y 98 → 115 within 1 s, then stable
  (27 / 33).
- Lens covered by hand: exposure 53 → 360 → 720 lines (multiples of
  10 ms), gain up to 55. Uncovered: back to 36 / 33 within about 1 s, with
  no oscillation.
- `ae_target` 70: mean Y 73 within 1 s (`ae70.bin`).
- Manual mode (`-e 30 -g 32`) unchanged.
- All runs 30.00 fps, 0 dropped, 4 buffers (`ae1.bin`).

Auto white balance (2026-11-11, gray world). Trace from `cam-grab`: Cb/Cr
are mean chroma of mid-luma points ×10, the gains are 0x200 = 1.0.
- From the office gains (red 0x29a, blue 0x6eb): Cb/Cr 0/0 within 2 s at
  red 0x2a4, blue 0x5b0 (`wb1.bin`, neutral).
- From 1.0/1.0 (Cb −109, Cr −38, yellow-green): Cb/Cr about 0 within 2 s at
  red 0x2a2, blue 0x5a9…0x5b9, stable afterwards (`wb2.bin`, neutral).
- AWB off at 1.0/1.0 for comparison: yellow-green (`wb0.bin`).
- The controls keep their value between runs: after `-w 0`, a plain
  `cam-grab` stays in manual mode.
- AE runs alongside unchanged; all runs at 30 fps, 0 dropped.
- So under this light B ≈ 2.8–2.9 rather than the 3.5 picked by eye
  earlier.

Board test tool: `cam-grab` (rootfs/tools/cam-grab.c). It sets exposure and
gain, streams N frames and reports the fps, dropped frames and Y/C
statistics, then writes the last frame.

### JPEG encoder (`jpeg.ko`, 2026-11-11)

Read from the disassembly; table formats checked against the stock tables.

Resources:
- Registers 0xE8300000 (0x600 bytes; `get_chip_info` +0xF4), loop buffer
  0xE8700000.
- Interrupt: media offset + 13 = GIC SPI 13.
- Limits: width 2304, height 2688.
- ID: +0x00 reads 0x00038401 (`is_hw_work` compares it).

Clocks (vendor kernel `fh_clks` table):

| Clock | PMU bits |
|---|---|
| `jpeg_clk` | divider +0x5C[21:16] (6: 1500/7 = 214 MHz), gate +0x20 bit 30 |
| `jpg_hclk_gate` | +0x20 bit 31 |
| `jpeg_adapt_clk` | +0x1C bit 29 |

Registers (`jpeg_config`, `jpeg_start`, `jpeg_process_isr`, `jpeg_hw_reset`):

| Offset | Content |
|---|---|
| +0x04 | status, bit 0 = frame done (write 1 to clear) |
| +0x08 | 3 |
| +0x0C | length of the output in bytes |
| +0x10 | 0x80000001 = start |
| +0x14 | 2 \| 1 << 4 \| 0x100 \| height << 16 (rotation 90/270: width; bit 2 for a negative t+0x2c) |
| +0x1C | width << 16 (rotated: height) |
| +0x34, +0x38 | output address |
| +0x3C, +0x40 | input Y and C address |
| +0x44 | (height/16 − 1) << 16 \| (width/16 − 1) |
| +0x4C | t+0x1C << 4 \| t+0x18 bit 0; for format 1 scaled by the burst (probably the line stride) |
| +0x50, +0x54 | output buffer size (at most 0xFFFFF0) |
| +0x58 | width << 16 \| rotation << 8 \| format code << 4 \| 1 |
| +0x5C | bit 31 set (chip variant ≠ 16) |
| +0x64 | burst: 1; format 1 with width % 64 = 0: 7, % 32 = 0: 3 |
| +0x6C / +0x70 | reset: write 15, wait until +0x70 = 0 |
| +0x80…+0xFC | quantisation tables, 64 luma + 64 chroma bytes, zigzag |
| +0x100…+0x3FC | Huffman encoder table: per symbol (code & 0xFF, length − 1); AC luma/chroma (16 runs × sizes 1–10, EOB, ZRL, padding, RST0–7), DC luma/chroma |
| +0x400…+0x598 | DHT payload (bits + values of DC/AC luma, DC/AC chroma) |

Input formats: user format 0 → code 0, format 1 → code 2.

All tables are the standard ones of T.81 Annex K; `scripts/jpeg-tables.py`
computes them:
- The Huffman table and the DHT payload are byte-identical to the stock
  tables.
- The quantisation tables (stock: 99 scales, standard table × (i+1)/100)
  match for 97 of 99 scales; the other two differ only in rounding.

The hardware writes a complete JFIF stream itself:
- SOI, two DQT, SOF0 4:2:0, four DHT, SOS, data, EOI.
- The stock driver only checks FFD8/FFD9 and patches the SOF size for
  sizes that are not multiples of 16.

First board test (devmem, input = a camera buffer, format 0):
- Valid JPEG of 1920x1088: 414 KB at scale 50, 246 KB at scale 100.
- Done within one status poll (< 1 ms).
- The content is scrambled in 16x16 macroblocks: format 0 is not linear
  NV12 (probably the VPU tile layout).

Input format test (same frame, scale 50):

| +0x58 | +0x64 | +0x4C | Result |
|---|---|---|---|
| 0x07800021 (format code 2) | 7 | 0x7800 | **correct**, Y PSNR 32.3 dB vs the source (`fa.jpg`) |
| 0x07800021 | 7 | 0 | byte-identical to the above |
| 0x07800001 (code 0) | 1 | 0x7800 | scrambled as before |

So for linear NV12:
- Format code 2 in +0x58 bits 5:4.
- Burst 7 in +0x64 (width a multiple of 64).
- +0x4C does not matter here.
- The line stride is the width.
- One 1920x1088 frame at scale 50 is 371 KB.

MJPEG on the camera node (driver, 2026-11-11):
- `cam-grab -f mjpeg` runs at 30.00 fps with 0 dropped frames and 4
  buffers of 1 MiB.
- The JPEG interrupt (GIC 45 = SPI 13) works: 330 interrupts for 330
  frames, the fallback was not needed.
- AE/AWB run on the internal NV12 frames as before; NV12 still works
  afterwards.

| Quality | Mean frame size | Bit rate at 30 fps |
|---|---|---|
| 50 | 205 KB | 49 Mbit/s |
| 75 (default) | 329–362 KB | 79 Mbit/s |
| 90 | 696 KB | 167 Mbit/s |

- The pictures are correct (`m50/m75/m90.jpg`).
- A large part of the size is sensor noise, since there is no noise
  reduction yet.
- For Wi-Fi (target 15–25 Mbit/s) we need fewer frames per second, lower
  quality, a smaller picture or noise reduction.

## Open questions

1. Is the ARC needed for the ISP path at all, or only for the encoder and audio?
   (Ghidra on `media_process.ko` and `isp.ko`: what goes over `vbus_mesg_write`?)
2. What are the register bases and IRQs of the ISP, MIPI-RX and VPU? (For
   I2C0, see above.) Two sources: `isp.ko` (not stripped), and a class C
   cross-check against the OpenIPC 4.9 patch.
3. ~~What is the I2C address of the sensor, and does its chip ID match the
   SC2336?~~ Answered: 0x30, ID 0xCB3A (see milestone 1 observations).
4. How do the ARC's addresses (`0xbff80000`) map onto DRAM, and what does the
   `xbus` mailbox/IRQ protocol look like?
5. What is the format of the `.hex` tuning blobs, and how far can the ISP run
   without them?
6. Licence: the stock `.ko`, `.so`, `sdk_app`, the ARC image and the `.hex`
   files are proprietary. As with Wi-Fi, they serve only as reference for
   analysis and must not enter the project.

## Possible routes (route A chosen, see the roadmap below)

- **A. Native.** Write our own I2C sensor driver and V4L2 drivers for the
  MIPI-RX and ISP, from reverse-engineered register knowledge. This is the
  cleanest result and the most work. The ISP 3A algorithms would have to be
  rewritten, or the image left untuned at first.
- **B. Shim.** Provide the missing vendor kernel symbols on 6.18 and load the
  stock modules and user space. It is quick to get pixels this way, but
  binary-only, and it conflicts with the project's rule of using no vendor code.
  At most it could be a measurement tool for the analysis.
- **First milestone either way:** I2C0 up, sensor out of reset with its MCLK
  running, and the SC2336 chip ID read from Linux. None of that needs the ARC
  or the ISP.

## Driver architecture and roadmap (decided 2026-11-02)

Route A (native) is the direction. The first goal is **MJPEG**: a V4L2 camera
pipeline plus the on-chip JPEG unit as a V4L2 encoder, at least 720p or
1080p at 15 fps. H.264/H.265 is a later stage.

**Why MJPEG first (class B, imports and strings of the stock modules):**
- `jpeg.ko` imports only `media_process` helpers (clocks, pools, IRQ offset)
  and has no `vbus`/`xbus` references. The JPEG unit very likely works
  without the ARC.
- The H.264/H.265 encoder ("hhe"/VEU) does need the ARC:
  - `enc.ko` makes no ARC calls itself.
  - `media_process.ko` has `softcore.c`, `softcore_veu.c`,
    `softcore_gen_hhe_ctu_info`, `arc_sw_kick` and `flag_arcfwinit` next to
    `vbus_mesg_write`. The ARC (RT-Thread) apparently generates the CTU
    information for the encoder.
  - An open H.264 path therefore means reverse engineering what
    `rtthread_arc.bin` does with the encoder blocks. That is deferred.
- No reference frames, so far less memory than H.264.
- MJPEG over HTTP runs in any browser and in Home Assistant, Frigate etc.

**Bandwidth.** These are estimates, not yet measured:

| Stream | Rate |
|---|---|
| MJPEG 1080p30 | about 40–60 Mbit/s, too much for the 1×1 2.4 GHz Wi-Fi |
| MJPEG 720p30 or 1080p10–15 | about 15–25 Mbit/s, the target range |

**Planned V4L2 structure:**

```
SC2336 subdev → CSI-2 rx subdev → ISP subdev → VPU ch → /dev/videoN (NV12)
                                                            │ dmabuf
                                                            ▼
                                     /dev/videoM  M2M JPEG encoder (NV12 → JPEG)
```

- Media controller from the start. The sensor is already a separate subdev.
- CSI-2 receiver subdev (DesignWare CSI-2 host + D-PHY + wrap): the frequency
  code comes from the sensor's `V4L2_CID_LINK_FREQ`, the link via fwnode
  endpoints.
- Capture node: vb2 dma-contig with `VIDIOC_EXPBUF`, so frames go to the
  encoder without a copy.
- Buffers from a shared CMA pool instead of fixed addresses: the camera and
  the encoder share 64 MiB.
- On stock, VPU channel 0 feeds the encoder directly (loopbuf). Keep it free
  for that later and use the DDR path first.
- Clocks, resets and PMU bits shared by the ISP and the JPEG unit go in a small
  common driver (the role of `media_process.ko`). The ARC loader would join
  it later.
- JPEG encoder as V4L2 M2M (models: `imx-jpeg`, `mtk-jpeg`, `rcar_jpu`).

**Steps:**
1. (Done 2026-11-10: `cam-probe own`, 1044 writes of our own, see
   "Own bring-up without vendor values".) Reduce the 1731 replayed writes
   (`stock-init.regs`) to a minimal set we understand, by bisecting on the board. Use our own scaler coefficients and
   gamma, and switch off the aux units (CPY, BGM, SAD, mean) where possible.
   No vendor table may go into the driver. Tools: `cam-probe groups` (75
   groups), `cam-probe snap` once after boot, then
   `SKIP="…" cam-probe stock` per step, which ends in `result: PASS|FAIL`.
2. Move the bring-up from `cam-probe` into the kernel: DT resources, clocks
   (`isp_aclk` 100 MHz), resets, IRQ servicing (ISPF START/END, ISPP END),
   CMA buffers.
3. Split it into the CSI-2, ISP and VPU subdevs plus the video node, then
   streaming with vb2.
4. Pass `v4l2-compliance -m /dev/media0 -s`; capture with `v4l2-ctl`,
   GStreamer and ffmpeg.
5. Minimal AE (sensor exposure/gain from the VPU Y mean), so the image is no
   longer overexposed.
6. (Done 2026-11-11, as MJPEG on the camera node instead of an M2M
   driver.) Reverse engineer the JPEG unit (`jpeg.ko`, Ghidra; check the
   throughput first: `jpeg-maxw`/`jpeg-maxh`), then the M2M driver.
7. MJPEG streaming end to end (e.g. GStreamer to HTTP multipart), at 720p via
   VPU scaling or 1080p at 15 fps.
8. Later: H.264 via the ARC, ISP tuning, day/night switching.
