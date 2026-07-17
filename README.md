# Focus Audio website

Static showcase (dark landing). Media placeholders only — add real Grok Build / doctor captures under `media/`.

## Preview

```bash
cd website
python3 -m http.server 8080
# http://127.0.0.1:8080
```

## GitHub Pages (later, not in this PR)

Option A — orphan **`site`** branch (repo root = site): copy or rsync these files to `site` and enable Pages on branch `site` / root.

Option B — Pages from **`main` / `website`**: needs a small workflow or Pages “folder” support; root `/docs` is the usual GitHub UI path.

Do **not** enable Pages until media is ready.
