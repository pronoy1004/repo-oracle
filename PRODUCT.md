# Product

## Register

product

## Platform

web

## Users

The primary user is a hiring reviewer opening repo-oracle for the first time, for about ten
minutes, to judge how the person who built it thinks. They have never seen it before, will
not read documentation first, and will not forgive a stall. Their context is evaluative
rather than operational: they are not trying to ship anything, they are trying to work out
whether the answers on screen can be trusted.

That framing sets the bar for the first thirty seconds. Nothing in the interface may require
prior knowledge of what a two-tier index is, what the map tier means, or why an ingest takes
minutes. The job the user is trying to get done is: ask a question about a codebase they do
not know, and satisfy themselves that the answer is real.

## Product Purpose

repo-oracle answers questions about a codebase and shows the evidence for every answer. It
indexes a repository, retrieves against a question, and streams an answer where each claim
carries a `path:line` citation that opens the cited file at the cited line.

Success is a specific moment: the user reads an answer about something they did not already
know, clicks a citation, sees that the code says what the answer claimed, and stops
double-checking. The whole interface exists to make that cite-then-verify loop fast and
repeatable. A session where every answer was correct but nothing was verified is a weaker
outcome than one where a single answer was checked and believed.

## Positioning

Every claim is checkable in one click. Other tools in this category cite loosely or not at
all; here the citation is the product, and the answer and its evidence sit side by side on
every screen.

## Brand Personality

Precise, quiet, evidential. An instrument rather than an assistant. It does not persuade, it
does not perform helpfulness, and it does not have a voice of its own beyond being exact.
Confidence comes from showing its work: what was retrieved, from where, and at which lines.
Everything on screen is load-bearing, and anything that cannot justify its pixels is removed.

Linear is the reference, for one specific reason rather than a general vibe: total restraint
in color, a single accent used only to carry state, and interaction speed treated as a design
property rather than an engineering afterthought.

## Anti-references

- **A consumer AI chatbot.** No avatars, no facing bubbles, no typing dots, no sparkle icons,
  no "Ask me anything!" warmth.
- **A SaaS dashboard.** No stat tiles, no gradient hero metric, no identical card grid, no
  small tracked eyebrow above every section.
- **A generic dark IDE clone.** No fake window chrome, no traffic-light dots, no
  syntax-highlight rainbow used as decoration rather than meaning.
- **A docs site.** No wide prose column with a sticky sidebar, no rounded pastel callouts, no
  marketing gloss applied to a tool.

## Design Principles

**Evidence sits beside the claim.** The citation and the source it points at are never more
than one click and one glance apart. Any layout change that separates them is wrong,
whatever else it improves.

**Show the retrieval, don't hide it.** What the system looked at is part of the answer. A
user who can see the excerpts can tell a retrieval miss from a reasoning miss, and that is
the difference between a tool they trust and one they stop using.

**Admit the gap.** When retrieval is thin, the interface says so and says what it did find.
An interface that never signals uncertainty teaches users to distrust all of it.

**No ceremony.** Loading is honest and specific, not decorative. Motion carries state or does
not exist. The tool disappears into the task.

**Legible under stress.** The user is reading unfamiliar code, which is already cognitively
expensive. Contrast, line length, and hierarchy are budgeted for someone who is concentrating
on something other than the interface.

## Accessibility & Inclusion

WCAG 2.1 AA. Body text at 4.5:1 or better against its background, large text at 3:1, and the
same 4.5:1 for placeholder text. A visible focus ring on every interactive element, including
the citation chips and the source-panel controls. A complete keyboard path through ask, read,
cite, and dismiss. Every animation needs a `prefers-reduced-motion` alternative.

Measured, the palette holds: body 16.12:1, muted gray 5.91:1 on panel, accent 8.34:1 both
directions, citation chip 6.90:1. The failure was somewhere I had not looked, the source-panel
line numbers at 2.52:1, which matters more than most gutter styling because a line number is
what a citation addresses. Fixed by a dedicated `--color-gutter` token at 5.02:1.
