# GridPlayer IPTV

A local Flask + pywebview IPTV control app for managing live M3U channels and launching individual streams in GridPlayer.

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
- Click a channel's open button to launch that stream in GridPlayer.
- If GridPlayer is not auto-detected, set the executable path in Settings.

## Build

```bat
build_exe.bat
```

The build script creates a temporary virtual environment, installs requirements and PyInstaller, builds `GridPlayer IPTV.exe` into the project root, then removes `.build_venv`, `build`, `dist`, and the generated spec file.
