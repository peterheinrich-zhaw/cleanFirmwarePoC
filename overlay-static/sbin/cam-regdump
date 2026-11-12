#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Dump the camera-related registers of the FH8626V200 as "address value" lines.
# Runs on the stock firmware (BusyBox 1.26, devmem) and on ours, so the two
# outputs can be diffed: run it while the camera streams.
#
#   sh cam-regdump.sh > regs.txt
#
# No tabs and no trailing comments: the file must survive being pasted into a
# serial console. No arithmetic on full addresses: the stock shell computes in
# 32-bit signed integers, so 0xe8400000 and up would turn negative. Addresses
# are built as text from the upper 16 bits and a 16-bit offset.

# dump HIGH16 FIRST_OFFSET LAST_OFFSET, e.g. dump 0xe840 0x000 0x2fc
dump() {
  o=$(($2))
  while [ $o -le $(($3)) ]; do
    a=$(printf '%s%04x' $1 $o)
    echo "$a $(devmem $a 32)"
    o=$((o + 4))
  done
}

# PMU: PLL, clock select/divider/gate, resets, pads; chip/system control; +0x2108
dump 0xf000 0x000 0x13c
dump 0xf000 0x200 0x23c
dump 0xf000 0x2100 0x2110
# MIPI: CSI-2 host, D-PHY settings, wrap
dump 0xf110 0x000 0x13c
dump 0xf170 0x000 0x040
dump 0xf100 0x000 0x03c
# ISP: control, input, dump; input statistics
dump 0xe840 0x000 0x2fc
dump 0xe840 0x1400 0x14fc
# media blocks (VPU?)
dump 0xe830 0x000 0x0fc
dump 0xe870 0x000 0x0fc
