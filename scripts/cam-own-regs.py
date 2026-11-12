#!/usr/bin/env python3
"""Our own ISP/VPU register list for the SC2336 at 1920x1080 (docs/CAM.md, step 1).

Every value here is either computed from the frame geometry or a neutral setting
found by the bisects in docs/CAM.md; no vendor tuning table. The output has the
format of stock-init.regs ("ADDR VALUE # GROUP"), so cam-probe can replay it
instead of the stock bring-up (cam-probe own) and SKIP works on its groups.

    scripts/cam-own-regs.py > overlay-static/etc/cam-own.regs
    scripts/cam-own-regs.py --gamma-c    # gamma curve as a C table (fh8626-camera.c)
"""

import sys

ISP = 0xE8400000
W, H = 1920, 1080
HA = (H + 15) // 16 * 16          # VPU channel height, 16-line aligned: 1088
Y_ADDR, C_ADDR = 0xA3016000, 0xA3016000 + W * HA   # C follows Y (0xA3214000)
RAW_STRIDE = (W * 10 + 63) // 64 * 8                # 2400 bytes per RAW10 line

out = []


def group(name, writes):
    for off, val in writes:
        out.append(f"0x{ISP + off:08x} 0x{val & 0xffffffff:08x}  # {name}")


def pack16(lo, hi):
    return (hi << 16) | lo


def size(w, h):                   # (h - 1) << 16 | (w - 1)
    return pack16(w - 1, h - 1)


# --- before the pipeline start ---------------------------------------------------
# VPU channels 1 and 2 are unused, but need a valid size and scale step before the
# ISP start, written while the channel is switched on in +0x30 (bits 21/22);
# otherwise no frame arrives (storm 0x10008). The size is free: 640x360.
CW, CH = 640, 360


def chn(n):
    b, s = 0xF40 + 0x38 * (n - 1), 0x1200 + 8 * n
    hs = (W * (1 << 20) + CW // 2) // CW
    vs = (HA * (1 << 20) + CH // 2) // CH
    return [(b, CW - 1), (b + 8, CH - 1), (s, 0), (s + 4, size(CW, CH)),
            (b + 0x24, hs), (b + 0x28, hs), (b + 0x2C, vs), (b + 0x30, vs),
            (b + 0x0C, 0), (b + 0x10, 0), (b + 0x14, 0)]


group("vpu_ch12", [(0x030, 0x00E6DF83)] + chn(1) + [(0x030, 0x00C6DF83)]
      + chn(2) + [(0x030, 0x0086DF83)])

group("isp_irq_enable", [(0x008, 0x07FFFFFF)])

group("isp_pic_size", [(0x138, size(W, H)), (0x14C, size(W, H)), (0x0D0, size(W, H))])

# Line buffer strides/windows of SetIspPicSize. Partly understood (0x2E8/0x2F0 =
# RAW10 stride 2400; 640 = W/3, 600 = 2400/4); kept as a separate group so that a
# run can leave it out.
group("isp_pic_size_misc", [
    (0x490, 0x20270000), (0x500, 0), (0x504, 0), (0x508, 0x7820783B),
    (0x4E0, 0x0F0F203B), (0x4E4, 0), (0x4E8, 0x0001FFF1),
    (0x2E8, RAW_STRIDE), (0x2F0, 0x80000000 | RAW_STRIDE),
    (0x2F8, W // 3), (0x300, 0x80000000 | W // 3),
    (0x308, RAW_STRIDE // 4), (0x310, 0x80000000 | RAW_STRIDE // 4),
    (0x318, RAW_STRIDE // 4), (0x320, 0x80000000 | RAW_STRIDE // 4),
    (0x41C, RAW_STRIDE // 4), (0xAA8, RAW_STRIDE // 4),
    (0x648, 0x80000348), (0x658, 0x80000348),
])

# Input: Bayer phase 2 (bits 3-4) plus bits 9 and 12.
group("isp_input", [(0x028, 0x00001210)])

# Black level 0x42 per channel pair.
group("isp_blc", [(0x13C, pack16(0x42, 0x42)), (0x140, pack16(0x42, 0x42))])

# Tone tables +0x1A40/+0x1B80/+0x1CC0 (12 bit) and +0x1E00 (10 bit): linear,
# 160 entries, two per word, low half first.
tone = []
for t in range(4):
    m = 4095 if t < 3 else 1023
    for j in range(80):
        a = int(2 * j * m / 159 + 0.5)
        b = int((2 * j + 1) * m / 159 + 0.5)
        tone.append((0x1A40 + 0x140 * t + 4 * j, pack16(a, b)))
group("isp_tone", tone)

# White balance (0x200 = 1.0): +0x570 B | G << 16, +0x574 G | R << 16.
# R 1.3, B 3.5 for the office fluorescent light; AWB replaces this later.
group("isp_wb", [(0x570, pack16(0x700, 0x200)), (0x574, pack16(0x200, 0x29A))])

# DRC block on; must be written before the start (0 stalls the pipeline).
group("isp_drc", [(0x590, 0x0D)])

# Colour gain (lower field, 0 = no colour).
group("isp_sat", [(0x600, 0x100)])

# Gamma: 256 x 16 bit, power curve x^1.7, then the enable word.
gam = []
for j in range(128):
    a = min(65535, int(65535 * ((2 * j) / 255) ** 1.7 + 0.5))
    b = min(65535, int(65535 * ((2 * j + 1) / 255) ** 1.7 + 0.5))
    gam.append((0x690 + 4 * j, pack16(a, b)))
gam.append((0x890, 0x00010007))
group("isp_gamma", gam)

# Contrast 1.0, chroma on.
group("isp_yc", [(0xE00, 0x40), (0xE0C, 0x00010000)])

# VPU scaler coefficients: identity filter in all 4 banks x 64 phases
# (centre tap 0x100 = 1.0 in bits 19:10).
coef = []
for b in range(4):
    for p in range(64):
        coef += [(0x1F40 + 0x200 * b + 8 * p, 0x100 << 10), (0x1F44 + 0x200 * b + 8 * p, 0)]
group("vpu_scaler_coef", coef)

# Pipeline start (isp_startup_Isp_PkgEnable).
group("isp_start", [(0x100, 4), (0x034, 5), (0x100, 5)])

# --- after the start: VPU channel 0, 1920x1088 NV12 --------------------------------
group("vpu_vi_size", [
    (0xF04, pack16(W - 1, W - 1)), (0xF0C, pack16(H - 1, H // 2 - 1)),
    (0xF3C, pack16(W - 1, W - 1)), (0xF44, pack16(H - 1, H // 2 - 1)),
    (0xF74, pack16(W - 1, W - 1)), (0xF7C, pack16(H - 1, H // 2 - 1)),
])
vstep = (H * (1 << 20) + HA // 2) // HA   # 1080 input lines onto 1088 output lines
group("vpu_ch0", [
    (0xF2C, 1 << 20), (0xF30, 1 << 20), (0xF34, vstep), (0xF38, vstep),
    (0xF08, W - 1), (0xF10, HA - 1), (0x1200, 0), (0x1204, size(W, HA)),
    (0xF14, 0), (0xF18, 0), (0xF1C, 0),
    (0x030, 0x0086DF83), (0x0E8, ((H - 1) << 12) | (H - 1)),
    (0x1358, pack16(W, W)), (0x1350, Y_ADDR), (0x1354, C_ADDR),
    (0x030, 0x0096DF83),
])

if sys.argv[1:] == ["--gamma-c"]:
    curve = [min(65535, int(65535 * (i / 255) ** 1.7 + 0.5)) for i in range(256)]
    for i in range(0, 256, 8):
        print("\t" + " ".join(f"0x{v:04x}," for v in curve[i:i + 8]))
    sys.exit(0)

print("# Own ISP/VPU bring-up for the SC2336, 1920x1080 NV12 (scripts/cam-own-regs.py).")
print("# Absolute addresses, in execution order; the group name follows #.")
print("# --- own")
print("\n".join(out))
