/* USB device library configuration for the firmware build (tools/fwsrc.py,
 * src/libraries.tsv). Ours, not ST's template. */
#ifndef USBD_CONF_H
#define USBD_CONF_H

#include "usb_conf.h"

#define USBD_CFG_MAX_NUM      1
#define USBD_ITF_MAX_NUM      1
/* Not read back from the image yet: no linked function uses it. */
#define USB_MAX_STR_DESC_SIZ  255

#endif
