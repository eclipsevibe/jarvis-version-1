"""Small atomic, text-free status bridge to the GNOME desktop renderer."""
import json
import os
import time
from pathlib import Path


def runtime_directory():
    return Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'jarvis-desktop'


def requested():
    return (Path.home() / '.config/jarvis/desktop-enabled').exists()


def renderer_ready():
    try:
        stamp = json.loads((runtime_directory() / 'ready.json').read_text())
        age = time.time() - stamp['time']
        return stamp.get('version') == 1 and 0 <= age < 6
    except (OSError, ValueError, KeyError, TypeError):
        return False


class DesktopState:
    def __init__(self):
        self.path = runtime_directory() / 'state.json'

    def publish(self, mode, level, telemetry):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = {'version': 1, 'time': time.time(), 'mode': mode,
                    'level': max(0., min(1., float(level))), 'telemetry': telemetry[:200]}
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(data))
            temp.chmod(0o600)
            temp.replace(self.path)
        except OSError:
            pass  # A missing renderer must never interrupt the assistant.
