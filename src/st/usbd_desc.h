/* The application's descriptor interface, which usbd_req.c includes. Ours: the firmware's
 * descriptors are generated code (usbd_desc in src/files.tsv). */
#ifndef USBD_DESC_H
#define USBD_DESC_H

#include "usbd_req.h"

#define USB_SIZ_DEVICE_DESC     18
#define USB_SIZ_STRING_LANGID   4

extern USBD_DEVICE USR_desc;

#endif
