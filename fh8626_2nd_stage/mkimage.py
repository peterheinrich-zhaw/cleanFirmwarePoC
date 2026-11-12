#!/usr/bin/env python3
"""
Build a full flash image. No dependency on the original vendor dump
FILE at build time -- everything needed (DDR_0's register table, its
section-table entry, and the header constants) is embedded here as
owned constants. Every byte we don't have a concrete, owned reason to
set is 0xFF (the erased-flash value).

Design decisions this makes, and why:

- Flash size: P25Q64SH = 64 Mbit = 8 MiB (0x800000).
- Two sections: "DDR_0" (the DRAM/PLL register-init table, serialized
  back to the same 16-byte-record flash format the vendor used) and
  "ram" (our own compiled stub, hello.bin). A from-scratch build
  WITHOUT a DDR_0 section produced an immediate boot ROM error
  (ERR: 209) even on a fully erased flash chip -- confirming the boot
  ROM itself looks for and depends on a DDR_0-named entry being
  present. DDR_0 is mandatory here from here on.
- The per-section checksum is a confirmed, verified algorithm:
  `crc32(padded_section_bytes) ^ 0xFFFFFFFF` (standard zlib CRC32,
  with the customary final XOR undone), computed over the section's
  *padded* size with the padding bytes set to zero -- NOT 0xFF.
  Verified against the original "ram" and "uboot" sections' known-
  good stored checksums.
- DDR_0's own section-table entry (build_ddr0_entry below) is built
  from named fields mirroring flash_layout.h's section_entry_ddr0
  struct, not a raw hex blob -- several of its fields (g1, and parts
  of g3) follow a different, not-fully-understood convention from
  the generic "ram"/"uboot" entry format, discovered by checksum-
  verifying against the original dump. See that struct's comments in
  flash_layout.h for the full field-by-field breakdown; this is the
  Python-side mirror of the same understanding.
- The real DRAM/PLL table replay (previously run from our own C code
  via a small interpreter, now removed) has been taken out of the
  active boot path entirely: hardware testing proved the boot ROM
  already performs real DRAM training itself ("Init DDR..Training
  done." on the console) before ever reaching our code, and our own
  redundant replay of the same registers caused a hard reset. DDR_0
  is still shipped here because the boot ROM requires and consumes
  it -- we just don't also replay it ourselves at runtime. DDR_TABLE
  below is the only copy of this data in the project now (the old
  C-side copy and its interpreter were removed as dead code).

Usage:
    python3 mkimage.py --stub hello.bin --out flash_image.bin
"""
import argparse
import struct
import zlib

FLASH_SIZE = 8 * 1024 * 1024  # P25Q64SH: 64 Mbit = 8 MiB

# --- constants taken verbatim from the original dump's header --------
# (documented provenance: bytes 0x00-0x1F of the original flash dump)
MAGIC = b"2BL*OABD"           # boot ROM signature check (confirmed by structure)
HDR_FIELD_1 = 1               # offset 0x08 in original -- meaning unconfirmed (version?)
HDR_FIELD_2_ORIG_SECTION_COUNT = 2  # offset 0x0C in original, when table had 2 code
                                     # sections (ram+uboot); we have 1, see below
HDR_FIELD_3 = 1                # offset 0x10 in original -- meaning unconfirmed
SYNC_PATTERN = 0xAA5555AA      # offset 0x18 in original -- classic bit-alternating
                                # sync word, common secondary validity check

# --- DRAM controller + PLL register-init table --------------------
# Extracted (not copied) from this exact board's original flash dump:
# parsed into (reg_addr, value, extra, tag) records -- see the earlier
# analysis thread for the extraction method and tag-opcode meanings
# (2=poll, 3/5=write, 4=write+delay). This is owned data, not a file
# dependency: it's inlined here as a plain constant.
DDR_TABLE = [
    (0xf000000c, 0x1, 0x10, 5),
    (0xf000003c, 0x1132131, 0x10, 3),
    (0xf000000c, 0x1, 0x10, 4),
    (0xf0000020, 0x4000000, 0x64, 4),
    (0xf0000010, 0x41010065, 0x0, 3),
    (0xf0000018, 0x8819, 0x1000, 3),
    (0xf0000010, 0x1010065, 0xffff, 3),
    (0xf0000020, 0x4000000, 0x0, 5),
    (0xed000000, 0x400, 0x0, 3),
    (0xed000004, 0x0, 0x0, 3),
    (0xed000008, 0x0, 0x0, 3),
    (0xed00000c, 0x0, 0x0, 3),
    (0xed000010, 0x0, 0x0, 3),
    (0xed000014, 0x0, 0x0, 3),
    (0xed000018, 0x0, 0x0, 3),
    (0xed00001c, 0x0, 0x0, 3),
    (0xed000020, 0x0, 0x0, 3),
    (0xed000024, 0x1d3d1, 0x0, 3),
    (0xed000028, 0x0, 0x0, 3),
    (0xed00002c, 0x0, 0x0, 3),
    (0xed000030, 0x1000000, 0x0, 3),
    (0xed000034, 0x78, 0x0, 3),
    (0xed000038, 0x12c, 0x0, 3),
    (0xed00003c, 0xf002, 0x0, 3),
    (0xed000040, 0xe0000c8, 0x0, 3),
    (0xed000044, 0x2020006, 0x0, 3),
    (0xed000048, 0x51b2306, 0x0, 3),
    (0xed00004c, 0x2050008, 0x0, 3),
    (0xed000050, 0xa3bc08, 0x0, 3),
    (0xed000054, 0x8000303, 0x0, 3),
    (0xed000058, 0x1000009, 0x0, 3),
    (0xed00005c, 0x8031101, 0x0, 3),
    (0xed000060, 0x1000000, 0x0, 3),
    (0xed000064, 0x123e003f, 0x0, 3),
    (0xed000068, 0x300, 0x0, 3),
    (0xed00006c, 0x3000f, 0x0, 3),
    (0xed000070, 0xc8000a, 0x0, 3),
    (0xed000074, 0x45, 0x0, 3),
    (0xed000078, 0x3000001, 0x0, 3),
    (0xed00007c, 0x2000003, 0x0, 3),
    (0xed000080, 0x1f0403, 0x0, 3),
    (0xed000084, 0x40003, 0x0, 3),
    (0xed000088, 0x7, 0x0, 3),
    (0xed00008c, 0x0, 0x0, 3),
    (0xed000090, 0x0, 0x0, 3),
    (0xed000094, 0x0, 0x0, 3),
    (0xed000098, 0x0, 0x0, 3),
    (0xed00009c, 0x0, 0x0, 3),
    (0xed0000a0, 0x12730000, 0x0, 3),
    (0xed0000a4, 0x40, 0x0, 3),
    (0xed0000a8, 0x0, 0x0, 3),
    (0xed0000ac, 0x0, 0x0, 3),
    (0xed0000b0, 0x1010000, 0x0, 3),
    (0xed0000b4, 0x0, 0x0, 3),
    (0xed0000b8, 0x0, 0x0, 3),
    (0xed0000bc, 0x20000, 0x0, 3),
    (0xed0000c0, 0x400100, 0x0, 3),
    (0xed0000c4, 0x200, 0x0, 3),
    (0xed0000c8, 0x40, 0x0, 3),
    (0xed0000cc, 0x0, 0x0, 3),
    (0xed0000d0, 0xa010301, 0x0, 3),
    (0xed0000d4, 0x101ffff, 0x0, 3),
    (0xed0000d8, 0x1010101, 0x0, 3),
    (0xed0000dc, 0x10b0101, 0x0, 3),
    (0xed0000e0, 0xc0000, 0x0, 3),
    (0xed0000e4, 0x0, 0x0, 3),
    (0xed0000e8, 0x100, 0x0, 3),
    (0xed0000ec, 0x0, 0x0, 3),
    (0xed0000f0, 0x0, 0x0, 3),
    (0xed0000f4, 0x0, 0x0, 3),
    (0xed0000f8, 0x0, 0x0, 3),
    (0xed0000fc, 0x0, 0x0, 3),
    (0xed000100, 0x0, 0x0, 3),
    (0xed000104, 0x0, 0x0, 3),
    (0xed000108, 0x0, 0x0, 3),
    (0xed00010c, 0x0, 0x0, 3),
    (0xed000110, 0x0, 0x0, 3),
    (0xed000114, 0x0, 0x0, 3),
    (0xed000118, 0x3000000, 0x0, 3),
    (0xed00011c, 0x30005, 0x0, 3),
    (0xed000120, 0x20000, 0x0, 3),
    (0xed000124, 0x0, 0x0, 3),
    (0xed000128, 0x0, 0x0, 3),
    (0xed00012c, 0x0, 0x0, 3),
    (0xed000130, 0x280d0000, 0x0, 3),
    (0xed000134, 0x0, 0x0, 3),
    (0xed000138, 0x0, 0x0, 3),
    (0xed00013c, 0x0, 0x0, 3),
    (0xed000140, 0x0, 0x0, 3),
    (0xed000144, 0x0, 0x0, 3),
    (0xed000148, 0x0, 0x0, 3),
    (0xed00014c, 0x0, 0x0, 3),
    (0xed000150, 0x4040, 0x0, 3),
    (0xed000154, 0x0, 0x0, 3),
    (0xed000158, 0x0, 0x0, 3),
    (0xed00015c, 0x404000, 0x0, 3),
    (0xed000160, 0xf0f0000, 0x0, 3),
    (0xed000164, 0x2020f0f, 0x0, 3),
    (0xed000168, 0x20200, 0x0, 3),
    (0xed00016c, 0x2000202, 0x0, 3),
    (0xed000170, 0x2020002, 0x0, 3),
    (0xed000174, 0x1280700, 0x0, 3),
    (0xed000178, 0x12800, 0x0, 3),
    (0xed00017c, 0x28000128, 0x0, 3),
    (0xed000180, 0x1280001, 0x0, 3),
    (0xed000184, 0x0, 0x0, 3),
    (0xed000188, 0x0, 0x0, 3),
    (0xed00018c, 0xf00, 0x0, 3),
    (0xed000190, 0x247c00, 0x0, 3),
    (0xed000194, 0x2000840, 0x0, 3),
    (0xed000198, 0x2000200, 0x0, 3),
    (0xed00019c, 0x247c, 0x0, 3),
    (0xed0001a0, 0xb66c, 0x0, 3),
    (0xed0001a4, 0x1020606, 0x0, 3),
    (0xed0001a8, 0xd0a03, 0x0, 3),
    (0xed0001ac, 0x0, 0x0, 3),
    (0xed0001b0, 0x0, 0x0, 3),
    (0xed0001b4, 0x4038000, 0x0, 3),
    (0xed0001b8, 0x7030a07, 0x0, 3),
    (0xed0001bc, 0xffff22, 0x0, 3),
    (0xed0001c0, 0xd0010, 0x0, 3),
    (0xed0001c4, 0x0, 0x0, 3),
    (0xed0001c8, 0x0, 0x0, 3),
    (0xed0001cc, 0x0, 0x0, 3),
    (0xed0001d0, 0x0, 0x0, 3),
    (0xed0001d4, 0x204, 0x0, 3),
    (0xed0001d8, 0x0, 0x0, 3),
    (0xed0001dc, 0x10000001, 0x0, 3),
    (0xed0001e0, 0xd0e0f17, 0x0, 3),
    (0xed0001e4, 0xb0c16, 0x0, 3),
    (0xed0001e8, 0x7080915, 0x0, 3),
    (0xed0001ec, 0x4050614, 0x0, 3),
    (0xed0001f0, 0x1020313, 0x0, 3),
    (0xed0001f4, 0x11180a12, 0x0, 3),
    (0xed0001f8, 0x67013245, 0x0, 3),
    (0xed0001fc, 0x67014532, 0x0, 3),
    (0xed000200, 0x812, 0x0, 3),
    (0xed000204, 0x814, 0x0, 3),
    (0xed000208, 0x800100a8, 0x0, 3),
    (0xed00020c, 0x5, 0x0, 3),
    (0xed000210, 0xc120018, 0x0, 3),
    (0xed000214, 0x4040, 0x0, 3),
    (0xed000218, 0x4921, 0x0, 3),
    (0xed00021c, 0x0, 0x0, 3),
    (0xed000220, 0x0, 0x0, 3),
    (0xed000224, 0x0, 0x0, 3),
    (0xed000228, 0x0, 0x0, 3),
    (0xed00022c, 0x0, 0x0, 3),
    (0xed000230, 0x818a, 0x0, 3),
    (0xed000234, 0x0, 0x0, 3),
    (0xed000238, 0x0, 0x0, 3),
    (0xed00023c, 0x4240f000, 0x0, 3),
    (0xed000240, 0x0, 0x0, 3),
    (0xed000244, 0x0, 0x0, 3),
    (0xed000248, 0x0, 0x0, 3),
    (0xed00024c, 0x0, 0x0, 3),
    (0xed000250, 0x0, 0x0, 3),
    (0xed000254, 0x0, 0x0, 3),
    (0xed000258, 0x0, 0x0, 3),
    (0xed00025c, 0x0, 0x0, 3),
    (0xed000260, 0x0, 0x0, 3),
    (0xed000264, 0x0, 0x0, 3),
    (0xed000268, 0x0, 0x0, 3),
    (0xed00026c, 0x0, 0x0, 3),
    (0xed000270, 0x0, 0x0, 3),
    (0xed000274, 0x0, 0x0, 3),
    (0xed000278, 0x0, 0x0, 3),
    (0xed00027c, 0x0, 0x0, 3),
    (0xed000280, 0x812, 0x0, 3),
    (0xed000284, 0x814, 0x0, 3),
    (0xed000288, 0x800100a8, 0x0, 3),
    (0xed00028c, 0x5, 0x0, 3),
    (0xed000290, 0xc120018, 0x0, 3),
    (0xed000294, 0x4040, 0x0, 3),
    (0xed000298, 0x4921, 0x0, 3),
    (0xed00029c, 0x0, 0x0, 3),
    (0xed0002a0, 0x0, 0x0, 3),
    (0xed0002a4, 0x0, 0x0, 3),
    (0xed0002a8, 0x0, 0x0, 3),
    (0xed0002ac, 0x0, 0x0, 3),
    (0xed0002b0, 0x818a, 0x0, 3),
    (0xed0002b4, 0x0, 0x0, 3),
    (0xed0002b8, 0x0, 0x0, 3),
    (0xed0002bc, 0x4240f000, 0x0, 3),
    (0xed0002c0, 0x0, 0x0, 3),
    (0xed0002c4, 0x0, 0x0, 3),
    (0xed0002c8, 0x0, 0x0, 3),
    (0xed0002cc, 0x0, 0x0, 3),
    (0xed0002d0, 0x0, 0x0, 3),
    (0xed0002d4, 0x0, 0x0, 3),
    (0xed0002d8, 0x0, 0x0, 3),
    (0xed0002dc, 0x0, 0x0, 3),
    (0xed0002e0, 0x0, 0x0, 3),
    (0xed0002e4, 0x0, 0x0, 3),
    (0xed0002e8, 0x0, 0x0, 3),
    (0xed0002ec, 0x0, 0x0, 3),
    (0xed0002f0, 0x0, 0x0, 3),
    (0xed0002f4, 0x0, 0x0, 3),
    (0xed0002f8, 0x0, 0x0, 3),
    (0xed0002fc, 0x0, 0x0, 3),
    (0xed000300, 0x812, 0x0, 3),
    (0xed000304, 0x814, 0x0, 3),
    (0xed000308, 0x800100a8, 0x0, 3),
    (0xed00030c, 0x5, 0x0, 3),
    (0xed000310, 0xc120018, 0x0, 3),
    (0xed000314, 0x7040, 0x0, 3),
    (0xed000318, 0x4921, 0x0, 3),
    (0xed00031c, 0x0, 0x0, 3),
    (0xed000320, 0x0, 0x0, 3),
    (0xed000324, 0x0, 0x0, 3),
    (0xed000328, 0x0, 0x0, 3),
    (0xed00032c, 0x0, 0x0, 3),
    (0xed000330, 0x818a, 0x0, 3),
    (0xed000334, 0x0, 0x0, 3),
    (0xed000338, 0x0, 0x0, 3),
    (0xed00033c, 0x4240f000, 0x0, 3),
    (0xed000340, 0x0, 0x0, 3),
    (0xed000344, 0x0, 0x0, 3),
    (0xed000348, 0x0, 0x0, 3),
    (0xed00034c, 0x0, 0x0, 3),
    (0xed000350, 0x0, 0x0, 3),
    (0xed000354, 0x0, 0x0, 3),
    (0xed000358, 0x0, 0x0, 3),
    (0xed00035c, 0x0, 0x0, 3),
    (0xed000360, 0x0, 0x0, 3),
    (0xed000364, 0x0, 0x0, 3),
    (0xed000368, 0x0, 0x0, 3),
    (0xed00036c, 0x0, 0x0, 3),
    (0xed000370, 0x0, 0x0, 3),
    (0xed000374, 0x0, 0x0, 3),
    (0xed000378, 0x0, 0x0, 3),
    (0xed00037c, 0x0, 0x0, 3),
    (0xed000380, 0x110007, 0x0, 3),
    (0xed000384, 0x0, 0x0, 3),
    (0xed000388, 0x700, 0x0, 3),
    (0xed00038c, 0x380038, 0x0, 3),
    (0xed000390, 0x380038, 0x0, 3),
    (0xed000394, 0x380038, 0x0, 3),
    (0xed000398, 0x0, 0x0, 3),
    (0xed00039c, 0x0, 0x0, 3),
    (0xed0003a0, 0x80ad0, 0x0, 3),
    (0xed0003a4, 0x0, 0x0, 3),
    (0xed0003a8, 0x0, 0x0, 3),
    (0xed0003ac, 0x61, 0x0, 3),
    (0xed000000, 0x1, 0x0, 4),
    (0xed0000ec, 0x10, 0xffff, 2),
    (0xf000001c, 0xa0c40012, 0x0, 3),
    (0xf0000020, 0xe0c04002, 0x0, 3),
    (0xf000005c, 0x1e060156, 0x0, 3),
]


SECTION_TABLE_OFF = 0x100
SECTION_ENTRY_SIZE = 0x40
NAME_SIZE = 0x10

RAM_LOAD_ADDR = 0x10008000     # confirmed: matches the vendor stub's own preserved reset
                                # vector location and its own SP-init convention
RAM_SLOT_SIZE = 0x4000         # back to the vendor's original 16KB. An earlier attempt at
                                # 32KB (to fit a preserved vendor-code blob alongside our
                                # own code) produced ERR:209 -- though note that attempt
                                # also had other differences (DDR_0's whole entry format
                                # was still wrong at the time, see build_ddr0_entry()
                                # below), so slot size specifically was never cleanly
                                # isolated as the cause. Reverted here for a clean,
                                # single-variable test.
RAM_FLASH_OFF = 0x4000         # our choice: keep the vendor's original layout offset,
                                # no reason to diverge, nothing depends on matching it exactly

DDR_LOAD_ADDR = 0x10000800     # matches the original dump's DDR_0 entry -- this SRAM window
                                # is where the vendor loaded it too, no reason to diverge
DDR_FLASH_OFF = 0x1000         # our choice: original layout offset, kept for no strong reason
                                # other than consistency; free to move if it ever matters
DDR_SLOT_SIZE = 0x1000         # matches original dump's reservation; our table (249*16=3984
                                # bytes) fits comfortably inside it

# DDR_0's section-table entry, built from named fields -- NOT a raw
# hex blob. Mirrors flash_layout.h's `struct section_entry_ddr0`
# field-by-field: several of these (unknown_0x10/14/18/1c, the
# padded-size convention, unknown_0x30/0x38) follow a different,
# not-fully-understood layout from the generic ram/uboot entry
# format (g1, g2[0], g3[0], g3[2] all diverge from that formula --
# g3[0]=0x43 where slot_size//0x400 would give 4; g3[2]=0x1a where
# ram/uboot both have 0). With only one real sample to derive a rule
# from, these are named-but-unexplained fields, same confidence
# level as the C struct, not a formula. checksum is computed for
# real below (DDR_PADDED_SIZE), not hardcoded -- it happens to equal
# the original's 0xf9a428a0 only because our table content is
# identical to the original's.
DDR0_NAME = b'DDR_0'
DDR0_UNKNOWN_0X10 = 0x78503  # plausible "real DRAM-training section" marker -- see
                             # flash_layout.h's section_entry_ddr0 comment for why
DDR0_UNKNOWN_0X14 = 0x0
DDR0_UNKNOWN_0X18 = 0x0
DDR0_UNKNOWN_0X1C = 0x0
DDR0_UNKNOWN_0X30 = 0x43
DDR0_UNKNOWN_0X38 = 0x1a


def build_ddr0_entry(padded_size, load_addr, exec_addr, slot_size, checksum):
    """Builds DDR_0's 64-byte section-table entry from named fields,
    in the exact field order of flash_layout.h's section_entry_ddr0."""
    e = bytearray(SECTION_ENTRY_SIZE)
    e[0:len(DDR0_NAME)] = DDR0_NAME
    struct.pack_into('<4I', e, 0x10, DDR0_UNKNOWN_0X10, DDR0_UNKNOWN_0X14,
                      DDR0_UNKNOWN_0X18, DDR0_UNKNOWN_0X1C)
    struct.pack_into('<4I', e, 0x20, padded_size, slot_size, load_addr, exec_addr)
    struct.pack_into('<4I', e, 0x30, DDR0_UNKNOWN_0X30, checksum, DDR0_UNKNOWN_0X38, 0)
    return bytes(e)


def serialize_ddr_table(table):
    """Pack (addr, val, extra, tag) tuples back into the 16-byte-record
    flash format -- see flash_layout.h's ddr_script_entry for the same
    layout, including the tag opcode semantics (2=poll, 3/5=write,
    4=write+delay), documented there even though nothing replays it
    at runtime anymore."""
    out = bytearray()
    for addr, val, extra, tag in table:
        out += struct.pack('<4I', addr, val, extra, tag)
    return bytes(out)


def real_checksum(padded_buf):
    """Confirmed by matching both the original 'ram' and 'uboot'
    sections' known-good stored checksums exactly. Must be called on
    the *padded*, zero-filled buffer -- not the raw content."""
    return zlib.crc32(padded_buf) ^ 0xFFFFFFFF


def build_header(num_sections, total_entries):
    """First 0x28 bytes: magic + fields + sync pattern + zero gap.
    Returns only up to offset 0x28 -- the caller must NOT zero-fill
    the rest of the 0x100-byte header region (0x28-0xFF is actually
    0xFF in the original, not zero as an earlier version of this
    function incorrectly assumed and overwrote).

    num_sections and total_entries are deliberately different things,
    confirmed by the original dump: its num_sections field was 2 while
    it physically had 3 table entries (DDR_0, ram, uboot) -- table_end
    tracks ALL physical entries (so the loader knows where the table
    ends), while num_sections apparently counts only non-DDR_0 "code"
    sections. Conflating them was a bug in an earlier version of this
    script.
    """
    hdr = bytearray(0x28)
    hdr[0x00:0x08] = MAGIC
    struct.pack_into('<I', hdr, 0x08, HDR_FIELD_1)
    struct.pack_into('<I', hdr, 0x0C, num_sections)
    struct.pack_into('<I', hdr, 0x10, HDR_FIELD_3)
    table_end = SECTION_TABLE_OFF + total_entries * SECTION_ENTRY_SIZE
    struct.pack_into('<I', hdr, 0x14, table_end)
    struct.pack_into('<I', hdr, 0x18, SYNC_PATTERN)
    # 0x1C-0x27: zero, matches original (confirmed by direct byte
    # inspection -- 0x28 onward is 0xFF, left untouched by the caller)
    return bytes(hdr)


def build_section_entry(name, raw_size, padded_size, load_addr, exec_addr,
                         slot_size, checksum):
    """
    Used for "ram" and "uboot"-style entries only now. DDR_0 is built
    from build_ddr0_entry() instead, with named fields -- its g1/g3
    fields follow a different, not-understood convention, discovered
    when this function's generic formula produced values that didn't
    match the original's known-good checksum. See build_ddr0_entry()'s
    comment for the details.
    """
    e = bytearray(SECTION_ENTRY_SIZE)
    e[0:len(name)] = name
    # g1 group (offset 0x10): [0, 0, raw_size, 0] -- ram/uboot convention
    struct.pack_into('<I', e, 0x10 + 0x08, raw_size)
    # g2 group (offset 0x20): [padded_size, slot_size, load_addr, exec_addr]
    struct.pack_into('<4I', e, 0x20, padded_size, slot_size, load_addr, exec_addr)
    # g3 group (offset 0x30): [slot_size/0x400, checksum, 0, 0]
    struct.pack_into('<4I', e, 0x30, slot_size // 0x400, checksum, 0, 0)
    return bytes(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stub', required=True, help='our compiled hello.bin')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    stub = open(args.stub, 'rb').read()
    if len(stub) > RAM_SLOT_SIZE:
        raise SystemExit(f"error: stub is {len(stub)} bytes, exceeds the "
                          f"{RAM_SLOT_SIZE}-byte ram slot.")

    ddr_bytes = serialize_ddr_table(DDR_TABLE)
    if len(ddr_bytes) > DDR_SLOT_SIZE:
        raise SystemExit(f"error: DDR table is {len(ddr_bytes)} bytes, exceeds the "
                          f"{DDR_SLOT_SIZE}-byte ddr slot.")

    image = bytearray(b'\xff' * FLASH_SIZE)  # everything starts erased

    header = build_header(num_sections=2, total_entries=2)
    image[0:len(header)] = header

    # Checksum covers the PADDED, ZERO-filled content (confirmed against
    # the original dump) -- so build each section's on-flash bytes as
    # zero-padded up to its padded_size, then checksum exactly that
    # buffer, then write exactly that buffer to flash. The surrounding
    # slot (padded_size..slot_size) stays 0xFF as before -- only the
    # raw_size..padded_size gap is zero, matching the original's own
    # convention.
    def pad_section(raw_bytes, padded_size=None):
        if padded_size is None:
            padded_size = ((len(raw_bytes) + 0x3f) // 0x40) * 0x40
        return raw_bytes + b'\x00' * (padded_size - len(raw_bytes)), padded_size

    # DDR_0's table entry is built from named fields (build_ddr0_entry,
    # mirroring flash_layout.h's section_entry_ddr0 struct) rather than
    # a raw hex blob -- several fields follow a different, not-fully-
    # understood convention from the generic ram/uboot formula (see
    # that function's comment). padded_size=0x1080 is likewise a
    # verified-but-unexplained constant, not computed from raw_size.
    DDR_PADDED_SIZE = 0x1080
    ddr_padded, _ = pad_section(ddr_bytes, DDR_PADDED_SIZE)
    ddr_checksum = real_checksum(ddr_padded)
    ddr_entry = build_ddr0_entry(
        padded_size=DDR_PADDED_SIZE,
        load_addr=DDR_LOAD_ADDR,
        exec_addr=DDR_LOAD_ADDR,
        slot_size=DDR_SLOT_SIZE,
        checksum=ddr_checksum,
    )

    ram_padded, ram_padded_size = pad_section(stub)
    ram_checksum = real_checksum(ram_padded)
    ram_entry = build_section_entry(
        name=b'ram',
        raw_size=len(stub),
        padded_size=ram_padded_size,
        load_addr=RAM_LOAD_ADDR,
        exec_addr=RAM_LOAD_ADDR,
        slot_size=RAM_SLOT_SIZE,
        checksum=ram_checksum,
    )

    # DDR_0 first, then ram -- same order as the original dump
    image[SECTION_TABLE_OFF:SECTION_TABLE_OFF + SECTION_ENTRY_SIZE] = ddr_entry
    image[SECTION_TABLE_OFF + SECTION_ENTRY_SIZE:SECTION_TABLE_OFF + 2 * SECTION_ENTRY_SIZE] = ram_entry

    image[DDR_FLASH_OFF:DDR_FLASH_OFF + len(ddr_padded)] = ddr_padded
    image[RAM_FLASH_OFF:RAM_FLASH_OFF + len(ram_padded)] = ram_padded
    # everything else -- including all of the rest of the 8MiB -- stays 0xFF

    open(args.out, 'wb').write(image)
    print(f"wrote {args.out}: {len(image)} bytes total, "
          f"{len(image) - image.count(0xff)} non-0xFF bytes")
    print(f"  header+table:  0x0-0x{SECTION_TABLE_OFF + 2 * SECTION_ENTRY_SIZE:x} (2 sections)")
    print(f"  DDR_0 section: 0x{DDR_FLASH_OFF:x}-0x{DDR_FLASH_OFF + len(ddr_padded):x} "
          f"({len(ddr_bytes)} bytes raw / {len(ddr_padded)} padded, "
          f"{len(DDR_TABLE)} entries, load/exec addr 0x{DDR_LOAD_ADDR:x}, "
          f"checksum 0x{ddr_checksum:08x}, entry built from named fields, see build_ddr0_entry())")
    print(f"  ram section:   0x{RAM_FLASH_OFF:x}-0x{RAM_FLASH_OFF + len(ram_padded):x} "
          f"({len(stub)} bytes raw / {len(ram_padded)} padded, "
          f"load/exec addr 0x{RAM_LOAD_ADDR:x}, checksum 0x{ram_checksum:08x})")


if __name__ == '__main__':
    main()
