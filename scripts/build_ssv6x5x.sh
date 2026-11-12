#!/bin/sh
# Build ssv6x5x.ko out of tree against work/linux.
# usage: build_ssv6x5x.sh [linux tree] [ssv tree] [output dir]
# env:   CROSS_COMPILE (default arm-linux-gnueabi-), LLVM=1 for clang
# The kernel must be configured and prepared (make fh8626_defconfig modules_prepare).
set -eu
top=$(cd "$(dirname "$0")/.." && pwd)
k=${1:-$top/work/linux}
s=${2:-$top/work/ssv6x5x}
out=${3:-$top/work/out}
mkdir -p "$out"

# modpost needs the kernel's exported-symbol list.  A plain "make zImage" does not
# create Module.symvers; "make modules" (or a full "make") does.  Without it every
# kernel symbol is reported as undefined.
if [ ! -f "$k/Module.symvers" ]; then
	echo "NOTE: $k/Module.symvers is missing, building the kernel modules first" >&2
	# modpost also needs vmlinux.o (the built-in symbols): link vmlinux first.  On a clean
	# tree "make modules" alone fails with "vmlinux.o is missing" and thousands of
	# undefined symbols.  The final zImage later only re-links with the full initramfs.
	for goal in vmlinux modules; do
		make -C "$k" ARCH=arm CROSS_COMPILE="${CROSS_COMPILE:-arm-linux-gnueabi-}" ${LLVM:+LLVM=$LLVM} \
		     -j"$(nproc)" $goal
	done
	[ -f "$k/Module.symvers" ] || {
		echo "ERROR: still no Module.symvers; run a full 'make' in $k (CONFIG_MODULES=y needed)" >&2
		exit 1
	}
fi

# The module must be compiled against exactly the configuration of the kernel it runs
# in.  struct net_device (and others) change layout with options such as CONFIG_INET;
# a stale module then corrupts the device structure and crashes in register_netdevice().
# vermagic does not contain the config, so remember a fingerprint and rebuild from
# scratch whenever it changes.
stamp="$s/.kernel-stamp"
now=$( { cat "$k/include/config/kernel.release"; cat "$k/include/config/auto.conf"; } | sha256sum | cut -d' ' -f1 )
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$now" ]; then
	echo "NOTE: kernel configuration changed since the last module build: cleaning $s" >&2
	make -C "$k" ARCH=arm CROSS_COMPILE="${CROSS_COMPILE:-arm-linux-gnueabi-}" ${LLVM:+LLVM=$LLVM} \
	     M="$s" KBUILD_TOP="$s" clean >/dev/null 2>&1 || true
fi

make -C "$k" ARCH=arm CROSS_COMPILE="${CROSS_COMPILE:-arm-linux-gnueabi-}" ${LLVM:+LLVM=$LLVM} \
     M="$s" KBUILD_TOP="$s" -j"$(nproc)" modules
echo "$now" > "$stamp"
cp "$s/ssv6x5x.ko" "$out/"
${CROSS_COMPILE:-arm-linux-gnueabi-}strip --strip-unneeded "$out/ssv6x5x.ko" 2>/dev/null || llvm-strip --strip-unneeded "$out/ssv6x5x.ko"
ls -l "$out/ssv6x5x.ko"
