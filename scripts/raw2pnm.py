#!/usr/bin/env python3
"""Convert a 10-bit packed raw Bayer frame (cam-probe raw) to PGM/PPM.

usage: raw2pnm.py RAW [--width 1920] [--height 1080] [--stride 2400]
                      [--packing mipi|le] [--bayer BGGR|RGGB|GRBG|GBRG]

Writes RAW.pgm (the Bayer mosaic, 8 bit) and RAW.ppm (colour at half
resolution: one RGB pixel per 2x2 Bayer cell, no white balance).

Packing is not known yet, so both candidates are supported:
  mipi  4 pixels in 5 bytes: 4 bytes with bits [9:2], then 1 byte with the
        four 2-bit LSB pairs (MIPI CSI-2 RAW10)
  le    continuous little-endian bit stream, 10 bits per pixel
The wrong one gives an obviously garbled image.
"""
import argparse
import sys


def unpack_line(line, width, packing):
    px = [0] * width
    if packing == "mipi":
        for i in range(0, width - 3, 4):
            b = line[i // 4 * 5:i // 4 * 5 + 5]
            lsb = b[4]
            for j in range(4):
                px[i + j] = (b[j] << 2) | ((lsb >> (2 * j)) & 3)
    else:
        val = int.from_bytes(line, "little")
        for i in range(width):
            px[i] = (val >> (10 * i)) & 0x3ff
    return px


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("raw")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--stride", type=int, default=2400)
    ap.add_argument("--packing", choices=("mipi", "le"), default="mipi")
    ap.add_argument("--bayer", choices=("BGGR", "RGGB", "GRBG", "GBRG"),
                    default="BGGR")
    a = ap.parse_args()

    data = open(a.raw, "rb").read()
    if len(data) < a.stride * a.height:
        sys.exit(f"{a.raw}: {len(data)} bytes, need {a.stride * a.height}")

    rows = [unpack_line(data[y * a.stride:(y + 1) * a.stride], a.width,
                        a.packing) for y in range(a.height)]

    written = [y for y, r in enumerate(rows) if any(r)]
    if written:
        print(f"rows with non-zero data: {len(written)}, "
              f"first {written[0]}, last {written[-1]}")
    else:
        print("all rows are zero: nothing was written")

    flat = [v for r in rows for v in r]
    print(f"min {min(flat)} max {max(flat)} mean {sum(flat) / len(flat):.1f}")

    with open(a.raw + ".pgm", "wb") as f:
        f.write(b"P5 %d %d 255\n" % (a.width, a.height))
        f.write(bytes(v >> 2 for v in flat))

    # position of R, G1, G2, B in a 2x2 cell (index y * 2 + x)
    pos = {c: [] for c in "RGB"}
    for i, c in enumerate(a.bayer):
        pos[c].append(i)
    w2, h2 = a.width // 2, a.height // 2
    out = bytearray()
    for y in range(h2):
        r0, r1 = rows[2 * y], rows[2 * y + 1]
        for x in range(w2):
            cell = (r0[2 * x], r0[2 * x + 1], r1[2 * x], r1[2 * x + 1])
            r = cell[pos["R"][0]]
            g = (cell[pos["G"][0]] + cell[pos["G"][1]]) // 2
            b = cell[pos["B"][0]]
            out += bytes((r >> 2, g >> 2, b >> 2))
    with open(a.raw + ".ppm", "wb") as f:
        f.write(b"P6 %d %d 255\n" % (w2, h2))
        f.write(out)
    print(f"wrote {a.raw}.pgm and {a.raw}.ppm")


if __name__ == "__main__":
    main()
