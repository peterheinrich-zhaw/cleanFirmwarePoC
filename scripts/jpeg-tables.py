#!/usr/bin/env python3
"""Tables of the FH8626V200 JPEG encoder, computed from the standard tables of
ITU-T T.81 Annex K (docs/CAM.md, "JPEG encoder").

  +0x100..+0x3fc  encoder code table: per symbol (code & 0xff, length - 1);
                  AC luma at 0, AC chroma at 0x160 (per run 0..15 the sizes
                  1..10, then EOB, ZRL, 6 x 0xff0f, RST0..7), DC luma at
                  0x2c0, DC chroma at 0x2e0 (12 entries, then zeros)
  +0x400..+0x598  DHT payload: bits + values of DC luma, AC luma, DC chroma,
                  AC chroma, in that order
  +0x80..+0xfc    quantisation tables (64 luma + 64 chroma bytes, zigzag)

    jpeg-tables.py words     both Huffman tables as hex words (0x100.., 0x400..)
    jpeg-tables.py q SCALE   quantisation words for SCALE (1..100 %, 100 = Annex K)
"""
import sys

DC_L_BITS = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
DC_C_BITS = [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
DC_VALS = list(range(12))
AC_L_BITS = [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d]
AC_L_VALS = bytes.fromhex(
    "01020300041105122131410613516107227114328191a1082342b1c11552d1f0"
    "2433627282090a161718191a25262728292a3435363738393a43444546474849"
    "4a535455565758595a636465666768696a737475767778797a83848586878889"
    "8a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5"
    "c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8"
    "f9fa")
AC_C_BITS = [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77]
AC_C_VALS = bytes.fromhex(
    "000102031104052131061241510761711322328108144291a1b1c109233352f0"
    "156272d10a162434e125f11718191a262728292a35363738393a434445464748"
    "494a535455565758595a636465666768696a737475767778797a828384858687"
    "88898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3"
    "c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8"
    "f9fa")
Q_L = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
       14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
       18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
       49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
Q_C = [17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
       24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99] + [99] * 32
ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
          12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
          35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
          58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63]


def codes(bits, vals):
    """Canonical Huffman codes (T.81 Annex C): symbol -> (code, length)."""
    out, code, k = {}, 0, 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            out[vals[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return out


def enc_entry(c):
    return bytes([c[0] & 0xff, c[1] - 1])


def ac_table(bits, vals):
    c = codes(bits, vals)
    b = b"".join(enc_entry(c[(run << 4) | size])
                 for run in range(16) for size in range(1, 11))
    b += enc_entry(c[0x00]) + enc_entry(c[0xf0])
    b += bytes([0xff, 0x0f]) * 6
    b += b"".join(bytes([0xd0 + i, 0x0f]) for i in range(8))	# RST0..7 markers
    return b


def dc_table(bits):
    c = codes(bits, DC_VALS)
    return b"".join(enc_entry(c[i]) for i in range(12)) + bytes(8)


def huff_table():
    return (ac_table(AC_L_BITS, AC_L_VALS) + ac_table(AC_C_BITS, AC_C_VALS) +
            dc_table(DC_L_BITS) + dc_table(DC_C_BITS))


def dht_table():
    return (bytes(DC_L_BITS) + bytes(DC_VALS) + bytes(AC_L_BITS) + AC_L_VALS +
            bytes(DC_C_BITS) + bytes(DC_VALS) + bytes(AC_C_BITS) + AC_C_VALS)


def qtable(scale):
    q = lambda v: max(1, min(255, (v * scale + 50) // 100))
    return bytes(q(Q_L[z]) for z in ZIGZAG) + bytes(q(Q_C[z]) for z in ZIGZAG)


def words(b):
    b = b + bytes(-len(b) % 4)
    return [int.from_bytes(b[i:i + 4], "little") for i in range(0, len(b), 4)]


if __name__ == "__main__":
    if sys.argv[1:] == ["words"]:
        print(" ".join("%08x" % w for w in words(huff_table())))
        print(" ".join("%08x" % w for w in words(dht_table())))
    elif sys.argv[1:2] == ["q"]:
        print(" ".join("%08x" % w for w in words(qtable(int(sys.argv[2])))))
    else:
        print(__doc__)
