# Website

Static website (HTML + CSS, no build step), deployed on Vercel.

```
index.html          page
assets/css/         styles
assets/img/         images and icons
vercel.json         Vercel settings (static site, no framework)
```

## Run locally

```bash
python -m http.server 8000
```

Open http://127.0.0.1:8000.

## Deploy

Vercel → Project → Settings → Build and Deployment: **Root Directory** empty, Framework preset
**Other**, no build command. Every push to `main` deploys.

The earlier desktop assistant (Nova: tray app, wake word, chat) was removed from this repository;
it is still in the Git history (commit `dee18cc`).
