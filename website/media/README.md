# Media for the Focus Audio site

Drop **real** captures here. The mock PNGs were discarded on purpose.

## Suggested files

| File | Content |
|------|---------|
| `hero.png` or `session.mp4` | Grok Build TUI / QuickTime of a real session with audio playing |
| `hero-poster.png` | Optional poster frame if you use video |
| `doctor.png` | Terminal screenshot of `focus-audio doctor` (overall OK) |
| `hotkeys.png` | Optional hotkeys or in-session controls shot |

## Tips

- Prefer dark mode captures that match Grok Build.
- Crop out secrets, private paths, and API keys.
- Keep video short (15–45s) and under GitHub’s soft limits; host large clips on GitHub Releases or a CDN if needed.
- After adding files, edit `../index.html`: remove the matching `.placeholder` and uncomment the `<img>` / `<video>` blocks.
- Set `og:image` in `index.html` once `hero.png` exists.

## Local preview

```bash
# from this site branch root
python3 -m http.server 8080
# open http://127.0.0.1:8080
```
