/* LED map test (overlay.toml). leds_update turns the per-channel LED levels that
 * leds_render_rgb leaves in the output buffer into the SPI frame, so overwriting that
 * buffer just before it decides what lights up. */
#include <firmware.h>

#define LED_LEVELS      ((volatile uint8_t *)0x200021f8) /* 0x200005e0 +8: output buffer */
#define LED_CHANNELS    352     /* 4 rows of 11 columns of 8 bits (0x200004a4 +0x68/+0x6a) */
#define TEST_MAX        8

#define MS_PREFIX_LEN   6       /* F0 00 20 29 01 61 */
#define CMD_LEDTEST     0x7d    /* byte 6; unused by the stock dispatcher */

/* CCM (docs/hardware.md "Free RAM"): not cleared at boot, so it carries a magic word. */
#define STATE_MAGIC     0x5453454cu /* "LEST" */
struct state {
    uint32_t magic;
    uint8_t count;
    uint16_t channel[TEST_MAX];
};
#define STATE (*(volatile struct state *)0x10000000)

static const uint8_t ms_prefix[MS_PREFIX_LEN] = {0xf0, 0x00, 0x20, 0x29, 0x01, 0x61};

void stock_sysex_dispatch(unsigned port, const uint8_t *msg, unsigned len);
void stock_leds_update(void);

void sysex_dispatch(unsigned port, const uint8_t *msg, unsigned len)
{
    unsigned i, n;

    for (i = 0; i < MS_PREFIX_LEN && i < len; i++)
        if (msg[i] != ms_prefix[i])
            break;
    if (i < MS_PREFIX_LEN || len < 9 || msg[6] != CMD_LEDTEST) {
        stock_sysex_dispatch(port, msg, len);
        return;
    }
    n = msg[7];
    if (n > TEST_MAX || len < 9 + 2 * n) {
        return;
    }
    for (i = 0; i < n; i++)
        STATE.channel[i] = (uint16_t)(msg[8 + 2 * i] << 7 | msg[9 + 2 * i]);
    STATE.count = (uint8_t)n;
    STATE.magic = n ? STATE_MAGIC : 0;
}

void leds_update(void)
{
    unsigned i;

    if (STATE.magic == STATE_MAGIC && STATE.count <= TEST_MAX) {
        for (i = 0; i < LED_CHANNELS; i++)
            LED_LEVELS[i] = 0;
        for (i = 0; i < STATE.count; i++)
            if (STATE.channel[i] < LED_CHANNELS)
                LED_LEVELS[STATE.channel[i]] = 0xff;
    }
    stock_leds_update();
}
