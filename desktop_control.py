"""Activate the desktop integration or restore the pre-integration assistant."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
UUID = 'jarvis-desktop@local'
EXTENSION = Path.home() / '.local/share/gnome-shell/extensions' / UUID
FLAG = Path.home() / '.config/jarvis/desktop-enabled'


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def activate():
    bus = run('gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
              '--object-path', '/org/freedesktop/DBus',
              '--method', 'org.freedesktop.DBus.ListNames')
    if bus.returncode:
        print('Cannot access the desktop session bus. Run jarvis desktop in a terminal\n'
              'opened on your desktop. Logging out again will not fix this access error.\n'
              + (bus.stderr.strip() or bus.stdout.strip()))
        return 1
    # Install before enabling: GNOME may require a new login to discover it.
    EXTENSION.mkdir(parents=True, exist_ok=True)
    for name in ('extension.js', 'metadata.json'):
        shutil.copy2(ROOT / 'desktop' / name, EXTENSION / name)
    check = run('gnome-extensions', 'info', UUID)
    if check.returncode:
        print('Extension files are installed. GNOME has not loaded the extension yet.\n'
              'Log out and back in, then run: jarvis desktop\n'
              'The current Jarvis interface remains active.\n'
              + check.stderr.strip())
        return 1
    enabled = run('gnome-extensions', 'enable', UUID)
    if enabled.returncode:
        print('Could not enable the desktop renderer: ' + enabled.stderr.strip())
        return 1
    import desktop_state
    for _ in range(30):
        if desktop_state.renderer_ready():
            break
        time.sleep(.2)
    else:
        run('gnome-extensions', 'disable', UUID)
        print('Renderer did not become ready; the current interface remains active.')
        return 1
    FLAG.parent.mkdir(parents=True, exist_ok=True)
    FLAG.touch()
    restarted = run('systemctl', '--user', 'restart', 'jarvis.service')
    if restarted.returncode:
        FLAG.unlink(missing_ok=True)
        run('gnome-extensions', 'disable', UUID)
        print('Jarvis could not restart: ' + restarted.stderr.strip())
        return 1
    print('Desktop Jarvis is enabled. Your shortcut opens the input. Escape hides it.\n'
          'Restore the previous setup any time with: jarvis restore')
    return 0


def restore():
    backup = ROOT / 'desktop-backup'
    manifest = json.loads((backup / 'manifest.json').read_text())
    # Validate all backup inputs before changing anything.
    for name in manifest['files']:
        if not (backup / 'files' / name).is_file():
            raise RuntimeError('Missing backup: ' + name)
    FLAG.unlink(missing_ok=True)
    disabled = run('gnome-extensions', 'disable', UUID)
    # Disable persistently as well if GNOME is unavailable (e.g. recovery TTY).
    if disabled.returncode:
        result = run('gsettings', 'get', 'org.gnome.shell', 'enabled-extensions')
        if result.returncode == 0:
            import ast
            value = result.stdout.strip().removeprefix('@as ')
            extensions = ast.literal_eval(value)
            if UUID in extensions:
                extensions.remove(UUID)
                run('gsettings', 'set', 'org.gnome.shell', 'enabled-extensions', repr(extensions))
    # Removing only this new extension ensures it cannot return at next login.
    if EXTENSION.exists():
        shutil.rmtree(EXTENSION)
    for name in manifest['files']:
        target = Path.home() / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / 'files' / name, target)
    result = run('systemctl', '--user', 'restart', 'jarvis.service')
    if result.returncode:
        print('Original files restored. Restart Jarvis in your desktop session:\n'
              'systemctl --user restart jarvis.service\n' + result.stderr.strip())
        return 1
    print('Restored the previous Jarvis interface and launcher. Shortcut and startup settings were preserved.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(restore() if sys.argv[1:] == ['restore'] else activate())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'Jarvis desktop: {exc}', file=sys.stderr)
        raise SystemExit(1)
