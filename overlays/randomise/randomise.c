/* #13: context-sensitive Mutate. The offsets are those stock ui_button_event uses
 * (docs/source-map.md ui_button_event; Shift+Oct pattern transpose walks the same steps). */
#include <firmware.h>

#define FIELD(obj, off, type) (*(type *)((char *)(obj) + (off)))

/* ui_root */
#define UI_SHIFT        0x10    /* object whose cell +0x10 is the Shift state */
#define UI_MUTATE_GATE  0x14    /* cell: Mutate is ignored unless byte 0 is 0 and byte 2 is not 5/6 */
#define UI_MODE_VIEW    0x2c
#define UI_VIEW_38      0x38    /* view whose cell +0x10 every other button clears */
#define UI_ACTIVE_VIEW  0x8c
#define UI_LOCK         0xb0    /* cell: bit 1 blocks most buttons */
#define UI_FORWARD      0x15e   /* byte: forward every button to the view at +0x60 */

/* mode view */
#define MV_KEYBOARDS    0x20    /* per-track keyboard views (Note and Expand) */
#define MV_TRACKS       0x68    /* per-track step objects, Osc 1 and Osc 2 only */
#define MV_TRACK        0x74
#define MV_SUBVIEW      0x75
#define TRACK_STEPS     0x0c    /* cell holding a pointer to the pattern's 16 steps */

/* keyboard view */
#define KV_LAYOUT       0x30
#define LAYOUT_NOTES    0x14    /* pad-to-note table, indexed by keyboard_pad_to_note */
#define EXPAND_PADS     32

#define BUTTON_MUTATE   0x1d    /* Pattern Settings; Mutate with Shift */

enum { SUBVIEW_VELOCITY = 1, SUBVIEW_GATE = 2, SUBVIEW_GLIDE = 3, SUBVIEW_EXPAND = 5 };

struct step {                   /* docs/session-format.md "Pattern" */
    uint8_t note;               /* 0 = empty */
    uint8_t velocity;
    uint8_t gate;               /* micro-steps, 6 per step */
    uint8_t glide;              /* 0xff = none */
    uint8_t patch;
    uint8_t unused[3];
};

static unsigned random_below(unsigned n)
{
    return rng_read() % n;
}

/* The distinct notes on the Expand view's pads: the current scale over its four octaves
 * (two for Chromatic), relative to C; the router adds the root note on playback. */
static unsigned expand_notes(void *keyboard_view, uint8_t *notes)
{
    const uint8_t *table = (const uint8_t *)FIELD(keyboard_view, KV_LAYOUT, void *) + LAYOUT_NOTES;
    unsigned count = 0;
    for (unsigned pad = 0; pad < EXPAND_PADS; pad++) {
        uint8_t note = table[keyboard_pad_to_note(keyboard_view, pad)];
        unsigned i = 0;
        while (i < count && notes[i] != note)
            i++;
        if (note && i == count)
            notes[count++] = note;
    }
    return count;
}

static void randomise(struct step *step, unsigned subview, unsigned max_gate,
                      const uint8_t *notes, unsigned note_count)
{
    switch (subview) {
    case SUBVIEW_EXPAND:
        step->note = notes[random_below(note_count)];
        break;
    case SUBVIEW_VELOCITY: {
        /* The velocity view's pad levels: (pad + 1) * 8, 127 for the top pad. */
        unsigned level = random_below(16) + 1;
        step->velocity = level == 16 ? 127 : level * 8;
        break;
    }
    case SUBVIEW_GATE:
        step->gate = random_below(max_gate) + 1;
        break;
    case SUBVIEW_GLIDE:
        step->glide = random_below(2) ? pad_levels[random_below(15) + 1] : 0xff;
        break;
    }
}

/* Returns 1 when it handled the button, 0 to leave it to the stock handler. */
static int mutate_field(void *ui)
{
    if (*(uint32_t *)cell_read(FIELD(ui, UI_LOCK, void *)) & 2)
        return 0;
    if (FIELD(ui, UI_FORWARD, uint8_t))
        return 0;
    if (!*(uint8_t *)cell_read(FIELD(FIELD(ui, UI_SHIFT, void *), 0x10, void *)))
        return 0;
    void *mode_view = FIELD(ui, UI_MODE_VIEW, void *);
    if (FIELD(ui, UI_ACTIVE_VIEW, void *) != mode_view)
        return 0;
    unsigned track = FIELD(mode_view, MV_TRACK, uint8_t);
    unsigned subview = FIELD(mode_view, MV_SUBVIEW, uint8_t);
    if (track > 1 || subview < SUBVIEW_VELOCITY ||
        (subview > SUBVIEW_GLIDE && subview != SUBVIEW_EXPAND))
        return 0;
    /* Byte 2 is the subview of the packed view id: stock ignores Mutate in the Expand
     * view (5), which hides the pattern; we handle it. */
    const uint8_t *gate = cell_read(FIELD(ui, UI_MUTATE_GATE, void *));
    if (gate[0] || (gate[2] == 5 && subview != SUBVIEW_EXPAND) || gate[2] == 6)
        return 0;
    uint8_t pitches[EXPAND_PADS];
    unsigned pitch_count = 0;
    if (subview == SUBVIEW_EXPAND) {
        pitch_count = expand_notes(FIELD(mode_view, MV_KEYBOARDS + 4 * track, void *), pitches);
        if (!pitch_count)
            return 0;
    }

    /* What stock does for every button but 0x14, 0x15 and 0x17 before dispatching. */
    void *view = FIELD(ui, UI_VIEW_38, void *);
    *(uint8_t *)cell_write_field0(FIELD(view, 0x10, void *)) = 0;
    cell_commit(FIELD(view, 0x10, void *));

    void *steps_cell = FIELD(FIELD(mode_view, MV_TRACKS + 4 * track, void *), TRACK_STEPS, void *);
    struct step *steps = cell_value(steps_cell);
    /* Gates go from 1/6 step up to the longest gate already on a note, at least one
     * step: set one long step first to allow longer gates. */
    unsigned max_gate = 6, notes = 0;
    for (unsigned i = 0; i < 16; i++)
        if (steps[i].note) {
            notes++;
            if (steps[i].gate > max_gate)
                max_gate = steps[i].gate;
        }
    if (max_gate > 96)
        max_gate = 96;
    for (unsigned i = 0; i < 16; i++)
        if (steps[i].note)
            randomise(&steps[i], subview, max_gate, pitches, pitch_count);
    /* One random note keeps the longest gate, so the range doesn't shrink press by press. */
    if (subview == SUBVIEW_GATE && notes) {
        unsigned keep = random_below(notes);
        for (unsigned i = 0; i < 16; i++)
            if (steps[i].note && keep-- == 0) {
                steps[i].gate = max_gate;
                break;
            }
    }
    cell_sync(steps_cell);
    cell_commit(steps_cell);
    return 1;
}

void ui_button_event(void *ui, unsigned button)
{
    if (button == BUTTON_MUTATE && mutate_field(ui))
        return;
    stock_ui_button_event(ui, button);
}
