"""Circuit Mono Station firmware .syx container (see docs/firmware-container.md)."""

NOVATION_HEADER = bytes([0xF0, 0x00, 0x20, 0x29, 0x00])

CMD_START = 0x71
CMD_DATA = 0x72
CMD_DATA_FIRST = 0x73  # image offset 0, sent last
CMD_TRAILER = 0x76

BLOCK_BYTES = 32
BLOCK_SEPTETS = 37  # 259 bits: 256 payload + 3 look-ahead bits
SPARE_BITS = BLOCK_SEPTETS * 7 - BLOCK_BYTES * 8

LOAD_ADDRESS = 0x0800C000


def split_messages(data):
    """Split a .syx byte string into a list of complete F0..F7 messages."""
    messages = []
    pos = 0
    while (start := data.find(0xF0, pos)) >= 0:
        end = data.find(0xF7, start)
        if end < 0:
            raise ValueError(f"unterminated SysEx message at offset {start}")
        messages.append(data[start:end + 1])
        pos = end + 1
    return messages


def command(message):
    if not message.startswith(NOVATION_HEADER):
        raise ValueError(f"not a Novation firmware message: {message[:8].hex(' ')}")
    return message[len(NOVATION_HEADER)]


def decode_block(septets):
    """Unpack 37 septets (MSB-first bitstream) into 32 bytes plus 3 spare bits."""
    if len(septets) != BLOCK_SEPTETS:
        raise ValueError(f"expected {BLOCK_SEPTETS} septets, got {len(septets)}")
    value = 0
    for septet in septets:
        if septet > 0x7F:
            raise ValueError("SysEx data byte has high bit set")
        value = (value << 7) | septet
    return (value >> SPARE_BITS).to_bytes(BLOCK_BYTES, "big"), value & ((1 << SPARE_BITS) - 1)


def encode_block(payload, spare):
    """Pack 32 bytes plus 3 spare bits into 37 septets (inverse of decode_block)."""
    if len(payload) != BLOCK_BYTES:
        raise ValueError(f"expected {BLOCK_BYTES} bytes, got {len(payload)}")
    value = (int.from_bytes(payload, "big") << SPARE_BITS) | spare
    return bytes((value >> (7 * i)) & 0x7F for i in reversed(range(BLOCK_SEPTETS)))


def data_blocks(messages):
    """Return (command, payload, spare) for each data message, in transmission order."""
    blocks = []
    for message in messages:
        cmd = command(message)
        if cmd in (CMD_DATA, CMD_DATA_FIRST):
            payload, spare = decode_block(message[len(NOVATION_HEADER) + 1:-1])
            blocks.append((cmd, payload, spare))
    return blocks


def decode_image(data):
    """Decode a firmware .syx into the raw flash image that loads at LOAD_ADDRESS."""
    blocks = data_blocks(split_messages(data))
    first = [payload for cmd, payload, _ in blocks if cmd == CMD_DATA_FIRST]
    if len(first) != 1:
        raise ValueError(f"expected one 0x{CMD_DATA_FIRST:02x} block, found {len(first)}")
    rest = [payload for cmd, payload, _ in blocks if cmd == CMD_DATA]
    return first[0] + b"".join(rest)
