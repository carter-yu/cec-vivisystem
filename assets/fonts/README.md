# Fonts

- `NotoSansTC-Regular.ttf` — Noto Sans TC (Traditional Chinese + Latin subsets), SIL Open Font License 1.1.
- Source: @fontsource/noto-sans-tc (OFL). See `OFL.txt`.
- Used by `calendar_board` for reproducible PNG renders offline/CI.
- Live Mini may also resolve PingFang TC / system Noto Sans CJK TC via fallback order.

The subset omits U+2192; the renderer draws continuation arrows with strokes.
These assets ship with the repository checkout, not the Python wheel. Wheel-only
installations must provide a font explicitly or install a supported TC system font.
