#!/bin/sh
# Install kernel modules (in-tree ones, if any, and the out-of-tree ssv6x5x) into the
# rootfs overlay under /lib/modules/<kernelrelease>/ and create modules.dep, so that
# BusyBox `modprobe <name>` works.
# usage: install_modules.sh <linux tree> <ssv tree> <overlay dir> [cross prefix]
set -eu
k=$1; s=$2; ov=$3; cross=${4:-arm-linux-gnueabi-}
[ -f "$k/include/config/kernel.release" ] || { echo "no kernel.release: build the kernel first" >&2; exit 1; }
krel=$(cat "$k/include/config/kernel.release")
dir=$ov/lib/modules/$krel
M="make -C $k ARCH=arm CROSS_COMPILE=$cross ${LLVM:+LLVM=$LLVM} INSTALL_MOD_PATH=$ov INSTALL_MOD_STRIP=1 DEPMOD=true"

# a directory of another release (older kernel build) would only confuse modprobe
rm -rf "$ov/lib/modules"
mkdir -p "$ov/lib/modules"

# in-tree modules (CONFIG_*=m), only if there are any
if grep -q '=m$' "$k/.config" && [ -s "$k/modules.order" ]; then
	$M modules_install
	rm -f "$dir/build" "$dir/source"          # host paths do not belong in the image
fi
# out-of-tree ssv6x5x -> $dir/updates/ssv6x5x.ko
$M M="$s" KBUILD_TOP="$s" modules_install
mkdir -p "$dir"

if command -v depmod >/dev/null 2>&1; then
	depmod -b "$ov" ${k:+-F "$k/System.map"} "$krel" 2>/dev/null || depmod -b "$ov" "$krel"
else
	echo "NOTE: depmod not found, writing a dependency-free modules.dep" >&2
	( cd "$dir" && find . -name '*.ko' | sed 's|^\./||; s|$|:|' | sort > modules.dep )
	: > "$dir/modules.alias"; : > "$dir/modules.symbols"
fi
echo "modules for $krel:"; ( cd "$dir" && find . -name '*.ko' ) | sed 's/^/  /'
echo "modprobe will look in /lib/modules/$krel (must equal 'uname -r' on the camera)"
