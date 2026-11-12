#include "uart.h"

/*
 * Confirmed directly from this board's vendor U-Boot code (real u-boot
 * stage, running from DRAM) -- NOT guessed:
 *   base       0xf0700000
 *   TX (THR)   base + 0x00
 *   status     base + 0x7c, bit 0x2 = TX FIFO not full
 * That bit position/offset combination matches a Synopsys DesignWare
 * APB UART (dw-apb-uart) USR register, which is a strong hint for the
 * rest of the register map (LCR/DLL/DLH offsets below), but the baud
 * divisor write itself was NOT found in either stage we've traced --
 * so UART_TRY_BAUD_INIT is OFF by default. Without it, this code
 * assumes something upstream (boot ROM / hardware default) already
 * leaves the UART at a usable rate, which is what the confirmed putc
 * code we found also assumes (it never touches LCR/DLL either).
 */
#define UART_BASE   0xf0700000u
#define UART_THR    (UART_BASE + 0x00)
#define UART_LCR    (UART_BASE + 0x0c)  /* UNCONFIRMED offset -- standard 16550/DW convention */
#define UART_DLL    (UART_BASE + 0x00)  /* UNCONFIRMED -- only valid when LCR.DLAB=1 */
#define UART_DLH    (UART_BASE + 0x04)  /* UNCONFIRMED -- only valid when LCR.DLAB=1 */
#define UART_USR    (UART_BASE + 0x7c)  /* CONFIRMED */
#define UART_USR_TFNF 0x2u              /* CONFIRMED bit */

/* Set to 1 only once you have a real UART_CLK figure (e.g. measured via
 * scope, or found in the still-untraced clock-tree init) and want to
 * try programming the divisor explicitly. Left off by default because
 * a wrong divisor gives garbled output that looks identical to "wrong
 * pinmux" or "wrong base address" -- don't add unknowns on top of
 * unknowns during first bring-up. */
#define UART_TRY_BAUD_INIT 0

static inline void mmio_write32(unsigned int addr, unsigned int val)
{
    *(volatile unsigned int *)addr = val;
}

static inline unsigned int mmio_read32(unsigned int addr)
{
    return *(volatile unsigned int *)addr;
}

void uart_init(void)
{
#if UART_TRY_BAUD_INIT
    /* Speculative, standard 16550-style divisor programming.
     * divisor = UART_CLK / (16 * baud). Fill in a real UART_CLK
     * before enabling this. */
    #define UART_CLK   24000000u   /* PLACEHOLDER -- not confirmed */
    #define UART_BAUD  115200u
    unsigned int divisor = UART_CLK / (16 * UART_BAUD);

    mmio_write32(UART_LCR, 0x80);           /* DLAB = 1 */
    mmio_write32(UART_DLL, divisor & 0xff);
    mmio_write32(UART_DLH, (divisor >> 8) & 0xff);
    mmio_write32(UART_LCR, 0x03);           /* 8N1, DLAB = 0 */
#else
    (void)0; /* nothing to do -- rely on inherited config, see header comment */
#endif
}

void uart_putc(char c)
{
    while ((mmio_read32(UART_USR) & UART_USR_TFNF) == 0)
        ;
    mmio_write32(UART_THR, (unsigned int)(unsigned char)c);
}

void uart_puts(const char *s)
{
    while (*s) {
        if (*s == '\n')
            uart_putc('\r');
        uart_putc(*s++);
    }
}

void uart_puthex(unsigned int v)
{
    static const char digits[] = "0123456789abcdef";
    int i;
    uart_putc('0');
    uart_putc('x');
    for (i = 28; i >= 0; i -= 4)
        uart_putc(digits[(v >> i) & 0xf]);
}
