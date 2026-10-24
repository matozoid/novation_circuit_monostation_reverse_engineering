#!/usr/bin/env python3
"""Static subsystem map of the Mono Station image: active interrupt vectors,
peripheral-register literals, and text-like strings. Read-only, stdlib only.

Usage: python3 tools/fwmap.py firmware/monostation-firmware-800.bin [xref_addr]
The optional second arg lists literal-pool cross-references to that address.
Feeds docs/firmware-map.md."""
import struct, sys, re, collections
from pathlib import Path

BASE = 0x0800C000
img = Path(sys.argv[1]).read_bytes()
end = BASE + len(img)

def w32(off):  # off = file offset
    return struct.unpack_from("<I", img, off)[0]

# ---- vector table ----
EXC = {0:"SP",1:"Reset",2:"NMI",3:"HardFault",4:"MemManage",5:"BusFault",
       6:"UsageFault",11:"SVCall",12:"DebugMon",14:"PendSV",15:"SysTick"}
IRQ = ["WWDG","PVD","TAMP_STAMP","RTC_WKUP","FLASH","RCC","EXTI0","EXTI1",
"EXTI2","EXTI3","EXTI4","DMA1_Str0","DMA1_Str1","DMA1_Str2","DMA1_Str3",
"DMA1_Str4","DMA1_Str5","DMA1_Str6","ADC","CAN1_TX","CAN1_RX0","CAN1_RX1",
"CAN1_SCE","EXTI9_5","TIM1_BRK_TIM9","TIM1_UP_TIM10","TIM1_TRG_COM_TIM11",
"TIM1_CC","TIM2","TIM3","TIM4","I2C1_EV","I2C1_ER","I2C2_EV","I2C2_ER",
"SPI1","SPI2","USART1","USART2","USART3","EXTI15_10","RTC_Alarm","OTG_FS_WKUP",
"TIM8_BRK_TIM12","TIM8_UP_TIM13","TIM8_TRG_COM_TIM14","TIM8_CC","DMA1_Str7",
"FSMC","SDIO","TIM5","SPI3","UART4","UART5","TIM6_DAC","TIM7","DMA2_Str0",
"DMA2_Str1","DMA2_Str2","DMA2_Str3","DMA2_Str4","ETH","ETH_WKUP","CAN2_TX",
"CAN2_RX0","CAN2_RX1","CAN2_SCE","OTG_FS","DMA2_Str5","DMA2_Str6","DMA2_Str7",
"USART6","I2C3_EV","I2C3_ER","OTG_HS_EP1_OUT","OTG_HS_EP1_IN","OTG_HS_WKUP",
"OTG_HS","DCMI","CRYP","HASH_RNG","FPU"]

def vecname(i):
    if i in EXC: return EXC[i]
    if i >= 16 and i-16 < len(IRQ): return "IRQ "+IRQ[i-16]
    return "Reserved" if i < 16 else "IRQ%d" % (i-16)

# find default handler = the most common handler value among reserved-looking slots
nvec = 16 + len(IRQ)  # 98
handlers = [w32(4*i) for i in range(nvec)]
cnt = collections.Counter(h for h in handlers[2:] if BASE <= (h & ~1) < end)
default = cnt.most_common(1)[0][0]
print("== Vector table (base %#x) ==" % BASE)
print("Initial SP %#010x" % handlers[0])
print("Default handler %#010x (used by %d slots)\n" % (default, cnt[default]))
print("Active (non-default) vectors:")
for i,h in enumerate(handlers):
    if i == 0: continue
    if h == default: continue
    if not (BASE <= (h & ~1) < end):
        tag = "  <not code>" if h else "  <null>"
    else:
        tag = ""
    print("  [%2d] %-22s -> %#010x%s" % (i, vecname(i), h, tag))

# ---- peripheral literal xrefs ----
PERIPH = {
 0x40007000:"PWR",0x40007400:"DAC",0x40003800:"SPI2/I2S2",0x40003C00:"SPI3/I2S3",
 0x40013000:"SPI1",0x40013400:"SPI4",0x40011000:"USART1",0x40011400:"USART6",
 0x40004400:"USART2",0x40004800:"USART3",0x40023800:"RCC",0x40023C00:"FLASH_IF",
 0x40026000:"DMA1",0x40026400:"DMA2",0x40020000:"GPIOA",0x40020400:"GPIOB",
 0x40020800:"GPIOC",0x40020C00:"GPIOD",0x40021000:"GPIOE",0x40012000:"ADC1",
 0x40013800:"SYSCFG",0x40013C00:"EXTI",0x40000000:"TIM2",0x40000400:"TIM3",
 0x40000800:"TIM4",0x40000C00:"TIM5",0x40001000:"TIM6",0x40001400:"TIM7",
 0x40010000:"TIM1",0x40010400:"TIM8",0x40014000:"TIM9",0x40014400:"TIM10",
 0x40014800:"TIM11",0x40012C00:"TIM13",0x40040000:"OTG_HS",0x50000000:"OTG_FS",
 0x40024000:"BKPSRAM",0x1FFF7A10:"UID",0x1FFF7A22:"FLASHSIZE",0xE000E000:"SCS",
 0x40012C00:"TIM13",
}
# scan every 32-bit aligned word for a value that equals a known base OR falls
# inside a peripheral register block (base .. base+0x400)
hits = collections.defaultdict(list)
for off in range(0, len(img)-3, 4):
    v = w32(off)
    for base,name in PERIPH.items():
        if base <= v < base+0x400:
            hits[name].append((BASE+off, v))
            break
print("\n== Peripheral register literals (word-aligned constants) ==")
for name in sorted(hits, key=lambda n:-len(hits[n])):
    locs = hits[name]
    sample = " ".join("%#x@%#x"%(v,a) for a,v in locs[:3])
    print("  %-10s %3d refs  e.g. %s" % (name, len(locs), sample))

# ---- strings ----
# The image is dense code, so raw ASCII runs are mostly instruction bytes that
# happen to be printable. Keep runs that look like real text: >=6 chars, mostly
# letters/spaces, with a vowel and a lowercase letter.
print("\n== Text-like strings (len>=6) ==")
seen = set()
for m in re.finditer(rb"[\x20-\x7e]{6,}", img):
    s = m.group().decode()
    if s in seen:
        continue
    seen.add(s)
    letters = sum(c.isalpha() or c.isspace() for c in s)
    if letters / len(s) > 0.75 and re.search(r"[aeiouAEIOU]", s) and re.search(r"[a-z]", s):
        print("  %#010x  %s" % (BASE + m.start(), s.rstrip()))


def xref(addr):
    """File addresses of every word-aligned literal equal to `addr`."""
    return [BASE + off for off in range(0, len(img) - 3, 4)
            if struct.unpack_from("<I", img, off)[0] == addr]


# ---- optional: literal-pool xrefs to an address given as argv[2] ----
if len(sys.argv) > 2:
    target = int(sys.argv[2], 0)
    xs = xref(target)
    print("\n== Literal xrefs to %#010x ==" % target)
    print("  " + (" ".join("%#x" % x for x in xs) or "(none)"))
