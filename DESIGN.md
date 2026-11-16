# Shelf design

Direction: control room, amber signal. A night-time station for a media library.
Character: watchful, quiet, exact. World: instrument panels, night shifts, film
shelves in a dark room — so the surfaces are graphite, and amber means traffic.

- Background: the owner's starfield photograph, served as a 39 KB WebP, fixed behind
  everything under a graded scrim (`0.52` to `0.93`), with one faint amber wash at the
  top left.
- Surfaces: frosted glass over that photo — `rgba(40,40,48,0.44)` with a 14–20 px
  backdrop blur, a 16% white hairline and a 1 px inner top highlight. No shadows.
- Motion: six CSS-only shooting stars, 2 px streaks with a white head, 45° path,
  visible ~1.4 s each, roughly one every three seconds, behind all content. Transform
  and opacity only, disabled under `prefers-reduced-motion`.
- Status is an icon, never a word: amber arrow (downloading), pause ring (paused),
  clock (queued), green check (complete), red alert (failed).
- Text roles: `#ededef` primary, `#a3a4a9` secondary, `#8b8d95` muted (4.6:1).
- Accent: `#e8a33d` amber, used only for live throughput, active progress, the
  primary button and focus. Green marks finished, red marks failed; nothing else
  carries colour.
- Type: Google Sans (display) + Google Sans Text (interface), self-hosted woff2
  from Google's font CDN; numerals set with tabular figures. 17px body, 17px row
  names, 15px meta in `#b7b9c1`, 14px minimum anywhere.
- Licensing note: Google Sans is proprietary. Fine for a private LAN page; a public
  deployment needs a licensed substitute.
- Tap targets: 48px minimum, 52px full-width action buttons on phones, 56px Add.
- Signature: the status dot per row and the single amber live dot in the header.
  Nothing pulses. The progress fill is the only thing that moves.
- Motion: one entrance on load, progress bars tween on transform, 900 ms linear.
  No looping animation, no hover scale.
- Layout: single 920 px column, one field on top, then grouped queue
  (Downloading, Queued, Paused, Failed, Finished). Rows are objects with a
  hairline, not cards in a grid.

Refused on purpose: sparklines, stat tiles, gradient text, glass, emoji icons.

Detector: `impeccable detect` on the three source files reports zero findings.
