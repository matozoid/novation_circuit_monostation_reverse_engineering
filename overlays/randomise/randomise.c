/* #13: context-sensitive Mutate, and #45: Mutate in the Patches view randomises the patch.
 * The offsets are those stock ui_button_event uses (docs/source-map.md ui_button_event;
 * Shift+Oct pattern transpose walks the same steps). */
#include <firmware.h>

#define FIELD(obj, off, type) (*(type *)((char *)(obj) + (off)))

/* ui_root */
#define UI_SHIFT        0x10    /* object whose cell +0x10 is the Shift state */
#define UI_MUTATE_GATE  0x14    /* cell: Mutate is ignored unless byte 0 is 0 and byte 2 is not 5/6 */
#define UI_MODE_VIEW    0x2c
#define UI_VIEW_38      0x38    /* view whose cell +0x10 every other button clears */
#define UI_PATCH_VIEW   0x3c    /* Patches view; the arrows page it */
#define UI_FILTER_VIEW  0x70    /* filter section view: its pots set params through +0x14 */
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

/* The param sink every section view's pots call: slot 0 forwards to param_set, which
 * stores the value in the live patch, updates the synth and sends it to MIDI out.
 * Values are raw, before the param table's centre is subtracted. */
#define VIEW_PARAM_SINK 0x14
#define SOURCE_PANEL    1
typedef void (*param_set_fn)(void *sink, unsigned id, unsigned value, unsigned source);

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

/* One synth param of the patch randomiser (param ids and raw ranges: the table at
 * 0x0802A62A, tools/patchbank.py PARAMS). With chance rest/8 the param gets the value
 * rest, otherwise a random value in lo..hi. Params not listed keep their value: the
 * category and genre, the octaves and ranges (the patch stays in its register), patch
 * level, audio-in level, the env-to-amp switch and the selected routing. */
struct param_rule {
    uint8_t id, lo, hi, value, rest;
};

/* A mod-matrix depth: mostly off, else up to +-depth around the centre. */
#define DEPTH(id, centre, depth, rest) { id, (centre) - (depth), (centre) + (depth), centre, rest }

enum { FILTER_CUTOFF = 0x1b, FILTER_SHAPE = 0x1c, FILTER_KEY_TRACK = 0x1f,
       ENV_DECAY = 0x21, ENV_SUSTAIN = 0x22 };
enum { SHAPE_BAND_PASS = 1, SHAPE_HIGH_PASS = 2 };

static const struct param_rule patch_rules[] = {
    { 0x02,   0,   1,   0, 0 },     /* env retrigger */
    { 0x04,   0,   3,   0, 0 },     /* osc 1 waveform */
    { 0x05,  16, 112,   0, 0 },     /* osc 1 pulse width: no silent extremes */
    { 0x06, 128, 128, 128, 8 },     /* osc 1 coarse: centred */
    { 0x07, 100, 100, 100, 8 },     /* osc 1 fine: centred */
    { 0x09,   0,  60,   0, 6 },     /* osc 1 glide */
    { 0x0b,   0,   3,   0, 0 },     /* osc 2 waveform */
    { 0x0c,  16, 112,   0, 0 },     /* osc 2 pulse width */
    { 0x0d, 128, 128, 128, 8 },     /* osc 2 coarse: centred */
    { 0x0e,  88, 112, 100, 3 },     /* osc 2 fine: a little detune */
    { 0x10,   0,  60,   0, 6 },     /* osc 2 glide */
    { 0x11,   1,   1,   0, 6 },     /* osc sync */
    { 0x13, 128, 255,   0, 0 },     /* mixer: osc 1, always audible */
    { 0x14,   0, 255,   0, 0 },     /* osc 2 */
    { 0x15,   0, 200,   0, 4 },     /* sub */
    { 0x16,   0, 120,   0, 6 },     /* noise */
    { 0x18,   0, 160,   0, 6 },     /* ring mod */
    { 0x19,   0,  90,   0, 0 },     /* filter overdrive */
    { 0x1a,   0, 100,   0, 0 },     /* resonance: short of self-oscillation */
    { 0x1b,  60, 255,   0, 0 },     /* cutoff: never closed */
    { 0x1c,   1,   2,   0, 4 },     /* shape: low pass half the time, else band or high pass */
    { 0x1d,   0,   1,   0, 0 },     /* slope */
    { 0x1e,   0,   0,   0, 8 },     /* filter bypass: off */
    { 0x1f,   0,   7,   0, 0 },     /* key tracking */
    { 0x20,   0,  90,   0, 4 },     /* attack */
    { 0x21,  10, 127,   0, 0 },     /* decay */
    { 0x22,   0, 127,   0, 0 },     /* sustain */
    { 0x23,   0,  90,   0, 0 },     /* release */
    { 0x24,   0,   3,   0, 0 },     /* LFO waveform */
    { 0x25,   0, 255,   0, 0 },     /* LFO rate */
    { 0x26,   0,  34,   0, 0 },     /* LFO sync rate */
    { 0x27,   0,   1,   0, 0 },     /* LFO clock sync */
    { 0x28,   0,   1,   0, 0 },     /* LFO key sync */
    { 0x29,   0,  80,   0, 4 },     /* distortion */
    { 0x2a,   0,   2,   0, 0 },     /* distortion type */
    /* Mod matrix: envelope, LFO, mod seq and velocity to osc 1 pitch, osc 1 PW, osc 2
     * pitch, osc 2 PW, amp, cutoff, distortion and aux. */
    DEPTH(0x2b, 128,  40, 7), DEPTH(0x2c,  64,  40, 6), DEPTH(0x2d, 128,  40, 7),
    DEPTH(0x2e,  64,  40, 6), DEPTH(0x30, 128, 100, 3), DEPTH(0x31,  64,  40, 6),
    DEPTH(0x32,  64,  63, 6),
    DEPTH(0x33, 128,  20, 7), DEPTH(0x34,  64,  40, 5), DEPTH(0x35, 128,  20, 7),
    DEPTH(0x36,  64,  40, 5), DEPTH(0x37,  64,  30, 7), DEPTH(0x38, 128,  80, 5),
    DEPTH(0x39,  64,  40, 6), DEPTH(0x3a,  64,  63, 6),
    DEPTH(0x3b, 128,  20, 7), DEPTH(0x3c,  64,  40, 6), DEPTH(0x3d, 128,  20, 7),
    DEPTH(0x3e,  64,  40, 6), DEPTH(0x3f,  64,  30, 7), DEPTH(0x40, 128,  80, 6),
    DEPTH(0x41,  64,  40, 6), DEPTH(0x42,  64,  63, 6),
    DEPTH(0x43,  64,  20, 7), DEPTH(0x44,  64,  40, 6), DEPTH(0x45,  64,  20, 7),
    DEPTH(0x46,  64,  40, 6), DEPTH(0x47,  64,  40, 5), DEPTH(0x48,  64,  50, 5),
    DEPTH(0x49,  64,  40, 6), DEPTH(0x4a,  64,  63, 6),
};

static unsigned random_param(const struct param_rule *rule)
{
    if (random_below(8) < rule->rest)
        return rule->value;
    return rule->lo + random_below(rule->hi - rule->lo + 1);
}

/* Shared prelude of both Mutates: 1 when Shift + Mutate may act on the active view. */
static int mutate_allowed(void *ui)
{
    if (*(uint32_t *)cell_read(FIELD(ui, UI_LOCK, void *)) & 2)
        return 0;
    if (FIELD(ui, UI_FORWARD, uint8_t))
        return 0;
    return *(uint8_t *)cell_read(FIELD(FIELD(ui, UI_SHIFT, void *), 0x10, void *));
}

/* What stock does for every button but 0x14, 0x15 and 0x17 before dispatching. */
static void clear_view_38(void *ui)
{
    void *view = FIELD(ui, UI_VIEW_38, void *);
    *(uint8_t *)cell_write_field0(FIELD(view, 0x10, void *)) = 0;
    cell_commit(FIELD(view, 0x10, void *));
}

/* #45: in the Patches view, give the current patch random but playable settings, as if
 * the knobs had been turned: the patch shows as edited and can be saved as usual. */
static int mutate_patch(void *ui)
{
    if (FIELD(ui, UI_ACTIVE_VIEW, void *) != FIELD(ui, UI_PATCH_VIEW, void *))
        return 0;
    clear_view_38(ui);
    void *sink = FIELD(FIELD(ui, UI_FILTER_VIEW, void *), VIEW_PARAM_SINK, void *);
    param_set_fn set = **(param_set_fn **)sink;
    enum { RULES = sizeof patch_rules / sizeof patch_rules[0] };
    uint8_t values[RULES];
    unsigned cutoff = 0, shape = 0, key_track = 0, decay = 0, sustain = 0;
    for (unsigned i = 0; i < RULES; i++) {
        values[i] = random_param(&patch_rules[i]);
        switch (patch_rules[i].id) {
        case FILTER_CUTOFF: cutoff = i; break;
        case FILTER_SHAPE: shape = i; break;
        case FILTER_KEY_TRACK: key_track = i; break;
        case ENV_DECAY: decay = i; break;
        case ENV_SUSTAIN: sustain = i; break;
        }
    }
    /* Band and high pass lose the notes when the cutoff is far off: keep the band in the
     * middle and the high pass low, both following the keys. */
    if (values[shape] == SHAPE_BAND_PASS) {
        values[cutoff] = 100 + random_below(101);
        values[key_track] = 5 + random_below(3);
    } else if (values[shape] == SHAPE_HIGH_PASS) {
        values[cutoff] = random_below(101);
        values[key_track] = 7;
    }
    /* A low sustain after a short decay is only a click: lengthen the decay. */
    if (values[sustain] < 32 && values[decay] < 40)
        values[decay] += 40;
    for (unsigned i = 0; i < RULES; i++)
        set(sink, patch_rules[i].id, values[i], SOURCE_PANEL);
    return 1;
}

/* Returns 1 when it handled the button, 0 to leave it to the stock handler. */
static int mutate_field(void *ui)
{
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

    clear_view_38(ui);

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
    if (button == BUTTON_MUTATE && mutate_allowed(ui) && (mutate_patch(ui) || mutate_field(ui)))
        return;
    stock_ui_button_event(ui, button);
}
