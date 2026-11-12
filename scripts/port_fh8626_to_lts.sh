#!/bin/sh
# Port the FH8626V200 kernel support from the 4.9 work tree to a modern
# (6.x) kernel work tree, as three reviewable commits.
#
#   sh port_fh8626_to_lts.sh <old tree: .../open-cam-fw/work/linux> <new tree: work/linux>
#
# The old tree is only READ. The new tree must be the clean, prepared
# 6.18 tree (git repo, no uncommitted changes). Every hook edit is checked;
# if an anchor is not found the script stops and prints what to look at,
# and you can discard its work with: git -C <new tree> reset --hard HEAD
set -eu
OLD="${1:-}"; NEW="${2:-}"
[ -d "$OLD" ] && [ -d "$NEW" ] || { echo "usage: $0 <old tree> <new tree>"; exit 1; }
# Absolute paths: the script changes directory below.
OLD=$(cd "$OLD" && pwd); NEW=$(cd "$NEW" && pwd)
[ -d "$OLD/arch/arm/mach-fh8626" ] || { echo "old tree: $OLD/arch/arm/mach-fh8626 not found"; exit 1; }
[ -f "$OLD/arch/arm/boot/dts/fh8626v200.dts" ] || { echo "old tree: DTS not found"; exit 1; }
[ -f "$OLD/arch/arm/configs/fh8626_defconfig" ] || { echo "old tree: defconfig not found"; exit 1; }
[ -d "$NEW/.git" ] && [ -d "$NEW/arch/arm" ] || { echo "new tree: not a prepared kernel git tree"; exit 1; }
cd "$NEW"
[ -z "$(git status --porcelain)" ] || { echo "new tree has uncommitted changes; refusing"; exit 1; }
git config user.name  >/dev/null || { echo "set git user.name / user.email first"; exit 1; }
git config user.email >/dev/null || { echo "set git user.name / user.email first"; exit 1; }
[ -d arch/arm/boot/dts/fullhan ] && { echo "arch/arm/boot/dts/fullhan already exists; refusing"; exit 1; }
[ -d arch/arm/mach-fh8626 ]      && { echo "arch/arm/mach-fh8626 already exists; refusing"; exit 1; }
export LC_ALL=C

fail() { echo "FAILED: $1"; echo "Look at: $2"; exit 1; }

# ---- commit 1: machine support ---------------------------------------
mkdir arch/arm/mach-fh8626
cp "$OLD"/arch/arm/mach-fh8626/Kconfig "$OLD"/arch/arm/mach-fh8626/Makefile \
   "$OLD"/arch/arm/mach-fh8626/fh8626.c arch/arm/mach-fh8626/
# arch/arm/Kconfig: sorted list of  source "arch/arm/mach-*/Kconfig"
grep -q 'mach-footbridge/Kconfig' arch/arm/Kconfig || \
  fail "Kconfig anchor (mach-footbridge) not found" "grep -n 'source \"arch/arm/mach-' arch/arm/Kconfig"
awk '/^source "arch\/arm\/mach-footbridge\/Kconfig"/ && !d {
        print "source \"arch/arm/mach-fh8626/Kconfig\""; print ""; d=1 }
     { print }' arch/arm/Kconfig > arch/arm/Kconfig.new && mv arch/arm/Kconfig.new arch/arm/Kconfig
grep -q 'mach-fh8626/Kconfig' arch/arm/Kconfig || fail "Kconfig hook not inserted" "arch/arm/Kconfig"

# arch/arm/Makefile: sorted list of  machine-$(CONFIG_ARCH_X) += dir
grep -q '^machine-\$(CONFIG_' arch/arm/Makefile || \
  fail "no 'machine-\$(CONFIG_...)' lines in arch/arm/Makefile" "grep -n 'machine-\\|machdirs\\|core-y' arch/arm/Makefile"
awk '
  /^machine-\$\(CONFIG_[A-Z0-9_]+\)/ {
     sym=$0; sub(/^machine-\$\(CONFIG_/,"",sym); sub(/\).*/,"",sym)
     if (!ins && sym > "ARCH_FH8626") ins=NR
     last=NR
  }
  { line[NR]=$0 }
  END {
     if (!ins) ins=last+1
     for (i=1;i<=NR+1;i++) {
        if (i==ins) printf "machine-$(CONFIG_ARCH_FH8626)\t\t+= fh8626\n"
        if (i<=NR) print line[i]
     }
  }' arch/arm/Makefile > arch/arm/Makefile.new && mv arch/arm/Makefile.new arch/arm/Makefile
grep -q 'CONFIG_ARCH_FH8626' arch/arm/Makefile || fail "Makefile hook not inserted" "arch/arm/Makefile"

git add arch/arm/mach-fh8626 arch/arm/Kconfig arch/arm/Makefile
git commit -q -F - <<'MSG'
ARM: add Fullhan FH8626V200 machine support (device tree only)

Single Cortex-A7 SoC with a standard GIC and ARM generic timer. The
machine file only reports the PMU chip id (0x20102801 was observed on
the board) and populates the device tree.

This is an independent port; it uses no code from the vendor mach-fh
tree.
MSG

# ---- commit 2: device tree -------------------------------------------
mkdir arch/arm/boot/dts/fullhan
cp "$OLD"/arch/arm/boot/dts/fh8626v200.dts arch/arm/boot/dts/fullhan/
printf 'dtb-$(CONFIG_ARCH_FH8626) += fh8626v200.dtb\n' > arch/arm/boot/dts/fullhan/Makefile
D=arch/arm/boot/dts/Makefile
if   grep -q '^subdir-y[[:space:]]*+=[[:space:]]*gemini' $D; then P='subdir-y'
elif grep -q '^dts-dirs[[:space:]]*+=[[:space:]]*gemini'  $D; then P='dts-dirs'
else fail "no 'gemini' vendor line in $D" "grep -n 'gemini\\|cnxt\\|subdir\\|dts-dirs' $D"; fi
awk -v P="$P" '
  $0 ~ "^" P "[[:space:]]*\\+=[[:space:]]*gemini" && !d { print P " += fullhan"; d=1 }
  { print }' $D > $D.new && mv $D.new $D
grep -q 'fullhan' $D || fail "dts Makefile hook not inserted" "$D"
git add arch/arm/boot/dts/fullhan $D
git commit -q -F - <<'MSG'
ARM: dts: fullhan: add FH8626V200 device tree

Minimal bring-up tree: Cortex-A7, GIC at CBAR (0x30000000), ARM generic
timer with a DT-supplied 24 MHz (CNTFRQ reads 0), PMU block, DW APB UART0
at 0xf0700000 on GIC SPI 30. All values were read on the board.
MSG

# ---- commit 3: defconfig ---------------------------------------------
cp "$OLD"/arch/arm/configs/fh8626_defconfig arch/arm/configs/
git add arch/arm/configs/fh8626_defconfig
git commit -q -F - <<'MSG'
ARM: configs: add fh8626_defconfig

First copy from the 4.9 port; to be normalised with savedefconfig on
this kernel version.
MSG

echo "done:"; git log --oneline -3
echo "next: make fh8626_defconfig && make savedefconfig (see README of this branch)"
