#include "uart.h"

/*
 * FH8626V200: SPI NOR -> DDR boot loader
 *
 * Copies an image from SPI NOR flash into DDR and jumps to it.
 * No flash erase/write is performed.
 *
 *   SPI controller : DesignWare SSI @ 0xF0500000 (+ Fullhan wrapper regs)
 *   Chip select    : DW_SER bit 0 *and* GPIO0 (0xF0300000) bit 6, active low
 *   Flash          : Puya P25Q64 family, JEDEC ID 85 60 17
 *   Image          : flash 0x00008000 -> DDR 0xA0000000
 */

/* ---------------------------------------------------------------- */
/* Hardware                                                         */
/* ---------------------------------------------------------------- */

#define SPI_BASE        0xF0500000u
#define AUX_GPIO        0xF0300000u

#define REG(off)        (*(volatile unsigned int *)(SPI_BASE + (off)))
#define GPIO0           (*(volatile unsigned int *)(AUX_GPIO))

/* DesignWare SSI registers */
#define DW_CTRLR0       0x00
#define DW_CTRLR1       0x04
#define DW_SSIENR       0x08
#define DW_SER          0x10
#define DW_BAUDR        0x14
#define DW_TXFLTR       0x18
#define DW_RXFLTR       0x1C
#define DW_SR           0x28
#define DW_DR           0x60

/* Fullhan wrapper registers */
#define FH_CCFGR        0xF4
#define FH_OPCR         0xF8

/* Status register bits */
#define SR_BUSY         (1u << 0)
#define SR_TFNF         (1u << 1)
#define SR_RFNE         (1u << 3)

/* Chip select */
#define CS0_MASK        1u
#define AUX_GPIO_BIT    (1u << 6)

/* ---------------------------------------------------------------- */
/* Image / flash configuration                                      */
/* ---------------------------------------------------------------- */

#define FLASH_IMAGE_BASE    0x00008000u
#define IMAGE_SIZE          0x00060000u     /* 384 KiB */
#define DDR_IMAGE_BASE      0xA0000000u
#define READ_CHUNK_SIZE     0x00001000u     /* 4 KiB, CS released between chunks */

#define JEDEC_MANUFACTURER  0x85u
#define JEDEC_MEMORY_TYPE   0x60u
#define JEDEC_CAPACITY      0x17u

#define SPI_TIMEOUT         1000000u

/* ---------------------------------------------------------------- */
/* Exception handler (called from vectors.S)                        */
/* ---------------------------------------------------------------- */

void report_exception(char vector_id,
                      unsigned int lr,
                      unsigned int fault_status,
                      unsigned int fault_addr)
{
    uart_puts("\nEXCEPTION ");
    uart_putc(vector_id);
    uart_puts(" LR=");
    uart_puthex(lr);
    uart_puts(" FSR=");
    uart_puthex(fault_status);
    uart_puts(" FAR=");
    uart_puthex(fault_addr);
    uart_puts("\n");
}

/* ---------------------------------------------------------------- */
/* Wrapper initialisation (from the vendor first-stage loader)      */
/* ---------------------------------------------------------------- */

static void wrapper_init(void)
{
    unsigned int v;

    REG(FH_CCFGR) &= ~0x00000800u;              /* XIP off */
    REG(FH_OPCR)  |=  0x0FF00000u;
    REG(FH_OPCR)  &= ~0x00010000u;              /* DPI off */
    REG(FH_OPCR)  &= ~0x00020000u;              /* QPI off */

    v = REG(FH_CCFGR);
    v = (v & ~0x0000778Fu) | 0x00002000u;       /* standard 1-wire read */
    REG(FH_CCFGR) = v;
}

/* ---------------------------------------------------------------- */
/* Chip select                                                      */
/* ---------------------------------------------------------------- */

static void cs_aux_assert(void)
{
    GPIO0 &= ~AUX_GPIO_BIT;
}

static void cs_aux_deassert(void)
{
    GPIO0 |= AUX_GPIO_BIT;
}

/* ---------------------------------------------------------------- */
/* SPI                                                              */
/* ---------------------------------------------------------------- */

static void spi_init(void)
{
    REG(DW_SSIENR) = 0;
    REG(DW_CTRLR0) = 0x00000007u;               /* 8-bit frames */
    REG(DW_CTRLR1) = 0;
    REG(DW_BAUDR)  = 4;
    REG(DW_TXFLTR) = 0;
    REG(DW_RXFLTR) = 0;
    REG(DW_SER)    = 0;

    while (REG(DW_SR) & SR_RFNE)                /* drain stale RX data */
        (void)REG(DW_DR);

    REG(DW_SSIENR) = 1;
}

static int wait_idle(void)
{
    unsigned int timeout = SPI_TIMEOUT;

    while (REG(DW_SR) & SR_BUSY)
        if (--timeout == 0)
            return 0;
    return 1;
}

static void spi_flush_rx(void)
{
    while (REG(DW_SR) & SR_RFNE)
        (void)REG(DW_DR);
}

/* Transmit one byte, then collect the byte clocked in at the same time. */
static int spi_xfer(unsigned int tx, unsigned int *rx)
{
    unsigned int timeout;

    timeout = SPI_TIMEOUT;
    while (!(REG(DW_SR) & SR_TFNF))
        if (--timeout == 0)
            return 0;

    REG(DW_DR) = tx & 0xFFu;

    timeout = SPI_TIMEOUT;
    while (!(REG(DW_SR) & SR_RFNE))
        if (--timeout == 0)
            return 0;

    *rx = REG(DW_DR) & 0xFFu;
    return 1;
}

static int spi_cs_begin(void)
{
    if (!wait_idle())
        return 0;

    spi_flush_rx();
    cs_aux_assert();                    /* aux GPIO low, then SER bit */
    REG(DW_SER) |= CS0_MASK;
    return 1;
}

static void spi_cs_end(void)
{
    REG(DW_SER) &= ~CS0_MASK;           /* SER bit, then aux GPIO high */
    cs_aux_deassert();
    spi_flush_rx();
}

/* ---------------------------------------------------------------- */
/* Flash                                                            */
/* ---------------------------------------------------------------- */

static int flash_detect(void)
{
    unsigned int dummy, manufacturer, memory_type, capacity;
    int ok;

    if (!spi_cs_begin())
        return 0;

    ok = spi_xfer(0x9F, &dummy) &&              /* JEDEC Read ID */
         spi_xfer(0x00, &manufacturer) &&
         spi_xfer(0x00, &memory_type) &&
         spi_xfer(0x00, &capacity) &&
         wait_idle();

    spi_cs_end();

    if (!ok) {
        uart_puts("ERROR: flash JEDEC read failed\n");
        return 0;
    }

    if (manufacturer != JEDEC_MANUFACTURER ||
        memory_type  != JEDEC_MEMORY_TYPE  ||
        capacity     != JEDEC_CAPACITY) {
        uart_puts("ERROR: unexpected flash ID ");
        uart_puthex(manufacturer);
        uart_puts(" ");
        uart_puthex(memory_type);
        uart_puts(" ");
        uart_puthex(capacity);
        uart_puts("\n");
        return 0;
    }

    return 1;
}

/* Standard 0x03 READ with a 24-bit address; CS stays low for the chunk. */
static int flash_read_chunk(unsigned int flash_address,
                            volatile unsigned char *destination,
                            unsigned int length)
{
    unsigned int i = 0;
    unsigned int value;
    int ok;

    if (!spi_cs_begin())
        return 0;

    ok = spi_xfer(0x03, &value) &&
         spi_xfer((flash_address >> 16) & 0xFFu, &value) &&
         spi_xfer((flash_address >>  8) & 0xFFu, &value) &&
         spi_xfer( flash_address        & 0xFFu, &value);

    for (i = 0; ok && i < length; i++) {
        if (!spi_xfer(0x00, &value)) {
            ok = 0;
            break;
        }
        destination[i] = (unsigned char)value;
    }

    if (ok)
        ok = wait_idle();

    spi_cs_end();

    if (!ok) {
        uart_puts("ERROR: flash read failed at ");
        uart_puthex(flash_address);
        uart_puts(" +");
        uart_puthex(i);
        uart_puts("\n");
    }

    return ok;
}

static int load_image(void)
{
    unsigned int flash_address = FLASH_IMAGE_BASE;
    unsigned int ddr_address   = DDR_IMAGE_BASE;
    unsigned int remaining     = IMAGE_SIZE;
    unsigned int chunk;

    while (remaining != 0) {
        chunk = (remaining > READ_CHUNK_SIZE) ? READ_CHUNK_SIZE : remaining;

        if (!flash_read_chunk(flash_address,
                              (volatile unsigned char *)ddr_address,
                              chunk))
            return 0;

        flash_address += chunk;
        ddr_address   += chunk;
        remaining     -= chunk;
    }

    return 1;
}

/* ---------------------------------------------------------------- */
/* Main                                                             */
/* ---------------------------------------------------------------- */

static void halt(void)
{
    for (;;)
        ;
}

int main(void)
{
    void (*entry)(void) = (void (*)(void))DDR_IMAGE_BASE;

    uart_puts("\nFH8626 SPI NOR -> DDR loader\n");

    wrapper_init();
    spi_init();
    REG(DW_SER) = 0;
    cs_aux_deassert();

    if (!flash_detect())
        halt();

    uart_puts("Copying image: flash ");
    uart_puthex(FLASH_IMAGE_BASE);
    uart_puts(" -> DDR ");
    uart_puthex(DDR_IMAGE_BASE);
    uart_puts(" (");
    uart_puthex(IMAGE_SIZE);
    uart_puts(" bytes)\n");

    if (!load_image()) {
        uart_puts("ERROR: image load failed\n");
        halt();
    }

    /* Leave the SPI controller quiet before handing over. */
    REG(DW_SER) = 0;
    cs_aux_deassert();
    wait_idle();

    uart_puts("Executing image at ");
    uart_puthex(DDR_IMAGE_BASE);
    uart_puts("\n");

    entry();

    uart_puts("ERROR: image returned\n");
    halt();

    return 0;
}

