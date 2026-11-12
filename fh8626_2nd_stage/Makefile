# Cross-compile with the arm-none-eabi toolchain (works fine for this
# freestanding/bare-metal use even though our target is an ARM9/Cortex-A
# core, not a Cortex-M -- we're not using any Thumb-only or -M-only
# features).
#
# On macOS (Homebrew):
#   brew install --cask gcc-arm-embedded
# or, if that cask is unavailable on your macOS/Homebrew version:
#   brew tap ArmMbed/homebrew-formulae
#   brew install arm-none-eabi-gcc
#
# Then just:  make

CROSS   ?= arm-none-eabi-
CC      := $(CROSS)gcc
OBJCOPY := $(CROSS)objcopy
OBJDUMP := $(CROSS)objdump
SIZE    := $(CROSS)size

# arm926ej-s target: conservative subset both a real ARM9 core and a
# Cortex-A core (in ARM state) can execute -- see README for why the
# exact core type at this boot stage is still unconfirmed.
CFLAGS  := -mcpu=arm926ej-s -marm -mfloat-abi=soft \
           -ffreestanding -nostdlib -nostartfiles -fno-builtin \
           -O1 -Wall -Wextra -g
ASFLAGS := -mcpu=arm926ej-s -marm
LDFLAGS := -T link.ld -nostdlib -static

OBJS := start.o vectors.o main.o uart.o
# No DRAM/PLL table replay here: hardware testing proved the boot ROM
# already performs real DRAM training itself, and our own redundant
# replay of it caused a hard reset. The 249-entry register table
# lives in mkimage.py now (DDR_TABLE), not as C source.

all: 2nd_stage.bin size

2nd_stage.elf: $(OBJS) link.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(OBJS)

2nd_stage.bin: 2nd_stage.elf
	$(OBJCOPY) -O binary $< $@

size: 2nd_stage.elf
	$(SIZE) 2nd_stage.elf
	@echo "Raw image size (must be <= 0x4000 = 16384 bytes to fit the vendor 'ram' slot as-is):"
	@wc -c < 2nd_stage.bin

disasm: 2nd_stage.elf
	$(OBJDUMP) -d 2nd_stage.elf | less

# make image
image: 2nd_stage.bin
	python3 mkimage.py --stub 2nd_stage.bin --out flash_image.bin

clean:
	rm -f *.o 2nd_stage.elf 2nd_stage.bin flash_image.bin

.PHONY: all size disasm clean image
