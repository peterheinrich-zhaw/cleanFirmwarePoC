# FH8626V200 Open Bring-up: Approach, Steps, Results and Provenance

2026-10-12

## Summary

The FullHan FH8626V200 camera SoC now boots from its own SPI NOR image: boot ROM, then our 2nd-stage loader, then our U-Boot 2026.07 port, then our Linux 4.9.129 port, ending in a BusyBox shell from an embedded initramfs. The project repository holds only our own work: sources, patches against pinned upstream releases, and build scripts.

This document explains the approach and the steps, and above all where each piece of information came from. The provenance table is the core: a table that gives every register value, address and artefact an origin class and a confidence label. The project notes record one vendor-derived item inside the flash image, the DDR register script. Whether there are others is an open check, listed under Open items.

## Starting point and ground rules

**Target.** FullHan FH8626V200 with 64 MiB in-package DRAM, a Puya P25Q64SH SPI NOR (8 MiB, SOIC-8) and a UART. There is no JTAG header. The flash can be reprogrammed externally with flashrom at any time, so a bad image cannot brick the board.

**Constraints.** The project wants full source control and no chainloaded vendor code. It also wants a legally well-documented port: every constant should be traceable to a source, and where it is not, that has to be said.

**Method.** Facts enter the project through six routes. Each fact in the provenance table carries one of these origin classes.

| Class | Origin | Citable? |
| --- | --- | --- |
| A | Observed on the board: register reads in U-Boot, oscilloscope traces, boot logs, kernel `/proc` output | Yes, by reproducing the observation |
| B | Analysis of the stock firmware: Ghidra disassembly of the stock first stage and stock U-Boot, and the layout of the stock flash image | Yes, as analysis of a binary; the binaries themselves are not redistributed |
| C | Public GPL source: the OpenIPC/FullHan Linux 4.9 patch, which is a patch to a GPL-2.0 kernel tree | Yes, by file name inside the patch |
| D | Other public open-source projects and vendor documents used only for comparison of register patterns: AMD/Xilinx UG1087, STM32MP1 and ARM Trusted Firmware DDR drivers | Structural support only; no code or values taken |
| E | Our own design and code: loader, U-Boot board port, Linux machine port, scripts, flash layout | Yes, it is the repository |
| F | General knowledge of the AI assistant about upstream APIs and ARM architecture | No. Not accepted as the source of a register value unless a board observation or a public source backs it |

**Confidence labels.** The labels are the ones used throughout the project.

- CONFIRMED: seen on the board, or quoted from a named public source at exactly that place.
- STRUCTURAL: the pattern is supported by independent open projects, but without exact register names.
- INTERNAL: supported only by our own analysis.
- UNCONFIRMED: no support yet.

A claim that could not be sourced was downgraded, not kept. Example: a match of one DDR offset to a public register name was withdrawn as coincidence once the surrounding structure pointed to a different block.

**Role of AI assistance.** The work was done in dialogue with Claude, Anthropic's AI assistant. Claude drafted code, patches, scripts and analysis. The project lead ran every build and test on the real board and supplied the disassembly, the oscilloscope observations, the logs and the decisions about what to adopt. Authorship of the repository therefore combines both; this is disclosed here so that nobody has to infer it.

## Boot chain and flash layout

The board starts from the boot ROM and ends in a shell in five stages.

1. **Boot ROM** (SoC mask ROM, not ours). It reads the flash image header, replays the DDR initialisation script from the DDR_0 section, trains the DRAM and prints `ROM: Init DDR..Training done.` on the UART.
2. **2nd-stage loader** (ours). It runs from on-chip SRAM at 0x10008000. It brings up the SPI controller, detects the flash by its JEDEC ID, copies U-Boot into DRAM at 0xA0000000 and jumps there.
3. **U-Boot 2026.07** (our board port). It initialises the console and DRAM size, and gives a prompt. `sf read` loads the kernel blob from flash to 0xA1000000 and `bootz` starts it.
4. **Linux 4.9.129** (our machine port). zImage, device tree and initramfs form one blob. The device tree is appended to the zImage.
5. **BusyBox** (init in the initramfs). It mounts `/proc`, `/sys` and `/dev`, and starts a shell on the console.

Flash layout as built (8 MiB):

| Offset | Size | Content | Origin |
| --- | --- | --- | --- |
| 0x000000 | 32 KiB | Image header, DDR_0 script and 2nd-stage loader | Header format from stock image analysis (B); loader is ours (E) |
| 0x008000 | 384 KiB | U-Boot (`u-boot.bin`, about 340 KiB used) | Ours (E) |
| 0x070000 | 64 KiB | Reserved for U-Boot environment, not yet used | Design decision (E) |
| 0x080000 | 4 MiB | Kernel blob: zImage, appended DTB, embedded initramfs (about 2.3 MiB) | Ours (E) |
| 0x480000 | rest | Free | |

The offsets and size limits live in one block of variables at the top of the top-level Makefile. Image assembly checks each part against its slot, so an oversized U-Boot or kernel fails the build instead of silently overwriting its neighbour.

## How each stage was built

**1. Flash image format and boot ROM behaviour.** The stock flash image was analysed first (class B). Its header starts with the magic `2BL*OABD`. A section table at 0x100 holds 64-byte entries, each with a checksum of `zlib.crc32(padded section) ^ 0xFFFFFFFF`. The DDR_0 section is a script of 249 records of 16 bytes: address, value, mask, tag. Tag 2 means poll, 3 write, 4 write plus delay, 5 block-end write. The boot ROM trains the DRAM only when the section carries the right type marker (0x78503); that was observed on the UART (class A). Our own Python image builder writes this format.

**2. SPI NOR access from the loader.** The flash controller is a DesignWare SSI at 0xF0500000. Chip select needs two signals at once: bit 0 of the SER register and GPIO0 bit 6 driven low. That was found with an oscilloscope (class A), after disassembly of the stock first stage (class B) gave the candidate registers. The vendor wrapper registers at +0xF4 and +0xF8 need a fixed setup sequence, taken from the stock first stage. The JEDEC ID reads 85 60 17, which is a Puya P25Q64SH. With this the loader copies images from flash to DRAM, and a test program loaded and ran at 0xA0000000.

**3. Public GPL cross-check.** The OpenIPC/FullHan Linux 4.9.129 patch was then searched for the same facts (class C). It names the SPI register map including the two wrapper registers, the GPIO register layout, the chip-select pins (`FH_SPI0_CS0 = 6`, so GPIO0 bit 6), and the base addresses of the peripherals. In substance these agreed with what we had found by disassembly and oscilloscope; the one difference, the release order of the two chip-select signals, is noted in the table. Two things the patch changed: it named the wrapper registers CCFGR and OPCR, and it corrected our reading of the idle check (bit 9 must be set, bits 10 and 3 must be clear).

**4. U-Boot 2026.07 port.** Our board support adds a `fh8626` board and machine, a DesignWare-compatible 16550 UART at 0xF0700000 with register shift 2, and the upstream designware SPI driver, adapted for the extra chip-select GPIO (the exact change is in the U-Boot patch series). `sf probe` detects the flash as p25q64h, and `sf read` results match host CRCs. U-Boot is linked for 0xA0000000, where the loader places it, and reports `CPU: FH8626, chip id 0x20102801` and `DRAM: 64 MiB`. `bootz` needed `CONFIG_SUPPORT_PASSING_ATAGS` and a boot-parameter address of 0xA0000100 because the kernel carries its own appended device tree.

**5. Probing the CPU from U-Boot.** A short probe run in U-Boot read the CPU identification and interrupt hardware (class A): Cortex-A7 r0p5, an ARM generic interrupt controller at the address given by CBAR, and a 24 MHz generic timer whose frequency register is zero. That settled the design of the Linux port. The OpenIPC patch targets older FH885x chips with an ARM1176 core and a custom interrupt controller, so its machine code is not reused. The FH8626V200 is a plain ARMv7 multiplatform machine with the standard GIC and arch-timer drivers.

**6. Linux 4.9.129 port.** The kernel is the official 4.9.129 tarball plus the OpenIPC patch, kept verbatim as patch 0001, plus our commits. Ours add a small machine file (`arch/arm/mach-fh8626`), a device tree (`fh8626v200.dts`) and a defconfig. They also carry build fixes for current binutils and GCC (assembler section-flag syntax) and a Kconfig dependency fix so a vendor driver is no longer enabled on every ARM kernel. The device tree supplies the timer frequency because the hardware register reads zero.

**7. Root filesystem.** A static BusyBox 1.37.0 and a tiny `/init` are packed as an initramfs inside the kernel image. The first boot reached a BusyBox shell, `/proc/interrupts` showed the arch timer and the UART interrupt counting, and `sleep 1` took one second.

## Provenance table

Most hardware facts were observed on the board or taken from the stock firmware, and then cross-checked against the public GPL patch. The two things with no public confirmation are the V200 chip ID and the meaning of most DDR table entries. Classes A to F are defined above.

| Item | Value | Class | Where to check it | Label |
| --- | --- | --- | --- | --- |
| Chip ID | PMU +0x00 reads 0x20102801; PMU IP version +0x04 reads 0x00008503 | A | `md.l 0xf0000000 4` in U-Boot | CONFIRMED (observed) |
| Chip ID in public source | The patch lists only FH8626V100 = 0x18112301 (512 Mbit). No V200 entry exists. The value does not match our board | C | `arch/arm/mach-fh/fh_chipid.c` in the patch | V200 value is first-hand only |
| CPU core | Cortex-A7 r0p5 (MIDR 0x410FC075), ARMv7-A, one core, LPAE, hardware divide; CTR 0x84448003 | A | CP15 reads in U-Boot; `/proc/cpuinfo` in Linux | CONFIRMED (observed) |
| Interrupt controller | CBAR 0x30000000; GIC distributor 0x30001000, CPU interface 0x30002000; GICD_IIDR 0x0100143B; GICD_TYPER 0xFC02 (96 lines) | A | Reads in U-Boot; the kernel takes interrupts through it. The CPU interface offset follows the standard Cortex-A7 layout (class F) and is exercised by the working kernel | CONFIRMED (observed) |
| Generic timer | 24 MHz counter; CNTFRQ reads 0, so the device tree supplies the frequency | A | Counter delta over a 10 ms delay; Linux `sleep 1` takes one second | CONFIRMED (observed) |
| UART0 | Base 0xF0700000, 16550-compatible, register shift 2, 32-bit access | A, B, C | Working console; stock first-stage code; standard 16550 status bits in the patch | CONFIRMED |
| UART clock | 16,666,667 Hz | A | Gives correct 115200 baud; Linux reports base_baud 1041666 | CONFIRMED (derived) |
| UART0 interrupt | GIC SPI 30 (hwirq 62) | A | Count in `/proc/interrupts` rises with console input | CONFIRMED for this one interrupt; no other SoC interrupt number is verified |
| SPI controller | DesignWare SSI at 0xF0500000 | A, B, C | Oscilloscope; stock disassembly; `SPI0_REG_BASE` in the patch | CONFIRMED |
| SPI wrapper registers | CCFGR at +0xF4, OPCR at +0xF8 and the init sequence | B, then C | Stock first stage routine at 0xAA8; `fh_spi_common.h` and `fh_spi_multi_wire.c` in the patch | CONFIRMED (names and bits from C, sequence from B) |
| Chip select | SER bit 0 and GPIO0 bit 6 driven low, both required | A, C | Oscilloscope; `FH_SPI0_CS0 = 6` and `Spi_EnableSlaveen` in the patch | CONFIRMED. The patch releases the two signals in the opposite order to our code |
| GPIO banks | 0xF0300000 + n × 0x3D00000; bank 1 at 0xF4000000; data register at +0 | B, C | Stock first stage; `GPIO0_REG_BASE`, `GPIO1_REG_BASE` in the patch | CONFIRMED |
| Flash chip | JEDEC ID 85 60 17, P25Q64SH, 8 MiB, 4 KiB erase size | A | Loader probe; `sf probe 0:0` in U-Boot | CONFIRMED (observed) |
| PMU base | 0xF0000000 | A, C | `PMU_REG_BASE` in the patch; reads work on the board | CONFIRMED. Reset-register bits are from the patch and not exercised |
| Other peripheral bases | OTP/eFuse 0xF1600000, timer 0xF0C00000, watchdog 0xF0D00000, DMA 0xE0300000, DDRC 0xED000000 | B, C | Header names in the patch; addresses also seen in stock code | Named in a public source; not exercised on the board |
| DRAM | 64 MiB at 0xA0000000 | A | DRAM test; `DRAM: 64 MiB` in U-Boot | CONFIRMED (observed) |
| DDR_0 register script | 249 records of address, value, mask, tag | B | Copied as data from the stock flash image; used as an opaque script | Works on the board. Meaning of the entries: 0 CONFIRMED, 98 STRUCTURAL (39%), 11 INTERNAL (4%), 140 UNCONFIRMED (56%) |
| Flash image format | Magic, section table, checksum rule | B | Stock image analysis; reimplemented in our builder | INTERNAL |
| Boot ROM behaviour | Trains DRAM from DDR_0, prints status on UART | A | UART log at power-up | CONFIRMED (observed) |
| Flash layout | U-Boot 0x8000, environment 0x70000, kernel 0x80000 | E | Top-level Makefile | Our design |
| U-Boot port, Linux machine port, device tree, scripts | | E | The repository | Our code. Written with AI assistance, tested on the board |
| Pin multiplexing tables | None found for any FH8626 variant | | | UNCONFIRMED; not needed so far |

**Family caveat.** The patch covers the FH885x family and FH8626V100. Where it confirms a register layout, the layout is expected to apply to V200 because the whole family shares one driver tree, but the patch contains nothing specific to V200. Every row marked CONFIRMED by class C was also checked on the V200 board by working behaviour, except the "other peripheral bases" and the PMU reset bits.

## Third-party code, patches and licences

No upstream source is stored in the repository. It is downloaded as a release tarball and verified against a SHA-256 hash pinned in `config/versions.mk`. Our changes are stored as patches on top.

| Component | Version | Licence | Obtained how | In the repository? |
| --- | --- | --- | --- | --- |
| Linux kernel | 4.9.129 | GPL-2.0-only | Official tarball from kernel.org | No, only our patches |
| U-Boot | 2026.07 | GPL-2.0-or-later | Official tarball from denx.de | No, only our patches |
| OpenIPC/FullHan patch | Adds FH885x and FH8626V100 support to the kernel tree | GPL-2.0, as part of the kernel tree; copyright Shanghai Fullhan Microelectronics | Supplied by the project lead as `fullhan_openipc.patch` from the OpenIPC/linux repository (branches `fullhan-fh8852v100`, `fullhan-fh8852v200`) | Yes, verbatim as kernel patch 0001 |
| BusyBox | 1.37.0 | GPL-2.0-only | Source built by `rootfs/build_rootfs.sh` | No |
| Toolchains | `arm-none-eabi-gcc` for loader and U-Boot, `arm-linux-gnueabi-gcc` (soft-float) for the kernel | GPL with runtime exceptions | Distribution packages | No |
| flashrom | Programs the SPI NOR from a host | GPL-2.0 | Host tool only | No |
| Stock firmware | Flash dump, stock first stage and stock U-Boot binary | Vendor, not licensed to us | From the camera itself | No. Analysed for facts; the DDR_0 script is copied from it as data |
| Public documents | AMD/Xilinx UG1087, STM32MP1 and ARM Trusted Firmware DDR drivers, a Rockchip RK3576 U-Boot header | Various | Consulted to compare register patterns | No. No code or value from them is in the repository |

**Our own code.** The U-Boot board port and the Linux machine support are written to be merged into GPL-2.0 trees, so they should be licensed GPL-2.0-only (kernel) and GPL-2.0-or-later (U-Boot). The licence for the standalone loader and the scripts is an open decision for the project lead; a permissive licence is possible for these, but not for the patches.

**Evidence for the OpenIPC patch.** The repository should record the source URL, the retrieval date and the commit of the OpenIPC branch next to patch 0001. The session record shows which branches the patch came from but not the exact commit, so this is an open item.

**Vendor-derived data.** The DDR_0 script is numerical data from the stock flash image: register addresses and values, with no code. Whether copying it is acceptable, for example as functional data needed for interoperability, is a legal question this document does not answer. It is listed here so that the question can be put to counsel with the facts in hand.

## Project layout and reproducibility

The repository holds only what we wrote, in the low megabytes. Upstream releases are pinned and our changes are patch series on top of them. This replaced an earlier layout with git submodules, which pulled the full upstream history into the repository and slowed every git operation.

```
Makefile             top-level: fetch, prepare, build image, flash, export
config/versions.mk   upstream versions, URLs, SHA-256 hashes
patches/linux/       kernel patch series (0001 is the OpenIPC/FullHan patch)
patches/u-boot/      U-Boot patch series
scripts/             fetch, prepare_tree, export_patches
rootfs/              BusyBox build and initramfs file list
(loader sources)     2nd-stage loader
dl/  work/  build/   git-ignored: tarballs, working trees, build output
```

The workflow is the same for both trees:

1. `make fetch` downloads each tarball and refuses it unless the SHA-256 matches. With an empty hash it prints the hash and stops, so the first download is trusted once and then pinned.
2. `make prepare` unpacks it into `work/`, commits it as a pristine release with a fixed author and date (so the commit hash is identical on every machine), tags it `fh-vanilla`, and applies the patch series with `git am`.
3. Development happens in `work/linux` and `work/u-boot` as ordinary git repositories. They are throw-away and are not part of the project repository.
4. `make export` turns the commits since `fh-vanilla` into patch files with `git format-patch --no-signature --zero-commit`, so the output is byte-identical on every export. It refuses to run with uncommitted changes.
5. `make reprepare` rebuilds the trees from tarball plus patches, and refuses to run if there are commits that were not exported.

The round trip (wipe, `reprepare`, export again, patches byte-identical) was tested with stand-in tarballs. On the real trees, the project lead reports that the new layout compiles and boots. Two checks should be recorded in the repository: the tree hash of the old tree equals the tree hash rebuilt from patches (`git rev-parse HEAD^{tree}`), and the SHA-256 values pinned in `config/versions.mk` equal the hashes published by kernel.org and denx.de.

## Verification and results

**Rule, in force since the kernel bring-up.** A kernel is not booted without comparing a checksum with the host. The top-level `make bootcmd` prints the exact U-Boot commands and the expected CRC32 of the kernel blob; on the board, `crc32` of the RAM copy must equal it before `bootz`. A kernel rebuild changes bytes (build counter, timestamps) without changing the size, so a stale flashed copy looks plausible. The check caught exactly that once.

| Check | Result | Evidence |
| --- | --- | --- |
| Flash read path | U-Boot image of 0x55298 bytes read back from flash; CRC32 3aa94a50 equals the host CRC | `sf read` and `crc32` in U-Boot |
| Flash identification | Detected as p25q64h, 8 MiB, 4 KiB erase size | `sf probe 0:0` |
| U-Boot start | Banner reports `CPU: FH8626, chip id 0x20102801` and `DRAM: 64 MiB` | Serial log |
| CPU and interrupt hardware | Cortex-A7 r0p5; GIC at CBAR 0x30000000; 24 MHz counter | Probe run in U-Boot |
| Kernel start | Kernel blob of 0x24C692 bytes loaded from flash; boot reaches a BusyBox shell | Serial log; host CRC e97fda1e |
| Interrupts and timing | Arch timer and UART interrupts count up; `sleep 1` takes one second | `/proc/interrupts`, shell session |
| SPI NOR erase and write from U-Boot | Not working. `sf erase` and `sf write` report OK, but a read-back stays 0xFF | Serial log; cause not diagnosed |

The last row is the only failed check. It is parked until the board is at hand again, and it blocks booting from flash without typing, because that needs the U-Boot environment to be written.

## Open items and non-claims

What this document does not claim, and what still has to be checked before the repository is described as fully clean.

**To verify before publication**

- [ ] Compare `flash_image.bin` byte for byte with the stock dump outside the DDR_0 script, to show that no other vendor-authored bytes are in the image. The session record does not settle this for the first-stage area.
- [ ] Record the source URL, retrieval date and commit of the OpenIPC patch next to kernel patch 0001.
- [ ] Compare the pinned SHA-256 hashes with the published ones.
- [ ] Record the tree-hash comparison between the old trees and the trees rebuilt from patches.
- [ ] Add licence files and choose the licence for the loader and scripts. The repository currently has only a licence file created by the hosting service.
- [ ] Boot once from a clean clone of the new repository.
- [ ] Update `fh8626_confirmed_regs.h` with the values observed on the board, labelled "observed on hardware". It currently cites only the OpenIPC patch.
- [ ] Put the DDR_0 question to counsel (see the licence section).

**Not claimed**

- The meaning of most DDR table entries is unknown. 140 of 249 are UNCONFIRMED. The table works as an opaque script.
- The patch has no FH8626V200 entry, so the V200 chip ID rests on one observation of this board.
- Only one SoC interrupt number (UART0 on SPI 30) is verified. Other interrupt numbers have no support yet.
- Pin multiplexing for FH8626 has no public source.
- VFP and NEON are disabled in the kernel configuration; their presence in the hardware is not checked.
- U-Boot runs with caches off and has no working `reset_cpu()`; the real reset path (watchdog or PMU) is not implemented.
- Writing the SPI NOR from U-Boot does not work yet.
- The register facts come from one board of one hardware revision.

## Reproducing the result

You need a Linux host with git, make, Python 3, `arm-none-eabi-gcc`, `arm-linux-gnueabi-gcc` (soft-float), the usual kernel build dependencies, and flashrom with a SPI programmer attached to the flash. Both toolchain prefixes can be overridden with `CROSS_COMPILE` and `KERNEL_CROSS`.

```
git clone <repository> open-cam-fw && cd open-cam-fw
make fetch        # downloads tarballs, checks SHA-256
make prepare      # unpacks into work/, applies the patch series
make              # loader + U-Boot + kernel + initramfs -> flash_image.bin
make flash        # writes the image with flashrom
make bootcmd      # prints the U-Boot commands and the expected CRC32
```

Power the board with the UART at 115200 baud. U-Boot stops at its prompt. Enter the commands printed by `make bootcmd`, for example:

```
sf probe 0:0
sf read 0xA1000000 0x80000 0x<blob length>
crc32 0xA1000000 0x<blob length>   # must equal the CRC printed by make bootcmd
bootz 0xA1000000
```

The kernel prints its boot log, then `FH8626V200: userspace is up (BusyBox)` and a shell. The environment is not stored yet, so the commands have to be typed after each reset.
