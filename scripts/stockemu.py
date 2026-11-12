#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run functions of the stock FH8626V200 kernel modules in an ARM emulator and
log every access to the media register blocks.

The stock modules (media_process.ko, isp.ko from orig_firmware/) are loaded as
relocatable ELF objects into Unicorn, linked against each other, and every
other imported symbol (kernel API) is replaced by a small Python stub. ioremap
returns the physical address, so register accesses show up with their real
addresses. Reads return the last written value, or a value from REG_DEFAULTS.

Used to extract register sequences of the stock initialisation and interrupt
handling that are too branchy to reconstruct by hand. Analysis tool only: the
stock binaries are not part of the project.

    stockemu.py [--modules DIR] SCENARIO

SCENARIO is the name of a function in this file starting with "scenario_".
"""
import argparse
import struct
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from elftools.elf.sections import SymbolTableSection
from unicorn import (Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
                     UC_HOOK_MEM_UNMAPPED)
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_PC, UC_ARM_REG_C1_C0_2,
                               UC_ARM_REG_FPEXC)

MOD_BASE = 0x10000000		# modules are loaded from here
STUB_BASE = 0x0f000000		# one 4-byte slot per stubbed function
DATA_BASE = 0x0f800000		# imported data symbols
HEAP_BASE = 0x40000000
HEAP_SIZE = 0x04000000
STACK_TOP = 0x7f000000
STACK_SIZE = 0x00100000
EXIT_ADDR = 0x0e000000		# return address that ends a call
SO_BASE = 0x20000000		# user-space shared objects
DDR_BASE = 0xa0000000		# physical RAM, mapped 1:1 (virtual = physical)
DDR_SIZE = 0x04000000
DDR_ALLOC = 0xa3000000		# buffer_malloc_withname: our reserved area (DTS)

# Physical register blocks (ioremap returns the physical address)
MMIO = [
    (0xe7000000, 0x00100000, "ENC0"),
    (0xe7400000, 0x00100000, "ENC1/LPBUF"),
    (0xe8300000, 0x00100000, "E830"),
    (0xe8400000, 0x00100000, "ISP"),
    (0xe8600000, 0x00100000, "E860"),
    (0xe8700000, 0x00100000, "E870/LPBUF"),
    (0xf0000000, 0x00010000, "PMU"),
    (0xf1000000, 0x00010000, "WRAP"),
    (0xf1100000, 0x00010000, "CSI"),
    (0xf1700000, 0x00010000, "DPHY"),
]

# Read values for registers the code polls or checks
REG_DEFAULTS = {
    0xe8400018: 0x0007ffff,	# DMU idle flags (test_dmu waits for bits 10:9)
    0xe8400014: 0x00001fff,
    0xe8400000: 0x5a542093,
    0xf0000000: 0x20102801,	# chip id
}

R = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]


def u32(v):
    return v & 0xffffffff


class Emu:
    def __init__(self):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        # VFP/NEON on (libispcore's AE uses it): CPACR cp10/cp11 full
        # access, FPEXC.EN
        cpacr = self.uc.reg_read(UC_ARM_REG_C1_C0_2)
        self.uc.reg_write(UC_ARM_REG_C1_C0_2, cpacr | (0xf << 20))
        self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        self.syms = {}			# name -> address (all modules, defined)
        self.addr2sym = []		# (address, name) sorted, for tracing
        self.stubs = {}			# address -> name
        self.stub_next = STUB_BASE
        self.data_next = DATA_BASE
        self.heap_next = HEAP_BASE
        self.mod_next = MOD_BASE
        self.regs = dict(REG_DEFAULTS)
        self.log = []			# (kind, addr, value, function)
        self.unknown_stubs = set()
        self.verbose_calls = False

        uc = self.uc
        uc.mem_map(STUB_BASE, 0x00800000)
        uc.mem_map(DATA_BASE, 0x00800000)
        uc.mem_map(HEAP_BASE, HEAP_SIZE)
        uc.mem_map(STACK_TOP - STACK_SIZE, STACK_SIZE)
        uc.mem_map(EXIT_ADDR, 0x1000)
        uc.mem_map(DDR_BASE, DDR_SIZE)
        self.ddr_next = DDR_ALLOC
        self.so_next = SO_BASE
        self.fds = {}			# fd -> path
        for base, size, _ in MMIO:
            uc.mem_map(base, size)
        uc.hook_add(UC_HOOK_CODE, self._stub_hook, begin=STUB_BASE,
                    end=STUB_BASE + 0x007fffff)
        uc.hook_add(UC_HOOK_CODE, self._exit_hook, begin=EXIT_ADDR,
                    end=EXIT_ADDR + 3)
        for base, size, _ in MMIO:
            uc.hook_add(UC_HOOK_MEM_READ, self._mmio_read, begin=base,
                        end=base + size - 1)
            uc.hook_add(UC_HOOK_MEM_WRITE, self._mmio_write, begin=base,
                        end=base + size - 1)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        self.traced = {}		# address -> name, filled by trace()

    def trace(self, names):
        """Print arguments on entry of these functions (stderr)."""
        for n in names:
            if n in self.syms:
                self.traced[self.syms[n]] = n
                self.uc.hook_add(UC_HOOK_CODE, self._trace_hook,
                                 begin=self.syms[n], end=self.syms[n])

    def _trace_hook(self, uc, addr, size, user):
        args = [uc.reg_read(r) for r in R]
        extra = ""
        if HEAP_BASE <= args[1] < STACK_TOP or args[1] >= 0x7e000000:
            try:
                extra = " [r1] " + " ".join("%08x" % self.rd(args[1] + 4 * i)
                                           for i in range(4))
            except UcError:
                pass
        print("  > %s(%s)%s" % (self.traced[addr], ", ".join("%#x" % v
              for v in args), extra), file=sys.stderr)

    # --- memory helpers -------------------------------------------------
    def rd(self, addr, n=4):
        return int.from_bytes(self.uc.mem_read(addr, n), "little")

    def wr(self, addr, val, n=4):
        self.uc.mem_write(addr, u32(val).to_bytes(4, "little")[:n])

    def cstr(self, addr, maxlen=256):
        if not addr:
            return ""
        try:
            b = bytes(self.uc.mem_read(addr, maxlen))
        except UcError:
            return "<bad %#x>" % addr
        return b.split(b"\0", 1)[0].decode("latin1")

    def alloc(self, size, align=8):
        a = (self.heap_next + align - 1) & ~(align - 1)
        self.heap_next = a + max(size, 4)
        if self.heap_next > HEAP_BASE + HEAP_SIZE:
            raise RuntimeError("emulated heap exhausted")
        self.uc.mem_write(a, b"\0" * max(size, 4))
        return a

    def func_at(self, pc):
        lo, hi = 0, len(self.addr2sym)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.addr2sym[mid][0] <= pc:
                lo = mid + 1
            else:
                hi = mid
        return self.addr2sym[lo - 1][1] if lo else "?"

    # --- loading --------------------------------------------------------
    def load(self, path):
        f = open(path, "rb")
        elf = ELFFile(f)
        secaddr = {}
        for i, sec in enumerate(elf.iter_sections()):
            if not sec["sh_flags"] & 2:		# SHF_ALLOC
                continue
            align = max(sec["sh_addralign"], 4)
            a = (self.mod_next + align - 1) & ~(align - 1)
            size = sec["sh_size"]
            secaddr[i] = a
            self.mod_next = a + size
            end = (self.mod_next + 0xfff) & ~0xfff
            start = a & ~0xfff
            # map lazily in 4 KiB pages
            for page in range(start, end, 0x1000):
                try:
                    self.uc.mem_map(page, 0x1000)
                except UcError:
                    pass
            if sec["sh_type"] != "SHT_NOBITS" and size:
                self.uc.mem_write(a, sec.data())
            else:
                self.uc.mem_write(a, b"\0" * size)
        symtab = elf.get_section_by_name(".symtab")
        local = []
        for sym in symtab.iter_symbols():
            shndx = sym["st_shndx"]
            if isinstance(shndx, int) and shndx in secaddr:
                addr = secaddr[shndx] + sym["st_value"]
                local.append((sym, addr))
                if sym["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT") \
                        and sym.name:
                    if sym["st_info"]["bind"] == "STB_GLOBAL" \
                            or sym.name not in self.syms:
                        self.syms[sym.name] = addr
                    self.addr2sym.append((addr, sym.name))
        self.addr2sym.sort()
        return elf, f, secaddr, symtab

    def relocate(self, elf, secaddr, symtab, extern):
        for sec in elf.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            target = sec["sh_info"]
            if target not in secaddr:
                continue
            base = secaddr[target]
            for rel in sec.iter_relocations():
                typ = rel["r_info_type"]
                sym = symtab.get_symbol(rel["r_info_sym"])
                P = base + rel["r_offset"]
                if sym["st_info"]["type"] == "STT_SECTION":
                    S = secaddr.get(sym["st_shndx"], 0)
                elif sym["st_shndx"] == "SHN_UNDEF":
                    S = extern(sym.name, typ)
                else:
                    S = secaddr[sym["st_shndx"]] + sym["st_value"]
                insn = self.rd(P)
                if typ == 2:				# R_ARM_ABS32
                    self.wr(P, S + insn)
                elif typ in (28, 29):			# CALL, JUMP24
                    A = ((insn & 0xffffff) ^ 0x800000) - 0x800000
                    off = (S + (A << 2) - P) >> 2
                    self.wr(P, (insn & 0xff000000) | (off & 0xffffff))
                elif typ in (43, 44):			# MOVW_ABS_NC, MOVT_ABS
                    A = ((insn >> 4) & 0xf000) | (insn & 0xfff)
                    A = (A ^ 0x8000) - 0x8000
                    v = S + A
                    if typ == 44:
                        v >>= 16
                    v &= 0xffff
                    self.wr(P, (insn & 0xfff0f000) | ((v & 0xf000) << 4)
                            | (v & 0xfff))
                elif typ in (0, 42):			# NONE, PREL31 (unwind)
                    pass
                else:
                    raise RuntimeError("relocation type %d" % typ)

    def load_so(self, path):
        """Load a shared object (user space) and apply its dynamic
        relocations; unresolved symbols become stubs or data."""
        f = open(path, "rb")
        elf = ELFFile(f)
        base = self.so_next
        top = 0
        for seg in elf.iter_segments():
            if seg["p_type"] != "PT_LOAD":
                continue
            va = base + seg["p_vaddr"]
            end = va + seg["p_memsz"]
            for page in range(va & ~0xfff, (end + 0xfff) & ~0xfff, 0x1000):
                try:
                    self.uc.mem_map(page, 0x1000)
                except UcError:
                    pass
            self.uc.mem_write(va, seg.data())
            top = max(top, end)
        self.so_next = (top + 0xfffff) & ~0xfffff
        dynsym = elf.get_section_by_name(".dynsym")
        for sym in dynsym.iter_symbols():
            if sym["st_shndx"] != "SHN_UNDEF" and sym.name and \
                    sym["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT"):
                self.syms.setdefault(sym.name, base + sym["st_value"])
                self.addr2sym.append((base + sym["st_value"], sym.name))
        self.addr2sym.sort()
        for sec in elf.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            symtab = elf.get_section(sec["sh_link"])
            for rel in sec.iter_relocations():
                typ = rel["r_info_type"]
                P = base + rel["r_offset"]
                if typ == 23:				# R_ARM_RELATIVE
                    self.wr(P, base + self.rd(P))
                    continue
                sym = symtab.get_symbol(rel["r_info_sym"])
                if sym["st_shndx"] != "SHN_UNDEF":
                    S = base + sym["st_value"]
                elif sym.name in self.syms:
                    S = self.syms[sym.name]
                elif typ == 22 or sym["st_info"]["type"] == "STT_FUNC":
                    S = self.stub(sym.name)
                else:
                    S = self.data(sym.name)
                if typ in (21, 22):			# GLOB_DAT, JUMP_SLOT
                    self.wr(P, S)
                elif typ == 2:				# ABS32
                    self.wr(P, S + self.rd(P))
                else:
                    raise RuntimeError("dynamic relocation type %d" % typ)
        f.close()
        return base

    def ddr_alloc(self, size, align=0x1000):
        a = (self.ddr_next + align - 1) & ~(align - 1)
        self.ddr_next = a + size
        if self.ddr_next > DDR_BASE + DDR_SIZE:
            raise RuntimeError("emulated DDR exhausted")
        return a

    def link(self, paths):
        loaded = [self.load(p) for p in paths]

        def extern(name, typ):
            if name in self.syms:
                return self.syms[name]
            if typ in (28, 29):			# called: function stub
                return self.stub(name)
            return self.data(name)		# referenced: data object
        for elf, f, secaddr, symtab in loaded:
            self.relocate(elf, secaddr, symtab, extern)
            f.close()

    def stub(self, name):
        if name in self.syms:
            return self.syms[name]
        a = self.stub_next
        self.stub_next += 4
        self.stubs[a] = name
        self.syms[name] = a
        return a

    def data(self, name):
        if name in self.syms:
            return self.syms[name]
        a = self.data_next
        self.data_next += 0x400
        self.syms[name] = a
        if name == "kmalloc_caches":		# array of fake cache pointers
            for i in range(32):
                self.wr(a + 4 * i, 0x100 + i)
        return a

    # --- hooks ----------------------------------------------------------
    def _mmio_read(self, uc, access, addr, size, value, user):
        v = self.regs.get(addr & ~3, 0)
        uc.mem_write(addr & ~3, u32(v).to_bytes(4, "little"))
        pc = uc.reg_read(UC_ARM_REG_PC)
        self.log.append(("R", addr, v, self.func_at(pc)))

    def _mmio_write(self, uc, access, addr, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        self.regs[addr & ~3] = u32(value)
        self.log.append(("W", addr, u32(value), self.func_at(pc)))

    def _unmapped(self, uc, access, addr, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        print("unmapped access %#x at pc %#x (%s)" % (addr, pc,
              self.func_at(pc)), file=sys.stderr)
        return False

    def _exit_hook(self, uc, addr, size, user):
        uc.emu_stop()

    def _stub_hook(self, uc, addr, size, user):
        name = self.stubs.get(addr)
        if name is None:
            return
        args = [uc.reg_read(r) for r in R]
        fn = STUBS.get(name)
        if fn is None:
            self.unknown_stubs.add(name)
            ret = 0
        else:
            ret = fn(self, args)
        if self.verbose_calls:
            print("  call %s(%s) = %s" % (name, ", ".join("%#x" % a for a in
                  args), ret), file=sys.stderr)
        if isinstance(ret, Jump):		# tail call into emulated code
            for r, v in zip(R, ret.args):
                uc.reg_write(r, u32(v))
            uc.reg_write(UC_ARM_REG_PC, ret.target)
            return
        if isinstance(ret, tuple):
            uc.reg_write(UC_ARM_REG_R0, u32(ret[0]))
            uc.reg_write(UC_ARM_REG_R1, u32(ret[1]))
        elif ret is not None:
            uc.reg_write(UC_ARM_REG_R0, u32(ret))
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    # --- calling --------------------------------------------------------
    def call(self, name, *args, limit=20000000):
        addr = self.syms[name] if isinstance(name, str) else name
        uc = self.uc
        sp = STACK_TOP - 0x100
        stack_args = list(args[4:])
        sp -= 4 * len(stack_args)
        for i, a in enumerate(stack_args):
            self.wr(sp + 4 * i, a)
        for r, a in zip(R, args[:4]):
            uc.reg_write(r, u32(a))
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, EXIT_ADDR)
        try:
            uc.emu_start(addr, EXIT_ADDR, count=limit)
        except UcError as e:
            pc = uc.reg_read(UC_ARM_REG_PC)
            raise RuntimeError("%s in %s at pc %#x (%s)" % (e, name, pc,
                               self.func_at(pc)))
        return uc.reg_read(UC_ARM_REG_R0)

    def mark(self, text):
        self.log.append(("#", 0, 0, text))

    def dump_log(self, writes_only=True, out=sys.stdout):
        names = sorted(MMIO)
        for kind, addr, val, fn in self.log:
            if kind == "#":
                print("# --- %s" % fn, file=out)
                continue
            if writes_only and kind != "W":
                continue
            blk = next((n for b, s, n in names if b <= addr < b + s), "?")
            base = next((b for b, s, n in names if b <= addr < b + s), 0)
            print("%s %-10s +0x%04x = 0x%08x  %s" % (kind, blk, addr - base,
                  val, fn), file=out)


class Jump:
    """Stub result: continue in emulated code at target with these args."""
    def __init__(self, target, *args):
        self.target = target
        self.args = args


# --- user-space (libc, libvmm) stubs -----------------------------------------
def _open(e, a):
    path = e.cstr(a[0])
    fd = 3 + len(e.fds)
    e.fds[fd] = path
    e.mark("open %s = %d" % (path, fd))
    return fd


IOCTL_HANDLERS = {"/dev/isp": "fh81_isp_ioctl",
                  "/dev/media_process": "media_control"}


def _ioctl(e, a):
    path = e.fds.get(a[0], "?")
    handler = IOCTL_HANDLERS.get(path)
    e.mark("ioctl %s cmd %#x arg %#x" % (path, a[1], a[2]))
    forced = getattr(e, "ioctl_ret", {}).get(a[1])
    if callable(forced):
        return forced(e, a)
    if forced is not None:
        return forced
    if handler is None:
        return 0
    return Jump(e.syms[handler], 0, a[1], a[2])	# (file, cmd, arg)


def _mmap(e, a):
    sp = e.uc.reg_read(UC_ARM_REG_SP)
    fd, off = e.rd(sp), e.rd(sp + 4)
    path = e.fds.get(fd, "?")
    e.mark("mmap %s len %#x off %#x" % (path, a[1], off))
    if path == "/dev/mem":
        return off
    if path == "/dev/isp":
        return 0xe8400000 + off
    return e.alloc(a[1])


PRESENT = ("/dev/isp", "/dev/media_process", "/dev/mem")


def _access(e, a):
    path = e.cstr(a[0])
    ok = path in PRESENT
    e.mark("access %s = %s" % (path, "ok" if ok else "missing"))
    return 0 if ok else 0xffffffff


def _buffer_malloc(e, a):
    # (desc, size, align, name): desc[0] physical, desc[1] virtual address
    addr = e.ddr_alloc(a[1], max(a[2], 0x1000))
    e.wr(a[0], addr)
    e.wr(a[0] + 4, addr)
    e.wr(a[0] + 8, a[1])
    e.mark("buffer_malloc %s size %#x = %#x" % (e.cstr(a[3]), a[1], addr))
    return 0


def _memset(e, a):
    if a[2]:
        e.uc.mem_write(a[0], bytes([a[1] & 0xff]) * a[2])
    return a[0]


def _sensor_cb(e, a):
    """Stub for every callback of the stock SC2336 sensor driver (it lives in
    sdk_app). The 'get vi attr' callback fills a structure of u16 fields:
    +0/+2 sizes, +4/+6 input height/width, +8/+10 crop offsets, +12/+14 ISP
    picture height/width."""
    lr = e.uc.reg_read(UC_ARM_REG_LR)
    if e.func_at(lr) == "isp_core_get_sensor_vi_attr" and a[0]:
        W, H = e.sensor_size
        vals = (W, H, H, W, 0, 0, H, W, 0, 0, 0, 0)
        e.uc.mem_write(a[0], b"".join(v.to_bytes(2, "little") for v in vals))
    return 0


def _sprintf(e, a):
    e.wr(a[0], 0, 1)
    return 0


USER_STUBS = {
    "open": _open, "close": lambda e, a: 0, "ioctl": _ioctl, "mmap": _mmap,
    "munmap": lambda e, a: 0, "access": _access,
    "malloc": lambda e, a: e.alloc(a[0]),
    "calloc": lambda e, a: e.alloc(a[0] * a[1]),
    "free": lambda e, a: 0, "memset": _memset,
    "sprintf": _sprintf, "snprintf": _sprintf,
    "printf": lambda e, a: 0, "puts": lambda e, a: 0,
    "fprintf": lambda e, a: 0, "fopen": lambda e, a: 0,
    "fclose": lambda e, a: 0, "fwrite": lambda e, a: 0,
    "write": lambda e, a: a[2],
    "buffer_malloc_withname": _buffer_malloc,
    "buffer_malloc_withname_cached": _buffer_malloc,
    "FH_SYS_VmmGetPaddr_ByVaddr": lambda e, a: a[0],
    "FH_SYS_VmmFlushCache": lambda e, a: 0,
    "pthread_mutex_init": lambda e, a: 0, "pthread_mutex_lock": lambda e, a: 0,
    "pthread_mutex_unlock": lambda e, a: 0,
    "pthread_mutex_destroy": lambda e, a: 0,
    "usleep": lambda e, a: 0, "raise": lambda e, a: 0,
    "__errno_location": lambda e, a: e.syms.get("__emu_errno", 0),
    "strcmp": lambda e, a: 0 if e.cstr(a[0]) == e.cstr(a[1]) else 1,
    "putchar": lambda e, a: a[0], "perror": lambda e, a: 0,
    "vsprintf": _sprintf, "__assert": lambda e, a: 0,
    "ispTrace_FunctionName": lambda e, a: 0,
    "__emu_sensor_cb": _sensor_cb,
}


# --- kernel API stubs ------------------------------------------------------
def _alloc(e, a):
    return e.alloc(a[0] if a[0] < 0x1000000 else 0x1000)


def _memcpy(e, a):
    if a[2]:
        e.uc.mem_write(a[0], bytes(e.uc.mem_read(a[1], a[2])))
    return a[0]


def _memzero(e, a):
    if a[1]:
        e.uc.mem_write(a[0], b"\0" * a[1])
    return a[0]


def _copy_from_user(e, a):
    _memcpy(e, a)
    return 0


def _uidiv(e, a):
    return a[0] // a[1] if a[1] else 0


def _uidivmod(e, a):
    return (a[0] // a[1], a[0] % a[1]) if a[1] else (0, 0)


def _sdiv(x):
    return x - (1 << 32) if x & 0x80000000 else x


def _idiv(e, a):
    n, d = _sdiv(a[0]), _sdiv(a[1])
    return int(n / d) if d else 0


def _idivmod(e, a):
    n, d = _sdiv(a[0]), _sdiv(a[1])
    if not d:
        return (0, 0)
    q = int(n / d)
    return (q, n - q * d)


def _strlen(e, a):
    return len(e.cstr(a[0], 4096))


def _chipid(e, a):
    if a[0]:
        e.wr(a[0], 0x20102801)
    if a[1]:
        e.wr(a[1], 0)
    return 0


def _pmu_get_reg(e, a):
    return e.regs.get(0xf0000000 + a[0], 0x20003 if a[0] == 0x33c else 0)


_pts = [0]


def _get_pts64(e, a):
    _pts[0] += 33333
    return (_pts[0], 0)


def _printk(e, a):
    if e.verbose_calls:
        print("  printk: " + e.cstr(a[0]).rstrip(), file=sys.stderr)
    return 0


STUBS = {
    "__kmalloc": _alloc, "vmalloc": _alloc, "kmalloc_order": _alloc,
    "kmem_cache_alloc": lambda e, a: e.alloc(0x2000),
    "media_malloc": _alloc, "media_malloc_ex": _alloc,
    "kfree": lambda e, a: 0, "vfree": lambda e, a: 0,
    "memcpy": _memcpy, "__memzero": _memzero,
    "arm_copy_from_user": _copy_from_user,
    "arm_copy_to_user": _copy_from_user,
    "__aeabi_uidiv": _uidiv, "__aeabi_uidivmod": _uidivmod,
    "__aeabi_idiv": _idiv, "__aeabi_idivmod": _idivmod,
    "strlen": _strlen,
    "ioremap": lambda e, a: a[0], "fh_ioremap": lambda e, a: a[0],
    "iounmap": lambda e, a: 0,
    "fh_get_chipid": _chipid, "fh_pmu_get_reg": _pmu_get_reg,
    "fh_get_chip_info": lambda e, a: 0,
    "fh_irq_create_mapping": lambda e, a: a[1],
    "clk_get": lambda e, a: 0x1000, "fh_clk_is_enabled": lambda e, a: 1,
    "fh_clk_get_rate": lambda e, a: 100000000,
    "get_pts64": _get_pts64, "get_pts_rate": lambda e, a: 1000000,
    "printk": _printk,
    "kthread_create_on_node": lambda e, a: e.alloc(0x400),
    "proc_create_data": lambda e, a: e.alloc(0x100),
    "misc_register": lambda e, a: 0,
    "fhk_mod2name": lambda e, a: e.syms.get("__emu_empty", 0),
    "fhk_errid2name": lambda e, a: e.syms.get("__emu_empty", 0),
    "mutex_trylock": lambda e, a: 1,
    "schedule_timeout": lambda e, a: 0,
}
STUBS.update(USER_STUBS)


# --- scenarios ---------------------------------------------------------------
def scenario_init(e):
    """Only the module inits (done for every scenario)."""


def scenario_chipinfo(e):
    """Print the chip info table of media_process (base addresses, IRQs)."""
    p = e.call("get_chip_info")
    for off in range(0, 0x140, 4):
        print("  +%#05x %08x" % (off, e.rd(p + off)), file=sys.stderr)


def scenario_vpss(e):
    """Stock VPSS bring-up as in sdk_app: system init, input, enable, one
    channel as 1920x1080 to DDR."""
    import os
    W = int(os.environ.get("W", 1920))
    H = int(os.environ.get("H", 1080))
    CHN = int(os.environ.get("CHN", 0))
    VOMODE = int(os.environ.get("VOMODE", 0))
    VIW = int(os.environ.get("VIW", 1920))
    VIH = int(os.environ.get("VIH", 1080))
    vi = e.alloc(8)
    e.wr(vi, VIW)
    e.wr(vi + 4, VIH)
    size = e.alloc(8)
    e.wr(size, W)
    e.wr(size + 4, H)
    steps = getattr(e, "vpss_steps", ("sysinit", "channel"))
    calls = []
    if "sysinit" in steps:
        calls.append(("FH_SYS_Init", ()))
    if "channel" in steps:
        calls += [("FH_VPSS_SetViAttr", (vi,)),
                  ("FH_VPSS_Enable", (0,)),
                  ("FH_VPSS_SetChnAttr", (CHN, size)),
                  ("FH_VPSS_SetVOMode", (CHN, VOMODE)),
                  ("FH_VPSS_OpenChn", (CHN,))]
    for name, args in calls:
        e.mark("%s%s" % (name, args))
        ret = e.call(name, *args)
        e.mark("%s returned %#x" % (name, ret))
        print("%s -> %#x" % (name, ret), file=sys.stderr)


def scenario_frames(e):
    """VPSS bring-up, then the stock ISP interrupt for a few frames: the status
    events as seen on the board, and the ones that would complete a frame."""
    import os
    scenario_vpss(e)
    # statistics/info buffers the ISP user library registers via ioctl 0x40786904
    # (copied to fh81_isp +0xac..+0xfc); give each one emulated memory
    isp = e.rd(e.syms["fh81_isp"])
    for off in range(0xac, 0x100, 4):
        e.wr(isp + off, e.alloc(0x10000))
    seq = os.environ.get("EVENTS", "0x20000,0x10000,0x2,0x1,0x20000,0x10000,0x2,0x1")
    for st in seq.split(","):
        st = int(st, 0)
        e.regs[0xe8400004] = st
        e.mark("isp_interrupt status %#x" % st)
        r = e.call("isp_interrupt", 0, 0)
        print("isp_interrupt(%#x) -> %#x" % (st, r), file=sys.stderr)


def scenario_isp(e):
    """Stock ISP user-space bring-up as in sdk_app (libisp/libispcore):
    MemInit, GetBinAddr, SensorRegCb (stub sensor), SensorInit, SetSensorFmt,
    Init, LoadIspParam (sc2336 tuning file), SetCisClk."""
    import os
    moddir = os.environ.get("MODULES", "work/stock")
    e.load_so("%s/libispcore.so" % moddir)
    e.load_so("%s/libisp.so" % moddir)
    W = int(os.environ.get("W", 1920))
    H = int(os.environ.get("H", 1080))
    FMT = int(os.environ.get("FMT", "0"), 0)
    e.sensor_size = (W, H)
    # sensor callback table: every entry a function returning 0
    ret0 = e.stub("__emu_sensor_cb")
    sensor = e.alloc(0x400)
    for i in range(0, 0x400, 4):
        e.wr(sensor + i, ret0)
    a, b = e.alloc(4), e.alloc(4)
    hexfile = "%s/sc2336_mipi_attr.hex" % moddir
    param = e.alloc(0x2000)
    e.uc.mem_write(param, open(hexfile, "rb").read())
    for name, args in (("API_ISP_MemInit", (W, H)),
                       ("API_ISP_GetBinAddr", (a, b)),
                       ("API_ISP_SensorRegCb", (0, sensor)),
                       ("API_ISP_SensorInit", ()),
                       ("API_ISP_SetSensorFmt", (FMT,)),
                       ("API_ISP_Init", ()),
                       ("API_ISP_LoadIspParam", (param,)),
                       ("API_ISP_SetCisClk", (36000000,))):
        if name not in e.syms:
            print("%s: not found" % name, file=sys.stderr)
            continue
        e.mark("%s%s" % (name, args))
        try:
            ret = e.call(name, *args)
        except RuntimeError as err:
            print("%s: %s" % (name, err), file=sys.stderr)
            break
        e.mark("%s returned %#x" % (name, ret))
        print("%s -> %#x" % (name, ret), file=sys.stderr)


def scenario_isprun(e):
    """scenario_isp, then FRAMES (default 3) per-frame cycles as the stock
    ISP thread runs them (RUNFN, default API_ISP_Run). Shows the registers
    the *_ctrl_run loops (blc, ccm, yc, gamma, drc, ...) write."""
    import os
    scenario_isp(e)
    # isp_core_run waits for the frame with ioctl 0x40046906 (no interrupt
    # in the emulator): report the frame as ready
    # handle_no_pic_end: ioctl 0x40046909 returns the picture-end counter,
    # which has to advance from frame to frame
    pic_end = [0]

    def pic_end_count(e, a):
        pic_end[0] += 1
        e.wr(a[2], pic_end[0])
        return 0
    e.ioctl_ret = {0x40046906: 1, 0x40046909: pic_end_count}
    # stat_update gives up while the frame measurement +0x1460 (height <<
    # 16 | width) is 0; +0x1464/+0x1468 are divided by it
    W, H = e.sensor_size
    e.regs[0xe8401460] = H << 16 | W
    e.regs[0xe8401464] = W * H
    e.regs[0xe8401468] = H * 2250
    for i in range(int(os.environ.get("FRAMES", 3))):
        for name in os.environ.get("RUNFN", "API_ISP_Run").split(","):
            e.mark("frame %d %s" % (i, name))
            try:
                ret = e.call(name)
            except RuntimeError as err:
                print("frame %d %s: %s" % (i, name, err), file=sys.stderr)
                return
            print("frame %d %s -> %#x" % (i, name, ret), file=sys.stderr)


def scenario_full(e):
    """Stock order as in sdk_app: FH_SYS_Init, ISP bring-up, VPSS channel."""
    import os
    os.environ.setdefault("VOMODE", "1")
    e.vpss_steps = ("sysinit",)
    scenario_vpss(e)
    scenario_isp(e)
    e.vpss_steps = ("channel",)
    scenario_vpss(e)


def write_replay_abs(e, path):
    """All register writes of the scenario as 'ADDRESS VALUE' lines (absolute
    addresses) for cam-probe. Left out: the timestamp latch PMU+0x22c and the
    scaler coefficient banks of channels 1/2 (bus errors on the board)."""
    lines = ["# Register writes of the stock bring-up, produced by",
             "# scripts/stockemu.py (emulated stock binaries, private vendor data).",
             "# Absolute addresses, in execution order."]
    n = 0
    for kind, addr, val, fn in e.log:
        if kind == "#":
            if (fn.startswith("FH_") or fn.startswith("API_")) \
                    and "returned" not in fn:
                lines.append("# --- " + fn)
            continue
        if kind != "W" or addr == 0xf000022c:
            continue
        if 0xe8402740 <= addr < 0xe8403740:
            continue
        lines.append("0x%08x 0x%08x  # %s" % (addr, val, fn))
        n += 1
    open(path, "w").write("\n".join(lines) + "\n")
    print("replay: %d writes -> %s" % (n, path), file=sys.stderr)


def write_replay(e, path):
    """ISP-block writes of the scenario as 'OFFSET VALUE' lines for cam-probe.
    Scaler coefficients only for channel 0 (+0x1f40..+0x273c): the banks of
    channels 1/2 gave bus errors on the board."""
    lines = ["# VPU register writes of the stock VPSS bring-up, produced by",
             "# scripts/stockemu.py (emulated stock modules, private vendor data).",
             "# Offsets from 0xE8400000, in execution order."]
    n = 0
    for kind, addr, val, fn in e.log:
        if kind == "#":
            if fn.startswith("FH_") and "returned" not in fn:
                lines.append("# --- " + fn)
            continue
        if kind != "W" or not 0xe8400000 <= addr < 0xe8500000:
            continue
        off = addr - 0xe8400000
        if 0x2740 <= off < 0x3740:
            continue
        lines.append("0x%04x 0x%08x  # %s" % (off, val, fn))
        n += 1
    open(path, "w").write("\n".join(lines) + "\n")
    print("replay: %d writes -> %s" % (n, path), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--modules", default="work/stock")
    ap.add_argument("--reads", action="store_true", help="log reads too")
    ap.add_argument("--calls", action="store_true", help="trace stub calls")
    ap.add_argument("scenario")
    a = ap.parse_args()
    e = Emu()
    e.verbose_calls = a.calls
    # load each module separately to keep their init_module apart
    e.inits = {}
    loaded = []
    for mod in ("media_process", "isp"):
        before = dict(e.syms)
        res = e.load("%s/%s.ko" % (a.modules, mod))
        loaded.append(res)
        e.inits[mod] = e.syms["init_module"]
        if "init_module" in before:
            e.syms["init_module"] = before["init_module"]

    def extern(name, typ):
        if name in e.syms:
            return e.syms[name]
        return e.stub(name) if typ in (28, 29) else e.data(name)
    for elf, f, secaddr, symtab in loaded:
        e.relocate(elf, secaddr, symtab, extern)
        f.close()
    e.syms["__emu_empty"] = e.alloc(16)
    e.syms["__emu_errno"] = e.alloc(16)
    e.call("media_chip_base_init")
    e.call(e.inits["media_process"])
    e.call(e.inits["isp"])
    # open the device files once, as the stock application does (vpu_drv_open
    # sets the VPU limits); dummy inode and file structures
    for fop in ("fh_media_open", "fh81_isp_open"):
        r = e.call(fop, e.alloc(0x200), e.alloc(0x200))
        print("%s -> %#x" % (fop, r), file=sys.stderr)
    e.log.clear()
    if a.scenario != "init":
        e.load_so("%s/libdsp.so" % a.modules)

    import os
    # realistic starting values: ISP registers of the running stock firmware
    seed = os.environ.get("SEED", "orig_firmware/derived/isp-stock-runtime.txt")
    if seed and os.path.exists(seed):
        for line in open(seed):
            if line.startswith("#") or not line.strip():
                continue
            off, val = line.split()
            addr = 0xe8400000 + int(off, 16)
            if addr not in REG_DEFAULTS:
                e.regs[addr] = int(val, 16)
    if os.environ.get("REPLAY"):	# write the register writes as a replay list
        e.replay_file = os.environ["REPLAY"]
    if os.environ.get("TRACE"):
        e.trace(os.environ["TRACE"].split(","))
    globals()["scenario_" + a.scenario](e)
    if os.environ.get("DUMPVAR"):		# NAME[:OFFSET:COUNT], pointer var
        for spec in os.environ["DUMPVAR"].split(","):
            name, off, cnt = (spec.split(":") + ["0", "32"])[:3]
            p = e.rd(e.syms[name])
            print("%s -> %#x:" % (name, p), file=sys.stderr)
            for i in range(int(cnt, 0)):
                o = int(off, 0) + 4 * i
                print("  +%#05x %08x" % (o, e.rd(p + o)), file=sys.stderr)
    if getattr(e, "replay_file", None):
        if a.scenario == "full":
            write_replay_abs(e, e.replay_file)
        else:
            write_replay(e, e.replay_file)
    e.dump_log(writes_only=not a.reads)
    if e.unknown_stubs:
        print("stubbed with return 0: " + " ".join(sorted(e.unknown_stubs)),
              file=sys.stderr)


if __name__ == "__main__":
    main()
