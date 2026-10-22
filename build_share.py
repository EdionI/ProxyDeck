"""Build a clean Windows RAR from an allowlist, never a used app folder.

Run with a Windows Python with tkinter and PyInstaller installed.
WinRAR's Rar.exe must be installed. Output: dist/ProxyDeck_Share.rar.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tkinter

ROOT = Path(__file__).resolve().parent


def build() -> None:
    rar = shutil.which('rar') or str(
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'WinRAR' / 'Rar.exe'
    )
    if not Path(rar).is_file():
        raise SystemExit('Install WinRAR (Rar.exe) to create a real RAR archive.')
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    # A fresh directory prevents private profiles or stale build files from
    # leaking into the archive. Compile only these two reviewed sources.
    with tempfile.TemporaryDirectory(prefix='proxydeck-build-', dir=output) as temporary:
        work = Path(temporary)
        source = work / 'source'
        source.mkdir()
        for name in ('main.py', 'proxy_manager.py'):
            shutil.copyfile(ROOT / name, source / name)
        build_env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(work / 'cache'))
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--onefile', '--windowed', '--name', 'ProxyDeck',
            '--distpath', str(work / 'bin'), '--workpath', str(work / 'build'),
            '--specpath', str(work), str(source / 'main.py'),
        ], cwd=source, env=build_env, check=True)
        package = work / 'ProxyDeck'
        package.mkdir()
        shutil.copyfile(work / 'bin' / 'ProxyDeck.exe', package / 'ProxyDeck.exe')
        for name in ('README.md', 'START_ProxyDeck.bat', 'Setup_ProxyDeck.ps1'):
            shutil.copyfile(ROOT / name, package / name)
        # Never read/copy the owner's proxies.txt or proxydeck_data.
        (package / 'proxies.txt').write_text(
            '# Add your own proxies in the app. No proxies are supplied.\n', encoding='utf-8'
        )
        licenses = package / 'licenses'
        licenses.mkdir()
        python_license = Path(sys.base_prefix) / 'LICENSE.txt'
        if not python_license.is_file():
            raise RuntimeError('Python license was not found.')
        shutil.copyfile(python_license, licenses / 'Python.txt')
        tk_root = tkinter.Tk()
        tk_root.withdraw()
        try:
            # Tcl 9 embeds these resources in DLLs, accessible via its zipfs.
            for component, query in (('tcl', 'info library'), ('tk', 'set tk_library')):
                library = tk_root.tk.eval(query)
                candidates = tk_root.tk.call('glob', '-nocomplain', library + '/license*')
                if not candidates:
                    raise RuntimeError(f'{component} license was not found.')
                channel = tk_root.tk.call('open', candidates[0], 'r')
                try:
                    text = tk_root.tk.call('read', channel)
                finally:
                    tk_root.tk.call('close', channel)
                (licenses / f'{component}.txt').write_text(text, encoding='utf-8')
        finally:
            tk_root.destroy()
        files = sorted(p for p in package.rglob('*') if p.is_file())
        (package / 'SHA256SUMS.txt').write_text(''.join(
            f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(package).as_posix()}\n'
            for p in files
        ), encoding='utf-8')
        archive = work / 'ProxyDeck_Share.rar'
        subprocess.run([rar, 'a', '-ma5', '-m4', '-ep1', str(archive), 'ProxyDeck'],
                       cwd=work, check=True)
        subprocess.run([rar, 't', str(archive)], check=True)
        shutil.copyfile(archive, output / archive.name)
    print(f'Clean share archive: {output / "ProxyDeck_Share.rar"}')


if __name__ == '__main__':
    build()
