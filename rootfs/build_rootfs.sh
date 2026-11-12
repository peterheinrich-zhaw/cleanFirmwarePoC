#!/bin/sh
# Build a static BusyBox and an initramfs description file for the kernel.
#   sh build_rootfs.sh            -> rootfs/initramfs.list (use as INITRAMFS_SOURCE)
# Variables: BB_VER (default 1.37.0), CROSS (default arm-linux-gnueabi-),
#            ROOTFS_OUT (default ./build/rootfs)
# SKIP_BUILD=1 reuses the installed tree in $ROOTFS_OUT/root (no BusyBox rebuild).
# ROOTFS_OVERLAY: colon-separated directories whose contents are copied into the
#            root before the list is written (build outputs such as kernel modules).
set -e
BB_VER=${BB_VER:-1.37.0}
CROSS=${CROSS:-arm-linux-gnueabi-}
TOP=${ROOTFS_OUT:-$(pwd)/build/rootfs}
ROOT=$TOP/root
mkdir -p "$TOP"

if [ -z "$SKIP_BUILD" ]; then
	cd "$TOP"
	[ -d "busybox-$BB_VER" ] || {
		wget -q "https://busybox.net/downloads/busybox-$BB_VER.tar.bz2"
		tar xf "busybox-$BB_VER.tar.bz2"
	}
	cd "busybox-$BB_VER"
	# configure once; re-running defconfig would force a full rebuild
	if [ ! -f .config ]; then
		make defconfig >/dev/null
		# static; 'tc' fails against current kernel headers and is not needed
		sed -i 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/; s/^CONFIG_TC=y/# CONFIG_TC is not set/' .config
		# x86-only SHA hardware acceleration breaks the ARM build in 1.37.0
		sed -i "s/^CONFIG_SHA1_HWACCEL=y/# CONFIG_SHA1_HWACCEL is not set/; s/^CONFIG_SHA256_HWACCEL=y/# CONFIG_SHA256_HWACCEL is not set/" .config
	fi
	make -j"$(nproc)" CROSS_COMPILE="$CROSS"
	rm -rf "$ROOT"
	make CROSS_COMPILE="$CROSS" CONFIG_PREFIX="$ROOT" install >/dev/null
fi

[ -x "$ROOT/bin/busybox" ] || { echo "no $ROOT/bin/busybox"; exit 1; }
mkdir -p "$ROOT/proc" "$ROOT/sys" "$ROOT/dev" "$ROOT/tmp" "$ROOT/etc" "$ROOT/root"

cat > "$ROOT/init" <<'INIT'
#!/bin/busybox sh
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t devtmpfs devtmpfs /dev
echo
echo "FH8626V200: userspace is up (BusyBox)"
[ -x /sbin/wifi-up ] && [ -f /etc/wpa_supplicant.conf ] && /bin/busybox setsid /sbin/wifi-up >/dev/console 2>&1 &
[ -x /etc/init.d/rcS ] && /bin/busybox setsid /etc/init.d/rcS >/dev/console 2>&1 &
exec /bin/busybox setsid /bin/busybox sh -c 'exec /bin/busybox sh </dev/console >/dev/console 2>&1'
INIT
chmod 755 "$ROOT/init"

# overlays (build outputs that must not live in the repository)
# Modules come only from an overlay. Drop those of earlier kernel releases,
# which SKIP_BUILD would otherwise keep in $ROOT.
rm -rf "$ROOT/lib/modules"
OLDIFS=$IFS; IFS=:
for d in $ROOTFS_OVERLAY; do
	[ -n "$d" ] || continue
	[ -d "$d" ] || { echo "overlay $d does not exist, skipped"; continue; }
	cp -a "$d"/. "$ROOT"/
	echo "overlay: $d"
done
IFS=$OLDIFS

# cpio description for gen_init_cpio: works without root privileges
LIST=$TOP/initramfs.list
{
	echo "dir /dev 0755 0 0"
	echo "nod /dev/console 0600 0 0 c 5 1"
	echo "nod /dev/null 0666 0 0 c 1 3"
	cd "$ROOT"
	find . -mindepth 1 | sort | while read -r p; do
		n=${p#.}
		case $n in /dev|/dev/*) continue ;; esac
		if [ -L "$p" ]; then
			echo "slink $n $(readlink "$p") 0777 0 0"
		elif [ -d "$p" ]; then
			echo "dir $n 0755 0 0"
		else
			echo "file $n $ROOT$n 0755 0 0"
		fi
	done
} > "$LIST"
echo "wrote $LIST ($(wc -l < "$LIST") entries)"
echo "kernel: scripts/config --set-str INITRAMFS_SOURCE $LIST && make olddefconfig"
