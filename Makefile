# Top-level build for the FH8626V200 open firmware.
#
# Upstream sources are NOT stored here. We pin official releases
# (config/versions.mk), download and verify the tarballs, unpack them into
# work/ and apply our patches/. work/ holds ordinary local git repos for
# development; `make export` writes the commits back to patches/.
#
#   make fetch        download + verify the pinned tarballs into dl/
#   make prepare      unpack + patch work/linux and work/u-boot
#   make              build everything -> flash_image.bin: loader, U-Boot, BusyBox,
#                     Wi-Fi module + iw/wpa_supplicant (WITH_WIFI=0 to skip), kernel
#   make export       work/ commits -> patches/ (review, then commit patches/)
#   make reprepare    throw work/ away and rebuild it from the patches
#                     (refuses if work/ has commits that are not exported)
#   make flash        write the WHOLE flash with flashrom
#   make flash-kernel write only the kernel region
#   make bootcmd      print the U-Boot commands that boot the flashed kernel
#   make backup       read the current flash contents to backup-<date>.bin
#   make help         list all targets
#
# Flash layout (keep in sync with FLASH_IMAGE_BASE/IMAGE_SIZE in the loader):
#   0x000000  32 KiB   first stage + 2nd-stage loader
#   0x008000  384 KiB  U-Boot
#   0x070000  64 KiB   reserved (U-Boot environment later)
#   0x080000  4 MiB    kernel (zImage + appended DTB, initramfs inside)
#   0x480000  rest     free

include config/versions.mk

ARCH           ?= arm
CROSS_COMPILE  ?= arm-none-eabi-
JOBS           ?= $(shell nproc 2>/dev/null || echo 1)

DL_DIR         := dl
WORK_DIR       := work
LOADER_DIR     := fh8626_2nd_stage
UBOOT_DIR      := $(WORK_DIR)/u-boot
UBOOT_DEFCONFIG := fh8626_defconfig
LINUX_TARBALL  := $(DL_DIR)/linux-$(LINUX_VERSION).tar.xz
UBOOT_TARBALL  := $(DL_DIR)/u-boot-$(UBOOT_VERSION).tar.bz2

# Kernel: soft-float toolchain, separate from the U-Boot one.
KERNEL_DIR     := $(WORK_DIR)/linux
KERNEL_CROSS   ?= arm-linux-gnueabi-
KERNEL_DEFCONFIG := fh8626_defconfig
KERNEL_DTB_NAME := fh8626v200
# Directory of the DTS below arch/arm/boot/dts, with trailing slash. Empty for
# 4.9; "fullhan/" for 6.x, where DTS files live in vendor directories
# (set in config/versions.mk).
KERNEL_DTB_DIR ?=
WITH_KERNEL    ?= 1
# Static BusyBox initramfs, built by rootfs/build_rootfs.sh and linked into the kernel.
WITH_ROOTFS    ?= 1
ROOTFS_SCRIPT  := rootfs/build_rootfs.sh
ROOTFS_OUT     := $(abspath build/rootfs)
ROOTFS_LIST    := $(ROOTFS_OUT)/initramfs.list

IMAGE          := flash_image.bin
UBOOT_BIN      := $(UBOOT_DIR)/u-boot.bin
KERNEL_ZIMAGE  := $(KERNEL_DIR)/arch/arm/boot/zImage
KERNEL_DTB     := $(KERNEL_DIR)/arch/arm/boot/dts/$(KERNEL_DTB_DIR)$(KERNEL_DTB_NAME).dtb
BUILD_DIR      := build
KERNEL_BLOB    := $(BUILD_DIR)/zImage.dtb

# Layout (bytes). dd works in 4 KiB blocks; all offsets are multiples of that.
FLASH_SIZE     := 8388608
UBOOT_OFFSET   := 0x8000
MAX_UBOOT_SIZE := 393216
KERNEL_OFFSET  := 0x80000
MAX_KERNEL_SIZE := 6291456
DD_BS          := 4096
# DRAM address where U-Boot loads the kernel for `make bootcmd`.
KERNEL_LOAD_ADDR := 0xA1000000

FLASHROM       ?= flashrom
FLASH_PROG     ?= linux_spi:dev=/dev/spidev0.0,spispeed=20000

UBOOT_MAKE  = $(MAKE) -C $(UBOOT_DIR) ARCH=$(ARCH) CROSS_COMPILE=$(CROSS_COMPILE)
KERNEL_MAKE = $(MAKE) -C $(KERNEL_DIR) ARCH=arm CROSS_COMPILE=$(KERNEL_CROSS)

IMAGE_DEPS := loader size-check
ifeq ($(WITH_KERNEL),1)
IMAGE_DEPS += kernel-blob
endif

.PHONY: FORCE all fetch prepare export check-export reprepare loader uboot rootfs kernel \
        kernel-blob image size-check flash flash-kernel bootcmd backup \
        menuconfig kernel-menuconfig kernel-config wifi wifi-tools wifi-refetch clean distclean help

all: image

help:
	@echo "Sources:"
	@echo "  fetch         download + verify upstream tarballs (dl/)"
	@echo "  prepare       unpack + patch work/linux and work/u-boot"
	@echo "  export        write work/ commits to patches/ (then git add patches/)"
	@echo "  reprepare     rebuild work/ from patches/ (refuses if unexported commits)"
	@echo "Build:"
	@echo "  all / image   build loader + U-Boot + kernel and assemble $(IMAGE)"
	@echo "                (WITH_KERNEL=0 leaves the kernel out, WITH_ROOTFS=0 the initramfs)"
	@echo "  loader uboot rootfs kernel   build one component"
	@echo "  wifi wifi-tools wifi-refetch  Wi-Fi module+overlay / iw+wpa_supplicant / re-fetch driver"
	@echo "  flash         write the whole $(IMAGE) with flashrom"
	@echo "  flash-kernel  write only the kernel region (0x80000..)"
	@echo "  bootcmd       print U-Boot commands to boot the flashed kernel"
	@echo "  backup        read current flash to backup-<date>.bin"
	@echo "  menuconfig / kernel-menuconfig"
	@echo "  clean         clean build output in loader, U-Boot, kernel"
	@echo "  distclean     clean + remove $(IMAGE) and $(BUILD_DIR) (work/ and dl/ stay)"
	@echo "Variables: CROSS_COMPILE KERNEL_CROSS WITH_KERNEL WITH_ROOTFS FLASH_PROG FLASHROM JOBS WITH_WIFI WITH_WIFI_TOOLS"

# ---- upstream sources --------------------------------------------

fetch: $(LINUX_TARBALL) $(UBOOT_TARBALL)

$(LINUX_TARBALL):
	sh scripts/fetch.sh $@ $(LINUX_URL) $(LINUX_SHA256)

$(UBOOT_TARBALL):
	sh scripts/fetch.sh $@ $(UBOOT_URL) $(UBOOT_SHA256)

prepare: $(KERNEL_DIR)/.git $(UBOOT_DIR)/.git

$(KERNEL_DIR)/.git: | $(LINUX_TARBALL)
	sh scripts/prepare_tree.sh linux $(LINUX_TARBALL) $(KERNEL_DIR) patches/linux

$(UBOOT_DIR)/.git: | $(UBOOT_TARBALL)
	sh scripts/prepare_tree.sh u-boot $(UBOOT_TARBALL) $(UBOOT_DIR) patches/u-boot

# Files that come from our patches: make sure the trees exist first.
$(UBOOT_DIR)/configs/$(UBOOT_DEFCONFIG): $(UBOOT_DIR)/.git
	@test -f $@ || { echo "ERROR: $@ missing: patches/u-boot does not provide it"; exit 1; }
$(KERNEL_DIR)/arch/arm/configs/$(KERNEL_DEFCONFIG): $(KERNEL_DIR)/.git
	@test -f $@ || { echo "ERROR: $@ missing: patches/linux does not provide it"; exit 1; }

# Commits in work/ -> patches/. Refuses with uncommitted or untracked files.
export:
	sh scripts/export_patches.sh linux  $(KERNEL_DIR) patches/linux
	sh scripts/export_patches.sh u-boot $(UBOOT_DIR)  patches/u-boot

check-export:
	sh scripts/export_patches.sh linux  $(KERNEL_DIR) patches/linux  --check
	sh scripts/export_patches.sh u-boot $(UBOOT_DIR)  patches/u-boot --check

# Safe reset: only if everything in work/ is already in patches/.
reprepare: check-export
	rm -rf $(KERNEL_DIR) $(UBOOT_DIR)
	$(MAKE) prepare

# ---- loader ------------------------------------------------------

loader:
	$(MAKE) -C $(LOADER_DIR) image

# ---- U-Boot ------------------------------------------------------

# Regenerate .config only when the defconfig changed.
$(UBOOT_DIR)/.config: $(UBOOT_DIR)/configs/$(UBOOT_DEFCONFIG)
	$(UBOOT_MAKE) $(UBOOT_DEFCONFIG)

# Always call into U-Boot's own Makefile; it decides what to rebuild.
uboot: $(UBOOT_DIR)/.config
	$(UBOOT_MAKE) -j$(JOBS)

menuconfig: $(UBOOT_DIR)/.config
	$(UBOOT_MAKE) menuconfig

# Fail if U-Boot no longer fits what the loader copies.
size-check: uboot
	@size=$$(stat -c %s $(UBOOT_BIN)) || exit 1; \
	echo "u-boot.bin: $$size bytes (loader copies $(MAX_UBOOT_SIZE))"; \
	if [ $$size -gt $(MAX_UBOOT_SIZE) ]; then \
		echo "ERROR: u-boot.bin is larger than the loader's IMAGE_SIZE"; \
		exit 1; \
	fi

# ---- kernel ------------------------------------------------------

$(KERNEL_DIR)/.config: $(KERNEL_DIR)/arch/arm/configs/$(KERNEL_DEFCONFIG)
	$(KERNEL_MAKE) $(KERNEL_DEFCONFIG)

# The final kernel configuration, created ONCE before anything that depends on
# it (out-of-tree modules must be built against exactly this configuration).
# Re-runs when .config changes (defconfig, menuconfig).  INITRAMFS_SOURCE is
# set here so the path never has to be typed.
KERNEL_CFG_STAMP := $(BUILD_DIR)/kernel-config.stamp
$(KERNEL_CFG_STAMP): $(KERNEL_DIR)/.config
	mkdir -p $(BUILD_DIR)
ifeq ($(WITH_ROOTFS),1)
	$(KERNEL_DIR)/scripts/config --file $(KERNEL_DIR)/.config \
		--set-str INITRAMFS_SOURCE $(ROOTFS_LIST)
else
	$(KERNEL_DIR)/scripts/config --file $(KERNEL_DIR)/.config \
		--set-str INITRAMFS_SOURCE ""
endif
	$(KERNEL_MAKE) olddefconfig
	touch $@

kernel-config: $(KERNEL_CFG_STAMP)

# The kernel release (version + git hash, CONFIG_LOCALVERSION_AUTO) changes
# with every commit in work/linux. Out-of-tree modules carry it as vermagic and
# must be rebuilt when it changes. `prepare` refreshes kernel.release and
# utsrelease.h; the stamp is rewritten only when the release changed.
KERNEL_REL_STAMP := $(BUILD_DIR)/kernel-release.stamp
$(KERNEL_REL_STAMP): $(KERNEL_CFG_STAMP) FORCE
	$(KERNEL_MAKE) -s prepare
	@rel=$$(cat $(KERNEL_DIR)/include/config/kernel.release); \
	[ "$$(cat $@ 2>/dev/null)" = "$$rel" ] || { echo "kernel release: $$rel"; echo "$$rel" > $@; }

FORCE:

# ---- rootfs (BusyBox initramfs) ----------------------------------

# Rebuilt only when the list is missing or the script changed. Delete
# build/rootfs to force a fresh BusyBox build.
$(ROOTFS_LIST): $(ROOTFS_SCRIPT)
	ROOTFS_OUT=$(ROOTFS_OUT) CROSS=$(KERNEL_CROSS) sh $(ROOTFS_SCRIPT)

rootfs: $(ROOTFS_LIST)

# ---- Wi-Fi (ssv6x5x module + iw + wpa_supplicant) -----------------
# Third-party code is fetched at build time from a pinned commit, patched with
# patches/ssv6x5x/*.patch and never committed.  See docs/WIFI.md.
# Order (all by dependencies): kernel config -> BusyBox -> userspace tools ->
# module build against that config -> overlay + initramfs list -> kernel.
WITH_WIFI       ?= $(WITH_ROOTFS)
WITH_WIFI_TOOLS ?= 1
SSV_DIR         := $(WORK_DIR)/ssv6x5x
SSV_OUT         := $(BUILD_DIR)/wifi
# Files that are added to the initramfs on top of BusyBox.
ROOTFS_OVERLAY  ?= $(abspath $(BUILD_DIR)/overlay)
export ROOTFS_OVERLAY

# Credentials for the target network: NOT in git (.env/ is ignored).
WIFI_CONF        ?= .env/wpa_supplicant.conf
WIFI_STAMP       := $(BUILD_DIR)/wifi-staged.stamp
SENSOR_DRIVER    := $(KERNEL_DIR)/drivers/media/i2c/sc2336.c
WIFI_TOOLS_STAMP := $(BUILD_DIR)/wifi-tools.stamp

$(SSV_DIR)/.git: $(wildcard patches/ssv6x5x/*.patch) config/ssv6x5x.mk
	sh scripts/fetch_ssv6x5x.sh $(abspath $(WORK_DIR))

# Static iw + wpa_supplicant.  Stops with an error and prints the sha256 if
# config/wifi-tools.mk has no pinned hash yet.
$(WIFI_TOOLS_STAMP): scripts/build_wifi_tools.sh config/wifi-tools.mk
	mkdir -p $(BUILD_DIR)
	CROSS=$(KERNEL_CROSS) sh scripts/build_wifi_tools.sh $(abspath $(DL_DIR)) \
	    $(abspath $(WORK_DIR)/wifi-tools) $(ROOTFS_OVERLAY)
	touch $@

WIFI_DEPS := $(KERNEL_CFG_STAMP) $(KERNEL_REL_STAMP) $(ROOTFS_LIST) $(SSV_DIR)/.git \
             scripts/build_ssv6x5x.sh scripts/install_modules.sh \
             $(shell find overlay-static -type f 2>/dev/null) $(wildcard $(WIFI_CONF)) \
             $(SENSOR_DRIVER) rootfs/tools/memcat.c rootfs/tools/cam-grab.c rootfs/tools/cam-http.c
ifeq ($(WITH_WIFI_TOOLS),1)
WIFI_DEPS += $(WIFI_TOOLS_STAMP)
endif

# Build the module against the final kernel tree, install it as
# /lib/modules/<release>/updates/ + modules.dep into the overlay, add the
# configs, and regenerate the initramfs list (BusyBox is not rebuilt).
$(WIFI_STAMP): $(WIFI_DEPS)
	mkdir -p $(ROOTFS_OVERLAY)
	CROSS_COMPILE=$(KERNEL_CROSS) sh scripts/build_ssv6x5x.sh \
	    $(abspath $(KERNEL_DIR)) $(abspath $(SSV_DIR)) $(abspath $(SSV_OUT))
	sh scripts/install_modules.sh $(abspath $(KERNEL_DIR)) $(abspath $(SSV_DIR)) \
	    $(ROOTFS_OVERLAY) $(KERNEL_CROSS)
	install -D -m 644 $(SSV_DIR)/ssv6x5x-wifi.cfg $(ROOTFS_OVERLAY)/etc/ssv6x5x-wifi.cfg
	cp -a overlay-static/. $(ROOTFS_OVERLAY)/
	$(KERNEL_CROSS)gcc -static -Os -s -Wall -o $(ROOTFS_OVERLAY)/sbin/memcat rootfs/tools/memcat.c
	$(KERNEL_CROSS)gcc -static -Os -s -Wall -o $(ROOTFS_OVERLAY)/sbin/cam-grab rootfs/tools/cam-grab.c
	$(KERNEL_CROSS)gcc -static -Os -s -Wall -o $(ROOTFS_OVERLAY)/sbin/cam-http rootfs/tools/cam-http.c
	@# sensor mode table for cam-probe, taken from the kernel driver (GPL-2.0)
	sed -n '/sc2336_1080p_30fps_regs\[\] = {/,/^};/s/.*CCI_REG8(0x\([0-9a-f]*\)), 0x\([0-9a-f]*\).*/\1 \2/p' \
	    $(SENSOR_DRIVER) > $(ROOTFS_OVERLAY)/etc/sc2336-1080p30.regs
	@test $$(wc -l < $(ROOTFS_OVERLAY)/etc/sc2336-1080p30.regs) -gt 100 || \
	    { echo "ERROR: no SC2336 mode table found in $(SENSOR_DRIVER)"; exit 1; }
	@if [ -f $(WIFI_CONF) ]; then \
		install -D -m 600 $(WIFI_CONF) $(ROOTFS_OVERLAY)/etc/wpa_supplicant.conf; \
		echo "Wi-Fi: $(WIFI_CONF) included in the image"; \
	else rm -f $(ROOTFS_OVERLAY)/etc/wpa_supplicant.conf; \
		echo 'NOTE: no $(WIFI_CONF): image has no network configuration (autostart skipped)'; fi
	SKIP_BUILD=1 ROOTFS_OUT=$(ROOTFS_OUT) CROSS=$(KERNEL_CROSS) sh $(ROOTFS_SCRIPT)
	@grep -c -e ssv6x5x -e modules.dep $(ROOTFS_OUT)/initramfs.list | \
		sed 's/^/initramfs.list entries (module, modules.dep): /'
	touch $@

wifi: $(WIFI_STAMP)
wifi-tools: $(WIFI_TOOLS_STAMP)
# Throw the fetched/patched driver tree away (after changing patches/ssv6x5x).
wifi-refetch:
	rm -rf $(SSV_DIR) $(WIFI_STAMP)

KERNEL_DEPS := $(KERNEL_CFG_STAMP)
ifeq ($(WITH_ROOTFS),1)
KERNEL_DEPS += $(ROOTFS_LIST)
endif
ifeq ($(WITH_WIFI),1)
KERNEL_DEPS += $(WIFI_STAMP)
endif

# Always call into the kernel's own Makefile; it decides what to rebuild
# (it also notices a changed initramfs list).
kernel: $(KERNEL_DEPS)
	$(KERNEL_MAKE) -j$(JOBS) zImage $(KERNEL_DTB_DIR)$(KERNEL_DTB_NAME).dtb

kernel-menuconfig: $(KERNEL_DIR)/.config
	$(KERNEL_MAKE) menuconfig

# zImage with the DTB appended (needs CONFIG_ARM_APPENDED_DTB=y), so the
# flash holds a single blob and U-Boot is started without a separate DTB.
kernel-blob: kernel
	@grep -q '^CONFIG_ARM_APPENDED_DTB=y' $(KERNEL_DIR)/.config || { \
		echo "ERROR: kernel .config lacks CONFIG_ARM_APPENDED_DTB=y"; exit 1; }
	mkdir -p $(BUILD_DIR)
	cat $(KERNEL_ZIMAGE) $(KERNEL_DTB) > $(KERNEL_BLOB)
	@size=$$(stat -c %s $(KERNEL_BLOB)) || exit 1; \
	echo "zImage+dtb: $$size bytes (slot $(MAX_KERNEL_SIZE))"; \
	if [ $$size -gt $(MAX_KERNEL_SIZE) ]; then \
		echo "ERROR: kernel blob does not fit the kernel flash slot"; \
		exit 1; \
	fi

# ---- final image -------------------------------------------------

# Start from a fresh copy of the loader image every time, so bytes of an
# older, larger U-Boot or kernel can't survive in the tail.
image: $(IMAGE_DEPS)
	cp $(LOADER_DIR)/$(IMAGE) $(IMAGE)
	dd if=$(UBOOT_BIN) of=$(IMAGE) bs=$(DD_BS) \
		seek=$$(( $(UBOOT_OFFSET) / $(DD_BS) )) conv=notrunc
ifeq ($(WITH_KERNEL),1)
	dd if=$(KERNEL_BLOB) of=$(IMAGE) bs=$(DD_BS) \
		seek=$$(( $(KERNEL_OFFSET) / $(DD_BS) )) conv=notrunc
endif
	@size=$$(stat -c %s $(IMAGE)); \
	if [ $$size -gt $(FLASH_SIZE) ]; then \
		echo "ERROR: $(IMAGE) ($$size bytes) is larger than the flash ($(FLASH_SIZE))"; \
		exit 1; \
	elif [ $$size -lt $(FLASH_SIZE) ]; then \
		echo "WARNING: $(IMAGE) is $$size bytes, flash is $(FLASH_SIZE)"; \
	fi; \
	echo "Built $(IMAGE): $$size bytes"

flash: image
	$(FLASHROM) -p $(FLASH_PROG) -w $(IMAGE)

# Write only the kernel region, leaving loader and U-Boot untouched.
flash-kernel: image
	mkdir -p $(BUILD_DIR)
	@printf '%08x:%08x kernel\n' $$(( $(KERNEL_OFFSET) )) \
		$$(( $(KERNEL_OFFSET) + $(MAX_KERNEL_SIZE) - 1 )) > $(BUILD_DIR)/layout.txt
	$(FLASHROM) -p $(FLASH_PROG) -l $(BUILD_DIR)/layout.txt -i kernel -w $(IMAGE)

# U-Boot commands that load and boot the kernel from flash. Reads the
# blob that was built last; it deliberately does NOT rebuild anything, so
# the printed length and CRC describe what `make flash`/`flash-kernel`
# wrote (rebuilding would change the bytes: build counter, timestamps).
bootcmd:
	@test -f $(KERNEL_BLOB) || { echo "no $(KERNEL_BLOB): run make first"; exit 1; }
	@size=$$(stat -c %s $(KERNEL_BLOB)); \
	len=$$(( (size + 4095) / 4096 * 4096 )); \
	printf 'sf probe 0:0; sf read %s 0x%x 0x%x; bootz %s\n' \
		$(KERNEL_LOAD_ADDR) $$(( $(KERNEL_OFFSET) )) $$len $(KERNEL_LOAD_ADDR); \
	python3 -I -c "import zlib,sys;d=open(sys.argv[1],'rb').read();print('check (expect %08x): crc32 %s 0x%x' % (zlib.crc32(d), sys.argv[2], len(d)))" \
		$(KERNEL_BLOB) $(KERNEL_LOAD_ADDR)

backup:
	$(FLASHROM) -p $(FLASH_PROG) -r backup-$$(date +%Y%m%d-%H%M%S).bin

# ---- cleanup -----------------------------------------------------

clean:
	-$(MAKE) -C $(LOADER_DIR) clean
	-test -d $(UBOOT_DIR) && $(UBOOT_MAKE) clean
	-test -d $(KERNEL_DIR) && $(KERNEL_MAKE) clean

# Does not touch work/ (your development trees) or dl/.
distclean: clean
	-test -d $(UBOOT_DIR) && $(UBOOT_MAKE) distclean
	-test -d $(KERNEL_DIR) && $(KERNEL_MAKE) distclean
	rm -rf $(IMAGE) $(BUILD_DIR)
