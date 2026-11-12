$# FH8626V200 bare-metal "Hello World" (DRAM + PLL + best-effort pinmux + UART)

Goal: prove we can bring up DRAM/PLL and get a UART string out, with
full control over what runs on this board, as a proof-of-concept
milestone before the real modern-U-Boot port.

**Current status:** our `DDR_0` flash section is now verified
byte-for-byte identical to the original firmware's -- header, full
64-byte section-table entry, and all 4224 bytes of padded content,
confirmed by direct comparison, not just a matching checksum. This
took two real bugs to find and fix, both the kind that look
structurally fine (avoid `ERR:209`/`216`) while silently producing
wrong content:

1. **DDR\_0's whole table entry uses a different field convention
 than `ram`/`uboot`.** Its `g1` group is `[0x78503, 0, 0, 0]`
 (plausibly a type/marker the boot ROM checks to recognize "real
 DRAM-training section"); its padded content size is `0x1080`
 bytes, not the `0xfc0` our generic 64-byte-rounding formula would
 give; and `g3[0]`/`g3[2]` also don't follow the generic formula
 (`0x43`/`0x1a` vs. the `4`/`0` we were computing). `mkimage.py`
 builds this entry from named fields (`build_ddr0_entry()`,
 mirroring `flash_layout.h`'s `section_entry_ddr0` struct) rather
 than the generic per-field formula or a raw hex blob -- the
 unexplained fields get honestly-named constants
 (`DDR0_UNKNOWN_0X10` etc.), not a plausible-sounding guess.
2. **The header's padding convention was also wrong**: bytes
 `0x28-0xFF` are `0xFF` (erased flash) in the original, not zero
 as an earlier version of this script assumed and overwrote.
 `build_header()` now only returns the meaningful first `0x28`
 bytes and leaves the rest alone.

Both were found the same way: comparing our rebuilt header+table+
`DDR_0` region against the real dump byte-for-byte turned up exactly
where they diverged, rather than guessing. This was prompted by a
very informative hardware experiment: flashing only the original
firmware's first 16KB (header+table+`DDR_0`, no `ram`/`uboot`
content) produced `"ROM: Init DDR..Training done."` -- the first
time that message had ever appeared, in any test, including every
one of our own from-scratch builds up to that point. That proved
`DDR_0`'s _content_ genuinely gets consumed by the boot ROM, not
just checked for presence, and gave us a known-good reference to
diff our own reconstruction against.

Two things worth being transparent about from this same session: an
earlier theory that a `num_sections` header field needed correcting
turned out to be contradicted by an earlier successful structural
test and was walked back rather than carried forward unverified. And
a separate line of investigation (reusing the vendor's own compiled
bring-up functions directly) was reverted in favor of this cleaner,
byte-verified fix -- that work isn't wasted, just set aside.

**Confirmed on hardware, with a significant follow-up finding.**
`"Init DDR..Training done."` now appears in our own build's output
for the first time -- the `DDR_0` fix worked. But our own code then
tried to redundantly replay that same DDR\_0/PLL table a second time
(`apply_init_table()`), and the board **hard-reset** partway through
(around register `0xed000008`). The clean explanation: the boot ROM
already fully trains DRAM itself before jumping to our code, and
re-poking the same DRAM controller registers afterward -- while it's
already live -- is both unnecessary and apparently harmful. That
replay has been removed entirely (see "DRAM/PLL replay removed"
below); `main.c` now trusts the boot ROM's own training and moves
straight to pinmux/UART/DRAM-test.

## What this does and doesn't do

- **No longer replays the DRAM/PLL register table.** The boot ROM
 does real training itself (confirmed: `"ROM: Init DDR..Training $  done."`) before ever reaching our code -- our own redundant replay
 caused a hard reset and has been removed. `DDR_0` is still shipped
 in the flash image (`mkimage.py`), since the boot ROM requires it
 structurally and clearly consumes it -- we just don't also run it
 ourselves at runtime anymore. The old C-side interpreter and table
 (`init_table.c`/`dram_table.c`) were removed as dead code once this
 was confirmed; the 249-entry table lives only in `mkimage.py`'s
 `DDR_TABLE` now.
- Does **one** best-effort pinmux/syscon write (clear bit 0 of
 `0xf0000234`) because it's the only pinmux-looking operation
 actually traced in the known-good boot path. Its real meaning is
 unconfirmed.
- Prints "Hello World" over UART using **confirmed** register
 offsets (base `0xf0700000`, TX at `+0x0`, TX-ready status at
 `+0x7C` bit `0x2` -- matches a Synopsys DesignWare APB UART).
- Does **not** program a baud-rate divisor. Hardware testing
 confirmed the boot ROM's own console runs at 115200-8N1, and since
 the boot ROM already owns the UART before our code runs, inheriting
 its configuration is the natural choice, not just a guess of
 convenience.
- Touches CP15 for exception-vector setup (SCTLR.V clear + VBAR
 install, see below) -- not cache/MMU configuration. This reuses
 the exact sequence confirmed present in the vendor's own real U-Boot
 startup code, so it's piggybacking on known-working behavior rather
 than a new guess.

## Build on macOS

Install a bare-metal ARM GCC via Homebrew:

`text $brew install --cask gcc-arm-embedded $`

(If that cask isn't available for your macOS/Homebrew combination:)

`text $brew tap ArmMbed/homebrew-formulae $brew install arm-none-eabi-gcc $`

Then:

`text $make $`

This produces `hello.elf` and `hello.bin`. `hello.bin` must be
**\<= 16384 bytes (0x4000)** to drop straight into the vendor's
reserved "ram" slot -- `make` prints the size at the end.

## Building a flashable image (mkimage.py)

See `flash_layout.h` for the full, honest breakdown of what's
structurally understood vs. not, with C structs for every portion of
the flash header/table/DDR\_0 region -- including the entire DRAM
controller register file as individually-named fields (`reg_0xNNN`),
not left as an opaque array. `mkimage.py`'s own DDR\_0 entry
construction (`build_ddr0_entry()`) mirrors that header's
`section_entry_ddr0` struct field-by-field, so neither file has a
raw hex/base64 blob standing in for data we could otherwise name.

This builds a full 8 MiB flash image (matching the `P25Q64SH`, 64 Mbit)
**entirely from scratch** -- no dependency on the original vendor dump
at build time. Every byte defaults to `0xFF` (erased flash); only the
following are ever written:

`text $0x000-0x0FF  header: magic "2BL*OABD", a handful of fields, sync pattern 0xaa5555aa $0x100-0x17F  section table: 2 entries (DDR_0, ram) $0x180-0x0FFF 0xFF (unused) $0x1000-0x1F8F DDR_0 register table (249 entries, embedded as a Python constant) $0x1F90-0x3FFF 0xFF (unused) $0x4000-...   hello.bin itself (load/exec addr 0x10008000) $...-0x7FFFFF 0xFF (unused -- everything else) $`

The header/magic constants and the DDR\_0 table are hardcoded in
`mkimage.py` with their provenance documented in the module
docstring (originally extracted from the vendor dump, but the tool
no longer reads that file at build time -- they're plain Python
constants now).

`text $make image $`

produces `flash_image.bin`, ready to flash to the `P25Q64SH` at
offset 0.

### Two deliberate simplifications from the original layout

1. **`DDR_0` section is mandatory, not optional.** An earlier version
 of this tool omitted it (reasoning: our own C code already
 replays the table at runtime, so why duplicate it in flash?). On
 real hardware that produced an immediate boot ROM error
 (`ERR: 209`) even against a fully erased flash chip, before any
 of our own code ran -- confirming the boot ROM itself requires a
 `DDR_0`-named entry to be present, independent of whatever our
 own code does later. So it's back, unconditionally.
2. **Checksum fields set to 0**, not computed, for both sections. We
 tested the original dump's checksum field against standard CRC32,
 CRC32 without the final XOR, Adler32, and little/big-endian
 additive and XOR 32-bit sums -- none matched the known-good value,
 so zero is an honest placeholder, not a disguised guess. (The
 `ERR: 209` case above already had checksum=0 too, so a zero checksum
 alone doesn't appear to be what triggered that particular error --
 though that isn't proven in isolation, since two things changed
 between the working-header and broken-header attempts at once.)

## Boot ROM error codes (decoded empirically)

Two distinct numeric codes have shown up, with different meanings:

- **`ERR: 209`** -- a required section is structurally missing (seen
 when `DDR_0` was entirely absent from the table).
- **`ERR: 216`** -- a section's checksum doesn't match its content,
 printed once per failing section. Confirmed directly: flashing only
 the original firmware's first 16KB (header+table+`DDR_0`, leaving
 `ram`/`uboot`'s actual flash bytes stale/wrong) produced `216` twice
 -- once per code section with now-mismatched content -- while
 `DDR_0` itself trained successfully first (`"Init DDR..Training $  done."` printed before the errors). That same test also settled
 the "still unconfirmed" question below: the checksum **is**
 enforced, not merely structural.

## Checksum algorithm (confirmed)

We recovered the real algorithm by testing it against the original
dump's known-good (content, checksum) pairs for `ram` and `uboot`:

`text $checksum = zlib.crc32(padded_section_bytes) ^ 0xFFFFFFFF $`

Two details had to be right together, or nothing matched: it's the
CRC32 register value _before_ the standard final XOR (not the normal
zlib output), and it covers the section's _padded_ size with the
gap **zero-filled** (not `0xFF`) -- not just the raw content length.
`mkimage.py` now computes this for real and zero-pads each section's
on-flash bytes to match what's checksummed. This same algorithm also
reproduces `DDR_0`'s own known-good checksum (`0xf9a428a0`) exactly,
once its padded size is corrected to `0x1080` (see above) -- useful
independent confirmation that both the algorithm and that padding
value are right, not just a coincidence.

## Suggested test order

1. Build with `make image` and flash the resulting `flash_image.bin`
 (full 8 MiB, built from scratch, now verified byte-identical to
 the original for the entire header+table+`DDR_0` region).
2. **First and most important: watch for `"ROM: Init DDR..Training $   done."` between `"Use nor flash."` and `"Ok"`.** This single line
 is now the main thing being tested:
 - **It appears** -- the g1 fix worked, the boot ROM recognizes
 DDR\_0 as a real training section and runs it for real. This is a
 significant result on its own, independent of whatever happens
 afterward.
 - **It's still absent** -- the g1 fix wasn't the (whole) answer;
 something else still differs between our header/table and what
 the boot ROM needs to recognize this as a real DRAM section.
 **This is what happened on the most recent hardware test**
 (`01` then silence, no `T`, no `2` -- see point 4 below for what
 that specific pattern means).
3. Watch UART at 115200-8N1 (confirmed correct) for the checkpoint
 sequence:

`text $0        <- start.S, before ANY C code, mode switch, or stack setup. 
$           Proves the CPU reached 0x10008000 and can already talk 
$           to the UART. 
$1        <- main() reached: C environment is working. 
$2        <- no-op now (DRAM/PLL replay removed -- see "DRAM/PLL 
$           replay removed" below). Kept as a checkpoint purely so 
$           the numbering stays stable across this change. 
$3        <- survived the pinmux guess (clearing bit 0 of 
$           0xf0000234). 
$4        <- survived uart_init() (currently a no-op). 
$Hello World $`

### DRAM/PLL replay removed

A hardware test with the corrected `DDR_0` section produced
`"Init DDR..Training done."` for the first time in any of our own
builds -- confirming the boot ROM performs real DRAM training
itself, before ever jumping to our code. Our own code then tried to
redundantly replay the same DDR\_0/PLL table (`apply_init_table()`),
and the board **hard-reset** partway through (the whole boot
sequence restarted from `"Use nor flash."`, around register address
`0xed000008`). That's consistent with re-poking an already-active
DRAM controller's trigger registers after the boot ROM has already
brought it up -- or possibly an unrelated watchdog timeout from the
extra time our UART-tracing version spent on 249 entries. Either
way: this replay is both unnecessary (the boot ROM already did it)
and apparently harmful, so `main.c` no longer calls it.
`init_table.c`/`dram_table.c` were removed as dead code; the
249-entry table lives only in `mkimage.py`'s `DDR_TABLE` now, should
a from-scratch replay ever be needed on different hardware.

4. **`Hello World` printing is not the finish line.** Everything up
 to that point runs entirely from on-chip SRAM and would look
 identical whether or not DRAM was ever actually trained. The
 build also runs a real DRAM write/read-back self-test afterward
 (`dram_test()` in `main.c`, preceded by a single-word
 `dram_probe_single()` with its own checkpoints) at `0xa0000000` --
 inferred, not confirmed, from the original vendor "uboot"
 section's load/exec address (`0xa0800000`) only making sense as a
 jump target if DRAM is mapped there already. Outcomes:

 - **`wWrR probe=0x12345678` then `DRAM TEST PASS`** -- DRAM is
 genuinely working. If `"Init DDR..Training done."` also
 appeared, that confirms the g1 fix was the real missing piece all
 along, and this is the real milestone completion.
 - **`wWrR probe=...` then a `FAIL` line** -- the single word
 round-trips fine but the fuller pattern test doesn't; the
 printed address/expected/got values are the next thing to
 chase.
 - **Stops at `w` (no `W`)** -- the write itself never completes.
 Bus-level stall or data abort during a write, before any read is
 attempted.
 - **`wW` then stops (no `r`/`R`)** -- write completed, but this
 specific line shouldn't be reachable without the read also
 starting almost immediately; if you see this, something odd is
 happening between the two checkpoints.
 - **`wWr` then stops (no `R`)** -- write succeeded, read hangs.
 Points more specifically at a read-side issue (unusual, but
 narrows things).
 - **Nothing at all, not even `w`** -- this is what happened on an
 earlier hardware test. This build installs a minimal exception
 vector table (`vectors.S`, via VBAR -- the same SCTLR.V-clear +
 `mcr p15,0,r0,c12,c0,0` sequence confirmed present in the
 vendor's own real U-Boot startup code) so that a genuine data
 abort reports itself instead of hanging silently:

`text $EXCEPTION D LR=0x... FSR=0x... FAR=0x... $`

 `D` = data abort (the expected case for a bad memory access);
 `FAR` is the faulting address (should read back as `0xa0000000`
 if this really is about that address); `FSR`'s low 4 bits are
 the fault status code -- with the MMU off, an external abort
 (bus/memory-controller-reported error) is the most likely value
 if the DRAM controller/bus genuinely has no valid mapping there
 yet. Any other single letter (`U`/`S`/`P`/`I`/`F`) means a
 different, unexpected exception type fired instead -- useful
 information on its own.

 **If this is still completely silent, even with the vector table
 installed**, that's a real, fairly strong result on its own: it
 rules out a CPU-detected fault and points at a genuine hardware
 bus stall (the transaction itself never completing, no exception
 ever generated) -- which without JTAG is a much harder thing to
 debug further from software alone, and would be the point to
 revisit whether `0xa0000000` is the right address at all, or
 whether some additional bus/remap enable step (not present in
 anything we've traced so far) is needed before DRAM becomes
 CPU-addressable.

5. **SPI controller base-address sweep** (`spi_base_sweep()` in
 `main.c`, runs right after the DRAM test). We have no confirmed
 SPI controller base -- static literal-pool analysis of the real
 U-Boot binary found the pin-mode-select register at the already-
 known pinmux base (`0xf0500000+0xF4`, cross-validated against an
 independent finding on a different chip in this family), but the
 controller itself was never pinned down: direct measurement showed
 a \~3.8% chance of a 4-byte word matching the "confirmed LDR
 literal" bit pattern purely by coincidence in that code region,
 once it became clear code and string data are tightly interleaved
 there. Rather than trust another single guess, this reads register
 offset 0 from each of 8 plausible candidate addresses (the
 unassigned slots in the confirmed `0xf0xx0000` peripheral family,
 plus two addresses from a whole-binary scan) and reports either the
 value or a clean fault for each -- turning "which address is real"
 into something the hardware answers directly:

`text $SPI base sweep: $0xf0100000: FAULT (nothing mapped here) $0xf0200000: reg[0]=0x... $... $`

 This relies on the data-abort handler now _recovering_ rather than
 halting (skip the faulting instruction, resume at the next one) --
 a real correctness subtlety, since resuming means every register
 the interrupted code was using must come back exactly as it left
 it. `r0-r3`/`r12` are saved/restored explicitly around the
 `report_exception()` call (they're caller-saved and get clobbered
 by any ordinary C call); `r4-r11` need no extra handling since AAPCS
 already guarantees a well-formed C function preserves those.

 Reading a result for an address does **not** by itself confirm
 it's the SPI controller -- a non-fault here only means _something_
 responds on the bus, which could be any mapped peripheral, or
 unmapped-but-silently-returning-a-fixed-value space. A faulting
 candidate is a clean negative; a non-faulting one is a lead worth
 following up on, not a confirmed answer.

 **First hardware run: no faults at all.** All 8 candidates
 responded -- the whole `0xf0xx0000` range behaves like a live APB
 bus that returns a value for any address rather than faulting on
 unmapped ones, so absence-of-fault alone doesn't narrow anything
 down here. Most values were suspiciously uniform (four exact
 `0x00000000`, two identical `0x00000065`) -- consistent with
 unassigned decode slots returning a fixed default, not distinct
 real peripherals. Two stood out: `0xf0300000=0xf8002040` and
 `0xf0600000=0x00000007`, both non-uniform with the rest. Notably,
 these are the same two addresses an earlier, separately-
 discredited static scan also flagged -- two independent methods
 converging on the same two addresses is real signal even though
 neither alone was conclusive.

 `spi_detail_probe()` follows up on exactly these two, checking a
 concrete, falsifiable prediction from static analysis: that
 `fh_spic_check_idle` expects status bits `0x400|0x200|0x8` all set
 (at a struct-relative `+0x28` field) to mean "idle". If the real
 SPI base's `+0x28` shows these bits set -- plausible, since the
 boot ROM just finished using it moments earlier -- that's genuine
 corroboration, independent of the uniform/non-uniform heuristic
 that picked these two candidates out in the first place. A miss
 here doesn't rule either address out (the struct-offset-to-
 register mapping itself is still unconfirmed), but a hit would be
 a strong, multi-method-converging result.

 **First hardware run: the prediction was falsified for both.**
 `0xf0300000+0x28 = 0x00000000`, `0xf0600000+0x28 = 0x00000006` --
 neither has any of the three expected bits set. This is a real
 negative result, not inconclusive noise: it means the software
 driver struct's `+0x28` field does NOT map directly onto a
 hardware MMIO register at `base+0x28` for either candidate (most
 likely explanation: that struct field is populated through
 further indirection -- a function pointer or nested pointer --
 rather than being a flat overlay of the real register file, which
 fits everything we'd already seen of `fh_spi.c`'s layered design).
 It does **not** rule either base out as the real SPI controller,
 it only rules out this specific offset-mapping assumption.

 One structural detail at `0xf0300000` is still worth following up
 on its own, independent of the falsified prediction:
 `spi_stability_check()` reads `+0x0` and `+0x4` (the only two
 offsets that weren't exactly zero) five times each, back-to-back,
 to tell a static register (chip-ID/version-like, same value every
 read) apart from a dynamic one (status-like, value may change) --
 a different, independent signal from anything tried so far.

 **First hardware run: both offsets were perfectly stable across
 all 5 reads** -- consistent with a chip-ID/version-style register,
 not an active transfer-status register (which should change if a
 real transaction just happened, as one would have moments earlier
 when the boot ROM loaded this very code from flash). This doesn't
 prove `0xf0300000` isn't SPI-related, but it argues against it
 being the live engine's own status register.

### A much better-motivated candidate: 0xf1600000

A sharp architectural observation broke this open: the 16KB
first-stage SRAM stub is far too small to contain the \~200KB "uboot"
image itself, so it must read that image from SPI flash into DRAM
itself, using its own, necessarily simpler SPI code -- independent
of the full driver (`fh_spi.c`) in the second-stage image we'd been
searching fruitlessly. Disassembling the stub's own, previously-
unexamined call 5 (the one we'd skipped as "presumably copies to DRAM
and jumps") found **seven** references to `0xf1600000` within one
function, forming a clear trigger/poll/status pattern straight out of
real, working flash-loading code -- not another guess:

`text $+0x00: write 1 to start a transfer 
$+0x2C: status register -- bit 1 polled for "done"; bits[27:20] hold 
$       a received data byte after a transfer completes 
$+0x08: command/opcode-ish register (e.g. 0x25 written here before a 
$       variant that polls bit 3 and reads a result from +0x28) 
$+0x28: a second result register, used by that bit-3 variant 
$`

This is real evidence from the actual flash-loading code path, which
makes it far more trustworthy than `0xf0300000`/`0xf0600000` (both
found by weaker, indirect methods). `main.c` now does three things
with it: a passive `spi_detail_probe()` dump, an active trigger test
(write 1 to `+0x00`, poll `+0x2C` for bit 1), and a replica of the
full decoded command sequence with the real JEDEC-ID opcode (`0x9F`)
substituted for the mystery constant (`0x25`) the original code used.

**First hardware run: strong, multi-line confirmation.** The detail
dump showed rich, structured values at nearly every offset (`+0xC $=0x0102021b`, `+0x10=0x0201112c`, `+0x18=0x02010505`, etc.) --
completely unlike the sparse, mostly-zero pattern at the other two
candidates, and consistent with a real, actively-configured
peripheral's timing/config registers. The trigger test found bit 1
already set (consistent with an idle "ready" flag) -- and, most
importantly, the status value read moments earlier during the detail
dump (`0x8003800a`) differed from the one read during the trigger
test (`0x8001800a`) by exactly one bit (bit 17) -- a real, causal,
reproducible change in a live register driven by our own write. This
is categorically stronger evidence than anything else gathered in
this investigation: not a static pattern-match, not a passive read,
but hardware observably responding to an interaction we initiated,
based on a protocol read directly out of working code.

`check_idle_bits` was added as a parameter to `spi_detail_probe()`
specifically so the (irrelevant here) `fh_spi.c`-derived idle-bit
annotation isn't printed for `0xf1600000`, whose real status
semantics (bit1/bit3 of `+0x2C`) were independently confirmed from
the stub's own code and have nothing to do with that unrelated
struct-offset hypothesis.

The JEDEC-ID replica is honestly uncertain about one thing: whether
chip-select stays asserted across repeated calls (so 3 calls would
read the flash's 3 distinct ID bytes) or toggles each time (so 3
calls would just re-read the same first byte). It's called 3x with
the same command specifically to find out empirically -- a response
byte of `0x85` (Puya's manufacturer ID) on the first call would be
the clearest possible confirmation.

**First hardware run: even the sanity baseline (nominally `0x25`)
timed out at step C.** That pointed at the sequence replica itself,
not the opcode -- and re-checking the disassembly carefully found the
actual bug: the literal at `0x10009b50` is the full 32-bit word
**`0x92FC0025`**, not plain `0x25`. Fixed, and confirmed working on
hardware (baseline completed, `response byte=0x0`) -- but a follow-up
substitution attempt (`0x92FC009F`, swapping only the low byte for the
real JEDEC-ID opcode) still timed out.

### Ghidra decompilation resolved it properly

Rather than keep guessing at individual bytes, the user decompiled
the full 16KB stub in Ghidra and shared the output. That surfaced the
real addressing mechanism directly: a generic `(index << 4) | 3`
value written to `+0x00`, used identically by the OTP/calibration
reader (indices `0x3c`-`0x3e`, called from the clock-calibration step)
and the bulk byte-read loop (indices starting at `0x10`). Checking all
three known call sites' `+0x08` constants confirmed they're all
**exactly** `0x92FC0025` -- not three different "opcodes" as hoped,
but one universal, fixed "enable" value. This definitively rules out
the "opcode byte" theory the whole JEDEC-ID experiment was built on:
there is no variable command byte at `+0x08` to substitute at all.

`main.c` now replicates the _actual_ decoded function
(`FUN_00001ba8`/`spic_read_bytes`) instead: write `0x92FC0025` once to
`+0x08`, then for 16 bytes, trigger + write `(index<<4)|3` to `+0x00`
+ wait bit3 + read `+0x28`, incrementing `index` from `0x10`. This
answers a real open question either way: whether `index` addresses a
continuing, meaningful flash byte stream, or a small, fixed register/
fuse set where indices beyond some range just repeat or return junk.

**First hardware run: all bytes read back as `0x00000000`.** Before
concluding anything from that, a real gap in the replica was found by
comparing against Ghidra's decompilation again: the real
`FUN_00001ba8` calls `FUN_00001aac` (the readiness/status check)
_first_, checking **bit1** (not bit3) of the result before proceeding
-- the earlier replica skipped straight to the `+0x08` write and read
loop. This precondition call does its own full trigger+wait+read
cycle, and quite possibly has a side effect (e.g. initializing an
internal position counter) that the main read loop depends on. Added
now, with its own checkpoint output (precondition byte + bit1 state)
so a future all-zero result is distinguishable from "the precondition
itself failed or was never satisfied" rather than just repeating the
same ambiguous outcome.

### 0xf1600000 is not the flash controller after all

Direct oscilloscope evidence (the user's own standalone F050
DesignWare-SPI experiment, built independently) confirms `0xf0500000`
drives real CLK/MOSI SPI bus activity -- `0xf1600000` was never the
flash controller. In hindsight this fits everything found there: a
tiny `(index<<4)|3` address space (6 bits, `0x00`-`0x3F`) shared with
per-die PLL calibration reads is a much better match for an OTP/fuse-
calibration block than a flash interface. The open problem in that
experiment: CS0/CS1 never physically asserts.

Re-reading the shared Ghidra decompilation resolved an old, separate
mystery along the way: `FUN_00000294`/`FUN_000002bc` are generic GPIO
bit set/clear helpers, `addr = index * 0x3d00000 + 0xf0300000` --
explaining the very first SPI sweep's "rich, stable"
`0xf0300000` reading (`0xf8002040`) from many sessions ago as simply
the current state of several GPIO pins, not a controller register as
assumed at the time.

`gpio_toggle_sweep()` (new in `main.c`) uses this confirmed formula to
toggle one `(index, bit)` pair at a time as a continuous square wave
(index 0-15, bit fixed at `GPIO_SWEEP_BIT`, a few seconds each),
printed over UART so it can be correlated against the physical
CS0/CS1 pins with an oscilloscope -- turning "which GPIO drives CS"
into a directly observable question rather than another guess.
