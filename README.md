# Focus Audio — site branch

Public showcase for [focus-audio](https://github.com/vbusnita/focus-audio).

This is an **orphan `site` branch**: HTML only, no plugin source. Source of truth for install remains `main`’s README.

## Enable GitHub Pages

1. Repo **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **`site`** / folder **`/` (root)**
4. Save → site at `https://vbusnita.github.io/focus-audio/`

Optional: set that URL as the repo **Homepage** (About box).

## Add your real media

See [`media/README.md`](media/README.md). Do **not** commit AI mock screenshots here — use Grok Build TUI captures, QuickTime of a session, and a real `focus-audio doctor` terminal shot.

## Local preview

```bash
git checkout site
python3 -m http.server 8080
```

Open http://127.0.0.1:8080

## Updating the site

```bash
git checkout site
# edit index.html / styles.css / media/*
git add -A && git commit -m "…"
git push origin site
```

To rebuild the branch from scratch (rare):

```bash
git checkout --orphan site-new
# copy only site files, commit, force-push as site
```
