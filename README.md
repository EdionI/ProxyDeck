# ProxyDeck

A Python desktop app for managing HTTP/HTTPS proxies in separate Google Chrome workspaces, with proxy health checks and traffic usage.

## Run from source

Requires Windows, Google Chrome, and Python 3.10+ with Tkinter. The app uses only the Python standard library; no pip dependencies are required.

From this folder, run:

```powershell
python main.py
```

Add your own proxies in the app. Local settings and browser profiles are created when needed.

## Tests

```powershell
python -m unittest test_proxy_manager.py
```

## Build a distributable

`build_share.py` builds `dist/ProxyDeck_Share.rar`. It requires PyInstaller, WinRAR's `Rar.exe`, and a Windows Python installation with Tkinter. Install PyInstaller in your build environment, then run:

```powershell
python build_share.py
```

`START_ProxyDeck.bat` and `Setup_ProxyDeck.ps1` launch the compiled distribution and expect `ProxyDeck.exe`; use `python main.py` for this source checkout. `START_HERE_Setup_ProxyDeck.bat` and `Create_Desktop_Shortcut.ps1` are legacy laptop-transfer helpers that expect extra files from a transfer bundle.

## Proxy format and local data

Use `host:port` or `host:port:username:password`, optionally ending in `|REGION`. Example with placeholders: `proxy.example:8080:YOUR_USER:YOUR_PASSWORD|HU`. SOCKS proxies are not supported for browser workspaces.

Credentials are stored as plain text in `proxies.txt`; browser profiles, settings, notes and usage are stored in `proxydeck_data/`. Both are excluded by `.gitignore`. Health checks use `https://ipwho.is/` through the selected proxy.

## Upload to Git

This source export includes no Git history, proxy credentials, browser profiles, installers, build output, or virtual environment. Upload the contents of this folder to your repository, including `.gitignore`.
