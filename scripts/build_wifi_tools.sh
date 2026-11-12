#!/bin/sh
# Build static iw + wpa_supplicant (nl80211, internal crypto, no OpenSSL) for the initramfs.
# usage: build_wifi_tools.sh <dl dir> <work dir> <overlay dir>
# env:   CROSS (default arm-linux-gnueabi-)   needs: pkg-config, make, curl or wget
set -eu
top=$(cd "$(dirname "$0")/.." && pwd)
dl=${1:-$top/dl}; work=${2:-$top/work/wifi-tools}; ov=${3:-$top/build/overlay}
CROSS=${CROSS:-arm-linux-gnueabi-}
cfg=$top/config/wifi-tools.mk
# the config is a makefile (it uses $(subst ...)): let make expand the variable
get() { printf 'p:\n\t@echo $(%s)\n' "$1" | make -s -f "$cfg" -f - p; }
mkdir -p "$dl" "$work" "$ov/usr/sbin"
prefix=$work/prefix

fetch() {                       # name url sha256
	f=$dl/$(basename "$2")
	[ -f "$f" ] || { echo "download $2"; (curl -fsSL -o "$f" "$2" || wget -q -O "$f" "$2"); }
	got=$(sha256sum "$f" | cut -d' ' -f1)
	if [ -z "$3" ]; then
		echo "NO PINNED HASH for $1. Downloaded file has sha256:"; echo "  $got  $f"
		echo "Verify it against the upstream signature, put it into $cfg, run again." >&2
		exit 1
	fi
	[ "$got" = "$3" ] || { echo "HASH MISMATCH for $f: $got" >&2; rm -f "$f"; exit 1; }
	echo "$f"
}
unpack() { rm -rf "$work/$2"; mkdir -p "$work/$2"; tar xf "$1" -C "$work/$2" --strip-components=1; }

# --- libnl (static) ---
f=$(fetch libnl "$(get LIBNL_URL)" "$(get LIBNL_SHA256)" | tail -1)
unpack "$f" libnl
( cd "$work/libnl" && ./configure --host="${CROSS%-}" --prefix="$prefix" --enable-static --disable-shared \
	--disable-cli --disable-debug >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null )

export PKG_CONFIG_PATH=$prefix/lib/pkgconfig PKG_CONFIG_LIBDIR=$prefix/lib/pkgconfig

# --- iw ---
f=$(fetch iw "$(get IW_URL)" "$(get IW_SHA256)" | tail -1)
unpack "$f" iw
make -C "$work/iw" CC="${CROSS}gcc" PKG_CONFIG="pkg-config --static" LDFLAGS="-static" -j"$(nproc)" >/dev/null
"${CROSS}strip" -o "$ov/usr/sbin/iw" "$work/iw/iw"

# --- wpa_supplicant ---
f=$(fetch wpa_supplicant "$(get WPAS_URL)" "$(get WPAS_SHA256)" | tail -1)
unpack "$f" wpas
cat > "$work/wpas/wpa_supplicant/.config" <<'CFG'
CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_CTRL_IFACE=y
CONFIG_TLS=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_INTERNAL_LIBTOMMATH_FAST=y
CONFIG_IEEE80211W=y
CONFIG_IEEE80211N=y
CONFIG_IEEE80211AC=y
CONFIG_NO_RANDOM_POOL=y
CONFIG_NO_WPA_PASSPHRASE=y
CFG
# WPA3 (SAE) is off by default: with the internal crypto it pulls in dragonfly.c/dh_groups.c, which
# need crypto_bignum_*/crypto_dh_* that this configuration does not link (undefined references).
# Try WPAS_SAE=y after adding an EAP/TLS method or switching CONFIG_TLS to a real crypto library.
[ "${WPAS_SAE:-n}" = y ] && echo CONFIG_SAE=y >> "$work/wpas/wpa_supplicant/.config"
# wpa_supplicant's Makefile hard-codes -lnl-3 -lnl-genl-3 without a -L path, so point the
# linker at our private libnl prefix.  LIBS is passed through the environment so that the
# Makefile's "LIBS += ..." appends to it (a command-line LIBS= would replace the driver libs).
LIBS="-lm -lpthread" make -C "$work/wpas/wpa_supplicant" CC="${CROSS}gcc" PKG_CONFIG="pkg-config --static" \
     LDFLAGS="-static -L$prefix/lib" -j"$(nproc)" wpa_supplicant >/dev/null
"${CROSS}strip" -o "$ov/usr/sbin/wpa_supplicant" "$work/wpas/wpa_supplicant/wpa_supplicant"
ls -l "$ov/usr/sbin/iw" "$ov/usr/sbin/wpa_supplicant"
