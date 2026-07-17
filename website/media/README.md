# Media for the Focus Audio site

Real captures used by `../index.html` (See it section).

## Files

| File | Content |
|------|---------|
| `hero.webm` | Preferred: VP9 + **alpha** exterior (no black plate behind the window) |
| `hero.mp4` | Fallback: H.264, exterior painted page bg `#0a0a0c` (no alpha in MP4) |
| `hero-poster.jpg` | First-frame poster, exterior exact page bg |
| `doctor.png` | `focus-audio doctor` overall OK — exterior matches page bg |
| `hotkeys.png` | Slash-commands overlay — exterior matches page bg |

Source exports with a white canvas (`doctor.jpg`, `hotkeys.jpg`) may sit alongside the PNGs; the site uses the transparent PNGs only.

## Tips

- Prefer dark mode captures that match Grok Build.
- Crop out secrets, private paths, and API keys.
- Export window shots on a **transparent** or pure-white canvas, then convert white → alpha for soft shadows.
- Keep video under GitHub’s soft limits; host large clips on Releases or a CDN if needed.
- `og:image` in `index.html` points at `doctor.png`.

## Local preview

```bash
# from website/
python3 -m http.server 8080
# open http://127.0.0.1:8080
```
