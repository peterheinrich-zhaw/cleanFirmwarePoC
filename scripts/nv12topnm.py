#!/usr/bin/env python3
"""Convert an NV12 frame (cam-probe vpu) to PGM/PPM.

usage: nv12topnm.py NV12 [--width 1920] [--height 1080]

Writes NV12.pgm (the Y plane) and NV12.ppm (colour, BT.601 full range).
NV12: W x H bytes of Y, then W x H/2 bytes of interleaved Cb, Cr at half
resolution in both directions.
"""
import argparse
import sys


def clamp(v):
    return 0 if v < 0 else 255 if v > 255 else int(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("nv12")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()
    w, h = a.width, a.height

    data = open(a.nv12, "rb").read()
    if len(data) < w * h * 3 // 2:
        sys.exit(f"{a.nv12}: {len(data)} bytes, need {w * h * 3 // 2}")
    y, c = data[:w * h], data[w * h:w * h * 3 // 2]
    print(f"Y min {min(y)} max {max(y)} mean {sum(y) / len(y):.1f}; "
          f"C min {min(c)} max {max(c)}")

    with open(a.nv12 + ".pgm", "wb") as f:
        f.write(b"P5 %d %d 255\n" % (w, h))
        f.write(y)

    rgb = bytearray(w * h * 3)
    for row in range(h):
        crow = (row // 2) * w
        for col in range(w):
            yy = y[row * w + col]
            cb = c[crow + (col & ~1)] - 128
            cr = c[crow + (col & ~1) + 1] - 128
            i = (row * w + col) * 3
            rgb[i] = clamp(yy + 1.402 * cr)
            rgb[i + 1] = clamp(yy - 0.344136 * cb - 0.714136 * cr)
            rgb[i + 2] = clamp(yy + 1.772 * cb)
    with open(a.nv12 + ".ppm", "wb") as f:
        f.write(b"P6 %d %d 255\n" % (w, h))
        f.write(rgb)
    print(f"wrote {a.nv12}.pgm and {a.nv12}.ppm")


if __name__ == "__main__":
    main()
