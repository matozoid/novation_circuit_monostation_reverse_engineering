/* USB OTG driver configuration for the firmware build (tools/fwsrc.py, src/libraries.tsv).
 * Ours, not ST's template. The firmware runs the full-speed core in device mode
 * (USE_USB_OTG_FS and USE_EMBEDDED_PHY come from libraries.tsv `define` lines). */
#ifndef USB_CONF_H
#define USB_CONF_H

#include "stm32f4xx.h"

#define USB_OTG_FS_CORE
#define USE_DEVICE_MODE

/* FIFO sizes in words. Not read back from the image yet: no linked function uses them. */
#define RX_FIFO_FS_SIZE   128
#define TX0_FIFO_FS_SIZE   32
#define TX1_FIFO_FS_SIZE  128
#define TX2_FIFO_FS_SIZE   32
#define TX3_FIFO_FS_SIZE    0

/* no VBUS_SENSING_ENABLED: the stock USBD_DCD_INT_cb table (0x0802B67C in the .data load
 * image) has 9 entries, without DevConnected/DevDisconnected */

#define __ALIGN_BEGIN
#define __ALIGN_END
#define __packed __attribute__((__packed__))

#endif
