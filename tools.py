"""Bounded desktop bridge. Commands are fixed or require an explicit UI approval."""
from __future__ import annotations

import ipaddress
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse
import urllib.request
import socket
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

import psutil


def _run(argv: list[str], timeout: int = 8) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return (p.stdout or p.stderr).strip() or f"Command exited {p.returncode}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def set_volume(mode: str, value: int = 5) -> str:
    if not shutil.which("wpctl") and not shutil.which("pactl"):
        return "No PulseAudio/PipeWire control command found."
    if shutil.which("wpctl"):
        if mode == "mute": return _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"])
        if mode == "unmute": return _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
        if mode == "set": return _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(0,min(value,100))}%"])
        return _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(1,min(value,25))}%{'+' if mode == 'up' else '-'}"])
    if mode == "mute": return _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"])
    if mode == "unmute": return _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
    if mode == "set": return _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{max(0,min(value,100))}%"])
    return _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%{'+' if mode == 'up' else '-'}"])


def set_brightness(mode: str, value: int = 5) -> str:
    if shutil.which("brightnessctl"):
        if mode == "set": return _run(["brightnessctl", "set", f"{max(0,min(value,100))}%"])
        return _run(["brightnessctl", "set", f"{max(1,min(value,25))}%{'+' if mode == 'up' else '-'}"])
    if shutil.which("light"):
        return _run(["light", "-S" if mode == "set" else ("-A" if mode == "up" else "-U"), str(value)])
    return "Install brightnessctl or light and ensure your user can access the backlight device."


def media(action: str) -> str:
    key = {"play": "play", "pause": "pause", "next": "next", "previous": "previous"}.get(action)
    if key is None: return "Unknown media action."
    if shutil.which("playerctl"):
        return _run(["playerctl", key])
    if shutil.which("gdbus"):
        import spotify_control
        try:
            result = spotify_control.call('org.mpris.MediaPlayer2.Player.' + key.title())
            return f'Spotify {key} command accepted.' if result.returncode == 0 else 'Spotify playback controls are unavailable. Open Spotify and sign in first.'
        except (OSError, subprocess.TimeoutExpired):
            return 'Spotify playback controls are unavailable.'
    return "No desktop media control utility is installed."


def weather(city: str = "") -> str:
    """Fetch a compact live weather report from wttr.in."""
    location = urllib.parse.quote(city.strip(), safe="") if city.strip() else ""
    url = "https://wttr.in/" + location + "?format=%l:+%t,+%C"
    try:
        result = subprocess.run(["curl", "-s", "--max-time", "4", url], capture_output=True,
                                text=True, timeout=4, check=False)
        report = result.stdout.strip()
        if result.returncode == 0 and report:
            return report
        return "Live weather is unavailable right now."
    except (OSError, subprocess.TimeoutExpired):
        return "Live weather is unavailable right now."


def installed_apps() -> list[tuple[str, str]]:
    found = []
    roots = [Path.home()/".local/share/applications", Path("/usr/share/applications"), Path("/var/lib/flatpak/exports/share/applications")]
    for root in roots:
        if root.exists():
            for desktop in root.glob("*.desktop"):
                try:
                    content = desktop.read_text(errors="ignore")
                    if "NoDisplay=true" in content: continue
                    name = re.search(r"(?m)^Name=(.+)$", content)
                    if name: found.append((name.group(1).strip(), desktop.stem))
                except OSError: pass
    return found


def launch_app(name: str) -> str:
    apps = installed_apps()
    if not apps: return "No desktop applications found."
    choices = [a[0] for a in apps]
    try:
        from rapidfuzz import process
        hit = process.extractOne(name, choices, score_cutoff=70)
        entry = apps[hit[2]] if hit else None
    except ImportError:
        entry = next((a for a in apps if a[0].lower() == name.lower()), None)
    if not entry: return f"No installed application matched {name!r}."
    try:
        subprocess.run(["gtk-launch", entry[1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=8, check=False, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Could not launch {entry[0]}: {exc}"
    return f"Launched {entry[0]}"


def close_app(name: str) -> str:
    # Match the process executable name only; never pass raw text to a shell.
    target = re.sub(r"[^a-zA-Z0-9_.-]", "", name).lower()
    if not target: return "Provide an application name."
    hits = [p for p in psutil.process_iter(["pid", "name"]) if (p.info.get("name") or "").lower() == target]
    if not hits: return f"No running process named {target}."
    for proc in hits: proc.terminate()
    return f"Asked {len(hits)} process(es) to close."


def system_summary() -> str:
    vm = psutil.virtual_memory()
    lines = [f"RAM: {vm.percent:.0f}% used ({vm.used//(1024**3)} / {vm.total//(1024**3)} GiB)", f"CPU: {psutil.cpu_percent(interval=None):.0f}%"]
    cpu_temp = None
    fan_rpm = []
    fan_auto = False
    manual_pwm = False
    for base in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            chip = (base / "name").read_text().strip()
            if chip == "k10temp":
                for sensor in base.glob("temp*_input"):
                    value = int(sensor.read_text().strip())
                    if value > 0:
                        cpu_temp = round(value / 1000)
                        break
            for sensor in base.glob("fan*_input"):
                value = int(sensor.read_text().strip())
                if value > 0:
                    fan_rpm.append(value)
            for mode in base.glob("pwm*_enable"):
                channel = mode.name.removesuffix("_enable")
                state = int(mode.read_text().strip())
                fan_auto = fan_auto or state >= 2
                manual_pwm = manual_pwm or ((base / channel).is_file() and
                                            os.access(base / channel, os.W_OK) and
                                            os.access(mode, os.W_OK))
        except (OSError, ValueError):
            continue
    if cpu_temp is not None:
        lines.append(f"CPU temperature: {cpu_temp}°C")
    if fan_rpm:
        lines.append(f"Fan speed: {max(fan_rpm)} RPM")
    elif fan_auto:
        lines.append("Fan: firmware automatic; fan RPM is not exposed to Linux")
    else:
        lines.append("Fan speed: not exposed to Linux")
    lines.append("Manual fan control: available" if manual_pwm else "Manual fan control: not exposed by the hardware driver")
    bat = psutil.sensors_battery()
    if bat: lines.append(f"Battery: {bat.percent:.0f}%" + (" charging" if bat.power_plugged else ""))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("1.1.1.1", 53)); ip = s.getsockname()[0]; s.close()
        lines.append(f"IP: {ip}")
    except OSError: lines.append("IP: unavailable")
    return "\n".join(lines)


def internet_available() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2): return True
    except OSError: return False


def wifi(state: str) -> str:
    if not shutil.which("nmcli"): return "NetworkManager's nmcli was not found."
    if state == "status": return _run(["nmcli", "radio", "wifi"])
    return _run(["nmcli", "radio", "wifi", state])


class _SearchResults(HTMLParser):
    def __init__(self):
        super().__init__(); self.items = []; self.current = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result__a" in attrs.get("class", ""):
            self.current = [attrs.get("href", ""), ""]
    def handle_data(self, data):
        if self.current is not None: self.current[1] += data
    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.items.append(self.current); self.current = None


def web_search(query: str, timeout: float = 8) -> str:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 JarvisDesktop/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response: html = response.read(1_000_000).decode("utf-8", "replace")
        parser = _SearchResults(); parser.feed(html)
        return "\n".join(f"{title.strip()}: {link}" for link, title in parser.items[:8]) or "No search results returned."
    except Exception as exc: return f"Web search failed: {exc}"


def _validate_public_web_url(url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP or HTTPS webpages can be opened.")
    host = parsed.hostname.rstrip(".")
    if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(".localhost"):
        raise ValueError("Local and private network addresses cannot be opened.")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(info[4][0]) for info in
                         socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                            type=socket.SOCK_STREAM)}
        except (OSError, ValueError) as exc:
            raise ValueError("The webpage host could not be resolved.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Local and private network addresses cannot be opened.")
    return parsed


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_web_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _VisiblePageText(HTMLParser):
    BLOCKS = {"address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2", "h3", "h4", "h5", "h6", "li", "main", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul"}
    HIDDEN = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
        elif not self.hidden and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.HIDDEN and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def read_webpage(url: str) -> str:
    """Read visible text from one public webpage, without accessing local networks."""
    try:
        parsed = _validate_public_web_url(url)
        if not parsed.path:
            url = urllib.parse.urlunsplit(parsed._replace(path="/"))
        opener = urllib.request.build_opener(_SafeRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 JarvisDesktop/1.0",
                                                      "Accept": "text/html,text/plain,application/xhtml+xml"})
        with opener.open(request, timeout=12) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                return f"That page returned unsupported content type: {content_type}."
            raw = response.read(1_500_000)
            charset = response.headers.get_content_charset() or "utf-8"
        page = raw.decode(charset, "replace")
        if content_type == "text/plain":
            text = page
        else:
            parser = _VisiblePageText()
            parser.feed(page)
            text = " ".join(" ".join(parser.parts).split())
        text = text[:12000]
        return text or "The webpage had no readable text."
    except Exception as exc:
        return f"Could not read that webpage: {exc}"


def search_files(query: str, directory: str | Path | None = None) -> str:
    base = Path(directory or Path.home()).expanduser().resolve()
    if not base.is_dir(): return "Search directory does not exist."
    if shutil.which("rg"):
        try:
            result = subprocess.run(["rg", "-n", "-F", "--hidden", "--glob", "!.git/**", "--glob", "!.cache/**", "--glob", "!.venv/**", "--glob", "!node_modules/**", "--", query, str(base)], capture_output=True, text=True, timeout=8, check=False)
            matches = result.stdout.splitlines()[:30]
            if matches: return "\n".join(matches)
        except subprocess.TimeoutExpired: pass
    results = []
    visited = 0
    for root, dirs, files in os.walk(base):
        visited += 1
        if visited > 500: break
        dirs[:] = [d for d in dirs if d not in {".git", ".cache", ".venv", "node_modules"}]
        for name in files:
            if query.casefold() in name.casefold():
                results.append(str(Path(root)/name))
                if len(results) >= 30: return "\n".join(results)
    return "\n".join(results) if results else "No matching filenames found."


def _approved(confirm: Callable[[str], bool], description: str) -> bool:
    return bool(confirm(description))


def dispatch(name: str, args: dict, confirm: Callable[[str], bool], check_cancel: Callable[[], None] | None = None) -> str:
    if name in {'network_settings','bluetooth_settings','sound_settings','window_control','type_text','system_data'}:
        import desktop_tools
        return desktop_tools.dispatch(name,args,confirm,check_cancel or (lambda:None))
    if name=='resource_limits':
        from resource_limits import get_manager
        return get_manager().command(args,confirm)
    if name=='open_web_content':
        import web_content
        return web_content.open_content(args['query'],args.get('browser','browser'),args.get('context',[]),check_cancel or (lambda:None),**({'allow_codex':False} if args.get('allow_codex') is False else {}))
    if name == 'play_playlist':
        import spotify_playlists
        return spotify_playlists.play(args['query'],args.get('scope','auto'),lambda query:web_search(query,timeout=3),check_cancel or (lambda:None))
    if name == 'play_song':
        import spotify_playlists
        check=check_cancel or (lambda:None)
        playlist=spotify_playlists.intent(args['query']) if args.get('media_type')!='song' else None
        if playlist is not None:
            return spotify_playlists.play(playlist['query'],playlist['scope'],lambda query:web_search(query,timeout=3),check)
        if args.get('media_type')!='song' and not spotify_playlists.spotify.track_id(args['query']) and (Path.home()/'.config/jarvis/spotify-oauth.json').exists():
            try:
                match=spotify_playlists.own_match(args['query'],check)
                if match:
                    return spotify_playlists.play('spotify:playlist:'+match[0],'mine',lambda _: '',check)
            except spotify_playlists.LibraryUnavailable:pass
            except spotify_playlists.spotify.SpotifyError as exc:return str(exc)
        import spotify_control
        return spotify_control.play(args['query'], lambda query:web_search(query,timeout=3), check_cancel or (lambda: None),**({'allow_codex':False} if args.get('allow_codex') is False else {}))
    if name in {"email_search", "email_read", "calendar_events"}:
        import accounts
        return accounts.dispatch(name, args)
    if name == "read_file":
        path = Path(args["path"]).expanduser()
        try: return path.read_text(errors="replace")[:30000]
        except OSError as e: return f"Could not read file: {e}"
    if name == "write_file":
        path = Path(args["path"]).expanduser()
        if path.resolve().as_posix().startswith(("/etc/", "/usr/", "/boot/")) and not _approved(confirm, f"Modify system file {path}?"): return "Cancelled."
        try: path.parent.mkdir(parents=True, exist_ok=True); path.write_text(args["content"]); return f"Wrote {path}"
        except OSError as e: return f"Could not write file: {e}"
    if name == "move_file":
        src, dst = Path(args["source"]).expanduser(), Path(args["destination"]).expanduser()
        protected = ("/etc/", "/usr/", "/boot/")
        if any(p.resolve().as_posix().startswith(protected) for p in (src, dst)) and not _approved(confirm, f"Move system configuration file {src} to {dst}?"): return "Cancelled."
        if dst.exists() and not _approved(confirm, f"Replace existing destination {dst}?"): return "Cancelled."
        try: dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(dst)); return f"Moved to {dst}"
        except OSError as e: return f"Could not move file: {e}"
    if name == "delete_file":
        path = Path(args["path"]).expanduser()
        if not _approved(confirm, f"Delete {path}? This cannot be undone."): return "Cancelled."
        try: path.unlink(); return f"Deleted {path}"
        except OSError as e: return f"Could not delete file: {e}"
    if name == "search_files": return search_files(args["query"], args.get("directory"))
    if name == "system_info": return system_summary()
    if name == "launch_app":
        import web_content
        request='open '+args['name']
        if web_content.is_content_request(request):
            return web_content.open_content(request,web_content.request_browser(request),check=check_cancel or (lambda:None),**({'allow_codex':False} if args.get('allow_codex') is False else {}))
        return launch_app(args["name"])
    if name == "close_app":
        from close_targets import close
        request=args['name']
        if not re.match(r'^(?:please )?(?:close|dismiss|quit|exit)\s+',request,re.I):request='close '+request
        return close(request,confirm,check_cancel or (lambda:None))
    if name == "set_volume": return set_volume(args["mode"], int(args.get("value", 5)))
    if name == "set_brightness": return set_brightness(args["mode"], int(args.get("value", 5)))
    if name == "media": return media(args["action"])
    if name == "wifi":
        if args['state']=='status':return wifi('status')
        import desktop_tools
        return desktop_tools.dispatch('network_settings',{'action':'power','value':args['state']},confirm,check_cancel or (lambda:None))
    if name == "web_search": return web_search(args["query"])
    if name == "read_webpage": return read_webpage(args["url"])
    if name == "run_command":
        cmd = args["command"]
        safe_read = {"pwd", "ls", "cat", "head", "tail", "stat", "file", "find", "rg", "grep", "df", "free", "uname", "whoami", "id", "date"}
        try: words = shlex.split(cmd)
        except ValueError: words = []
        read_only = bool(words and words[0] in safe_read and not re.search(r"[;&|><$`()\n]", cmd)
                         and not any(arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for arg in words))
        if read_only:
            try: return subprocess.run(words, capture_output=True, text=True, timeout=8, check=False).stdout[:12000]
            except (OSError, subprocess.TimeoutExpired) as e: return f"Command failed: {e}"
        prompt = f"Run sudo command?\n{cmd}" if re.search(r"(^|[;&|\s])sudo(\s|$)", cmd) else f"Run shell command?\n{cmd}"
        if not _approved(confirm, prompt): return "Cancelled."
        try: return subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=8, check=False).stdout[:12000]
        except (OSError, subprocess.TimeoutExpired) as e: return f"Command failed: {e}"
    return f"Unknown tool: {name}"
