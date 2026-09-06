# Astral Pins design exploration

The selected [combined design](06-recommended.png) uses the six-card layout and slate/blue palette. Its [prompt](RECOMMENDED-PROMPT.md) is saved separately. The GTK implementation now follows this direction; run `../../astral_pins.py --demo` from this directory to preview it. Earlier boards below remain exploratory.

Five image-generated boards explore a more readable GTK4 desktop monitoring app. This package changes no application code. Images use illustrative telemetry and are visual concepts; the behavior and data rules below are the implementation reference.

## Design directions

| Board | Direction | Strength | Tradeoff |
| --- | --- | --- | --- |
| [01 — Midnight Instrument](01-midnight-instrument.png) | Slate surfaces, restrained blue, aligned pin rows | Calm hierarchy and easy comparison | Logical-channel schematic repeats information; omit it in a first implementation |
| [02 — Daylight Native](02-daylight-native.png) | Light surfaces, cobalt accents, summary rail | Clear native desktop feel and readable labels | Summary rail costs space in narrow windows |
| [03 — Precision Console](03-precision-console.png) | Six pin tiles, dominant chart, amber accents | Fast scanning and strong chart hierarchy | Tiles need to reflow at smaller sizes |
| [04 — Monitoring states](04-midnight-fault-states.png) | Elevated, sustained overcurrent, possible dropout, stale readings | Explicit fault hierarchy and affected-pin identification | Requires new stale-data presentation |
| [05 — Compact layout and UI system](05-midnight-compact-and-system.png) | Compact window, components, startup and unavailable states | Covers resizing, keyboard focus and missing telemetry | Startup/retry UI is proposed new behavior |

Recommended direction: use **03's six-tile layout with 01's restrained slate/blue palette**, and switch to compact rows at small window sizes. Keep all six channels visible. Use 02 as a light-theme companion. The boards are alternatives to choose among, not five screens to add to navigation.

## Visual hierarchy and layout

1. Connection freshness and monitoring status.
2. Connector power and total current, with explicit units.
3. Six pins, always PIN 0 through PIN 5, with current, voltage and status.
4. Current history with a shared scale, threshold and stable legend.
5. GPU/coolant context, visually separate from connector readings.

Target a comfortable default near 960 × 760 logical pixels and a compact layout near 560 × 680; validate minimum sizes against actual GTK font metrics. At narrower widths replace tiles with six rows, wrap secondary telemetry, and allow vertical scrolling before clipping. These are proposed layout targets, not tested runtime dimensions.

Use a native headerbar, 8 px spacing increments, roughly 16–24 px panel padding, modest corner radii and thin borders. Prefer system sans-serif labels with tabular telemetry numerals. Avoid all-uppercase body copy, excessive shadows and decorative gradients. Keep watts distinct from amperes and preserve connector versus board-power labels.

## Behavior specification

The existing source constants and comparisons are authoritative:

| State | Existing trigger | Proposed presentation |
| --- | --- | --- |
| Within thresholds | Valid sample with no elevated pin or active fault | Quiet status badge; no claim that hardware is guaranteed safe |
| Elevated | Current at least 8.0 A | Amber value, icon and text on the affected row |
| Above alarm threshold, pending | Current at least 9.2 A for less than 5 seconds | Red affected value with explicit pending duration; distinguish from sustained alert |
| Sustained overcurrent | Current at least 9.2 A for at least 5 seconds | Persistent red banner naming the pin, measured current and duration |
| Possible dropout | Pin current below 0.2 A while total current exceeds 10.0 A | Red banner and affected-pin marker; present as a possible dropout |
| Sensor read failed | I2C read raises an error | Show age of last successful sample, replace live values with dashes, retain clearly labeled history |
| Finding sensor | Initial discovery, before first successful sample | Progress state and unavailable readings; no live or freshness claim |
| Sensor unavailable | Discovery cannot find a supported sensor | Explain unavailable state; proposed Retry and Connection details controls |
| GPU telemetry unavailable | nvidia-smi data cannot be read | Neutral unavailable state; connector readings can remain live independently |
| Coolant not detected | Optional hwmon sensor absent | Neutral Not detected, or omit the tile consistently; never show a false 0 °C |

For combined faults, list every affected pin and use the highest severity for the overall status. Elevation alone must not leave an overall Within thresholds badge. Connection loss overrides any assertion about current connector condition. Do not clear or dismiss an ongoing measured fault through a visual acknowledgement.

The existing script scans before opening its window; startup, retry, connection details and explicit stale handling require implementation work beyond CSS. An I2C fault should not erase independently valid GPU telemetry. The fault board uses dashes in some summary cells to focus on state composition; actual valid readings should remain visible.

## Chart and telemetry rules

- Preserve the existing 120-second history and one-second pin sampling. GPU telemetry currently updates every two seconds.
- Use a consistent 0–10 A comparison scale, with 8.0 A warning and 9.2 A alarm references. Mark values above the chart ceiling explicitly; do not silently imply a clipped value equals 10 A.
- Six stable identity colors: blue, green, amber, pink, violet, cyan. Apply severity to badges, outlines and values without losing channel identity.
- Graph endpoints must agree with current readings. Generated plots are illustrative and not validated datasets.
- Connector current is the sum of six currents; connector power is the sum of each voltage times its current. The normal sample is 31.00 A and 372.0 W at 12.00 V per pin.
- Keep PIN 0–5 as logical channel names. Do not turn the conceptual schematic or generated header icon into a physical connector pinout.
- Consider a future pointer/keyboard-accessible timestamp readout for exact comparison; it is a proposed enhancement, not existing functionality.

## Accessibility and component guidance

- Use text and icons alongside severity color. Keep pin labels visible beside charts.
- Verify text contrast in implemented light/dark themes; the raster boards are not a contrast certification.
- Use system font scaling, visible keyboard focus, clear accessible names and comfortable native pointer targets.
- Announce state transitions accessibly without announcing every polling update.
- Preserve decimal precision deliberately: current can retain the existing three decimals; the concept boards use two for visual exploration.
- Respect reduced motion for startup progress. Avoid pulsing alarms and continually animated background decoration.
- Generated icons and logos are exploratory; use a consistent native symbolic icon set during implementation.

## Review scope and next step

The normal screens, four fault cases, compact window, startup/unavailable panels and component examples were visually inspected. Targeted image edits addressed inconsistent fault readings, identity colors and misleading freshness labels. Small graph/text differences remain possible in raster mockups; use this specification and source constants for implementation.

Choose a visual direction, then implement the native layout and telemetry grouping first, followed by explicit freshness/startup states and chart accessibility. Validate normal, warning, pending alarm, sustained alarm, dropout and disconnected behavior using simulated samples before hardware review.

All five PNGs were generated with the built-in image generation tool. Full generation and refinement prompts are in [PROMPTS.md](PROMPTS.md).
