---
name: Olympus Connect
description: A digital darkroom for a Linux desktop: three cool-neutral greys, one cyan, and the photographs as the only source of light.
colors:
  shell: "#1b1e21"
  panel: "#232629"
  surface: "#2b2f33"
  raised: "#343a3f"
  sunken: "#191c1f"
  line: "#33383d"
  line-strong: "#3f464c"
  text: "#e9ebed"
  text-dim: "#a8b0b6"
  text-faint: "#959ea5"
  accent: "#4db8d4"
  accent-hover: "#67cbe5"
  accent-press: "#3ba3bf"
  accent-ink: "#0e1b20"
  ok: "#7cc08a"
  warn: "#d8a648"
  error: "#e4705a"
  progress-file: "#3f7f90"
typography:
  title:
    fontFamily: "platform UI sans (Qt application font; KDE Plasma default)"
    fontSize: "16px"
    fontWeight: 400
  strong:
    fontFamily: "platform UI sans (Qt application font; KDE Plasma default)"
    fontSize: "13px"
    fontWeight: 600
  body:
    fontFamily: "platform UI sans (Qt application font; KDE Plasma default)"
    fontSize: "12px"
    fontWeight: 400
    fontFeature: "tnum 1"
  small:
    fontFamily: "platform UI sans (Qt application font; KDE Plasma default)"
    fontSize: "11px"
    fontWeight: 400
    fontFeature: "tnum 1"
  label:
    fontFamily: "platform UI sans (Qt application font; KDE Plasma default)"
    fontSize: "10px"
    fontWeight: 600
    letterSpacing: "1.1px"
  caption:
    fontFamily: "platform UI sans (Qt application font; KDE Plasma default)"
    fontSize: "8pt"
    fontWeight: 400
rounded:
  xs: "2px"
  sm: "3px"
  md: "4px"
  lg: "5px"
spacing:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "12px"
components:
  button-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "5px 12px"
  button-default-hover:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.text}"
  button-default-disabled:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-faint}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.strong}"
    rounded: "{rounded.md}"
    padding: "5px 12px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
    textColor: "{colors.accent-ink}"
  button-primary-pressed:
    backgroundColor: "{colors.accent-press}"
    textColor: "{colors.accent-ink}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-dim}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "4px 8px"
  button-ghost-hover:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
  button-ghost-checked:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.accent}"
  filter-row:
    backgroundColor: "transparent"
    textColor: "{colors.text-dim}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "5px 8px"
    height: "38px"
  filter-row-checked:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
  input-text:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "5px 8px"
  select:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "4px 8px"
    height: "18px"
  header-section:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-faint}"
    typography: "{typography.label}"
    padding: "5px 6px"
  list-item:
    backgroundColor: "transparent"
    textColor: "{colors.text-dim}"
    rounded: "{rounded.md}"
    padding: "3px"
  list-item-hover:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
  list-item-selected:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
  tooltip:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "5px 7px"
  progress-track:
    backgroundColor: "{colors.sunken}"
    rounded: "{rounded.xs}"
    height: "4px"
  progress-chunk-total:
    backgroundColor: "{colors.accent}"
    rounded: "{rounded.xs}"
    height: "5px"
  progress-chunk-file:
    backgroundColor: "{colors.progress-file}"
    rounded: "{rounded.xs}"
    height: "4px"
  thumbnail-cell:
    backgroundColor: "{colors.shell}"
    textColor: "{colors.text-dim}"
    typography: "{typography.caption}"
    rounded: "{rounded.md}"
    width: "182px"
    height: "160px"
  preview-frame:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.text-faint}"
    typography: "{typography.small}"
    rounded: "{rounded.md}"
---

# Design System: Olympus Connect

## Overview

**Creative North Star: "The Digital Darkroom"**

This is a Linux desktop application (PyQt6 on KDE Plasma/Wayland), not a website. There is no HTML, no CSS, no browser, no viewport. The entire visual system lives in one Qt stylesheet plus a QPalette in `olympus_connect/gui/theme.py`, every token a module constant; icons are authored SVG rasterised through `QSvgRenderer`; motion is `QPropertyAnimation` and `QVariantAnimation`; layout is `QLayout`. Nothing here translates to or from a web token set, and nothing in it should be restated in `rem`, breakpoints, or media queries.

The room is dark because the photographs have to be the only bright thing in it. Three cool-neutral greys stack from the window shell through the panels to the interactive surfaces, with the content area sunk *below* the shell so the thumbnails sit on the darkest ground available. Depth comes entirely from that tonal stack and from one-pixel hairlines; there is not a single shadow in the build. One cyan carries selection, the primary action, and active state, and nothing else is saturated at rest. Amber, green and red appear only where they carry file-state meaning.

Density is working density, not showroom density: 38–48px filter rows, 4–12px padding steps, a 46px command bar, a single status row. Both side panels retract to a 36px column with an animated real width, which is this system's entire equivalent of a responsive breakpoint: the vertical constraint of a 1366×768 screen is answered by hiding chrome, never by reflowing a grid.

Two cosmetic residuals shipped and are recorded here as known deviations, not as rules: the status row's two progress bars sit side by side although a comment in `theme.py` records that arrangement as previously rejected ("affiancate si leggevano come una barra sola spezzata"), tolerable only because the numbers beside them carry the reading; and the rail's kind-filter rows grew to a 48px pitch to hold their two-line count. Neither is a pattern to extend.

**Key Characteristics:**
- Dark, cool-neutral, four-step tonal stack; the content area is the darkest surface
- Exactly one saturated colour at rest (cyan `#4db8d4`), used for selection, primary action, active state
- Zero shadows; depth is tonal plus 1px hairlines
- One drawn icon set, 20×20 grid, 1.9 stroke, round caps and joins, recoloured at render time
- Motion is 180ms OutCubic and only ever reports state
- Tabular figures everywhere, so numbers do not dance while progress updates

## Colors

A cool, low-chroma neutral stack with one cyan accent and three semantic signal colours.

### Primary
- **Darkroom Cyan** (`{colors.accent}`): selection fill in both views, the primary "Scarica selezionati" button, the active view-mode toggle, focus borders, checked checkboxes, the total-progress chunk. The only saturated colour on a resting screen.
- **Cyan Lifted** (`{colors.accent-hover}`) and **Cyan Pressed** (`{colors.accent-press}`): hover and press states of the primary button only.
- **Darkroom Ink** (`{colors.accent-ink}`): the near-black teal that text and icons take when they sit *on* cyan — primary button label, selected row text, selected thumbnail caption and status glyph.

### Secondary
- **Damped Cyan** (`{colors.progress-file}`): the per-file progress chunk. Deliberately one step down from the accent so the file bar reads as subordinate to the total bar.

### Tertiary (signal)
- **Present Green** (`{colors.ok}`): a file already on disk.
- **Redo Amber** (`{colors.warn}`): a file that must be fetched again; also the "Riprova" icon.
- **Failed Red** (`{colors.error}`): a failed transfer.

### Neutral
- **Shell** (`{colors.shell}`): the window ground and the plate behind each thumbnail.
- **Panel** (`{colors.panel}`): rail, inspector, top bar, status bar, table header.
- **Surface** (`{colors.surface}`): interactive elements at rest — buttons, checked filter rows, hovered list items.
- **Raised** (`{colors.raised}`): hover for buttons, tooltips, combo popups. The highest tone in the stack.
- **Sunken** (`{colors.sunken}`): the content area, text inputs, progress tracks. Below the shell on purpose.
- **Hairline** (`{colors.line}`) and **Hairline Strong** (`{colors.line-strong}`): panel edges and separators; the stronger value draws control borders.
- **Text** (`{colors.text}`), **Text Dim** (`{colors.text-dim}`), **Text Faint** (`{colors.text-faint}`): primary copy, secondary copy, and labels/metadata keys.

### Named Rules
**The One Light Source Rule.** The photographs are the brightest thing on screen. No surface, fill or label competes with them; cyan is the only saturated colour at rest and never covers more than a selection.

**The Faint-Text Ceiling Rule.** `{colors.text-faint}` is used only on `{colors.panel}`, `{colors.surface}` and `{colors.sunken}`, where it clears 4.5:1. It does **not** clear 4.5:1 on `{colors.raised}`; on raised tone, step up to `{colors.text-dim}`.

**The Earned Colour Rule.** Green, amber and red are allowed only where they report file state. A colour that merely decorates is a defect.

## Typography

**UI Font:** the platform UI sans — the Qt application font as configured by the desktop (KDE Plasma default), set at 10pt base in `tune_fonts`. There is no display face, no second family, and no bundled font.

**Character:** one family, five sizes, nothing ornamental. The hierarchy is carried by size, weight and tone, never by a change of voice. Tabular figures (`tnum`) are switched on application-wide; without them sizes and percentages shift column on every progress tick, which is the one typographic failure this interface cannot afford.

### Hierarchy
- **Title** (400, 16px): the empty-state headline in the content area.
- **Strong** (600, 13px): panel titles ("Fotocamera", "Anteprima") and the primary button label.
- **Body** (400, 12px): the default for every widget — buttons, filter rows, list text, inputs.
- **Small** (400, 11px): metadata values and faint secondary lines in the rail and inspector.
- **Label** (600, 10px, 1.1px letter-spacing, uppercase): section headings inside panels and table column headers (0.8px there). Always set in `{colors.text-faint}`.
- **Caption** (400, 8pt): the filename and state glyph row drawn inside a thumbnail cell, and the rotated panel name in a collapsed column (8pt, 1.2px absolute letter-spacing).

### Named Rules
**The Tabular Figures Rule.** Every numeric readout — sizes, rates, counts, percentages — renders with `tnum` on. A number that changes width while it updates is a bug.

**The One Family Rule.** One system UI family across the whole application. Do not introduce a display face, a monospace column, or an icon font.

## Layout

Three horizontal zones over a single status row, built from `QLayout`, not a grid system.

- **Rail** (left, 232px fixed): connection state with camera model, the kind-filter rows with per-kind count and weight, the "Mostra" filter, the destination.
- **Work area** (centre, elastic): a 46px command bar (view toggles left, selection summary, primary action right) over the thumbnail grid or detail list, on `{colors.sunken}`.
- **Inspector** (right, 348px fixed): the large preview over a label/value detail grid, then the Shot section (exposure headline in Strong 13px/600, label/value rows for the fields present). Shot data arrives after the details, so it sits below them and never pushes them down.
- **Remote mode** (third top-bar toggle, enabled only with a connected camera and no transfer): the content area becomes the live view on `{colors.sunken}`, letterboxed to the frame's real size (16:9 while recording); a click focuses there and draws a 56 px focus box (accent while pending, ok/error for the result); the review image covers the live for 2 s with the standard 180 ms fade from 0.35. The inspector, retitled «Telecomando», holds a primary **Scatta** button, a **Registra/Ferma** toggle with a red record glyph and a tabular `mm:ss` timer, and the dial mode read-only. A **Settings** section below the shoot controls: one stepper row per camera setting (label column shared with the inspector grids; `‹ value ›` with 14 px chevrons, mouse wheel, click opens the full list); a changed value is shown faint until the camera confirms it, and is sent 250 ms after the last step; rows the current mode leaves to the camera are faint with «automatico» and no arrows; the aperture list follows the lens and zoom. Library controls (filters, download) are disabled in this mode.
- **Status row** (bottom, one line only): current file, the two progress bars, rate and ETA, Annulla, and Riprova when relevant. Transfer controls live *in* the status bar; a second strip above it would make two status rows where the layout promises one.

Both side panels collapse to **36px** and expand back over 180ms. Collapsed, the panel keeps its toggle button at the top and a rotated name down the column, so what was hidden can be found again. Minimum window: 880×560.

Rhythm: panel body margins 12/4/12/12 with 10px between groups; panel header 42px tall with 12/10/8/8 margins (5/9/5/9 when collapsed); command-bar margins 10/6 with 6px spacing; control padding 5×12px (buttons), 4×8px (ghost), 5×8px (filter rows and inputs). Filter rows are 38px minimum, the "Solo non scaricati" row 32px.

Grid cells are a fixed 182×160 (thumbnail 160×120 plus 22 and 40), spaced 4px, with uniform item sizes so a 1161-file card scrolls without relayout.

### Named Rules
**The Retract, Don't Reflow Rule.** The answer to a short screen is a collapsed panel, not a rearranged layout. There are no breakpoints; the only two layout states are panel-expanded and panel-collapsed.

**The Stable Cell Rule.** A cell's size is fixed before its content arrives. `sizeHint` returns the full cell, the decoration role never returns `None` for the name column (a placeholder plate of the final thumbnail size stands in), and every thumbnail is padded onto the declared 160×120 canvas. An item that measures itself on text alone will clip the image that arrives later.

**The Declared Size Rule.** A custom-painted widget declares its own `sizeHint` (and `minimumSizeHint`). The rotated panel label occupied zero height until it did.

**The Visible Filler Rule.** Qt ignores hidden widgets in a layout, so when a panel body is hidden a visible filler takes its stretch. Without it the layout centres the collapsed header in the middle of the window.

## Elevation & Depth

**No shadows.** Not one, anywhere in the build. Depth is a four-step tonal stack — `{colors.sunken}` content area, `{colors.shell}` window and thumbnail plate, `{colors.panel}` chrome, `{colors.surface}` resting controls, `{colors.raised}` for hover, tooltips and popups — reinforced by 1px hairlines in `{colors.line}` (panel edges, separators, thumbnail plates) and `{colors.line-strong}` (control borders).

Interactivity reads as a step *up* the tonal ladder: transparent → surface → raised. Containment reads as a step *down*: the content area and input fields are darker than everything around them.

### Named Rules
**The No-Shadow Rule.** Depth is tone and hairline. If a surface needs to feel raised, move it one step up the neutral stack; do not add a shadow, glow or gradient.

**The Sunken Content Rule.** The media area is darker than the window shell. Photographs are the brightest element, so the ground beneath them must be the darkest.

## Shapes

Small, consistent radii and no decorative geometry. `4px` is the house radius (buttons, filter rows, inputs, tooltips, combo boxes, list items, the preview frame, the thumbnail plate); `5px` for the selection fill behind a grid cell and for scrollbar handles; `3px` for the 14px checkbox indicator; `2px` for the progress track and its chunk. Borders are always exactly 1px. Nothing is circular, nothing is pill-shaped, nothing is clipped to a non-rectangular silhouette.

Icons share one silhouette language: a 20×20 drawing grid, 1.9 stroke, round caps and joins, fill none, stroke colour injected at render time so one path serves normal, disabled and accent states. Nothing in the set depends on an angle finer than that stroke can hold — the open chevron was replaced by a bordered rectangle with a divider for exactly that reason.

### Named Rules
**The Drawn Icon Rule.** Every icon is SVG path data in `icons.py`. No Unicode glyphs, no emoji, no icon font: their metrics and weight betray an assembled set rather than a drawn one.

**The Logical Coordinates Rule.** `icons.pixmap` renders into `QRectF(0, 0, size, size)` — never `size * dpr`. A painter on a pixmap that already carries a `devicePixelRatio` is already scaled; passing physical coordinates draws at 2× clipped to the top-left quadrant. This was the root cause of every malformed glyph in the build and must never be "fixed" back.

**The Declared Icon Size Rule.** Every button carrying an icon calls `setIconSize` explicitly (18×18 for chrome, 14–16 in-cell, 22 in the detail list). Without it Qt rescales to its own 16px default with a coarse filter and the icon arrives disfigured.

## Components

### Buttons
- **Shape:** gently rounded (4px), 1px border in `{colors.line-strong}`.
- **Default:** surface fill, primary text, 5×12px padding; hover lifts to `{colors.raised}` with a `#4a5258` border, press drops to `{colors.sunken}`, disabled falls to panel fill with faint text and a hairline border, focus turns the border cyan with no outline.
- **Primary:** full cyan with ink-coloured 600-weight label; hover and press use the lifted and pressed cyans; disabled degrades to a plain surface button. One primary action per screen.
- **Ghost:** transparent with a transparent border and dim text, 4×8px padding; hover fills with surface and brightens the text; checked fills with surface, turns the label cyan and reveals a `{colors.line-strong}` border — this is how the active view mode is shown. Disabled ghost text is `{colors.line-strong}`.
- **Collapser:** a 26×26 borderless ghost holding the 18px panel glyph. Hovered it takes a raised fill; **collapsed** it gains a surface fill and a strong hairline border, because in a 36px column it is the only element left and must stay findable.

### Filter rows
- **Style:** full-width transparent buttons with a left-aligned 18px drawn icon, body text, 4px radius, 38px minimum height, checkable.
- **State:** hover and checked both fill with `{colors.surface}`; checked additionally promotes the label from dim to primary text.
- **Count:** the per-kind count and weight live *inside* the button as a right-aligned faint label with mouse events disabled. Placed beside the button, a third of the row fell outside the hit area and the selection fill stopped two-thirds across.

### Checkboxes
14×14 indicator, 3px radius, sunken fill, `{colors.line-strong}` border, 7px to the label. Hover turns the border cyan; checked fills cyan.

### Inputs / Fields
Sunken fill, 1px `{colors.line-strong}` border, 4px radius, 5×8px padding, cyan selection with ink text. Focus changes the border to cyan only — no glow, no ring, no size change. Combo boxes use the surface fill instead, a borderless 18px drop-down, and a raised popup with cyan selection.

### Lists and table
- **Grid:** icon-mode list on sunken ground, 4px item spacing, uniform item sizes; the custom delegate draws plate, thumbnail, filename and state glyph.
- **Detail list:** tree view without root decoration, alternating rows, uniform row heights, sortable, 4×6px item padding; name column stretches, the four data columns size to content (a fixed width truncated the date to "01/01/2020 …").
- **Header:** panel fill, faint uppercase 10px label with 0.8px tracking, hairline bottom and right edges; hover brightens the text only.
- **Selection:** cyan fill with ink text in both views.

### Scrollbars
Never the Qt default. 11px track, transparent; handle `#3c4248` with a 5px radius, 2px margin and a 28px minimum length, lifting to `#4d555c` on hover. No arrow buttons, no page background.

### Progress
4px track on sunken ground, 2px radius, text hidden. The file bar uses damped cyan, the total bar full cyan at 5px, so the total reads as the dominant quantity. **Progress is always expressed in permille** (`PROGRESS_SCALE = 1000`), never raw bytes: `QProgressBar` is 32-bit and the reference card's 23.48 GB overflows it.

### Preview frame (signature)
The inspector's large preview is the move this product is built on: the camera serves a ~1 MB screennail even for RAW, so a shot can be judged before it is downloaded. Sunken fill, 1px hairline, 4px radius, faint 11px placeholder text, 160px minimum height. Its height is fixed at **width × aspect ratio** — the photograph is width-limited in a 348px column, and giving the frame more height only adds empty frame. The image fades in from **0.35**, never from 0.

### Thumbnail cell (signature)
182×160 fixed. A `{colors.shell}` plate with a `{colors.line}` hairline and 4px radius holds the 160×120 thumbnail so bright photographs do not bleed into the panel ground; selection draws a 5px-radius cyan fill behind the whole cell and darkens the plate to `#15383f`; hover draws the same shape in surface tone. Beneath, the elided filename (middle-elided, 8pt) and, on the right, the state glyph — drawn only when the state is not "new". Thumbnails fade in from **0.4**.

### Named Rules
**The Visible Fade Rule.** Every fade starts from an already-visible opacity (0.35 preview, 0.4 thumbnail) and ends at 1.0, over 180ms with an OutCubic curve. An element that exists only because an animation ran reads as missing whenever the event loop is starved, and the defect surfaces as "the thumbnail never arrives".

**The State-Only Motion Rule.** Motion exists to report state: panel retraction, arrival of an image, interpolated progress. 150–250ms, OutCubic, nothing on launch. The product opens inside a task.

**The Real Property Rule.** An animated dimension is a real Qt property (`panelWidth`, `fadeOpacity`) interpolated by `QPropertyAnimation`/`QVariantAnimation`, never a `setFixedWidth` driven from a timer.

## Do's and Don'ts

### Do:
- **Do** keep every colour, size, metric and rule in `theme.py` as a module constant, and reference it from widgets; the stylesheet is generated from those constants.
- **Do** set the QPalette alongside the stylesheet, so native widgets, tooltips and dialogs the QSS never reaches still belong to the theme.
- **Do** render icons in logical coordinates — `QRectF(0, 0, size, size)` — and call `setIconSize` on every button that carries one.
- **Do** draw the disabled state of an icon in a chosen colour from the palette rather than letting Qt wash it out to a grey that belongs to no system.
- **Do** start every fade from a visible opacity (0.35 / 0.4) and end at 1.0.
- **Do** return a placeholder decoration of the final thumbnail size rather than `None`, and give every custom widget a `sizeHint`.
- **Do** express progress in permille with `PROGRESS_SCALE = 1000`, and format both sizes and rates on **one base, 1000** (`core/stats.py`), so size ÷ rate equals the ETA shown on screen.
- **Do** fix the preview frame at `width × aspect` and let leftover space fall below the block.
- **Do** keep a visible filler holding the stretch when a panel body is hidden.
- **Do** restyle scrollbars, headers and tooltips; Qt's defaults belong to no design system.

### Don't:
- **Don't** add a shadow, glow or gradient. Depth is the tonal stack plus 1px hairlines.
- **Don't** introduce a second saturated colour, or let cyan spread beyond selection, primary action and active state.
- **Don't** put `{colors.text-faint}` on `{colors.raised}`; it fails 4.5:1 there.
- **Don't** use Unicode glyphs, emoji, or an icon font in place of a drawn icon from `icons.py`.
- **Don't** introduce a display face, a second font family, or type sizes outside 10/11/12/13/16px.
- **Don't** render an icon into `size * dpr`; that draws at 2× clipped to the top-left quadrant.
- **Don't** feed a `QProgressBar` raw byte counts; a 23.48 GB total overflows its 32-bit range.
- **Don't** animate a dimension with a timer and repeated `setFixedWidth` when a Qt property will interpolate it.
- **Don't** add a second status strip above the status bar; the layout promises one status row.
- **Don't** import web idioms into this system — no `rem`, no breakpoints, no media queries, no flex/grid vocabulary. Layout is `QLayout` and the only responsive states are the two collapsible panels.
