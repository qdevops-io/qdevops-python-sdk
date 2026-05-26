# Screenshots

The README references two screenshots that need to be added by hand:

| Filename            | What to capture                                                                          | Suggested source URL                                  |
| ------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `run-detail.png`    | A successful run-detail page showing circuit, backend, duration, queue wait, and counts. | `https://qdevops.io/runs/{id}` (pick a clean Bell run) |
| `bench.png`         | The public benchmark dashboard with at least 30 days of data.                            | `https://qdevops.io/bench`                            |

## Guidelines

- **Real data, not faked.** The trust this repo builds comes from showing
  the actual product. If a screenshot is staged or doctored, the rest of
  the README loses credibility.
- **Light mode unless your default UI is dark.** GitHub READMEs render
  on both light and dark themes; light-mode screenshots read on both.
  If you must commit a dark-mode version, suffix `-dark.png` and use a
  `<picture>` element in the README to swap.
- **Capture at 2× / Retina resolution**, then either commit the 2× PNG
  (≤ 500 KB) or downscale to 1× before commit. Avoid >1 MB images.
- **Crop to content.** No browser chrome, no system menu bar, no
  visible tabs. The screenshot should be the artifact, not the
  environment.
- **Redact tokens.** PATs, run ids belonging to other tenants, anything
  that looks sensitive.

## Optional extras worth adding later

- `compare.png` — `mode=compare` side-by-side diff view.
- `bench-detail.png` — drilling into one circuit's fidelity time series.
- `token-scopes.png` — the token-creation UI showing the scope picker
  (helps users self-serve the right scope for the example repos).
- `architecture-hero.svg` — exported version of the Mermaid diagram in
  the README, generated via `mmdc -i README.md -o architecture-hero.svg`.

Until these images land, the README will show broken-image icons in
those two table cells. That's intentional — better a visible TODO than
a faked screenshot.
