# IPTV Multi Player

A local Flask + pywebview IPTV client for importing M3U playlists and opening individual streams in GridPlayer, MPV, or VLC.

## Features

- Import local `.m3u`/`.m3u8` playlists or playlist URLs.
- Browse channels by playlist, category, favorites, search, and sortable columns.
- Choose GridPlayer, MPV, or VLC from the header before opening a stream.
- Configure external player executable paths in Settings.
- Enrich sports-style channel titles with ESPN game status, with optional API-Sports fallback.
- Check GitHub releases for updates from the About dialog.

## Run From Source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

For browser/server mode:

```powershell
.\.venv\Scripts\python.exe app.py --server --port 7555
```

## Sports Data

ESPN scoreboard data is used first and does not require an API key. The app caches ESPN responses briefly so live games can refresh frequently without hammering endpoints.

API-Sports is optional fallback data for sports or matches ESPN does not cover. The app reads `API_SPORTS_KEY` from a local `.env` file beside `app.py`, or from the Settings screen.

```env
API_SPORTS_KEY=your_api_key_here
```

Imported playlists, cached API responses, favorites, settings, and other local runtime data are stored under `data/`.

## Project Layout

- `app.py` starts the Flask server and pywebview desktop window.
- `iptv_multi_player/` contains the backend modules.
- `templates/` contains the HTML shell.
- `static/` contains frontend JavaScript and CSS.
- `requirements.txt` lists Python runtime dependencies.
