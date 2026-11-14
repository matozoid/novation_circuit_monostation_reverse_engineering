/* StdPeriph configuration for the firmware build (tools/fwsrc.py, src/libraries.tsv).
 * stm32f4xx.h includes this with USE_STDPERIPH_DRIVER. Ours, not ST's template. */
#ifndef STM32F4XX_CONF_H
#define STM32F4XX_CONF_H

#include "stm32f4xx_dma.h"
#include "stm32f4xx_gpio.h"
#include "stm32f4xx_rcc.h"
#include "stm32f4xx_tim.h"
#include "misc.h"

/* no parameter checks: the stock drivers have none */
#define assert_param(expr) ((void)0)

#endif
