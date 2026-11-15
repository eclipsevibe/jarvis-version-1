"""Resolve public Spotify track links and control the installed desktop client."""
import json
import re
import shutil
import subprocess
import time
import os
from pathlib import Path
import tempfile
import threading
import queue
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlsplit
import urllib.request


class SpotifyError(Exception):
    pass


class LookupStopped(Exception):
    pass


def cache_key(query):
    return ' '.join(word for word in re.findall(r'\w+',query.lower()) if word not in {'by','the','song','please'})


def private_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as output:json.dump(data,output,ensure_ascii=False)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def cached(query):
    try:
        data=json.loads((Path.home()/'.config/jarvis/spotify-tracks.json').read_text())
        item=data.get(cache_key(query),{})
        if track_id(item.get('uri','')) and item.get('title'):
            return track_id(item['uri']),item['title'],item.get('description',item['title'])
        identifier=track_id(query)
        requested=set(cache_key(query).split())
        matches={}
        for entry in data.values():
            entry_id=track_id(entry.get('uri',''))
            if not entry_id or not entry.get('title'):continue
            title=re.split(r'\s*\(|\s+-\s+',entry['title'],1)[0]
            title_words=set(cache_key(title).split())
            all_words=set(cache_key(entry['title']+' '+entry.get('description','')).split())
            if (identifier and identifier==entry_id) or (requested and title_words and title_words<=requested<=all_words):
                matches[entry_id]=entry
        if len(matches)==1:
            entry_id,entry=next(iter(matches.items()))
            return entry_id,entry['title'],entry.get('description',entry['title'])
    except (OSError,ValueError,TypeError,AttributeError):pass
    return None


def remember(query,identifier,title,description):
    path=Path.home()/'.config/jarvis/spotify-tracks.json'
    try:
        try:data=json.loads(path.read_text())
        except (OSError,ValueError):data={}
        if not isinstance(data,dict):data={}
        data[cache_key(query)]={'uri':'spotify:track:'+identifier,'title':title,'description':description}
        private_json(path,dict(list(data.items())[-100:]))
    except OSError:pass


def diagnostic(query,stage,detail='',**measurements):
    try:
        path=Path.home()/'jarvis/poweroff-diagnostics/spotify-last.json'
        previous={}
        if stage!='lookup':
            try:
                previous=json.loads(path.read_text())
                if previous.get('query')!=query[:200]:previous={}
            except (OSError,ValueError):pass
        previous.update(time=datetime.now().astimezone().isoformat(),query=query[:200],stage=stage,detail=str(detail)[:1200],**measurements)
        private_json(path,previous)
    except OSError:pass


def codex_lookup(query,check):
    import codex_bridge
    class Cancellation:
        def is_set(self):
            check()
            return False
    question=('Find the official Spotify track for this song request, using web search. '
              'Prefer the original studio recording or its official remaster; exclude live, tribute, karaoke, '
              'demo and cover versions unless explicitly requested. Return only JSON with title, artist, '
              'and spotify_url (an https://open.spotify.com/track/ URL). Return {} if no confident match. '
              'Song request: '+json.dumps(query))
    try:
        result=codex_bridge.answer(question,[],[],Cancellation(),timeout=20,effort_override='low')
        match=re.search(r'\{.*\}',result,re.S)
        data=json.loads(match[0]) if match else {}
        identifier=track_id(data.get('spotify_url',''))
        if identifier and data.get('title') and data.get('artist'):
            return identifier,str(data['title'])+' by '+str(data['artist'])
    except (codex_bridge.CodexError,codex_bridge.CodexCancelled,ValueError,TypeError,AttributeError):
        pass
    return None


def track_id(value):
    value = str(value).strip()
    match = re.fullmatch(r'spotify:track:([A-Za-z0-9]{22})', value)
    if match:
        return match[1]
    parsed = urlsplit(value)
    if parsed.scheme == 'https' and parsed.hostname == 'open.spotify.com':
        match = re.fullmatch(r'/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]{22})/?', parsed.path)
        if match:
            return match[1]
    return None


def search_candidate(query,search,check):
    results=search('site:open.spotify.com/track/ '+query[:200])
    check()
    words = [word for word in re.findall(r'\w+', query.lower())
             if word not in {'by', 'the', 'a', 'song', 'track', 'please'}]
    ranked = []
    for line in results.splitlines():
        links = re.findall(r'https?://\S+|//duckduckgo\.com/\S+', line)
        for link in links:
            if link.startswith('//'):
                link = 'https:' + link
            parsed = urlsplit(link)
            if parsed.hostname in {'duckduckgo.com', 'www.duckduckgo.com'}:
                link = parse_qs(parsed.query).get('uddg', [''])[0]
            identifier = track_id(unquote(link))
            if not identifier:
                continue
            title = line.split(': http', 1)[0].split(': //', 1)[0]
            normalized = set(re.findall(r'\w+', title.lower()))
            score = sum(word in normalized for word in words) / max(1, len(words))
            if score >= .8:
                if re.search(r'\b(live|karaoke|tribute|cover|demo)\b',title,re.I) and not re.search(r'\b(live|karaoke|tribute|cover|demo)\b',query,re.I):continue
                ranked.append((score, identifier, title))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return (ranked[0][1],ranked[0][2]) if ranked else None


def validate_candidate(candidate,check):
    identifier,description=candidate
    check()
    url = 'https://open.spotify.com/oembed?url=https://open.spotify.com/track/' + identifier
    request = urllib.request.Request(url, headers={'User-Agent': 'JarvisDesktop/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read(64000))
        title = data.get('title')
        if not title or data.get('provider_name') != 'Spotify':
            raise SpotifyError('Spotify did not confirm the track metadata.')
    except (OSError, ValueError):
        raise SpotifyError('Spotify track lookup is unavailable. No song was started.') from None
    check()
    return identifier, title, description


def resolve(query,search,check,allow_codex=True):
    started=time.monotonic()
    check()
    saved=cached(query)
    if saved:
        diagnostic(query,'resolved',saved[2],lookup_backend='cache',lookup_seconds=round(time.monotonic()-started,3))
        return saved
    identifier=track_id(query)
    if identifier:
        result=validate_candidate((identifier,query),check)
        remember(query,*result)
        diagnostic(query,'resolved',result[2],lookup_backend='direct',lookup_seconds=round(time.monotonic()-started,3))
        return result
    # Race read-only discovery paths. Losing workers cannot launch Spotify or
    # write the cache, and cancellation stops the losing Codex subprocess.
    stopped=threading.Event(); results=queue.Queue()
    def worker_check():
        check()
        if stopped.is_set():raise LookupStopped()
    def lookup(name,function):
        try:
            worker_check()
            candidate=function()
            if candidate:
                result=validate_candidate(candidate,worker_check)
                worker_check()
                results.put((name,result))
            else:results.put((name,None))
        except Exception:
            results.put((name,None))
    lookups=[('public_search',lambda:search_candidate(query,search,worker_check))]
    if allow_codex:lookups.append(('codex_lookup',lambda:codex_lookup(query,worker_check)))
    for name,function in lookups:
        threading.Thread(target=lookup,args=(name,function),daemon=True).start()
    try:
        pending=len(lookups)
        deadline=started+31
        while pending and time.monotonic()<deadline:
            check()
            try:name,result=results.get(timeout=.05)
            except queue.Empty:continue
            pending-=1
            if result is not None:
                stopped.set()
                check()
                remember(query,*result)
                diagnostic(query,'resolved',result[2],lookup_backend=name,lookup_seconds=round(time.monotonic()-started,3))
                return result
        raise SpotifyError("I couldn't find a matching Spotify track. The lookup failed; a direct Spotify track link will bypass that search.")
    finally:
        stopped.set()


def call(method, *args):
    if not shutil.which('gdbus'):
        raise SpotifyError('The desktop D-Bus control utility is missing.')
    result = subprocess.run(['gdbus', 'call', '--session', '--dest',
        'org.mpris.MediaPlayer2.spotify', '--object-path', '/org/mpris/MediaPlayer2',
        '--method', method, *args], capture_output=True, text=True, timeout=2)
    return result


def playing(identifier, metadata, status, title='', artist=''):
    ids = re.findall(r'(?:spotify:track:|/com/spotify/track/)([A-Za-z0-9]{22})', metadata)
    active=bool(re.search(r"['\"]Playing['\"]", status))
    if identifier in ids:return active
    # Spotify can relink a track to another release in the listener's region.
    # Accept that only when both the requested title and artist are confirmed.
    actual_title=re.search(r"['\"]xesam:title['\"]:\s*<['\"](.*?)['\"]>",metadata)
    actual_artist=re.search(r"['\"]xesam:artist['\"]:\s*<\[(.*?)\]>",metadata)
    core=re.split(r'\s*\(|\s+-\s+',title,1)[0]
    words=set(re.findall(r'\w+',core.lower()))
    artists=set(re.findall(r'\w+',artist.lower()))
    return bool(active and words and artists and actual_title and actual_artist
                and words<=set(re.findall(r'\w+',actual_title[1].lower()))
                and artists<=set(re.findall(r'\w+',actual_artist[1].lower())))


def play(query, search, check=lambda: None, allow_codex=True):
    started=time.monotonic()
    diagnostic(query,'lookup')
    if not query.strip():
        return 'Which song and artist would you like?'
    if not shutil.which('spotify'):
        return 'The Spotify desktop application is not installed.'
    if not shutil.which('gdbus'):
        return 'The desktop D-Bus control utility is missing.'
    try:
        identifier, title, description = resolve(query, search, check) if allow_codex else resolve(query,search,check,allow_codex=False)
        artist_match=re.search(r'\s+by\s+(.+?)(?:\s*\|\s*Spotify)?$',query,re.I)
        artist=artist_match[1].strip() if artist_match else ''
        check()
        uri = 'spotify:track:' + identifier
        # The URI is validated above; no shell or search-page URI is executed.
        diagnostic(query,'launch',uri)
        subprocess.Popen(['spotify', uri], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 8
        opened = False
        while time.monotonic() < deadline:
            check()
            result = call('org.mpris.MediaPlayer2.Player.OpenUri', uri)
            check()
            if result.returncode == 0:
                opened = True
                break
            time.sleep(.25)
        if not opened:
            diagnostic(query,'controls_unavailable',result.stderr)
            return f'Opened Spotify for {title}, but its playback controls are not ready. Check that Spotify is signed in.'
        check()
        call('org.mpris.MediaPlayer2.Player.Play')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            check()
            metadata = call('org.freedesktop.DBus.Properties.Get', 'org.mpris.MediaPlayer2.Player', 'Metadata')
            status = call('org.freedesktop.DBus.Properties.Get', 'org.mpris.MediaPlayer2.Player', 'PlaybackStatus')
            check()
            if metadata.returncode == status.returncode == 0 and playing(identifier, metadata.stdout, status.stdout,title,artist):
                diagnostic(query,'playing',uri,total_seconds=round(time.monotonic()-started,3))
                return f'Playing {title} in Spotify.'
            time.sleep(.25)
        diagnostic(query,'playback_unconfirmed',status.stdout or status.stderr)
        return f'Opened {title} in Spotify, but I could not confirm that the song is playing.'
    except SpotifyError as exc:
        diagnostic(query,'error',exc)
        return str(exc)
    except (OSError, subprocess.TimeoutExpired):
        diagnostic(query,'desktop_error','Spotify launch or desktop control failed.')
        return 'Spotify playback control failed. Check that Spotify is signed in and connected.'
