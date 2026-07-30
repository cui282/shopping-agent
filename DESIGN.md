# Shopping Agent design system

## 1. Visual theme and atmosphere

Shopping Agent is a precise, calm, and transparent shopping research desk. It uses the density of an operations tool, the readable comparison rhythm of a commerce catalog, and a visible agent activity rail so users can understand how each recommendation was reached.

The interface is light, compact, and content-led. Product photography and live task state provide the visual interest; decorative backgrounds, oversized headings, gradients, and marketing composition are intentionally absent.

## 2. Color palette and roles

| Token | Value | Role |
| --- | --- | --- |
| `canvas` | `oklch(0.975 0.004 165)` | Application background |
| `surface` | `oklch(1 0 0)` | Main workspace and elevated items |
| `surface-muted` | `oklch(0.955 0.006 165)` | Sidebars, table headers, inactive controls |
| `ink` | `oklch(0.22 0.012 250)` | Primary text and icons |
| `ink-muted` | `oklch(0.52 0.012 250)` | Secondary text |
| `line` | `oklch(0.89 0.008 165)` | Dividers and input boundaries |
| `accent` | `oklch(0.62 0.19 30)` | Primary actions and selected state |
| `accent-hover` | `oklch(0.56 0.19 30)` | Primary action hover |
| `accent-soft` | `oklch(0.94 0.035 30)` | Selected row and recommendation marker |
| `success` | `oklch(0.58 0.13 155)` | Completed agent stage and savings |
| `warning` | `oklch(0.72 0.13 80)` | Partial data and API fallback |
| `info` | `oklch(0.59 0.13 245)` | Running agent stage and links |
| `danger` | `oklch(0.56 0.19 25)` | Error and destructive action |

Color follows a 60-30-10 visual-weight balance. Neutral surfaces carry the workspace, charcoal carries content, and coral is reserved for decisions and actions.

## 3. Typography rules

The type stack is `-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Noto Sans SC", sans-serif`. A system stack is deliberate here: it keeps dense mixed Chinese and Latin commerce data legible and native on the target platform. Inter, DM Sans, and Space Grotesk were rejected as reflex display choices.

| Level | Size | Weight | Line height | Letter spacing |
| --- | --- | --- | --- | --- |
| Page title | `24px` | `680` | `1.25` | `0` |
| Section title | `16px` | `650` | `1.4` | `0` |
| Body | `14px` | `430` | `1.72` | `0` |
| Compact label | `12px` | `600` | `1.45` | `0` |
| Price | `18px` | `700` | `1.2` | `0` |

All numeric columns use tabular figures. Chinese content is tagged with `lang="zh-CN"`; letter spacing remains zero at every scale.

## 4. Component stylings

- Buttons use a fixed `6px` radius, minimum `40px` hit area, a `transform: scale(0.96)` pressed state, and explicit focus rings. Primary buttons are coral; secondary buttons use a white surface and divider outline; icon actions are square.
- Inputs use white surfaces, a `6px` radius, a one-pixel divider, and coral focus ring. Validation appears directly below the field.
- Navigation is flush with its sidebar. Active items use a tinted background, stronger label, and compact status dot rather than a wide accent rail.
- Product recommendations are the only repeated card surface. They use a `6px` radius, product photography with a subtle inset outline, and a quiet shadow. Comparison and activity content remain table-like and unframed.
- Segmented controls represent view modes; toggles represent binary settings; familiar Lucide symbols represent upload, send, cancel, download, close, and panel controls.
- Empty, loading, disconnected, cancelled, fallback, and error states retain the same dimensions as populated states to prevent layout shift.

## 5. Layout principles

Spacing follows `4, 8, 12, 16, 24, 32px`. The desktop shell uses a `232px` session rail, a flexible workspace with a `680px` comfortable reading width, and a `320px` activity rail. Dividers establish ownership between panes; sections inside the workspace are unframed.

The query composer remains attached to the bottom of the main workspace. Recommendations use a stable three-column grid where space permits, while comparison data uses aligned rows. The first viewport always shows the current query, agent status, and at least the leading recommendation area.

## 6. Depth and elevation

Depth is primarily communicated with surface-color steps. `surface-muted` separates navigation from the white workspace. Recommendation cards use `0 1px 3px rgb(20 28 26 / 0.12), 0 8px 24px rgb(20 28 26 / 0.06)`; the sticky composer uses `0 -8px 24px rgb(20 28 26 / 0.06)`. No blur or glass effect is used.

Radius scale: `r-sm: 4px`, `r-md: 6px`, `r-lg: 8px`, `r-pill: 999px`. Cards never exceed `8px`.

## 7. Do's and don'ts

- Do expose each Think, Act, Observe, and Reflect transition as live activity.
- Do keep recommendation reasons, landed cost, ETA, source platform, and confidence scannable.
- Do show whether data came from a live provider, curated knowledge, computed rules, or the explicit sandbox fixture catalog.
- Do keep image upload optional and preserve typed query text during reconnection.
- Do use product photography as the principal visual asset.
- Do not nest cards or put entire page sections in floating containers.
- Do not use gradients, glass surfaces, decorative blobs, or purple-led palettes.
- Do not hide cancellation, API fallback, upload failure, or WebSocket reconnect states.
- Do not use rounded text pills where a familiar icon or plain status label is clearer.

## 8. Responsive behavior

At `1200px`, the activity rail becomes a user-controlled side panel. At `900px`, the session rail becomes a compact top row and workspace views switch through tabs. At `640px`, product recommendations become a single column, comparison rows become two-line records, and the composer respects safe-area insets. Every touch target stays at least `40px`; the interface is verified at `1280px`, `375px`, and `320px` widths.

## 9. Agent prompt guide

Quick colors: `canvas: oklch(0.975 0.004 165)`, `surface: oklch(1 0 0)`, `ink: oklch(0.22 0.012 250)`, `accent: oklch(0.62 0.19 30)`, `success: oklch(0.58 0.13 155)`, `line: oklch(0.89 0.008 165)`.

- Create a compact query composer on `surface`, text at `14px/1.72` weight `430`, `6px` radius, `line` boundary, and a `40px` coral send icon button with zero letter spacing.
- Create a product recommendation card on `surface`, `6px` radius, `0 1px 3px rgb(20 28 26 / 0.12)` shadow, a stable `4:3` image, `18px` weight `700` landed cost, and a coral recommendation rank marker.
- Create an activity timeline on `surface-muted`, `12px/1.45` labels, `4px` status dots using `info`, `success`, and `danger`, one-pixel dividers, and no enclosing cards.
- Create a mobile view switcher at `375px` with three equal segments, `40px` height, `6px` outer radius, `surface-muted` background, and active `surface` segment with `ink` text and zero letter spacing.
