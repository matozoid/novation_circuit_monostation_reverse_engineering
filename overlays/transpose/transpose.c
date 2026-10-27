/* #21: live transpose of the Osc 1 and Osc 2 sequences by scale degrees. Shift + a key
 * in the Note or Expand view sets the offset, counted from the first note of that track's
 * pattern; the router shifts the sequencer's notes before its own scale mapping and
 * remembers each shifted note for its note-off. Loading a session resets it. While Shift is held, the reset key and the
 * current transpose key blink. */
#include <firmware.h>

#define FIELD(obj, off, type) (*(type *)((char *)(obj) + (off)))

/* ui_root (see overlays/randomise/randomise.c) */
#define UI_SHIFT        0x10    /* object whose cell +0x10 is the Shift state */
#define UI_MODE_VIEW    0x2c
#define UI_ACTIVE_VIEW  0x8c
#define UI_LOCK         0xb0    /* cell: bit 1 blocks pads */
#define UI_PAD_TOUCHED  0x15c   /* byte stock ui_root_pad_down sets */
#define UI_FORWARD      0x15e   /* byte: forward pads to the view at +0x60 */
#define UI_GLOBAL       (*(void **)0x20001494) /* app_construct stores the ui here */
#define SHIFT_SUBSCRIBER 0x0c   /* the Shift object's cell subscriber */

/* mode view */
#define MV_KEYBOARDS    0x20    /* per-track keyboard views (Note and Expand) */
#define MV_TRACK        0x74
#define MV_SUBVIEW      0x75
#define MV_TRACKS       0x68    /* per-track step objects, Osc 1 and Osc 2 only */
#define TRACK_STEPS     0x0c    /* cell holding a pointer to the pattern's 16 steps */
#define STEP_SIZE       8       /* byte 0 is the note, 0 = empty */

/* keyboard view */
#define KV_LEDS         0x04    /* pointer to the LED buffer pointer, one byte per entry */
#define KV_LAYOUT       0x30
#define LAYOUT_NOTES    0x14    /* pad-to-note table, indexed by keyboard_pad_to_note */
#define LAYOUT_ENTRIES  0xe0    /* up to the reverse table at +0xf4; unused entries are 0 */
#define LAYOUT_ALIAS    0x180   /* per entry: second LED showing the same key, 0xff = none */

/* Colour indices (palette at .data 0x200002a0). Osc 1 keys are magenta, Osc 2 green,
 * sounding keys white, and the Note view's steps cyan. The palette's own animated entries
 * pulse too fast, so the keys blink between these and their stock colour. */
#define COLOUR_RESET    0x07    /* yellow: the pattern's first note */
#define COLOUR_TARGET   0x27    /* blue: the key the sequences are transposed to */
#define BLINK_MS        400     /* each half of the blink */
#define MILLISECONDS    (*(volatile uint32_t *)0x200007e4) /* TIM5 ch3 1 ms count */

/* router */
#define ROUTER_SCALE    0x08    /* scale: +0 lowest note, +1 highest, +4 mask */
#define CH_SEQ_OSC1     3       /* router channels 3 and 4: sequencer, Osc 1 and Osc 2 */
#define CH_SEQ_OSC2     4

enum { SUBVIEW_NOTE = 0, SUBVIEW_EXPAND = 5 };

/* Notes are relative to the root (the router adds it on output). An empty pattern counts
 * from the lowest key of the lower row at the default octave (0x40). */
#define DEFAULT_REFERENCE 0x48
#define NOTES           0x8c    /* notes are below this */

/* CCM (docs/hardware.md "Free RAM"): not cleared at boot, so it carries a magic word. */
#define STATE_MAGIC     0x54525351u /* "QSRT" */
struct state {
    uint32_t magic;
    int8_t degrees;
    uint8_t blink_off;          /* the keys were last drawn in their stock colour */
    uint32_t blink_start;       /* MILLISECONDS when the blink restarted, lit */
    uint8_t playing[2][NOTES];  /* per Osc track: sequencer note -> note sent, 0 = none */
};
#define STATE ((struct state *)0x10000000)

static struct state *state(void)
{
    struct state *s = STATE;
    if (s->magic != STATE_MAGIC) {
        memset(s, 0, sizeof *s);
        s->magic = STATE_MAGIC;
    }
    return s;
}

/* The scale's 2-bit code for a semitone is 0 when the note is in the scale. */
static int in_scale(const uint8_t *scale, unsigned note)
{
    return !((FIELD(scale, 4, uint32_t) >> (note % 12 * 2)) & 3);
}

/* Moves the note by scale degrees, an octave back where it would leave the scale's range. */
static unsigned transpose(const uint8_t *scale, unsigned note, int degrees)
{
    for (; degrees > 0; degrees--)
        do {
            if (++note > scale[1])
                note -= 12;
        } while (!in_scale(scale, note));
    for (; degrees < 0; degrees++)
        do {
            if (--note < scale[0])
                note += 12;
        } while (!in_scale(scale, note));
    return note;
}

void router_note_on(void *router, unsigned note, unsigned velocity, unsigned channel)
{
    if ((channel == CH_SEQ_OSC1 || channel == CH_SEQ_OSC2) && note < NOTES) {
        struct state *s = state();
        unsigned sent = note;
        if (s->degrees)
            sent = transpose(FIELD(router, ROUTER_SCALE, uint8_t *), note, s->degrees);
        s->playing[channel - CH_SEQ_OSC1][note] = sent;
        note = sent;
    }
    stock_router_note_on(router, note, velocity, channel);
}

void router_note_off(void *router, unsigned note, unsigned channel)
{
    if ((channel == CH_SEQ_OSC1 || channel == CH_SEQ_OSC2) && note < NOTES) {
        uint8_t *sent = &state()->playing[channel - CH_SEQ_OSC1][note];
        if (*sent) {
            note = *sent;
            *sent = 0;
        }
    }
    stock_router_note_off(router, note, channel);
}

/* The first note of the track's pattern: tapping its key resets the transpose. */
static unsigned reference_note(void *mode_view, unsigned track)
{
    void *steps_cell = FIELD(FIELD(mode_view, MV_TRACKS + 4 * track, void *), TRACK_STEPS, void *);
    const uint8_t *steps = cell_value(steps_cell);
    for (unsigned i = 0; i < 16; i++)
        if (steps[i * STEP_SIZE] && steps[i * STEP_SIZE] < NOTES)
            return steps[i * STEP_SIZE];
    return DEFAULT_REFERENCE;
}

/* Marks the notes of the layout. It holds every note of the scale within the keyboard's
 * range. */
static void layout_notes(const uint8_t *table, uint8_t seen[NOTES])
{
    memset(seen, 0, NOTES);
    for (unsigned i = 0; i < LAYOUT_ENTRIES; i++)
        if (table[i] < NOTES)
            seen[table[i]] = 1;
}

/* Scale degrees from the reference to the note: the distinct notes of the layout between
 * them. */
static int degrees_from(const uint8_t *table, unsigned reference, unsigned note)
{
    uint8_t seen[NOTES];
    layout_notes(table, seen);
    unsigned lo = note < reference ? note : reference;
    unsigned hi = note < reference ? reference : note;
    int count = 0;
    for (unsigned n = lo + 1; n <= hi; n++)
        count += seen[n];
    return note < reference ? -count : count;
}

/* The key that many degrees from the reference, 0 when it is off the keyboard. */
static unsigned key_at_degrees(const uint8_t *table, unsigned reference, int degrees)
{
    uint8_t seen[NOTES];
    layout_notes(table, seen);
    seen[0] = 0;
    unsigned note = reference;
    for (; degrees > 0; degrees--)
        do
            if (++note >= NOTES)
                return 0;
        while (!seen[note]);
    for (; degrees < 0; degrees++)
        do
            if (--note == 0)
                return 0;
        while (!seen[note]);
    return note;
}

static int shift_held(void *ui)
{
    return *(uint8_t *)cell_read(FIELD(FIELD(ui, UI_SHIFT, void *), 0x10, void *));
}

/* The keyboard view when the Note or Expand view of Osc 1 or Osc 2 is up, else 0. */
static void *osc_keyboard(void *ui, void **mode_view, unsigned *track)
{
    if (FIELD(ui, UI_FORWARD, uint8_t))
        return 0;
    *mode_view = FIELD(ui, UI_MODE_VIEW, void *);
    if (FIELD(ui, UI_ACTIVE_VIEW, void *) != *mode_view)
        return 0;
    *track = FIELD(*mode_view, MV_TRACK, uint8_t);
    unsigned subview = FIELD(*mode_view, MV_SUBVIEW, uint8_t);
    if (*track > 1 || (subview != SUBVIEW_NOTE && subview != SUBVIEW_EXPAND))
        return 0;
    return FIELD(*mode_view, MV_KEYBOARDS + 4 * *track, void *);
}

static unsigned blink_off(void)
{
    return (MILLISECONDS - state()->blink_start) / BLINK_MS & 1;
}

/* The notes to light while Shift is held on this keyboard and the blink is on, else left
 * 0 (no key). */
static void highlighted_keys(void *keyboard_view, unsigned *reset, unsigned *target)
{
    void *ui = UI_GLOBAL, *mode_view;
    unsigned track;
    if (!ui || osc_keyboard(ui, &mode_view, &track) != keyboard_view || !shift_held(ui))
        return;
    struct state *s = state();
    s->blink_off = blink_off();
    if (s->blink_off)
        return;
    const uint8_t *table = FIELD(keyboard_view, KV_LAYOUT, uint8_t *) + LAYOUT_NOTES;
    int degrees = s->degrees;
    *reset = reference_note(mode_view, track);
    *target = degrees ? key_at_degrees(table, *reset, degrees) : 0;
}

/* Draws the entry as stock does, then the reset and transpose keys in their own colours. */
static void render_key(void *keyboard_view, unsigned entry, unsigned reset, unsigned target)
{
    stock_keyboard_leds_render(keyboard_view, entry);
    const uint8_t *layout = FIELD(keyboard_view, KV_LAYOUT, uint8_t *);
    unsigned note = layout[LAYOUT_NOTES + entry];
    unsigned colour;
    if (!note)
        return;
    if (note == target)
        colour = COLOUR_TARGET;
    else if (note == reset)
        colour = COLOUR_RESET;
    else
        return;
    uint8_t *leds = *FIELD(keyboard_view, KV_LEDS, uint8_t **);
    leds[entry] = colour;
    if (layout[LAYOUT_ALIAS + entry] != 0xff)
        leds[layout[LAYOUT_ALIAS + entry]] = colour;
}

void keyboard_leds_render(void *keyboard_view, unsigned entry)
{
    unsigned reset = 0, target = 0;
    highlighted_keys(keyboard_view, &reset, &target);
    render_key(keyboard_view, entry, reset, target);
}

static void redraw_keys(void *keyboard_view)
{
    unsigned reset = 0, target = 0;
    highlighted_keys(keyboard_view, &reset, &target);
    for (unsigned i = 0; i < LAYOUT_ENTRIES; i++)
        render_key(keyboard_view, i, reset, target);
}

/* Restarts the blink lit, so a change shows at once. */
static void restart_blink(void *keyboard_view)
{
    state()->blink_start = MILLISECONDS;
    redraw_keys(keyboard_view);
}

/* Shift's LED: also repaint the keyboard when Shift goes down or up. */
void flag_led_render_thunk(void *subscriber)
{
    stock_flag_led_render_thunk(subscriber);
    void *ui = UI_GLOBAL, *mode_view;
    unsigned track;
    if (!ui || (char *)subscriber - SHIFT_SUBSCRIBER != FIELD(ui, UI_SHIFT, void *))
        return;
    void *keyboard_view = osc_keyboard(ui, &mode_view, &track);
    if (keyboard_view)
        restart_blink(keyboard_view);
}

/* Each LED frame: repaint the keyboard when the blink turns on or off. */
int led_frame_compose(void *leds, unsigned frame, void *ui)
{
    void *mode_view;
    unsigned track;
    void *keyboard_view = osc_keyboard(ui, &mode_view, &track);
    if (keyboard_view && shift_held(ui) && blink_off() != state()->blink_off)
        redraw_keys(keyboard_view);
    return stock_led_frame_compose(leds, frame, ui);
}

/* A session finished loading from flash: its patterns start untransposed. */
void session_ctl_on_save_done(unsigned r0, unsigned r1, unsigned r2, void *session_ctl)
{
    state()->degrees = 0;
    stock_session_ctl_on_save_done(r0, r1, r2, session_ctl);
}

/* Returns 1 when the pad set the transpose, 0 to leave it to the stock handler. */
static int set_transpose(void *ui, unsigned pad)
{
    void *mode_view;
    unsigned track;
    void *keyboard_view = osc_keyboard(ui, &mode_view, &track);
    if (!keyboard_view || !shift_held(ui))
        return 0;
    /* Only keys: in the Note view the lower pads go to the step view. */
    if (mode_view_target(mode_view, pad) != keyboard_view)
        return 0;
    const uint8_t *table = FIELD(keyboard_view, KV_LAYOUT, uint8_t *) + LAYOUT_NOTES;
    unsigned note = table[keyboard_pad_to_note(keyboard_view, pad)];
    if (!note)
        return 0;
    state()->degrees = degrees_from(table, reference_note(mode_view, track), note);
    FIELD(ui, UI_PAD_TOUCHED, uint8_t) = 1;
    restart_blink(keyboard_view);
    return 1;
}

/* Not marking the pad in ui+0xc4 makes stock ui_root_pad_up ignore its release too. */
void ui_root_pad_down(void *ui, unsigned pad, unsigned velocity)
{
    if (*(uint32_t *)cell_read(FIELD(ui, UI_LOCK, void *)) & 2)
        return;
    if (set_transpose(ui, pad))
        return;
    stock_ui_root_pad_down(ui, pad, velocity);
}
