# Pinned upstream releases. Everything we change lives in patches/.
# The SHA256 values are recorded on first download (see scripts/fetch.sh):
# verify the printed hash against the project's published checksums/signature
# (kernel.org: sha256sums.asc in the release directory), then paste it here.
LINUX_VERSION  := 6.18.56
LINUX_URL      := https://mirrors.edge.kernel.org/pub/linux/kernel/v6.x/linux-$(LINUX_VERSION).tar.xz
LINUX_SHA256   := 4633bbda2ab95eaf2a7af4041236451d6759c72e6946265531af4b3aacefbf89 
KERNEL_DTB_DIR := fullhan/

UBOOT_VERSION  := 2026.07
UBOOT_URL      := https://git.u-boot-project.org/u-boot/u-boot/-/releases/v$(UBOOT_VERSION)/downloads/u-boot-$(UBOOT_VERSION).tar.bz2 
UBOOT_SHA256   := 78e8bfc382fe388f9b55aa1daf8c563522a037779b5d4c349d1415e381f1243e 

