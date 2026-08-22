# Design

The visual system for repo-oracle. Written to the
[DESIGN.md format](https://github.com/google-labs-code/design.md) so another agent can work
in this codebase without re-deriving the rules. Strategy lives in [PRODUCT.md](PRODUCT.md);
this file is only how it looks.

## Theme

Dark, and not by category reflex. The scene: an engineer or a reviewer reading unfamiliar
code at a desk, at night or in a dim room, with this window docked beside an editor that is
already dark. Switching to a light surface for one pane in that arrangement is a flash in the
face every time attention moves. That sentence forces dark, so dark it is.

Committed to as single-theme. There is no light mode and no toggle. A theme toggle for a tool
this size is a second palette to maintain and a second set of contrast measurements, bought
for a preference nobody has expressed.

## Color

Strategy: **restrained**. Tinted near-black neutrals carry the surface; one green accent
carries state and nothing else. The accent never decorates. It marks the primary action, the
current selection, the focus ring, and a citation, which is the only content in the product
that is also an affordance.

```css
--color-ink:    #0b0d10;  /* body: the deepest surface, everything sits on it */
--color-panel:  #12151a;  /* raised: sidebars, cards, inputs, the answer specimen */
--color-edge:   #232830;  /* 1px borders and dividers only, never a text color */
--color-dim:    #8b93a1;  /* secondary text, labels, metadata */
--color-gutter: #7a828f;  /* line numbers in the source panel */
--color-accent: #5fbb90;  /* primary action, selection, focus, citations */
```

Body text is `#e7eaee`, set on `body` rather than as a token, because nothing else uses it.

Measured contrast, all against the surface each is used on:

| Role | Ratio | Bar |
|---|---|---|
| body on ink | 16.12:1 | AA 4.5 |
| dim on panel | 5.91:1 | AA 4.5 |
| gutter on panel | 4.72:1 | AA 4.5 |
| accent on ink | 8.34:1 | AA 4.5 |
| ink on accent (button label) | 8.34:1 | AA 4.5 |
| citation green on its chip | 6.90:1 | AA 4.5 |

The amber pair (`amber-950/40` fill, `amber-200/80` text) marks map-tier excerpts and is the
one place a second hue appears. It is a category marker, not decoration: those excerpts are
model-written summaries rather than source, and the interface must never let the two be
confused.

Semantic states in use: default, hover, focus-visible, active/selected, disabled, loading,
error. Error is `red-950/30` on a `red-900/50` border with `red-300` text, and it is the only
red in the product.

## Typography

One family, in two roles.

- **UI text** is the system sans (`-apple-system` / `BlinkMacSystemFont` / `Segoe UI`), sized
  on a fixed rem scale, not a fluid clamp. Users view this at a consistent size in a docked
  window; a heading that shrinks in a narrow pane looks worse, not better.
- **Monospace** (`ui-monospace`, SF Mono, JetBrains Mono, Menlo) is not stylistic. It is used
  for exactly the things that are code or address code: file paths, line numbers, citations,
  code blocks, the ingest log, and the wordmark. If it is monospace, it is a machine fact.

Scale, tight by product convention: `0.7rem` metadata, `0.72rem` source lines, `0.82rem`
specimen, `0.85rem` labels, `0.92rem` answer body, `1.25rem` the single h1 in the empty state.
No display sizes anywhere; there is no hero to fill.

Answer prose runs at `line-height: 1.65` inside a `max-w-2xl` column, which lands in the
65–75ch band. Source code is deliberately outside that rule and runs as wide as it needs to,
scrolling horizontally inside its own pane.

## Layout

A three-pane shell, fixed roles left to right: repositories, conversation, source.

- **Left, 16rem**: ingest form and indexed repositories. Fixed width; it holds short labels
  and never needs to grow.
- **Centre, fluid**: the transcript, capped at `max-w-2xl` and centred, with a sticky
  composer pinned to the bottom edge.
- **Right, 30rem**: the cited source. Static column at `lg` and above. Below `lg` it becomes
  a fixed slide-over that appears only when a citation is opened, because at that width a
  static third column would leave nothing for the answer.

Flexbox throughout; there is no 2D grid in this interface, so there is no reason to reach for
one. Borders (`--color-edge`) separate the panes rather than gaps or shadows: a 1px line is
the quietest possible boundary and this layout wants boundaries, not cards.

Cards appear exactly once, for a repository in the list, where the affordance is genuinely
"a discrete selectable object". Nothing else is carded and nothing is nested.

## Components

Every interactive element carries default, hover, focus-visible, active, and where it applies
disabled and loading.

- **Citation chip** (`.cite`): monospace, accent text on a dark green fill, 1px accent-tinted
  border, 5px radius. Rendered as a real `<button>` with an `aria-label` reading "Open
  `<path>` at line `<n>`". It is the most important control in the product and the only one
  that appears inside prose.
- **Repository card**: a `div` with `role="button"`, `tabIndex`, `aria-pressed`, and a key
  handler, because it contains its own delete button and a button inside a button is invalid.
  Selected state is an accent border at 60% plus a 10% accent fill.
- **Buttons**: 8px radius, accent fill with ink text for the primary action, bordered panel
  fill for secondary. Disabled drops to 40% opacity.
- **Inputs**: panel fill, edge border, accent border on focus, plus the global focus ring.
- **Source viewer**: monospace lines, a 3rem gutter of line numbers, the cited line and one
  line either side tinted `emerald-500/10`, the cited line itself in `emerald-200`.
- **Sources disclosure**: a native `<details>`, collapsed by default, holding one chip per
  retrieved excerpt with tier, fusion source, and score in the title attribute.

Loading is stated, never spun: "retrieving…" in the transcript, and the ingest log streams
real phases (clone, chunk, embed, map) as they happen.

## Motion

Motion is state feedback only. `transition-colors` on interactive elements at Tailwind's
default 150ms, and a smooth scroll to the newest turn. There is no entrance animation, no
staggered reveal, and no page-load choreography, because the product loads into a task.

`prefers-reduced-motion: reduce` cuts all of it to 0.01ms. Nothing is communicated by the
movement that the end state does not already communicate, so there is no crossfade
substitute to design.

## Focus

One rule paints every focus ring: a 2px solid accent outline at 2px offset, on every
element that can hold focus, declared globally rather than per component because the citation
chips are injected as HTML by the markdown renderer and never pass through a className.

## What this system does not do

No gradients. No glassmorphism. No shadows except the one on the mobile slide-over, where it
separates a floating pane from what it covers. No icon set (two glyphs, `✕` and `↗`, and both
are text). No illustration. No brand imagery. Nothing on screen is decorative.
