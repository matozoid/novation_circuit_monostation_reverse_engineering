# Source map

Every function in the generated firmware source (`docs/source-build.md`), one line each,
grouped by source file (`src/files.tsv`). Names are ours: the image has no symbols.
A name followed by `(?)` in the purpose is a best guess from the code alone.

Coverage: 851 functions, 851 named, 851 with a purpose.

App files are app code between two linker fills (`docs/source-build.md` *Input
sections*), named for what they hold. Thunks (`*_thunk`) are the
this-adjusting entries that C++ multiple inheritance makes for a second base;
`*_body` is the code after a function's entry stub when the two are separate units.

## Object model

The app is C++ (vtables, `operator new`, `_GLOBAL__sub_I`). `app_construct` builds one
object graph at boot and never frees it.

### Cells

State lives in **cells**: double-buffered values with subscriber lists
(`docs/sequencer.md` *Observable state*). A writer calls `cell_write` (or
`cell_write_field0`/`_field1`, `cell_write_indexed`) to get the back buffer, fills it and
calls `cell_commit`, which swaps buffers and calls every subscriber's `on_cell` slot.
Readers call `cell_read` / `cell_read_field1`. A **port** (`port_post`) is a cell used as
a message: a view posts its id to ask the mode view to switch.

### Views

Each UI screen is a view object. Its vtable slots (confirmed by the input-logging
callbacks `input_log_*`, which print the event names):

| Slot | Event |
|---:|---|
| 0 | pad down |
| 1 | pad up |
| 2 | button down |
| 3 | button up |
| 4 | encoder (`"Continuous pot … delta"`) |
| 5 | pot |
| 6 | fader (`"Slider"`) |
| 7 | no-op |
| 8 | RGB LEDs (`view_rgb_leds`) |
| 9 | mono LEDs (`view_mono_leds`) |
| 10 | `on_cell` |

12-slot views put `return_1_b` at 10 and `on_cell` at 11. The default slots
`0801492a`–`08014930` are no-ops. `ui_root` holds the root views by slot; the mode view
picks one child at a time (`mode_view_select`, `mode_view_target`), and `ui_view_push`
stacks overlays.

| Root slot | Vtable | View |
|---:|---|---|
| `0x14` | `080277BC` | 8-LED view (`view_14_*`) |
| `0x15` | `08027A20` | record view: button `0x20` toggles a port flag |
| `0x17` | `08027324` | mode buttons: 7 LEDs (Osc1, Osc2, ModSeq, Note, Velocity, Gate, PatSettings); with Shift the alternates (key tracking, osc sync, smooth, expand, fixed velocity, glide, mutate); track colours `0x20`/`0x16`/`0x24` from `0x0802AB24` |
| `0x18` | `08027A60` | settings: 16 pads set the Osc1/Osc2 MIDI channel, buttons `0x11`/`0x12` pick the track, `0x21` commits, pads `0x10`–`0x14` the clock mode, `0x17`–`0x1F` setting bits |

Synth section views: osc (`0802ADF4`), mixer, tempo (`08027444`), LFO (`0802777C`),
filter (`080276F0`), distortion (`0802739C`), envelope (`08027484`). Osc pots use a ±`0x14`
pickup window for coarse/fine.

### Synth parameters

Param ids index the table at `0x0802A62A` (patch offset = table offset + 9;
`tools/patchbank.py` `PARAMS`). `param_set` stores a value and calls the update function
for its group from the table at `0x0802A028`–`0x0802A05C`: `synth_update_modes`,
`_distortion`, `_mixer`, `_envelope`, `_lfo`, `_resonance`, `_cutoff`, `_osc2`, `_osc1`,
`_glide` (`synth_update_nop` for the rest).

### Note path

MIDI in (`midi_in_parse` → `midi_ctl_*`) and the track players (`player_on_cell`) feed
note adapters (`note_adapter`, `player_source_adapter`, `midi_note_adapter`: ±12 and
track tags), the merge stage (two 128-bit held-note sets), the router
(`router_note_on/off`, `router_map_note`) and the sinks (`synth_obj_note_on`,
`midiout_note_on`). The record hub (`rec_hub_*`) taps notes and params for the recorder.
MIDI out goes through `midiout_send` → `midi_tx_queue_msg` → `midi_tx_pump`
(`docs/sequencer.md` *MIDI*).


## App files

### app_main

`0800C230`–`0801264C`. `main`, `app_construct`, `ui_construct`, the root UI object `ui_root` and its button/pad routing, plus the keytrack and mod-matrix views.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0800C230` | `memchr` | 160 | newlib arm memchr.S (uadd8/sel), used by vfprintf |
| `0800C2D0` | `main` | 7976 | main loop, soft-timer table 0x20003230 |
| `0800E1F8` | `app_construct` | 6500 | builds the object graph, reads settings and patches |
| `0800FB5C` | `ui_construct` | 6500 | builds the UI: views, mode view children, ui_root |
| `080114C0` | `step_button_subview` | 38 | Note/Velocity/Gate button (+Shift) -> mode-view subview 0..5 |
| `080114E6` | `ui_root_clear_15d` | 8 | byte +0x15d = 0 |
| `080114EE` | `ui_root_set_15c` | 8 | byte +0x15c = 1 |
| `080114F6` | `return_3` | 6 | vtable slot constant |
| `080114FC` | `return_1` | 6 | vtable slot constant |
| `08011502` | `return_0` | 6 | vtable slot constant |
| `08011508` | `mode_view_layer` | 14 | slot 10: 3 for subviews <4, else 1 |
| `08011516` | `keytrack_view_pad_down` | 54 | pads 0-7 set param 0x1f (filter key tracking 0..7) |
| `0801154C` | `led_colour_copy` | 28 | palette copy at 0x200002a0 |
| `08011568` | `modmatrix_dest_leds` | 160 | destination button LEDs of the selected routing |
| `08011608` | `cell_value` | 18 | pointer to a cell's current value |
| `0801161A` | `cell_store` | 14 | write a word to a cell's next buffer |
| `08011628` | `container_view_ctor` | 24 | 34-LED view, vtable 0x08027ba4 (pure render) |
| `08011640` | `keytrack_view_render` | 68 | 8 pads, selected key-tracking value lit |
| `08011684` | `keytrack_view_render_thunk` | 4 | this-adjusting entry of `keytrack_view_render` |
| `08011688` | `keytrack_view_render_thunk2` | 8 | this-adjusting entry of `keytrack_view_render` |
| `08011690` | `cell_subscribe1` | 6 | cell_subscribe(cell, obs, 1) |
| `08011696` | `map_range` | 58 | clamp and scale x from [a,b] to [c,d] |
| `080116D0` | `ui_root_fader` | 46 | vtable slot 6: ADSR faders -> envelope view |
| `080116FE` | `ui_root_pot` | 112 | vtable slot 5: pot by index -> section view |
| `0801176E` | `ui_root_nop` | 2 | ui_root vtable slot: no-op |
| `08011770` | `ui_root_button_up` | 58 | ui_root button release: filters by mode, then ui_root_button_release |
| `080117AA` | `ui_root_pad_up` | 34 | ui_root pad release unless the hold bit is set, then ui_root_release_pad |
| `080117CC` | `ui_root_pad_down` | 72 | forwards to the active pad view |
| `08011814` | `modmatrix_view_leds` | 268 | routing LEDs |
| `08011920` | `modmatrix_view_slot4` | 344 | reads routing and param table; adjusts the selected routing's depth |
| `08011A78` | `modmatrix_view_button` | 2 | Source button cycles routing +8; destinations select |
| `08011A7A` | `modmatrix_view_button_body` | 202 | body of `modmatrix_view_button` (after its entry stub) |
| `08011B44` | `view_08027800_render` | 264 | LEDs from three cells, tables 0x08027b90/0x08027bd0 |
| `08011C4C` | `view_08027800_render_thunk` | 4 | this-adjusting entry of `view_08027800_render` |
| `08011C50` | `view_08027800_render_thunk2` | 8 | this-adjusting entry of `view_08027800_render` |
| `08011C58` | `hold_button_view_render` | 88 | button 0x16 LED colour |
| `08011CB0` | `ui_root_on_cell` | 264 | render and state changes on the root cells |
| `08011DB8` | `ui_root_on_cell_thunk` | 8 | this-adjusting entry of `ui_root_on_cell` |
| `08011DC0` | `hold_button_view_render_thunk` | 4 | this-adjusting entry of `hold_button_view_render` |
| `08011DC4` | `hold_button_view_render_thunk2` | 8 | this-adjusting entry of `hold_button_view_render` |
| `08011DCC` | `ui_root_show_top_view` | 152 | apply the view-stack top |
| `08011E64` | `ui_root_restore_view` | 72 | pop momentary views |
| `08011EAC` | `ui_root_slot4` | 248 | held-button timers (0x200031dc/0x20003230/0x200032f4) |
| `08011FA4` | `cell_clear_flag` | 12 | field0 = 0 |
| `08011FB0` | `cell_set_flag` | 12 | field0 = 1 |
| `08011FBC` | `ui_button_event` | 1612 | routes one button event to the active view or global handler |
| `08012608` | `hold_button_view_up` | 24 | button 0x16 release clears the flag cell |
| `08012620` | `hold_button_view_down` | 44 | button 0x16 press sets the flag cell |

### pad_leds

`0801264C`–`080126E8`. Pad LED helpers: velocity to LED level, a view's LED byte for a pad.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801264C` | `pad_led_update` | 104 | pad flag bits -> colour |
| `080126B4` | `velocity_to_level` | 32 | first of 16 thresholds 0x08027bd9 >= value |
| `080126D4` | `view_led_ptr` | 20 | address of a view's LED byte for a pad (pad to note offset + base) |

### pad_flags

`080126E8`–`080127B4`. Per-pad flag bitsets and small cell accessors.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080126E8` | `pad_send_level` | 32 | sends table 0x08027bd9 level for the current pad |
| `08012708` | `pad_send_level_thunk` | 8 | this-adjusting entry of `pad_send_level` |
| `08012710` | `bitset_test_words` | 26 | test bit n of a word array bitset |
| `0801272A` | `pad_flag_set` | 32 | per-pad flag byte at +0x58, then pad_led_update |
| `0801274A` | `cell_read_word1` | 18 | read word 1 of a cell's current buffer |
| `0801275C` | `step_in_range` | 40 | is a step index within a pattern's start/end range |
| `08012784` | `bitset_test_row6` | 28 | bit row*6+col |
| `080127A0` | `cell_field3_ptr` | 20 | pointer to field 3 of a cell's current buffer |

### gate_octave_views

`080127B4`–`080128A4`. Gate and octave views, flushing held pads.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080127B4` | `gate_view_ctor` | 68 | vtable 0x08027bec |
| `080127F8` | `octave_view_render` | 100 | 2-LED bar from table 0x0802ab24 |
| `0801285C` | `octave_view_render_thunk` | 4 | this-adjusting entry of `octave_view_render` |
| `08012860` | `octave_view_render_thunk2` | 8 | this-adjusting entry of `octave_view_render` |
| `08012868` | `pads_flush_held` | 50 | sends 08023004 for held pads other than the current one |
| `0801289A` | `pads_flush_held_thunk` | 10 | this-adjusting entry of `pads_flush_held` |

### meter_keyboard_leds

`080128A4`–`08012A88`. Meter LEDs, keyboard LEDs, step-range marks.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080128A4` | `meter_leds_render` | 128 | value/6 full LEDs then remainder |
| `08012924` | `meter_leds_render_thunk` | 8 | this-adjusting entry of `meter_leds_render` |
| `0801292C` | `pads_mark_step_range` | 56 | flag bit 2 for steps outside step_in_range |
| `08012964` | `keyboard_leds_render` | 228 | scale-mapped pad colours |
| `08012A48` | `pad_release` | 64 | forwards the held note to the target vtable+4 |

### glide_step_views

`08012A88`–`08013218`. Glide view, step view (blink, range, observer), mod-seq level view.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08012A88` | `glide_view_pad_led` | 112 | one pad of the 16-level glide bar |
| `08012AF8` | `glide_view_on_cell` | 320 | glide view: cell-change observer |
| `08012C38` | `glide_view_on_cell_thunk` | 8 | this-adjusting entry of `glide_view_on_cell` |
| `08012C40` | `glide_view_pad_up` | 20 | glide view: pad release |
| `08012C54` | `glide_view_pad_down` | 100 | sets the track's glide param (id 9/0x10/0x4c) from table 0x08027bd9 |
| `08012CB8` | `step_view_blink_tick` | 124 | soft-timer step blink |
| `08012D34` | `step_view_mark_range` | 62 | pattern start/end on the step pads |
| `08012D72` | `step_view_on_cell` | 4 | step view: cell-change observer |
| `08012D76` | `step_view_on_cell_body` | 398 | body of `step_view_on_cell` (after its entry stub) |
| `08012F04` | `step_view_on_cell_thunk` | 8 | this-adjusting entry of `step_view_on_cell` |
| `08012F0C` | `modseq_level_view_render` | 128 | modseq level view: LED render |
| `08012F8C` | `modseq_level_view_render_thunk` | 8 | this-adjusting entry of `modseq_level_view_render` |
| `08012F94` | `modseq_level_view_pad_down` | 60 | field1 = (pad+1)*8 (127 for pad 15) |
| `08012FD0` | `keyboard_view_on_cell` | 574 | keyboard view: cell-change observer |
| `0801320E` | `keyboard_view_on_cell_thunk` | 10 | this-adjusting entry of `keyboard_view_on_cell` |

### pad_views

`08013218`–`0801392C`. Pad press handlers of the mod-matrix, keyboard, step, gate, scales and patterns views.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08013218` | `modmatrix_view_on_cell` | 620 | modmatrix view: cell-change observer |
| `08013484` | `modmatrix_view_on_cell_thunk` | 8 | this-adjusting entry of `modmatrix_view_on_cell` |
| `0801348C` | `keyboard_view_pad_down` | 318 | plays the scale-mapped note |
| `080135CA` | `step_view_pad_up` | 124 | step view: pad release |
| `08013646` | `step_view_pad_down` | 178 | selects / toggles a step |
| `080136F8` | `gate_view_pad_down` | 98 | sets the held step's gate (field2) |
| `0801375A` | `cell_byte_at` | 24 | byte n of the words from field 8 |
| `08013772` | `scales_view_pad_down` | 30 | pads 0-15 -> upper child, 16-31 -> lower child |
| `08013790` | `scale_mask` | 156 | scale index -> 12-bit note mask (0x15ad, ...) |
| `0801382C` | `patterns_pad_bit` | 72 | pad -> bit in its track's pattern mask (track from column) |
| `08013874` | `view_rgb_leds_b` | 6 | returns the RGB LED buffer |
| `0801387A` | `scales_view_rgb_leds` | 54 | joins the two children's 16 LEDs |
| `080138B0` | `patterns_view_pad_up` | 6 | patterns view: pad release |
| `080138B6` | `patterns_view_pad_up_body` | 66 | body of `patterns_view_pad_up` (after its entry stub) |
| `080138F8` | `patch_view_pad_up` | 14 | patch view: pad release |
| `08013906` | `cell_set_byte_flag` | 38 | byte +0x18+n = v and touch field n (pattern chain flags) |

### pattern_patch_views

`0801392C`–`08014948`. Patterns, patch, scale, sessions, pattern settings, velocity and save views; the transport source adapters and default view slots.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801392C` | `pattern_octave_ctor` | 44 | 2-LED Oct indicator for Shift+Oct pattern transpose |
| `08013958` | `select3_view_render` | 60 | 3-pad selector LEDs (field0 0..2), vtable 0x08027400 |
| `08013994` | `select3_view_render_thunk` | 4 | this-adjusting entry of `select3_view_render` |
| `08013998` | `select3_view_render_thunk2` | 8 | this-adjusting entry of `select3_view_render` |
| `080139A0` | `sessions_view_button` | 80 | Oct buttons step the page byte +0x35 (0..13) |
| `080139F0` | `patterns_view_on_cell` | 4 | patterns view: cell-change observer |
| `080139F4` | `patterns_view_on_cell_body` | 200 | body of `patterns_view_on_cell` (after its entry stub) |
| `08013ABC` | `patterns_view_on_cell_thunk` | 8 | this-adjusting entry of `patterns_view_on_cell` |
| `08013AC4` | `patch_view_render` | 352 | patch view: LED render |
| `08013C24` | `patch_view_on_cell` | 78 | patch view: cell-change observer |
| `08013C72` | `patch_view_on_cell_thunk` | 8 | this-adjusting entry of `patch_view_on_cell` |
| `08013C7A` | `scale_root_view_on_cell` | 2 | marks old/new root pad (colours 0x2f/0x2e) |
| `08013C7C` | `scale_root_view_on_cell_body` | 40 | body of `scale_root_view_on_cell` (after its entry stub) |
| `08013CA4` | `scale_root_view_on_cell_thunk` | 8 | this-adjusting entry of `scale_root_view_on_cell` |
| `08013CAC` | `scale_type_view_render` | 116 | selected scale and its note mask |
| `08013D20` | `scale_type_view_render_thunk` | 8 | this-adjusting entry of `scale_type_view_render` |
| `08013D28` | `pad_state_on_cell` | 380 | shared per-track pad-colour state (vtable 0x080275e8), uses scale_mask |
| `08013EA4` | `pattern_settings_pad_led` | 132 | pattern settings view: colour for one pad (track colour table 0x0802ab24) |
| `08013F28` | `pattern_settings_render` | 120 | pattern settings view: LED render |
| `08013FA0` | `velocity_view_render` | 128 | velocity view: LED render |
| `08014020` | `velocity_view_render_thunk` | 8 | this-adjusting entry of `velocity_view_render` |
| `08014028` | `scale_root_view_pad_down` | 40 | field1 = pad |
| `08014050` | `velocity_view_pad_down` | 72 | held step velocity = (pad+1)*8 (127 for pad 15) |
| `08014098` | `sessions_view_render` | 392 | sessions view: LED render |
| `08014220` | `sessions_view_on_cell` | 144 | sessions view: cell-change observer |
| `080142B0` | `sessions_view_on_cell_thunk` | 8 | this-adjusting entry of `sessions_view_on_cell` |
| `080142B8` | `sessions_view_pad_down` | 120 | posts the session slot (pad, +0x40 page) to the session port |
| `08014330` | `patch_view_pads` | 276 | patch view: pad press, select/load/preview a patch slot |
| `08014444` | `patterns_view_pad_down` | 6 | patterns view: pad press |
| `0801444A` | `patterns_view_pad_down_body` | 412 | body of `patterns_view_pad_down` (after its entry stub) |
| `080145E6` | `pattern_settings_on_cell` | 282 | pattern settings view: cell-change observer |
| `08014700` | `pattern_settings_on_cell_thunk` | 8 | this-adjusting entry of `pattern_settings_on_cell` |
| `08014708` | `select3_view_pad_down` | 2 | pads 0-2 set field0 |
| `0801470A` | `select3_view_pad_down_body` | 42 | body of `select3_view_pad_down` (after its entry stub) |
| `08014734` | `scale_type_view_pad_down` | 2 | pad -> scale via table 0x08027c78 |
| `08014736` | `scale_type_view_pad_down_body` | 50 | body of `scale_type_view_pad_down` (after its entry stub) |
| `08014768` | `save_view_on_cell` | 136 | Save button LED |
| `080147F0` | `save_view_on_cell_thunk` | 8 | this-adjusting entry of `save_view_on_cell` |
| `080147F8` | `save_view_button` | 122 | Save press |
| `08014872` | `pattern_settings_pad_down` | 2 | pads 0-7 sync rate, 12-15 direction, 16-31 start/end |
| `08014874` | `pattern_settings_pad_down_body` | 102 | body of `pattern_settings_pad_down` (after its entry stub) |
| `080148DA` | `transport_src_adapter_4` | 20 | forwards to target vtable+0x10 with the stored id |
| `080148EE` | `transport_src_adapter_3` | 20 | transport source adapter: forwards slot 3 (vtable +0xc) with its track index |
| `08014902` | `transport_src_adapter_2` | 12 | transport source adapter: forwards slot 2 with its track index |
| `0801490E` | `transport_src_adapter_1` | 12 | transport source adapter: forwards slot 1 with its track index |
| `0801491A` | `transport_src_adapter_0` | 12 | transport source adapter: forwards slot 0 with its track index |
| `08014926` | `return_true` | 4 | shared vtable slot |
| `0801492A` | `view_nop` | 2 | default view slots 0/5/6 |
| `0801492C` | `view_nop_slot7` | 2 | view default slot 7: no-op |
| `0801492E` | `view_nop_slot4` | 2 | view default slot 4 (encoder): no-op |
| `08014930` | `view_nop_slot1` | 2 | default slots 1-3 |
| `08014932` | `view_mono_leds` | 6 | slot 9: mono LED buffer |
| `08014938` | `view_rgb_leds` | 6 | slot 8: RGB LED buffer |
| `0801493E` | `view_flag_byte20` | 10 | sets byte +0x20 of arg2 |

### cells

`08014948`–`08014C48`. Observable cells: double-buffered write/commit, subscribe, sync.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08014948` | `view_ab_on_event` | 100 | events 10/11: sends cell field via target vtable[0] |
| `080149AC` | `mode_view_release` | 68 | button-up: clears held bit, forwards vtable+4 to the target view |
| `080149F0` | `mode_view_press` | 64 | button-down: sets held bit, forwards vtable[0] to the target view |
| `08014A30` | `cell_sync_partial` | 142 | copies changed fields between a cell's two buffers |
| `08014ABE` | `cell_write` | 74 | begin a write: return the cell's back buffer for field n |
| `08014B08` | `cell_sync` | 168 | cell double buffer: copy or merge the written fields |
| `08014BB0` | `cell_field_ptr` | 28 | current-buffer pointer of a cell field |
| `08014BCC` | `cell_write_indexed` | 24 | begin a write of element n of an array cell |
| `08014BE4` | `cell_commit` | 74 | commit a write: swap buffers, notify subscribers |
| `08014C2E` | `cell_subscribe` | 26 | add an observer to a cell's subscriber list |

### cell_ports

`08014C48`–`08014D58`. Cell constructor, intrusive lists, ports.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08014C48` | `cell_new` | 120 | (fields, kind): allocates both buffers |
| `08014CC0` | `blist_unlink` | 42 | byte-pair (prev,next) list in an array |
| `08014CEA` | `blist_insert` | 36 | insert a node into an intrusive list |
| `08014D0E` | `port_send` | 32 | follows forward links, calls target vtable+8 |
| `08014D2E` | `port_post` | 42 | post a value to a port (cell + notify) |

### view_base

`08014D58`–`08014DFC`. View base class, one-shot post, virtual-call helpers.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08014D58` | `oneshot_post` | 36 | posts the port once, else disarms soft timer 0x20003230 slot |
| `08014D7C` | `vcall_24` | 6 | calls vtable+0x24 |
| `08014D82` | `vcall_20` | 6 | calls vtable+0x20 |
| `08014D88` | `view_base_ctor` | 64 | base of the UI views, two input ports (08015be8) |
| `08014DC8` | `cell_field0_ptr` | 6 | pointer to field 0 of a cell's current buffer |
| `08014DCE` | `mode_view_select` | 46 | mode view: select child n, rebind, return packed id |

### view_leds

`08014DFC`–`08014F80`. LED group views that redraw on cell change.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08014DFC` | `led_group_6_9_update` | 60 | colours from 0x0802ab24 |
| `08014E38` | `led_group_2_5_update` | 60 | colours from 0x0802ab24 |
| `08014E74` | `view_leds_update` | 240 | view LEDs: recompute and post the LED state |
| `08014F64` | `view_leds_on_cell` | 18 | view LEDs: cell-change observer |
| `08014F76` | `view_leds_on_cell_thunk` | 10 | this-adjusting entry of `view_leds_on_cell` |

### mode_view_misc

`08014F80`–`080150A4`. Mode view redraw and enable-gated posting.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08014F80` | `led_two_cell_colour` | 64 | two flag cells -> colour 0x55/0x54/0x0a/0x0b |
| `08014FC0` | `view_post_if_enabled` | 38 | post the view's id to its port when its enable cell is set |
| `08014FE6` | `view_post_if_enabled_thunk` | 8 | this-adjusting entry of `view_post_if_enabled` |
| `08014FEE` | `mode_view_redraw` | 182 | mode view: redraw the active child (or its override source) |

### osc_mixer_views

`080150A4`–`0801548C`. Osc, mixer and record views: LEDs and observers.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080150A4` | `osc_view_waveform_led` | 40 | osc view: waveform LED colours |
| `080150CC` | `osc_view_on_cell` | 4 | osc view: cell-change observer |
| `080150D0` | `osc_view_on_cell_body` | 340 | body of `osc_view_on_cell` (after its entry stub) |
| `08015224` | `osc_view_on_cell_thunk` | 8 | this-adjusting entry of `osc_view_on_cell` |
| `0801522C` | `mixer_view_led4` | 36 | mixer view: LED 4 colour (0xc2 when off) |
| `08015250` | `mixer_view_on_cell` | 4 | mixer view: cell-change observer |
| `08015254` | `mixer_view_on_cell_body` | 308 | body of `mixer_view_on_cell` (after its entry stub) |
| `08015388` | `mixer_view_on_cell_thunk` | 8 | this-adjusting entry of `mixer_view_on_cell` |
| `08015390` | `rec_view_button` | 2 | button 0x20 toggles the port flag and arms a hold timer; 0x21 |
| `08015392` | `rec_view_button_body` | 138 | body of `rec_view_button` (after its entry stub) |
| `0801541C` | `rec_view_render` | 52 | rec view: LED render |
| `08015450` | `rec_view_on_cell` | 50 | rec view: cell-change observer |
| `08015482` | `rec_view_on_cell_thunk` | 10 | this-adjusting entry of `rec_view_on_cell` |

### synth_views

`0801548C`–`08015D40`. Mode buttons, synth section pots/buttons/LEDs (osc, mixer, LFO, filter, distortion), the settings view, the tempo view.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801548C` | `mode_buttons_render` | 420 | 7 LEDs Osc1/Osc2/ModSeq/Note/Velocity/Gate/PatSettings; Shift shows the alt functions |
| `08015630` | `mode_buttons_on_cell` | 164 | mode buttons: cell-change observer |
| `080156D4` | `mode_buttons_on_cell_thunk` | 8 | this-adjusting entry of `mode_buttons_on_cell` |
| `080156DC` | `osc_view_pot` | 372 | pots 2/3 coarse/fine with pickup window +-0x14 |
| `08015850` | `mixer_view_pot` | 352 | pots 4..9 |
| `080159B0` | `tempo_view_on_cell` | 54 | tempo view: cell-change observer |
| `080159E6` | `tempo_view_on_cell_thunk` | 8 | this-adjusting entry of `tempo_view_on_cell` |
| `080159EE` | `lfo_view_button` | 98 | cycles LFO waveform (param 0x24) / sync |
| `08015A50` | `filter_view_button` | 60 | buttons 0xc..0xe cycle params 0x1c..0x1e |
| `08015A8C` | `dist_view_button` | 50 | cycles distortion type, param 0x2a |
| `08015ABE` | `return_1_b` | 6 | slot 10 of 12-slot views |
| `08015AC4` | `lfo_view_sync_led` | 34 | LFO view: clock-sync LED |
| `08015AE6` | `lfo_view_wave_leds` | 44 | LFO view: waveform LEDs |
| `08015B12` | `filter_view_bypass_leds` | 58 | filter view: bypass LEDs |
| `08015B4C` | `filter_view_slope_leds` | 68 | filter view: slope LEDs |
| `08015B90` | `filter_view_shape_leds` | 44 | filter view: shape LEDs |
| `08015BBC` | `dist_view_type_leds` | 44 | distortion view: type LEDs |
| `08015BE8` | `led_buffer_ctor` | 44 | allocates n zeroed bytes |
| `08015C14` | `settings_view_channel_leds` | 108 | 16 pads: MIDI channel of Osc1/Osc2 synth |
| `08015C80` | `settings_view_button` | 92 | 0x11/0x12 pick the track, 0x21 commits |
| `08015CDC` | `tempo_view_tap` | 28 | tempo view: tap tempo button |
| `08015CF8` | `tempo_view_render` | 56 | tempo view: LED render |
| `08015D30` | `tempo_view_mono_leds` | 16 | tempo view: mono LEDs |

### knob_flag_leds

`08015D40`–`08015E48`. Knob pickup adjust and flag LEDs.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08015D40` | `knob_value_adjust` | 132 | adds a delta within table ranges 0x08027dc0 |
| `08015DC4` | `flag_led_render` | 36 | colour 0x0d/0x0e from a flag cell |
| `08015DE8` | `flag_led_render_thunk` | 8 | this-adjusting entry of `flag_led_render` |
| `08015DF0` | `led_pair_from_table` | 52 | LED pair colour from table 0x08027db2 when enabled |
| `08015E24` | `select_inverted_index` | 2 | cell field2 = 7 - index for index < 8 |
| `08015E26` | `select_inverted_index_body` | 34 | body of `select_inverted_index` (after its entry stub) |

### synth_views_transport

`08015E48`–`08016E78`. Synth section observers and pots/faders, note adapters, the merge stage note sets, the transport (start/stop/continue/song position).

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08015E48` | `lfo_view_on_cell` | 276 | lfo view: cell-change observer |
| `08015F5C` | `lfo_view_on_cell_thunk` | 8 | this-adjusting entry of `lfo_view_on_cell` |
| `08015F64` | `filter_view_on_cell` | 4 | filter view: cell-change observer |
| `08015F68` | `filter_view_on_cell_body` | 260 | body of `filter_view_on_cell` (after its entry stub) |
| `0801606C` | `filter_view_on_cell_thunk` | 8 | this-adjusting entry of `filter_view_on_cell` |
| `08016074` | `envelope_view_on_cell` | 224 | envelope view: cell-change observer |
| `08016154` | `envelope_view_on_cell_thunk` | 8 | this-adjusting entry of `envelope_view_on_cell` |
| `0801615C` | `dist_view_on_cell` | 4 | dist view: cell-change observer |
| `08016160` | `dist_view_on_cell_body` | 148 | body of `dist_view_on_cell` (after its entry stub) |
| `080161F4` | `dist_view_on_cell_thunk` | 8 | this-adjusting entry of `dist_view_on_cell` |
| `080161FC` | `lfo_view_pot` | 296 | pot 1 LFO rate |
| `08016324` | `filter_view_pot` | 256 | pots 10..12 |
| `08016424` | `envelope_view_fader` | 240 | 4 ADSR faders |
| `08016514` | `dist_view_pot` | 224 | pot 13 |
| `080165F4` | `settings_view_on_cell` | 260 | settings view: cell-change observer |
| `080166F8` | `settings_view_on_cell_thunk` | 8 | this-adjusting entry of `settings_view_on_cell` |
| `08016700` | `settings_view_pad_down` | 2 | pads 0..15 channel, 0x10..0x14 clock mode, 0x17..0x1f setting bits |
| `08016702` | `settings_view_pad_down_body` | 362 | body of `settings_view_pad_down` (after its entry stub) |
| `0801686C` | `view_14_render` | 48 | 8 LEDs, one lit at 7 - value |
| `0801689C` | `view_14_on_cell` | 4 | view 0x14: cell-change observer |
| `080168A0` | `view_14_on_cell_thunk` | 8 | this-adjusting entry of `view_14_on_cell` |
| `080168A8` | `note_adapter_note_off` | 34 | vt_note_adapter: channel < 3, note - 12 |
| `080168CA` | `note_adapter_note_on` | 34 | note adapter: note on with offset (±12 / track) |
| `080168EC` | `player_source_adapter_note_off` | 22 | adds the track offset |
| `08016902` | `player_source_adapter_note_on` | 34 | player source adapter: note on, tags the source track |
| `08016924` | `midi_note_adapter_note_off` | 34 | note + 12 |
| `08016946` | `midi_note_adapter_note_on` | 32 | MIDI note adapter: note on, maps channel to track |
| `08016966` | `bool_invert_adapter` | 28 | returns !inner(0, ...) |
| `08016982` | `forward_adapter` | 22 | calls inner slot 0 |
| `08016998` | `merge_stage_note_off` | 92 | two 128-bit held-note sets; forwards when neither holds the note |
| `080169F4` | `merge_stage_note_on` | 96 | merge stage: note on, records it in a 128-bit held set |
| `08016A54` | `cell_write_field2` | 6 | cell_write(cell, 2) |
| `08016A5A` | `pattern_length` | 24 | pattern length from the start/end nibbles (wraps at 16) |
| `08016A72` | `recorder_note_off` | 28 | recorder: note off |
| `08016A8E` | `transport_song_select` | 60 | transport: MIDI song select |
| `08016ACA` | `transport_song_select_thunk` | 8 | this-adjusting entry of `transport_song_select` |
| `08016AD2` | `transport_song_position` | 120 | transport: MIDI song position pointer |
| `08016B4A` | `transport_song_position_thunk` | 8 | this-adjusting entry of `transport_song_position` |
| `08016B52` | `transport_button` | 124 | transport: Play/Record button |
| `08016BCE` | `transport_button_thunk` | 8 | this-adjusting entry of `transport_button` |
| `08016BD6` | `transport_continue` | 128 | transport: continue |
| `08016C56` | `transport_continue_thunk` | 8 | this-adjusting entry of `transport_continue` |
| `08016C5E` | `transport_start` | 182 | transport: start |
| `08016D14` | `transport_start_thunk` | 8 | this-adjusting entry of `transport_start` |
| `08016D1C` | `transport_stop` | 338 | transport: stop |
| `08016E6E` | `transport_stop_thunk` | 10 | this-adjusting entry of `transport_stop` |

### patch_params

`08016E78`–`08017A74`. Transport tick and recorder observers, the patch object (param encode/decode, copy in/out), the synth object, input-event logging.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08016E78` | `transport_on_tick_cell` | 976 | transport: tick cell observer, advances players |
| `08017248` | `recorder_on_cell` | 896 | recorder: cell-change observer |
| `080175C8` | `recorder_on_cell_thunk` | 26 | this-adjusting entry of `recorder_on_cell` |
| `080175E2` | `param_decode` | 112 | reads one param from a patch by its param_table kind |
| `08017652` | `patch_copy_out` | 28 | vt 080271e0 slot 4 |
| `0801766E` | `patch_copy_in` | 28 | slot 3 |
| `0801768A` | `param_get` | 80 | slot 2: decoded value, signed/clamped |
| `080176DA` | `param_get_raw` | 14 | slot 1 |
| `080176E8` | `patch_store_param` | 412 | patch object: store one param (split-pair encoding) |
| `08017884` | `synth_obj_param` | 2 | CC 0x4c sets a global (normal mode), others forward |
| `08017886` | `synth_obj_param_body` | 70 | body of `synth_obj_param` (after its entry stub) |
| `080178CC` | `synth_obj_param_thunk` | 8 | this-adjusting entry of `synth_obj_param` |
| `080178D4` | `synth_obj_note_off` | 28 | synth object: note off |
| `080178F0` | `synth_obj_note_on` | 32 | synth object: note on |
| `08017910` | `input_log_slider` | 36 | printf "Slider %d moved to %d" |
| `08017934` | `input_log_encoder` | 36 | "Continuous pot %d moved with delta %d" |
| `08017958` | `input_log_pot` | 36 | "Pot %d value changed to %d" |
| `0801797C` | `input_log_button` | 42 | "Button %d changed state to %d" |
| `080179A6` | `input_log_pad` | 54 | "Pad %d changed state to %d with velocity %d" |
| `080179DC` | `clock_timer_arm` | 44 | arm the clock soft timer |
| `08017A08` | `clock_restart_ext` | 56 | in clock mode 3: reset counters, arm timer, first tick |
| `08017A40` | `cell_read_field1` | 18 | read field 1 of a cell's current buffer |
| `08017A52` | `cell_new3` | 16 | cell constructor with 3 fields |
| `08017A62` | `cell_new1` | 18 | cell constructor with 1 field |

### clock

`08017A74`–`080186B8`. LED frame compose, clock state, the clock engine, MIDI control realtime/notes.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08017A74` | `led_frame_compose` | 908 | per tick: blink phases, palette, resolves the 82 RGB LED colour indices (0x40+ scaled by params) |
| `08017E00` | `clock_state_on_cell` | 160 | clock state: cell observer, tempo fit from tap/ext timestamps |
| `08017EA0` | `clock_state_on_cell_thunk` | 8 | this-adjusting entry of `clock_state_on_cell` |
| `08017EA8` | `clock_state_on_midi_clock` | 116 | clock state: MIDI clock tick in |
| `08017F1C` | `clock_engine` | 2 | sequencer clock engine (1 ms soft timer) |
| `08017F1E` | `clock_engine_body` | 1610 | body of `clock_engine` (after its entry stub) |
| `08018568` | `midi_ctl_song_position` | 8 | forwards to transport events slot 3 |
| `08018570` | `midi_ctl_song_position_thunk` | 8 | this-adjusting entry of `midi_ctl_song_position` |
| `08018578` | `midi_ctl_stop` | 8 | MIDI control: stop, forwards to transport events |
| `08018580` | `midi_ctl_stop_thunk` | 8 | this-adjusting entry of `midi_ctl_stop` |
| `08018588` | `midi_ctl_continue` | 8 | MIDI control: continue |
| `08018590` | `midi_ctl_continue_thunk` | 8 | this-adjusting entry of `midi_ctl_continue` |
| `08018598` | `midi_ctl_start` | 8 | MIDI control: start |
| `080185A0` | `midi_ctl_start_thunk` | 8 | this-adjusting entry of `midi_ctl_start` |
| `080185A8` | `midi_ctl_cc` | 56 | MIDI control: CC in, param or setting |
| `080185E0` | `midi_ctl_cc_thunk` | 8 | clamps to the param range |
| `080185E8` | `midi_ctl_note_off` | 48 | MIDI control: note off in |
| `08018618` | `midi_ctl_note_on` | 62 | MIDI control: note on in |
| `08018656` | `midi_in_cc_pair` | 98 | CC n / n+32 MSB+LSB pairing via the CC map |

### midi_in_out

`080186B8`–`08018B90`. MIDI byte parser, note merger and sinks, MIDI out messages.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080186B8` | `midi_in_parse` | 544 | MIDI byte stream parser (running status, realtime, SysEx) |
| `080188D8` | `merger_note_off` | 104 | merger: note off |
| `08018940` | `merger_note_on` | 122 | merger: note on |
| `080189BA` | `note_sink_note_off` | 20 | note sink: note off |
| `080189CE` | `note_sink_note_on` | 20 | note sink: note on |
| `080189E2` | `scale_snap_note` | 38 | moves a note to the scale (mask 2 bits per semitone) |
| `08018A08` | `midiout_continue` | 38 | MIDI out: continue (0xFB) |
| `08018A2E` | `midiout_clock_thunk` | 8 | this-adjusting entry of `midiout_clock` |
| `08018A36` | `midiout_clock` | 38 | MIDI out: clock (0xF8) |
| `08018A5C` | `midiout_clock_tick` | 8 | MIDI out: clock tick wrapper |
| `08018A64` | `midiout_song_select` | 38 | MIDI out: song select |
| `08018A8A` | `midiout_song_position_thunk` | 8 | this-adjusting entry of `midiout_song_position` |
| `08018A92` | `midiout_song_position` | 42 | MIDI out: song position pointer |
| `08018ABC` | `midiout_stop_thunk` | 8 | this-adjusting entry of `midiout_stop` |
| `08018AC4` | `midiout_stop` | 38 | MIDI out: stop (0xFC) |
| `08018AEA` | `midiout_start_thunk` | 8 | this-adjusting entry of `midiout_start` |
| `08018AF2` | `midiout_start` | 38 | MIDI out: start (0xFA) |
| `08018B18` | `midiout_note_off_thunk` | 8 | this-adjusting entry of `midiout_note_off` |
| `08018B20` | `midiout_note_off` | 40 | MIDI out: note off |
| `08018B48` | `midiout_note_on` | 40 | MIDI out: note on |
| `08018B70` | `midiout_cc` | 32 | status 0xB0 \| channel |

### sysex_patch_session

`08018B90`–`08018EE8`. Param MIDI out, patch SysEx receive, session init, scale mapping.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08018B90` | `midiout_param` | 136 | send a synth param as CC / CC pair / NRPN on MIDI out |
| `08018C18` | `sysex_patch_rx` | 140 | F0 00 20 29 01 61: type 0 current / 1 slot write (136 bytes) |
| `08018CA4` | `session_init` | 504 | session init: read globals and current session from NOR |
| `08018E9C` | `scale_find_bounds` | 46 | lowest/highest note reachable in the scale |
| `08018ECA` | `scale_map_note` | 30 | map a note through the active scale |

### settings

`08018EE8`–`080192A4`. Settings save, session flash write, globals publish, session defaults.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08018EE8` | `synth_routing_on_cell` | 116 | selected routing (<= 31) to the synth globals |
| `08018F5C` | `settings_save_clear_pending` | 24 | settings save done: clear the pending flag |
| `08018F74` | `settings_save` | 268 | syncs the settings cell and writes it to NOR |
| `08019080` | `session_patch_flash_write` | 116 | read-modify-write of the 4 KB sector holding the session patch |
| `080190F4` | `session_ctl_publish_globals` | 204 | fills the global cells from the session image |
| `080191C0` | `session_fixup_defaults` | 184 | sets missing flag bits/defaults in a loaded session |
| `08019278` | `session_ctl_reset` | 44 | session_init, load its patch, reset the synth |

### session_ctl_save

`080192A4`–`08019430`. Session magic check and the save state machine.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080192A4` | `session_magic_valid` | 68 | check a session image's magic |
| `080192E8` | `cell_write_field0` | 6 | begin a write of field 0 |
| `080192EE` | `cell_write_byte` | 56 | byte n of the word array at word 8 |
| `08019326` | `cell_mirror_on_cell` | 40 | copies field0/field1 to two bytes |
| `0801934E` | `session_ctl_clear_pending_b` | 24 | session control: clear pending flag b |
| `08019366` | `session_ctl_save_step` | 202 | session control: one step of the save state machine |

### session_globals

`08019430`–`0801985C`. Patch-write completion, globals save/load.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08019430` | `session_ctl_patch_write_done` | 100 | session control: patch write finished, reapply patch, publish globals |
| `08019494` | `session_ctl_save_globals` | 464 | session control: save globals to NOR |
| `08019664` | `session_ctl_load_globals` | 504 | session control: load globals |

### session_ctl_usb_user

`0801985C`–`08019D90`. Session control observers, then the start of the ST USB library (EP transfers, USBD_USR callbacks).

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801985C` | `session_ctl_on_cell` | 648 | vt_session_ctl slot 1; session_ctl_patch_load is its this-4 thunk |
| `08019AE4` | `session_ctl_patch_load` | 8 | session control: load a patch from a session |
| `08019AEC` | `session_ctl_on_save_done` | 116 | session control: save/load done, validate magic, load globals or reset |
| `08019B60` | `USB_OTG_EP0StartXfer` | 154 | EP0 DIEPTSIZ/DOEPTSIZ, 0x7f size mask |
| `08019BFA` | `USB_OTG_EPStartXfer` | 290 | packet count (len+mps-1)/mps |
| `08019D1C` | `USBD_USR_DeviceSuspended` | 12 | USR cb table 0x0802af78 +0x0c, usb_configured=0 |
| `08019D28` | `USBD_USR_DeviceDisconnected` | 12 | USR cb +0x18 |
| `08019D34` | `USBD_USR_DeviceConnected` | 12 | USR cb +0x14 |
| `08019D40` | `USBD_USR_DeviceConfigured` | 12 | USR cb +0x08, usb_configured=1 |
| `08019D4C` | `USBD_USR_DeviceResumed` | 12 | USR cb +0x10 |
| `08019D58` | `USBD_USR_DeviceReset` | 12 | USR cb +0x04 |
| `08019D64` | `USBD_USR_Init` | 2 | USR cb +0x00, empty |
| `08019D66` | `IntToUnicode` | 42 | hex nibbles to UTF-16 string |

### usbd_desc

`08019D90`–`08019E94`. USB descriptors and serial number.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08019D90` | `USBD_USR_SerialStrDescriptor` | 2 | desc table 0x0802aeec +0x10 |
| `08019D92` | `Get_SerialNum` | 66 | serial string from the unique ID |
| `08019DD4` | `USBD_USR_LangIDStrDescriptor` | 12 | desc table +0x04 |
| `08019DE0` | `USBD_USR_DeviceDescriptor` | 12 | desc table +0x00, 18 bytes at 0x08028e96 |
| `08019DEC` | `usbd_midi_stub_0` | 4 | USB MIDI class stub: returns 0 |
| `08019DF0` | `USBD_USR_InterfaceStrDescriptor` | 20 | desc table +0x18 |
| `08019E04` | `USBD_USR_ConfigStrDescriptor` | 20 | desc table +0x14 |
| `08019E18` | `USBD_USR_ManufacturerStrDescriptor` | 20 | desc table +0x08 |
| `08019E2C` | `USBD_USR_ProductStrDescriptor` | 20 | desc table +0x0c |
| `08019E40` | `DCD_EP_Stall` | 84 | DIEPCTL/DOEPCTL STALL |

### usbd_core_a

`08019E94`–`0801A348`. USB device core: setup and data OUT stages, EP Tx/Rx.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08019E94` | `USBD_SetupStage` | 944 | DCD cb 0x0802b67c +0x08, standard requests inlined |
| `0801A244` | `DCD_EP_Tx` | 62 | USB OTG library: start an IN transfer on an endpoint |
| `0801A282` | `DCD_EP_PrepareRx` | 76 | USB OTG library: prepare an OUT endpoint for reception |
| `0801A2CE` | `USBD_DataOutStage` | 122 | DCD cb +0x00 |

### usbd_core_b

`0801A348`–`0801A534`. USB OTG core reset, EP0, data IN stage, EP open/close.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801A348` | `USB_OTG_CoreReset` | 76 | GRSTCTL AHBIDL then CSRST |
| `0801A394` | `USB_OTG_EP0_OutStart` | 40 | USB OTG library: arm EP0 for SETUP packets |
| `0801A3BC` | `USBD_DataInStage` | 216 | DCD cb +0x04 |
| `0801A494` | `DCD_EP_Close` | 66 | USB OTG library: close an endpoint |
| `0801A4D6` | `DCD_EP_Open` | 94 | USB OTG library: open an endpoint |

### usbd_midi

`0801A534`–`0801A790`. FIFO flush, endpoint stop, the USB MIDI class.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801A534` | `USB_OTG_FlushRxFifo` | 56 | USB OTG library: flush the RX FIFO |
| `0801A56C` | `USB_OTG_FlushTxFifo` | 64 | USB OTG library: flush a TX FIFO |
| `0801A5AC` | `usb_endpoint_stop` | 108 | stop USB endpoints before the bootloader handover |
| `0801A618` | `usbd_midi_GetCfgDesc` | 12 | class cb 0x0802af4c +0x28, 0x74-byte config descriptor 0x08028ee8 |
| `0801A624` | `usbd_midi_SOF` | 4 | class cb +0x1c, returns 0 |
| `0801A628` | `usbd_midi_IsoOUTIncomplete` | 4 | class cb +0x24, returns 0 |
| `0801A62C` | `usbd_midi_DataOut` | 28 | class cb +0x18, advances the USB MIDI rx write index |
| `0801A648` | `usbd_midi_DataIn` | 52 | class cb +0x14, advances the tx read index |
| `0801A67C` | `usbd_midi_EP0_RxReady` | 32 | class cb +0x10 |
| `0801A69C` | `usbd_midi_Setup` | 184 | class cb +0x08 |
| `0801A754` | `usbd_midi_DeInit` | 60 | class cb +0x04 |

### system_init

`0801A790`–`0801AB50`. USB MIDI init, BSP delay, soft disconnect, `SystemInit`, DMA and SPI init.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801A790` | `usbd_midi_Init` | 140 | class cb +0x00, opens EP 0x81/0x01, 64 bytes |
| `0801A81C` | `usb_bsp_udelay` | 20 | busy loop n*168/7 (USB_OTG_BSP_uDelay) |
| `0801A830` | `usb_soft_disconnect` | 24 | USB soft disconnect (DCTL.SDIS) |
| `0801A848` | `SystemInit` | 236 | CMSIS system_stm32f4xx.c with SetSysClock: HSE, PLL, FLASH ACR 0x705 |
| `0801A934` | `dma_stream_setup` | 300 | DMA_DeInit + DMA_Init from a config struct |
| `0801AA60` | `spi_init` | 128 | SPI1/SPI4 RCC enable, CR1 setup, SPE |
| `0801AAE0` | `spi_pins_init` | 112 | GPIO_Init + GPIO_PinAFConfig for SCK/MISO/MOSI |

### drivers_a

`0801AB50`–`0801AECC`. RNG, image jump/header check, SPI DMA, backup SRAM, ADC, mode checks.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801AB50` | `rng_init` | 76 | RCC AHB2 reset+enable RNG, RNGEN, first DRDY value |
| `0801AB9C` | `rng_read` | 80 | hardware RNG |
| `0801ABEC` | `jump_to_image` | 68 | set MSP and jump to an image's reset vector |
| `0801AC30` | `app_header_valid` | 76 | validate an image header (MAIN/BOOT tags) |
| `0801AC7C` | `header_version` | 60 | read the BCD version from an image header |
| `0801ACB8` | `dma_start` | 42 | set NDTR and EN when stream idle |
| `0801ACE2` | `spi_dma_receive` | 56 | rx DMA, tx a dummy byte without MINC |
| `0801AD1A` | `spi_dma_transfer` | 46 | tx/rx DMA with MINC |
| `0801AD48` | `bkpsram_enable` | 52 | PWR clock, BKPSRAM clock, DBP, BRE, wait BRR |
| `0801AD7C` | `adc_init` | 260 | ADC CR1/CR2, regular sequence SQRx, sample time 6 |
| `0801AE80` | `shutdown_notify` | 32 | calls registered hook 0x200019c0 or clears flag |
| `0801AEA0` | `is_normal_mode` | 16 | test/calibration flag 0x20002ec0 clear |
| `0801AEB0` | `synth_reset_if_flag` | 28 | reset the synth engine when in normal mode and the flag bit is set |

### tempo_midiout

`0801AECC`–`0801AEF4`. Tempo publish and MIDI send glue.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801AECC` | `clock_publish_tempo` | 28 | publish the tempo cell |
| `0801AEE8` | `midiout_send` | 12 | send a MIDI message through the tx queue |

### nor_async_a

`0801AEF4`–`0801B0BC`. Blocking NOR read, NOR async write enable/erase, held-note release, note map.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801AEF4` | `ext_flash_read` | 44 | read external NOR flash (blocking) |
| `0801AF20` | `lookup_pair_table` | 40 | two 2-byte tables by index 0-3 |
| `0801AF48` | `nor_async_write_enable` | 72 | sends 0x06 |
| `0801AF90` | `nor_async_erase_next` | 100 | subsector erase 0x20 of the next sector in range |
| `0801AFF4` | `midi_ctl_release_held` | 100 | for each set bit in a port's 160-bit held set calls vtable+4 (note off) |
| `0801B058` | `router_map_note2` | 100 | router: note map, variant 2 |

### drivers_seq

`0801B0BC`–`0801C010`. Mixed: async NOR read, patch apply, StdPeriph TIM/DMA/NVIC, LED buffers, sequencer track positions, step advance.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801B0BC` | `ext_flash_read_async` | 64 | (sector, offset, dst, len, cb, ctx): nor_async_read at sector*0x1000+offset |
| `0801B0FC` | `session_ctl_apply_patch` | 24 | synth vtable+0x10 with the session patch at +0x15F5C |
| `0801B114` | `TIM_OC2Init` | 128 | SPL stm32f4xx_tim.c |
| `0801B194` | `calibration_crc` | 56 | CRC-32 (0xEDB88320) over 0x40 bytes |
| `0801B1CC` | `DMA_Init` | 64 | SPL stm32f4xx_dma.c (CR mask 0xF01C803F) |
| `0801B20C` | `tim14_value_set` | 32 | stores a byte, sets update flag 0x2000049c bit0 |
| `0801B22C` | `leds_update` | 316 | builds the LED frame buffers 0x200004a4/0x200005e0 |
| `0801B368` | `leds_clear` | 108 | clear the LED frame buffers |
| `0801B3D4` | `NVIC_Init` | 68 | SPL misc.c |
| `0801B418` | `USBD_GetString` | 64 | ASCII -> USB string descriptor |
| `0801B458` | `nor_spi_xfer` | 136 | CS low, tx, optional rx, CS mode 1 keep / 3 release |
| `0801B4E0` | `patch_mask_update` | 228 | update the patch-changed mask |
| `0801B5C4` | `session_ctl_session_save` | 212 | writes the 0x16000-byte session image to ext flash 0x12000+slot*0x16000 |
| `0801B698` | `seq_track_position` | 488 | position of one track in its pattern |
| `0801B880` | `seq_track_positions_all` | 56 | positions of all tracks |
| `0801B8B8` | `seq_set_clock` | 34 | set the sequencer clock source |
| `0801B8DA` | `router_map_note` | 152 | router: map a note to its output |
| `0801B972` | `cell_load_preset_row` | 196 | copies 5 bytes of an 8-byte table row into cell fields 0-4 |
| `0801BA36` | `seq_step_advance` | 634 | sequencer step advance |
| `0801BCB0` | `session_ctl_clear_pending` | 60 | session control: clear the pending flag, post to the port |
| `0801BCEC` | `nop_callback_a` | 2 | no-op callback |
| `0801BCEE` | `nop_callback_b` | 2 | no-op callback |
| `0801BCF0` | `gpioc9_low_timer` | 24 | one-shot 200 ms soft timer: GPIOC pin 9 low |
| `0801BD08` | `heartbeat_toggle` | 16 | 1 s soft timer: toggles GPIOE pin 11 |
| `0801BD18` | `nor_async_erase_step_done` | 88 | calls write-done callback 0x200024b8 or posts next event |
| `0801BD70` | `nor_async_idle_ok` | 28 | status ready in state 8, posts event 6 |
| `0801BD8C` | `nor_async_wel_ok` | 28 | status ready in state 7, posts event 5 |
| `0801BDA8` | `gpio_set` | 14 | BSRRL = pin |
| `0801BDB6` | `gpio_reset` | 14 | BSRRH = pin |
| `0801BDC4` | `engine_tick_2khz` | 24 | led_brightness_step, synth_engine_update, counter |
| `0801BDDC` | `usb_midi_tx_watchdog` | 8 | 50 ms soft timer |
| `0801BDE4` | `usb_midi_tx_watchdog_body` | 60 | resets the USB MIDI tx queue (08020b04) |
| `0801BE20` | `input_poll_task` | 292 | 10 ms soft timer: pots_scan, control_dispatch |
| `0801BF44` | `nor_async_program_step_done` | 48 | programs next chunk or posts event 8 |
| `0801BF74` | `nor_async_read_chunk` | 56 | DMA receive of next <=64K chunk |
| `0801BFAC` | `nor_async_read_chunk_done` | 12 | next chunk or read-done callback 0x200024c0 |
| `0801BFB8` | `nor_async_read_chunk_done_tail` | 76 | NOR async read: chunk done, read the next one or raise fsm event 3 |
| `0801C004` | `nor_async_read_start` | 12 | fsm action after the read command is sent |

### nor_async_router

`0801C010`–`0801D028`. NOR async state machine, MIDI control clock/program/song select, note router, param set, track patterns.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801C010` | `nor_async_send_cmd` | 32 | fsm action: DMA the command buffer |
| `0801C030` | `nor_async_send_cmd_thunk` | 4 | this-adjusting entry of `nor_async_send_cmd` |
| `0801C034` | `nor_async_wait_idle` | 44 | fsm action: poll status until WIP clear |
| `0801C060` | `nor_async_wait_idle_thunk` | 4 | this-adjusting entry of `nor_async_wait_idle` |
| `0801C064` | `nor_async_status_check` | 72 | compares status & mask with expected, re-reads status 0x05 |
| `0801C0AC` | `nor_async_wait_wel` | 36 | fsm action: poll status until WEL set |
| `0801C0D0` | `nor_async_continue` | 4 | event 7 program next chunk, 8 erase next sector |
| `0801C0D4` | `nor_async_continue_body` | 22 | body of `nor_async_continue` (after its entry stub) |
| `0801C0EA` | `timer_nop` | 2 | empty 10 ms soft-timer callback |
| `0801C0EC` | `nor_async_read` | 104 | fast read 0x0B via DMA, returns 1 when busy |
| `0801C154` | `nor_async_program` | 100 | page program 0x02, returns 1 busy/2 address/3 length |
| `0801C1B8` | `nor_async_write` | 44 | (addr, src, len): erase subsectors then program; calibration save writes 0x44 bytes at 0 |
| `0801C1E4` | `nor_async_poll` | 36 | runs pending fsm events until idle |
| `0801C208` | `power_calib_task` | 1024 | 1 kHz soft timer: power switch, shutdown (IWDG refresh + WFI loop), calibration runner and its SysEx 70 60 5A report |
| `0801C608` | `midi_ctl_forward_clock` | 8 | vt_midi_ctl: calls field 0x28 vtable[0] |
| `0801C610` | `midi_ctl_clock` | 8 | MIDI control: clock in |
| `0801C618` | `midi_ctl_on_reset` | 14 | vt_midi_ctl: port_post when arg 0 |
| `0801C626` | `midi_ctl_program_change` | 50 | MIDI control: program change in, posts the slot to the patch-load port |
| `0801C658` | `midi_ctl_song_select` | 30 | MIDI control: song select in |
| `0801C676` | `midi_ctl_song_select_thunk` | 8 | this-adjusting entry of `midi_ctl_song_select` |
| `0801C67E` | `router_note_off` | 202 | router: note off |
| `0801C748` | `router_note_on` | 216 | router: note on |
| `0801C820` | `midi_ctl_release_held_thunk` | 4 | this-adjusting entry of `midi_ctl_release_held` |
| `0801C824` | `midi_ctl_send_b` | 12 | port_send via field 0x34 |
| `0801C830` | `midi_ctl_send_a` | 12 | port_send via field 0x30 |
| `0801C83C` | `cell_write_field1` | 6 | begin a write of field 1 |
| `0801C842` | `param_set` | 2 | set a synth param by id |
| `0801C844` | `param_set_body` | 468 | body of `param_set` (after its entry stub) |
| `0801CA18` | `midi_apply_settings` | 212 | apply the MIDI settings (channels, clock, out) |
| `0801CAEC` | `midi_ctl_on_cell` | 4 | settings cell changed: release held notes or midi_apply_settings |
| `0801CAF0` | `midi_ctl_on_cell_body` | 48 | body of `midi_ctl_on_cell` (after its entry stub) |
| `0801CB20` | `midi_ctl_on_cell_thunk` | 8 | this-adjusting entry of `midi_ctl_on_cell` |
| `0801CB28` | `router_on_cell` | 64 | vt_router: scale cell -> scale mask + bounds, root cell |
| `0801CB68` | `router_on_cell_thunk` | 8 | this-adjusting entry of `router_on_cell` |
| `0801CB70` | `track_patterns_update_playing` | 58 | cell 0x30 = pattern value when the view's pattern is the playing one |
| `0801CBAA` | `track_patterns_load_settings` | 52 | pattern bytes 0x80..0x82 into the settings cell |
| `0801CBDE` | `track_patterns_on_cell` | 1056 | vt 08029f68: selected vs playing pattern, 0x8c-byte headers, 0x6c-byte step rows |
| `0801CFFE` | `bipolar_x3` | 42 | (v-128)*3 with 0x7f/0x81 snapped to +-128 |

### synth_math

`0801D028`–`0801E080`. Pitch/exponent/envelope math, synth engine update, USBD core events.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801D028` | `cents_to_pitch` | 68 | signed value: tens * 0x100 + remainder / table 0x08029d1c |
| `0801D06C` | `exp2_fixed` | 38 | 4.10 mantissa/exponent to linear |
| `0801D092` | `env_time_to_rate` | 66 | 7-bit time to rate via exp2_fixed |
| `0801D0D4` | `engine_bind_buffers` | 276 | param 0: binds the modulation source/destination pointers |
| `0801D1E8` | `bitset_test` | 32 | bit n of the word array 0x20002f90 |
| `0801D208` | `synth_engine_update` | 3608 | 3608 bytes: glide, pitch CV, SPI DAC, gate/sync output pins |
| `0801E020` | `USBD_IsoOUTIncomplete` | 14 | DCD cb +0x20 |
| `0801E02E` | `USBD_IsoINIncomplete` | 14 | DCD cb +0x1c |
| `0801E03C` | `USBD_SOF` | 16 | DCD cb +0x0c |
| `0801E04C` | `USBD_Suspend` | 28 | DCD cb +0x14 |
| `0801E068` | `USBD_Resume` | 24 | DCD cb +0x18 |

### midi_send

`0801E080`–`0801E12C`. Short MIDI send, USBD reset.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801E080` | `midi_send_short` | 88 | 3 bytes to the port callback; also to 0x08020d30 when 0x200016a0 bit 5 is set |
| `0801E0D8` | `USBD_Reset` | 84 | DCD cb +0x10, opens EP0 in/out |

### io_helpers

`0801E12C`–`0801E310`. Output pins, SysTick delay, debug SysEx, ADC filter.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801E12C` | `output_pin_set` | 192 | mode 0: TIM2 CH4 forced high/low/PWM2 (CCMR2 OC4M); mode 2: GPIO pin via BSRR |
| `0801E1EC` | `delay_systick_wraps` | 24 | busy-wait for N SysTick counter wraps |
| `0801E204` | `sysex_send_thunk` | 16 | tail-calls midi_tx_sysex |
| `0801E214` | `sysex_send_debug` | 100 | F0 00 20 29 01 61 70 33 a b n n n n F7, 15 bytes |
| `0801E278` | `bitset_clear` | 30 | (n+7)/8 bytes |
| `0801E296` | `adc_filter_push` | 122 | power-of-two moving average per channel |

### controls_stdperiph

`0801E310`–`0801EE24`. Control scan and dispatch, StdPeriph TIM/RCC/GPIO/DMA, LED render, USBD control helpers.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801E310` | `control_dispatch` | 136 | id < 0x20 pads, 0x20..0x31 knobs, 0x32..0x35 signed pairs, via callbacks |
| `0801E398` | `button_event` | 60 | control_dispatch, then sysex_send_debug when 0x200016a0 bit 1/2 is set |
| `0801E3D4` | `pots_scan` | 236 | filtered value vs last, threshold, map range, control_dispatch |
| `0801E4C0` | `tim11_restart` | 28 | CNT=0, clear UIF, CEN |
| `0801E4DC` | `TIM_GetITStatus` | 22 | SPL |
| `0801E4F2` | `TIM_TimeBaseStructInit` | 18 | SPL |
| `0801E504` | `TIM_TimeBaseInit` | 124 | SPL |
| `0801E580` | `RCC_APB2PeriphResetCmd_off` | 16 | RCC->APB2RSTR &= ~x (DISABLE half) |
| `0801E590` | `RCC_APB1PeriphResetCmd_off` | 16 | RCC->APB1RSTR &= ~x |
| `0801E5A0` | `TIM_DeInit` | 352 | SPL |
| `0801E700` | `RCC_GetClocksFreq` | 136 | SPL |
| `0801E788` | `GPIO_PinAFConfig` | 36 | SPL |
| `0801E7AC` | `GPIO_Init` | 132 | SPL |
| `0801E830` | `DMA_ClearITPendingBit` | 48 | SPL |
| `0801E860` | `DMA_GetITStatus` | 80 | SPL |
| `0801E8B0` | `DMA_DeInit` | 268 | SPL |
| `0801E9BC` | `USBD_CtlError` | 28 | USB device library: stall EP0 on a request error |
| `0801E9D8` | `USBD_CtlSendStatus` | 30 | USB device library: send a zero-length status packet |
| `0801E9F6` | `USBD_CtlSendData` | 32 | USB device library: send EP0 data |
| `0801EA16` | `USBD_SetCfg` | 24 | class Init + USR DeviceConfigured |
| `0801EA2E` | `__cxa_pure_virtual` | 2 | b . in 8 vtable slots |
| `0801EA30` | `leds_render_rgb` | 312 | 82 RGB LEDs from colour table 0x080290d4 into 0x200005e0 |
| `0801EB68` | `leds_render_mono` | 144 | 21 single-colour LEDs |
| `0801EBF8` | `led_brightness_step` | 60 | step the LED brightness ramp |
| `0801EC34` | `nor_async_read_status` | 60 | SPI command 0x05 (RDSR), 2 bytes |
| `0801EC70` | `nor_async_program_chunk` | 88 | splits at the page boundary |
| `0801ECC8` | `buttons_scan` | 316 | debounced button matrix, key map u16 per bit |
| `0801EE04` | `to_decimal_digits` | 32 | LSB first |

### identity

`0801EE24`–`0801EEAC`. Universal identity reply.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801EE24` | `sysex_identity_reply` | 136 | answer the universal identity request |

### irq_calibration

`0801EEFC`–`0801FAC4`. USB/DMA2/TIM5/TIM14 handlers and the calibration runner.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801EEFC` | `OTG_FS_IRQHandler` | 2 | USB OTG_FS interrupt, dispatches GINTSTS bits |
| `0801EEFE` | `USBD_OTG_ISR_Handler` | 1498 | OTG_FS_IRQHandler falls into it |
| `0801F4D8` | `OTG_HS_IRQHandler` | 4 | USB OTG_HS interrupt: branches to the OTG_FS handler |
| `0801F4DC` | `DMA2_Stream1_IRQHandler` | 40 | DMA2 Stream1 complete: clear the stream's busy flag |
| `0801F504` | `DMA2_Stream0_IRQHandler` | 64 | DMA2 Stream0 complete: clear busy, set NOR-async state 2 |
| `0801F544` | `TIM5_IRQHandler` | 216 | TIM5: 10 kHz time base, 1 ms soft-timer tick |
| `0801F61C` | `TIM8_TRG_COM_TIM14_IRQHandler` | 56 | TIM14 update: load a 16-bit value into a timer register |
| `0801F654` | `calib_value_inc` | 18 | calibration word +1, saturates at 0xff |
| `0801F666` | `calib_value_dec` | 18 | calibration word -1 |
| `0801F678` | `calib_step_run` | 472 | default step of calib_table: waits 3 phases, then nudges the calibration word up/down until the ADC reading crosses the limit (entry type 0..5) |
| `0801F850` | `calib_load_patch` | 24 | copies the 0x4e-byte test patch 0x08026e3c over the live patch 0x200016a2 |
| `0801F868` | `calibration_apply` | 288 | scales the calibration table 0x200024c8 into the engine's trim globals |
| `0801F988` | `calib_set_mode` | 40 | mode byte + spi_dac ctrl; mode 1 sets the test flag 0x2000339c |
| `0801F9B0` | `exti12_enable` | 68 | EXTI line 12 IMR/EMR/RTSR/FTSR |
| `0801F9F4` | `calib_start` | 80 | resets the calibration state 0x2000339c for one table entry, enables TIM9 and EXTI12 |
| `0801FA44` | `pattern_ctl_flush` | 128 | commits the pending copy source/target cells (+0x24/+0x29), vt_pattern_ctl slot 0 |

### pattern_ctl

`0801FAC4`–`08020278`. Pattern control observer, session patch write.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801FAC4` | `pattern_ctl_on_cell` | 1860 | pattern copy/clear: memcpy/memset of 0x6c-byte pattern records, vt_pattern_ctl slot 1 |
| `08020208` | `pattern_ctl_on_cell_thunk` | 8 | this-4 adjust |
| `08020210` | `session_ctl_patch_write` | 94 | session control: write a patch into the session |
| `0802026E` | `session_ctl_patch_write_thunk` | 10 | this-4 adjust, vtable 0x08027084 |

### sysex

`08020278`–`08020B04`. SysEx reply, dispatcher, MIDI tx queue, SysEx rx delivery.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08020278` | `sysex_reply_init` | 40 | writes F0 00 20 29 01 .. F7 framing and length |
| `080202A0` | `sysex_dispatch` | 1952 | app SysEx dispatcher |
| `08020A40` | `midi_data_len` | 40 | data bytes after a status byte (0, 1 or 2) |
| `08020A68` | `midi_tx_queue_msg` | 130 | queues a short message; USB adds the cable/CIN byte |
| `08020AEA` | `midi_rx_sysex_deliver` | 26 | calls the port's SysEx handler, resets the buffer |

### midi_tx

`08020B04`–`08020EA0`. MIDI tx pump, merge stage ctor, step LEDs, mode view helpers.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08020B04` | `midi_tx_clear_busy` | 44 | clears port busy bit 0x20002ec5 when its queue is empty |
| `08020B30` | `midi_tx_pump` | 224 | main loop: moves a port's tx ring into USB packets |
| `08020C10` | `midi_tx_sysex` | 288 | port mask bit 0 USB, bit 1 DIN; mode 1 queue, 2 stream |
| `08020D30` | `midi_tx_short` | 76 | (mask, status, d1, d2): bit0 USB when configured, bit1 DIN |
| `08020D7C` | `merge_stage_new` | 44 | merge stage constructor |
| `08020DA8` | `step_leds_render` | 104 | per-step LED state for a track range |
| `08020E10` | `led_state_reset` | 44 | reset LED state buffers (0xff fill, zero) |
| `08020E3C` | `mode_view_has_override` | 38 | mode view: does the selected child have an override |
| `08020E62` | `mode_view_target` | 62 | mode view: target view for an event (override or source) |

### player_ctors

`08020EA0`–`08021128`. Track player and merger constructors.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08020EA0` | `player_new` | 256 | track player constructor |
| `08020FA0` | `merger_new` | 332 | merger constructor |
| `080210EC` | `cells_fill_from_source` | 60 | copies source vtable+4 values into a cell's fields |

### recorder_ctor

`08021128`–`0802129C`. Recorder constructor, player note offs/reset.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08021128` | `recorder_new` | 204 | recorder constructor |
| `080211F4` | `player_fire_note_offs` | 58 | player: send note offs for all held notes |
| `0802122E` | `player_reset` | 58 | fires pending note offs, clears note arrays |
| `08021268` | `recorder_touch_pattern` | 52 | recorder: mark the pattern as touched |

### synth_updates

`0802129C`–`0802284C`. Pattern chain LEDs, recorder steps, clock start, synth param update table, synth note on, ui_root release.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0802129C` | `patterns_view_chain_leds` | 152 | marks pads inside/outside the chain range |
| `08021334` | `recorder_write_step` | 180 | recorder: write one step |
| `080213E8` | `clock_begin` | 212 | 0: internal restart; 1/2: set run state, arm timer |
| `080214BC` | `track_patterns_new` | 640 | track patterns constructor |
| `0802173C` | `clock_at_step_boundary` | 84 | tick divisible by 2 x ticks_per_step of track and global rate |
| `08021790` | `clock_first_tick` | 180 | clock: first tick after start |
| `08021844` | `recorder_step_position` | 92 | recorder: step position for the current tick |
| `080218A0` | `pitch_depth_curve` | 74 | signed 8-bit depth to pitch offset |
| `080218EA` | `synth_update_nop` | 2 | synth update table: no-op entry |
| `080218EC` | `synth_release_start` | 64 | release time from env_release (param 0x23) |
| `0802192C` | `synth_update_modes` | 204 | osc waveforms, filter shape/slope/bypass, param 0x4c |
| `080219F8` | `synth_update_distortion` | 84 | dist amount/type and its 4 mod depths |
| `08021A4C` | `synth_update_mixer` | 404 | params 0x13..0x19 |
| `08021BE0` | `synth_voice_gate` | 196 | retrigger/legato decision on note on/off |
| `08021CA4` | `synth_update_envelope` | 228 | ADSR, retrigger, amp/aux mod depths |
| `08021D88` | `synth_update_lfo` | 120 | LFO params 0x24..0x28, amp/aux depths |
| `08021E00` | `synth_update_resonance` | 68 | synth update: resonance |
| `08021E44` | `synth_update_cutoff` | 196 | cutoff, key tracking, 4 cutoff mod depths |
| `08021F08` | `synth_update_osc2` | 416 | synth update: osc 2 |
| `080220A8` | `synth_update_osc1` | 388 | synth update: osc 1 |
| `0802222C` | `synth_update_glide` | 96 | synth update: glide |
| `0802228C` | `button_feature_enabled` | 32 | maps a button through a table to feature_bit |
| `080222AC` | `synth_mod_sources_init` | 100 | fills the mod-source pointer table |
| `08022310` | `synth_engine_reset` | 388 | runs 10 init callbacks, clears engine state |
| `08022494` | `synth_note_off` | 552 | clears the held-note bit, last-note priority, pair of synth_note_on |
| `080226BC` | `synth_note_on` | 224 | synth engine: note on |
| `0802279C` | `synth_clear_slots` | 28 | clears 3 x 16 word arrays at 0x20002f3c |
| `080227B8` | `ui_root_release_pad` | 52 | sends pad up for a held pad bit (+0xc4) |
| `080227EC` | `ui_root_release_all_pads` | 22 | ui_root: release all pads |
| `08022802` | `ui_root_flush_bit3` | 36 | ui_root: flush the deferred bit-3 redraw |
| `08022826` | `ui_root_flush_bit2` | 38 | ui_root: flush the deferred bit-2 redraw |

### ui_stack

`0802284C`–`080229F8`. View stack push, feature bits, mono LED compose.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0802284C` | `ui_view_push` | 116 | view code: track*7 + subview, or n + 0x13 |
| `080228C0` | `feature_bit` | 48 | bit n of a byte bitmap (0x20000840) |
| `080228F0` | `param_send_centre` | 24 | sends 0x40 for the view's param id |
| `08022908` | `mono_leds_compose` | 212 | gathers the views' mono LED buffers through table 0x0802a0be |
| `080229DC` | `glide_cell_write` | 28 | write the glide cell |

### keyboard_step_ui

`080229F8`–`08022ED8`. Keyboard and step view helpers, ui_root button release.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080229F8` | `step_view_clear_hold` | 32 | clears the hold flag cell and a UI global |
| `08022A18` | `keyboard_view_mark_notes` | 96 | marks held notes of 3 bitsets on the keyboard LEDs |
| `08022A78` | `keyboard_pad_to_note` | 48 | pad + (0x45 - octave cell) * 8 * (1 or 2) |
| `08022AA8` | `patch_view_end_preview` | 36 | patch view: end the patch preview |
| `08022ACC` | `pattern_pad_bit_set` | 168 | sets/clears a pattern-pad bit and recolours it |
| `08022B74` | `cell_flag_inverted` | 16 | read a flag cell inverted |
| `08022B84` | `keyboard_view_check_root` | 48 | keyboard view: is the note in the scale root list |
| `08022BB4` | `ui_root_button_release` | 572 | button up: clears latched flags, pops views |
| `08022DF0` | `ui_root_release_all_buttons` | 52 | ui_root: release all buttons |
| `08022E24` | `step_view_flag_hold` | 38 | step view: hold flag on the pad |
| `08022E4A` | `step_view_flag_4` | 34 | step view: flag 4 on the pad |
| `08022E6C` | `step_view_flag_7` | 34 | step view: flag 7 on the pad |
| `08022E8E` | `step_view_flag_6` | 74 | step view: flag 6 on the pad |

### recorder

`08022ED8`–`08023324`. Step edit, recorder note placement and lanes, cell read, tempo fit.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08022ED8` | `step_view_edit_begin` | 260 | step press: arms the step-edit state |
| `08022FDC` | `patch_view_set_page` | 40 | patch view: set the page |
| `08023004` | `keyboard_view_note_cell` | 62 | writes the note into the note cell (default gate 6, vel 0x60) |
| `08023042` | `recorder_note_pending` | 86 | recorder: is a note pending |
| `08023098` | `recorder_write_patch_change` | 18 | recorder: write a patch change into the lane |
| `080230AA` | `recorder_lane_ptr` | 18 | recorder: pointer to an automation lane |
| `080230BC` | `cell_deferred_notify` | 6 | applies the pending change mask and notifies subscribers |
| `080230C2` | `recorder_place_all` | 140 | recorder: place all pending notes |
| `0802314E` | `recorder_place_note` | 106 | recorder: place one note |
| `080231B8` | `recorder_note_release` | 58 | recorder: release a note |
| `080231F2` | `cell_field1_ptr` | 6 | pointer to field 1 of a cell's current buffer |
| `080231F8` | `cell_read` | 16 | read a cell's current buffer |
| `08023208` | `recorder_note_on` | 52 | recorder: note on |
| `0802323C` | `lane_clear` | 42 | clear an automation lane |
| `08023266` | `lane_write` | 38 | write an automation lane value |
| `0802328C` | `clock_tempo_fit` | 122 | fit a tempo from tap/clock timestamps |
| `08023306` | `clock_timestamp_push` | 30 | push a clock timestamp |

### clock_timestamps

`08023324`–`08023370`. Clock timestamp ring constructor.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08023324` | `clock_timestamps_new` | 56 | n-entry timestamp buffer, median index (n-1)/2 |
| `0802335C` | `cell_read_other` | 20 | read a cell's other buffer |

### players

`08023370`–`08023A78`. Record hub, track player observer, mod-seq output.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08023370` | `rec_hub_param` | 164 | record hub: param change |
| `08023414` | `rec_hub_param_thunk` | 8 | this-adjusting entry of `rec_hub_param` |
| `0802341C` | `rec_hub_note_off` | 64 | record hub: note off |
| `0802345C` | `rec_hub_note_on` | 68 | record hub: note on |
| `080234A0` | `recorder_sync_rate` | 12 | recorder: sync rate |
| `080234AC` | `player_on_cell` | 1260 | track player: cell observer, plays steps |
| `08023998` | `modseq_output` | 224 | mod-seq output: step value, crossfaded per tick when smooth is on |

### soft_timers

`08024070`–`080240B0`. Main-loop soft timers.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08024070` | `soft_timers_run` | 64 | sequencer.md Time base |

### nor_regs

`080240B0`–`08024218`. NOR flash register reads.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080240B0` | `nor_sector_addr` | 16 | n << device shift (+0x28) |
| `080240C0` | `nor_read_otp` | 68 | Micron N25Q command 0x4B |
| `08024104` | `nor_read_flag_status` | 42 | command 0x70 |
| `0802412E` | `nor_read_evcr` | 42 | command 0x65, enhanced volatile config |
| `08024158` | `nor_read_vcr` | 42 | command 0x85, volatile config |
| `08024182` | `nor_read_nvcr` | 44 | command 0xB5, 2 bytes |
| `080241AE` | `nor_clear_flag_status` | 38 | command 0x50 |
| `080241D4` | `nor_read_status` | 42 | command 0x05 |
| `080241FE` | `nor_busy` | 26 | status bit 0 (WIP) |

### nor_ctl

`08024218`–`0802434C`. NOR wait/write-enable/unprotect/ID.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08024218` | `nor_wait_ready` | 56 | polls nor_busy, timeout -> 10 |
| `08024250` | `nor_write_status` | 56 | command 0x01 |
| `08024288` | `nor_unprotect` | 12 | nor_write_status(0) |
| `08024294` | `nor_read_id` | 68 | command 0x9F, 3 bytes |
| `080242D8` | `nor_write_disable` | 60 | command 0x04, waits for WEL clear |
| `08024314` | `nor_write_enable` | 56 | command 0x06, waits for WEL set |

### nor_fsm_ring

`0802434C`–`08024690`. NOR config writes, a small FSM, SPI device transfer, ring buffers.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0802434C` | `nor_set_block_protect` | 116 | BP0-3 and TB bits from an address/length |
| `080243C0` | `nor_write_nvcr` | 44 | command 0xB1 |
| `080243EC` | `nor_write_evcr` | 46 | command 0x61 |
| `0802441A` | `nor_write_vcr` | 46 | command 0x81 |
| `08024448` | `nor_program_otp` | 168 | command 0x42 |
| `080244F0` | `fsm_on_event` | 54 | table of 12-byte {event, next state, fn, arg} |
| `08024526` | `fsm_step` | 62 | matches {state, event}, moves to next state, calls fn |
| `08024564` | `spi_dev_transfer` | 32 | copies the tx buffer, then spi_dma_transfer |
| `08024584` | `gpio_init_output_high` | 44 | configure a GPIO pin as output, driven high |
| `080245B0` | `ring_free` | 42 | u8 ring buffer: buf, size +4, write +6, read +8 |
| `080245DA` | `ring_put` | 44 | ring buffer: put a byte |
| `08024606` | `ring_write` | 36 | ring_put for each byte |
| `0802462A` | `ring_count` | 16 | ring buffer: byte count |
| `0802463A` | `ring_get` | 48 | ring buffer: get a byte |
| `0802466A` | `put_addr_be` | 38 | 24- or 32-bit address after the command byte |

### nor_io_pins

`08024690`–`08024ADC`. NOR erase/program/read, pin groups, bootloader handover, MIDI rx byte.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08024690` | `nor_erase_subsector` | 120 | command 0x20, checks flag status 0x22 |
| `08024708` | `nor_erase_sector` | 120 | command 0xD8 |
| `08024780` | `nor_program` | 146 | page program with the given command byte |
| `08024812` | `nor_program_mode` | 162 | mode -> command 0x02/0xA2/0x32 |
| `080248B4` | `nor_read` | 108 | mode -> command 0x03/0x0B/0x3B/0x6B |
| `08024920` | `pin_write` | 32 | {GPIO, mask, active level} via BSRR |
| `08024940` | `bootloader_handover` | 140 | bootloader handover (flag 2, reset) |
| `080249CC` | `pin_group_write` | 48 | writes table pattern n to a pin group via BSRR |
| `080249FC` | `midi_rx_byte` | 224 | serial MIDI byte: fixed-length/SysEx collection, running status |

### calibration_dac

`08024ADC`–`080256F4`. Calibration steps, SPI DAC, osc CV, synth param get.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08024ADC` | `calib_finish_01` | 20 | selftest table 0x0802941c entry |
| `08024AF0` | `calib_finish_00` | 44 | selftest table 0x0802941c entry |
| `08024B1C` | `calib_setup_41` | 28 | selftest table 0x0802941c entry |
| `08024B38` | `calib_setup_40` | 28 | selftest table 0x0802941c entry |
| `08024B54` | `calib_setup_39` | 28 | selftest table 0x0802941c entry |
| `08024B70` | `calib_setup_38` | 28 | selftest table 0x0802941c entry |
| `08024B8C` | `calib_setup_37` | 28 | selftest table 0x0802941c entry |
| `08024BA8` | `calib_setup_36` | 28 | selftest table 0x0802941c entry |
| `08024BC4` | `calib_setup_35` | 28 | selftest table 0x0802941c entry |
| `08024BE0` | `calib_setup_34` | 28 | selftest table 0x0802941c entry |
| `08024BFC` | `calib_setup_33` | 48 | selftest table 0x0802941c entry |
| `08024C2C` | `calib_setup_32` | 44 | selftest table 0x0802941c entry |
| `08024C58` | `calib_setup_29` | 32 | selftest table 0x0802941c entry |
| `08024C78` | `calib_setup_31` | 32 | selftest table 0x0802941c entry |
| `08024C98` | `calib_setup_30` | 32 | selftest table 0x0802941c entry |
| `08024CB8` | `calib_setup_26` | 28 | selftest table 0x0802941c entry |
| `08024CD4` | `calib_setup_28` | 28 | selftest table 0x0802941c entry |
| `08024CF0` | `calib_setup_27` | 28 | selftest table 0x0802941c entry |
| `08024D0C` | `calib_setup_23` | 28 | selftest table 0x0802941c entry |
| `08024D28` | `calib_setup_25` | 28 | selftest table 0x0802941c entry |
| `08024D44` | `calib_setup_24` | 28 | selftest table 0x0802941c entry |
| `08024D60` | `calib_setup_22` | 2 | selftest table 0x0802941c entry |
| `08024D62` | `calib_setup_23_body` | 50 | calib_setup_23 is a 2-byte stub that falls into it |
| `08024D94` | `calib_setup_21` | 56 | selftest table 0x0802941c entry |
| `08024DCC` | `calib_setup_17` | 48 | selftest table 0x0802941c entry |
| `08024DFC` | `calib_setup_16` | 44 | selftest table 0x0802941c entry |
| `08024E28` | `calib_setup_15` | 44 | selftest table 0x0802941c entry |
| `08024E54` | `calib_setup_14` | 52 | selftest table 0x0802941c entry |
| `08024E88` | `calib_setup_13` | 56 | selftest table 0x0802941c entry |
| `08024EC0` | `calib_setup_12` | 2 | selftest table 0x0802941c entry |
| `08024EC2` | `calib_setup_12_body` | 46 | calib_setup_12 falls into it |
| `08024EF0` | `calib_setup_11` | 52 | selftest table 0x0802941c entry |
| `08024F24` | `calib_setup_10` | 52 | selftest table 0x0802941c entry |
| `08024F58` | `calib_setup_09` | 52 | selftest table 0x0802941c entry |
| `08024F8C` | `calib_setup_08` | 52 | selftest table 0x0802941c entry |
| `08024FC0` | `calib_setup_07` | 52 | selftest table 0x0802941c entry |
| `08024FF4` | `calib_setup_06` | 44 | selftest table 0x0802941c entry |
| `08025020` | `calib_setup_05` | 44 | selftest table 0x0802941c entry |
| `0802504C` | `calib_setup_04` | 44 | selftest table 0x0802941c entry |
| `08025078` | `calib_setup_03` | 44 | selftest table 0x0802941c entry |
| `080250A4` | `calib_setup_02` | 44 | selftest table 0x0802941c entry |
| `080250D0` | `calib_setup_01` | 32 | selftest table 0x0802941c entry |
| `080250F0` | `calib_setup_00` | 40 | selftest table 0x0802941c entry |
| `08025118` | `calib_step_retrigger` | 96 | entries 19-21: toggles note 0x3c on/off every 20/5 ticks, then calib_step_run |
| `08025178` | `calib_retrigger_body` | 40 | shared tail of calib_setup_19..21: note 0x3c off, gate counters 0x3c |
| `080251A0` | `calib_setup_20` | 36 | selftest table 0x0802941c entry |
| `080251C4` | `calib_setup_19` | 36 | selftest table 0x0802941c entry |
| `080251E8` | `calib_setup_18` | 36 | selftest table 0x0802941c entry |
| `0802520C` | `osc_glide_step` | 84 | moves a 16.16 pitch value 0x8000 per call towards its target |
| `08025260` | `spi_dac_send` | 44 | latch pin low, sends 3 words over SPI DMA |
| `0802528C` | `spi_dac_write` | 24 | 2-byte word into the spi_dac buffer 0x20000970, slot from bit 7 |
| `080252A4` | `spi_dac_set_ctrl` | 48 | sets bits 7..2 of the control byte |
| `080252D4` | `osc_pitch_cv_update` | 840 | pitch to DAC/TIM1/TIM3 values, clamped -0x180000..0x850000 |
| `0802561C` | `mod_depth_scale` | 48 | signed depth applied to a 7-bit value, centre 0x7f00 |
| `0802564C` | `sine_lookup` | 64 | interpolated quarter-wave table 0x0802a5e8 |
| `0802568C` | `synth_param_get` | 64 | param id: live patch 0x200016a2, ids >= 0x4c from a second table, else a callback |
| `080256CC` | `for_each_channel` | 40 | calls vtable+4 for 0..15 or 16..31 |

### global_ctors

`080256F4`–`08025928`. `_GLOBAL__sub_I` (static constructors), mode view source/rebind.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080256F4` | `_GLOBAL__sub_I` | 184 | the one .init_array constructor: defaults at 0x20000b30/0x20000b44 |
| `080257AC` | `mode_view_source` | 64 | cell pointer for (index, mode 0..6) |
| `080257EC` | `mode_view_rebind` | 178 | for_each_channel unbind/rebind when the source changes |
| `0802589E` | `mode_view_set` | 138 | sets index +0x74 for mode +0x75, returns index<<8 \| mode<<16 |

### view_ctors

`08025928`–`08025E60`. UI view constructors.

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025928` | `osc_view_ctor` | 300 | constructor |
| `08025A54` | `octave_view_ctor` | 76 | constructor |
| `08025AA0` | `keyboard_view_ctor` | 220 | constructor |
| `08025B7C` | `glide_view_ctor` | 152 | constructor |
| `08025C14` | `pattern_settings_ctor` | 260 | constructor |
| `08025D18` | `step_view_ctor` | 328 | constructor |

## Runtime and libraries

C runtime, startup, IRQ handlers and the newlib-nano / libstdc++ objects (`docs/source-build.md`, `docs/firmware-map.md` *Runtime libraries*).

### crtbegin

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0800C1D0` | `__do_global_dtors_aux` | 36 | crtstuff destructor runner, once (done flag) |
| `0800C1F4` | `frame_dummy` | 60 | crtstuff frame_dummy: registers frame info when present |

### syscalls

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0801EEAC` | `_read` | 4 | newlib syscall stub |
| `0801EEB0` | `_fstat` | 12 | newlib syscall stub |
| `0801EEBC` | `_sbrk` | 44 | newlib syscall stub |
| `0801EEE8` | `_write` | 4 | returns the length: printf output is dropped |
| `0801EEEC` | `_lseek` | 4 | newlib syscall stub |
| `0801EEF0` | `_isatty` | 4 | newlib syscall stub |
| `0801EEF4` | `_close` | 8 | newlib syscall stub |

### stm32f4xx_it

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08023A78` | `EXTI15_10_IRQHandler` | 120 | EXTI12 edge: calibration frequency measurement against TIM9, smoothed 7/8 |
| `08023AF0` | `DMA1_Stream5_IRQHandler` | 40 | DMA1 Stream5 complete: clear the stream's busy flag |
| `08023B18` | `DMA1_Stream2_IRQHandler` | 128 | DMA1 Stream2 complete: spi_dac chip-select sequence, next 2-byte word |
| `08023B98` | `TIM6_DAC_IRQHandler` | 272 | TIM6: steps the multiplexer pin group sequence (DAC sample and hold) |
| `08023CA8` | `DMA2_Stream4_IRQHandler` | 144 | DMA2 Stream4 complete: copy a frame, next pin group, restart TIM11 |
| `08023D38` | `TIM1_TRG_COM_TIM11_IRQHandler` | 80 | TIM11 update: start the pending DMA transfer |
| `08023D88` | `TIM1_UP_TIM10_IRQHandler` | 440 | TIM10 update: LED frame scan, next row, start DMA |
| `08023F40` | `DMA1_Stream4_IRQHandler` | 72 | DMA1 Stream4 complete: LED scan, re-arm TIM10 |
| `08023F88` | `DMA1_Stream3_IRQHandler` | 88 | DMA1 Stream3 complete: copy the input scan buffer |
| `08023FE0` | `USART1_IRQHandler` | 2 | DIN MIDI |
| `08023FE2` | `USART1_IRQHandler_body` | 90 | body of `USART1_IRQHandler`: RXNE into the rx ring, TXE from the tx ring |
| `0802403C` | `SysTick_Handler` | 36 | SysTick: 1 kHz tick counter |
| `08024060` | `PendSV_Handler` | 2 | PendSV: unused |
| `08024062` | `DebugMon_Handler` | 2 | DebugMon: unused |
| `08024064` | `SVC_Handler` | 2 | SVC: unused |
| `08024066` | `UsageFault_Handler` | 2 | UsageFault: b . |
| `08024068` | `BusFault_Handler` | 2 | BusFault: b . |
| `0802406A` | `MemManage_Handler` | 2 | MemManage: b . |
| `0802406C` | `HardFault_Handler` | 2 | HardFault: b . |
| `0802406E` | `NMI_Handler` | 2 | NMI: b . |

### startup

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025E60` | `Reset_Handler` | 72 | reset: .data copy, .bss zero, SystemInit, __libc_init_array, main |
| `08025EA8` | `Default_Handler` | 4 | 66 vectors, b . |

### libstdcxx_new_handler

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025EAC` | `get_new_handler` | 16 | libstdc++ |

### libstdcxx_new_opv

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025EBC` | `operator_new_array` | 4 | libstdc++ |

### libstdcxx_new_op

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025EC0` | `operator_new` | 32 | libstdc++ |

### libc_init

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025EE0` | `__libc_init_array` | 76 | newlib-nano |

### libc_malloc

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025F2C` | `malloc` | 16 | newlib-nano |

### libc_memset

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025F3C` | `memset` | 16 | newlib-nano |

### libc_nano_freer

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025F4C` | `_free_r` | 140 | newlib-nano |

### libc_nano_mallocr

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08025FD8` | `_malloc_r` | 168 | newlib-nano |

### libc_sbrkr

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026080` | `_sbrk_r` | 32 | newlib-nano |

### libc_writer

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080260A0` | `_write_r` | 36 | newlib-nano |

### libc_closer

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080260C4` | `_close_r` | 32 | newlib-nano |

### libc_findfp

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080260E4` | `_cleanup_r` | 12 | newlib-nano |
| `080260F0` | `std` | 72 | newlib-nano |
| `08026138` | `__sfmoreglue` | 44 | newlib-nano |
| `08026164` | `__sinit` | 96 | newlib-nano |
| `080261C4` | `__sfp` | 120 | newlib-nano |

### libc_fwalk

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0802623C` | `_fwalk_reent` | 60 | newlib-nano |

### libc_stdio

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026278` | `__sread` | 34 | newlib-nano |
| `0802629A` | `__swrite` | 56 | newlib-nano |
| `080262D2` | `__sseek` | 36 | newlib-nano |
| `080262F6` | `__sclose` | 10 | newlib-nano |

### libc_wbuf

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026300` | `__swbuf_r` | 168 | newlib-nano |

### libc_wsetup

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080263A8` | `__swsetup_r` | 220 | newlib-nano |

### libc_fflush

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026484` | `__sflush_r` | 2 | newlib-nano |
| `08026486` | `__sflush_r_body` | 270 | body of `__sflush_r` (after its first instruction) |
| `08026594` | `_fflush_r` | 84 | newlib-nano |

### libc_lseekr

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080265E8` | `_lseek_r` | 36 | newlib-nano |

### libc_makebuf

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0802660C` | `__swhatbuf_r` | 72 | newlib-nano |
| `08026654` | `__smakebuf_r` | 120 | newlib-nano |

### libc_readr

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080266CC` | `_read_r` | 36 | newlib-nano |

### libc_fstatr

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080266F0` | `_fstat_r` | 36 | newlib-nano |

### libc_isattyr

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026714` | `_isatty_r` | 32 | newlib-nano |

### libc_memcmp

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026734` | `memcmp` | 30 | newlib-nano |

### libc_memcpy

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026752` | `memcpy` | 22 | newlib-nano |

### libc_memmove

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026768` | `memmove` | 56 | newlib-nano |

### libc_printf

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080267A0` | `printf` | 4 | newlib-nano |
| `080267A4` | `printf_body` | 44 | body of `printf` (after the varargs push) |

### libc_nano_vfprintf

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `080267D0` | `__sfputc_r` | 48 | newlib-nano |
| `08026800` | `__sfputs_r` | 36 | newlib-nano |
| `08026824` | `_vfprintf_r` | 568 | newlib-nano |

### libc_nano_vfprintf_i

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `08026A5C` | `_printf_common` | 236 | newlib-nano |
| `08026B48` | `_printf_i` | 17280 | newlib-nano |

### crti

| Address | Name | Size | Purpose |
|---|---|---:|---|
| `0802AEC8` | `_init` | 12 | crti/crtn prologue for .init |
| `0802AED4` | `_fini` | 3148 | crti/crtn prologue for .fini (the vector table's tail points here in Ghidra) |
