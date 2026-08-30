# CrisisMesh brand

The palette is not a design decision made in a document. It is the set of tokens
the running console already uses, lifted out of `static/index.html` so that
everything else can match the thing a judge actually opens.

## Colour

| Token | Hex | What it means in the product |
|---|---|---|
| `status-safe` | `#22C55E` | accounted for |
| `status-injured` | `#EF4444` | the live incident, the header banner |
| `status-evacuated` | `#06B6D4` | movement, coordination, data accents |
| `status-unknown` | `#9CA3AF` | not yet heard from |
| `surface` | `#0A0E17` | the console ground |
| `surface-container` | `#1F2937` | raised panels |
| `on-surface` | `#DEE3E6` | text |

Two rules that carry meaning rather than taste:

**Red is reserved for the incident.** It is the banner, and it is the one filled
room in the icon. Spread across a whole graphic it stops meaning anything and
the mark becomes an alarm clipart.

**Cyan is coordination, not emergency.** It is the colour of the thing running:
routes, the reconciliation loop, movement between places. If a diagram edge
represents the system acting on its own, it is cyan.

### On light backgrounds

`#06B6D4` is tuned for a near-black ground and goes weak on white. Use
`#0E7490` instead. Everything else holds.

| Role | Dark ground | Light ground |
|---|---|---|
| Ground | `#0A0E17` | `#F7F5F1` |
| Structure, quiet | `#9CA3AF` | `#6B7280` |
| Coordination | `#06B6D4` | `#0E7490` |
| The incident | `#EF4444` | `#EF4444` |
| Text | `#DEE3E6` | `#111827` |

## The icon

A floor plan read from above: a 3x3 grid of rooms, one continuous line threading
through them, exactly one room filled. The first half of the pitch is "your
rooms, your routes", and that is the mark.

Generation prompt, dark colourway:

```
A square app icon, flat geometric vector, on a solid very dark blue-black background (#0A0E17). The subject is a floor plan read from above, heavily abstracted: a 3x3 grid of small equal squares with even gaps between them, drawn as thin outlines of consistent stroke weight with slightly rounded corners, in desaturated slate grey (#9CA3AF). One continuous unbroken line of heavier weight in bright cyan (#06B6D4) enters at the left edge, threads between and through the squares in a single confident path, and exits at the bottom edge. Exactly one of the nine squares is filled solid in warm red (#EF4444). Generous margin. Calm, architectural, precise, high contrast. Flat colour only. No text, no letters, no numbers, no gradients, no drop shadows, no glow, no neon, no medical cross, no siren, no warning triangle, no shield, no human figures, no doors, no 3D, no perspective.
```

Light colourway: the same paragraph with the background at `#F7F5F1`, the grid
at `#6B7280`, and the threading line at `#0E7490`.

Load-bearing words in that prompt:

* **continuous, unbroken.** A line that breaks turns coordination into
  fragmentation, which is the opposite claim.
* **exactly one** filled square. More than one and it reads as an alarm rather
  than an incident being coordinated.
* **no glow, no neon.** Dark-ground icons attract bloom, and bloom destroys the
  mark at 32px.
* **no text.** Generated lettering comes out malformed and there is no monogram
  here worth the risk.

Ask for 1024x1024 and check it at 32px. If the 3x3 grid closes up, drop to 2x2:
four rooms still read as a plan and the line gets room to be legible.

## What not to draw

No sirens, medical crosses, warning triangles, exclamation marks, shields or
radar sweeps. The subject includes school shootings, and a stylised alarm would
be both tasteless and wrong about what this is. It is a coordination system that
runs after a human reports something, alongside 911. It is not an alarm and it
does not detect anything.

## Where the palette has to hold

* the console, `static/index.html`, which is the source of these values
* the architecture diagram, `docs/diagram/architecture.html`
* the app icon
* the Devpost thumbnail, which is 3:2 rather than square: place the square mark
  left of centre on the ground colour and set "CrisisMesh" beside it in real
  type rather than asking a model to render the words
