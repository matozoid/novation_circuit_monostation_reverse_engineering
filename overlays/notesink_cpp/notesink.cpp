// NoteSink in C++: forwards notes to its target and stamps them with its own id
// (the track for a player's sink, 8-16 for the merger outputs; docs/class-diagram.md).
// Stock objects keep their stock vtable (0x08027980); its two slots now hold the
// extern "C" functions below, which call the class methods non-virtually.
#include "firmware.h"

// The note interface every note-path object implements (vtable slots 0 and 1).
struct INoteSink {
    virtual void note_on(unsigned note, unsigned velocity, unsigned id) = 0;
    virtual void note_off(unsigned note, unsigned id) = 0;
};

// 16 bytes: vptr, an unknown word, the target sink, the id byte.
struct NoteSink final : INoteSink {
    uint32_t unknown_4;
    INoteSink *target;
    uint8_t id;

    // Defined in the class (inline), so g++ emits no vtable of its own: the objects are
    // built by the stock constructor.
    void note_on(unsigned note, unsigned velocity, unsigned) override
    {
        target->note_on(note, velocity, id);
    }
    void note_off(unsigned note, unsigned) override
    {
        target->note_off(note, id);
    }
};

// offsetof on a class with a vptr is conditionally supported; g++ gives the real offsets.
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Winvalid-offsetof"
static_assert(offsetof(NoteSink, target) == 8 && offsetof(NoteSink, id) == 12,
              "NoteSink layout must match the stock object");
#pragma GCC diagnostic pop

extern "C" void note_sink_note_on(NoteSink *self, unsigned note, unsigned velocity, unsigned id)
{
    self->NoteSink::note_on(note, velocity, id);
}

extern "C" void note_sink_note_off(NoteSink *self, unsigned note, unsigned id)
{
    self->NoteSink::note_off(note, id);
}
