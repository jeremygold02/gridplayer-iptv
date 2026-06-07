# GridPlayer IPTV

A local Flask + pywebview IPTV control app for managing live M3U channels and launching individual streams in GridPlayer, MPV, or VLC.

## Run

```powershell
python app.py
```

For browser testing or headless use:

```powershell
python app.py --server --port 7555
```

## Notes

- Import a local `.m3u`/`.m3u8` file or a playlist URL.
- Use the header dropdown to choose GridPlayer, MPV, or VLC before opening a channel.
- Click a channel's open button to launch that stream in the selected player.
- If a player is not auto-detected, set the executable path in Settings.

## API-Sports

The app can enrich sports-style channel titles with game status from API-Sports. It parses channel names, matches likely football, basketball, and MMA events, and shows whether a matched game is live, scheduled, finished, inactive, or unknown. Calls are cached in `data/sports_cache.json` and refresh about every 15 minutes while the app is open, with a 100-call daily limit per sport.

Put your API-Sports key in Settings under `API-Sports key`, or create a gitignored `.env` file beside `app.py` with:

```env
API_SPORTS_KEY=your_api_key_here
```

For the built exe, place `.env` beside `GridPlayer IPTV.exe`. The `.env` file is ignored by git and should not be committed.

## Build

```bat
build_exe.bat
```

The build script creates a temporary virtual environment, installs requirements and PyInstaller, builds `GridPlayer IPTV.exe` into the project root, then removes `.build_venv`, `build`, `dist`, and the generated spec file.
