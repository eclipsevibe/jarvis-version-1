"""Typed playlist resolution; library access uses explicit Spotify OAuth only."""
import json
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote, urlencode

import requests
import spotify_control as spotify


class LibraryUnavailable(spotify.SpotifyError):
    pass


def playlist_id(value):
    match=re.fullmatch(r'spotify:playlist:([A-Za-z0-9]{22})',str(value).strip())
    if match:return match[1]
    parsed=urlsplit(str(value).strip())
    if parsed.scheme=='https' and parsed.hostname=='open.spotify.com':
        match=re.fullmatch(r'/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]{22})/?',parsed.path)
        if match:return match[1]
    return None


def intent(query):
    query=query.strip()
    if re.match(r'^(?:the )?(?:song|track)(?: called| named)?\s+',query,re.I):return None
    if playlist_id(query):return dict(query=query,scope='auto')
    scope='mine' if re.search(r'\bmy\b',query,re.I) else 'public' if re.search(r'\bpublic\b|\bnot (?:one of )?mine\b',query,re.I) else 'auto'
    if not re.search(r'\bplaylists?\b',query,re.I):return None
    title=re.sub(r'\bnot (?:one of )?mine\b','',query,flags=re.I)
    title=re.sub(r'^(?:(?:me|a|the|my|public)\s+)*playlist(?:\s+(?:called|named|for|of))?\s*','',title,flags=re.I)
    title=re.sub(r'\s+(?:(?:public|my)\s+)?playlist$','',title,flags=re.I)
    title=re.sub(r'^(?:my|public)\s+','',title,flags=re.I).strip(' \"\'.,!?')
    title=re.sub(r'^(?:a|the)\s+','',title,flags=re.I)
    if title.lower() in {'playlist','a playlist','my playlist','public playlist'}:title=''
    return dict(query=title,scope=scope)


def key(title):
    return ' '.join(re.findall(r'\w+',title.casefold()))


def library(check):
    path=Path.home()/'.config/jarvis/spotify-oauth.json'
    try:token=json.loads(path.read_text())
    except (OSError,ValueError):raise LibraryUnavailable('Spotify library access is not connected. Run scripts/connect-spotify --client-id YOUR_SPOTIFY_APP_CLIENT_ID.') from None
    if token.get('expires_at',0)<time.time()+60:
        check()
        try:
            response=requests.post('https://accounts.spotify.com/api/token',data=dict(grant_type='refresh_token',refresh_token=token['refresh_token'],client_id=token['client_id']),timeout=8,allow_redirects=False)
            response.raise_for_status();fresh=response.json()
            token.update(fresh,expires_at=time.time()+fresh['expires_in'])
            spotify.private_json(path,token)
        except (requests.RequestException,ValueError,KeyError):raise LibraryUnavailable('Spotify library sign-in expired. Reconnect with scripts/connect-spotify.') from None
    items=[]
    for offset in range(0,10000,50):
        check()
        try:
            response=requests.get('https://api.spotify.com/v1/me/playlists',params=dict(limit=50,offset=offset),headers={'Authorization':'Bearer '+token['access_token']},timeout=8,allow_redirects=False)
            response.raise_for_status();data=response.json()
        except (requests.RequestException,ValueError,KeyError):raise LibraryUnavailable('Spotify library could not be read. Check the connection and Spotify playlist permissions.') from None
        items.extend(item for item in data.get('items',[]) if item and playlist_id(item.get('uri','')))
        if not data.get('next'):return items
    raise LibraryUnavailable('Spotify library is too large to search completely. Provide a playlist link.')


def own_match(query,check):
    matches=[p for p in library(check) if key(p.get('name',''))==key(query)]
    unique={p['uri']:p for p in matches}
    if len(unique)>1:raise spotify.SpotifyError('Several saved playlists have that title. Give me the Spotify playlist link to choose the right one.')
    if unique:
        p=next(iter(unique.values()));return playlist_id(p['uri']),p['name'],'library'
    return None


def validate(identifier,query,check):
    check()
    request=urllib.request.Request('https://open.spotify.com/oembed?'+urlencode({'url':'https://open.spotify.com/playlist/'+identifier}),headers={'User-Agent':'JarvisDesktop/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=5) as response:data=json.loads(response.read(64000))
    except (OSError,ValueError):raise spotify.SpotifyError('Spotify could not verify that playlist. Nothing was started.') from None
    title=data.get('title','')
    if data.get('provider_name')!='Spotify' or not title:raise spotify.SpotifyError('Spotify did not confirm the playlist metadata.')
    if query:
        words=set(key(query).split());actual=set(key(title).split())
        if not words or not words<=actual:raise spotify.SpotifyError('The playlist title did not match your request. Please give me a more specific title or link.')
    check();return identifier,title,'public'


def resolve(query,scope,search,check):
    check()
    identifier=playlist_id(query)
    if identifier:
        if scope!='public' and ((Path.home()/'.config/jarvis/spotify-oauth.json').exists() or scope=='mine'):
            try:
                for item in library(check):
                    if playlist_id(item.get('uri',''))==identifier:
                        return identifier,item['name'],'library'
            except LibraryUnavailable:
                if scope=='mine':raise
        return validate(identifier,'',check)
    if not query:raise spotify.SpotifyError('Which playlist would you like? Give me its title or a Spotify playlist link.')
    if scope!='public':
        try:
            found=own_match(query,check)
            if found:return found
        except LibraryUnavailable:
            if scope=='mine':raise
        if scope=='mine':raise spotify.SpotifyError('I could not find that title in your saved Spotify playlists. Say “public playlist” to search beyond your library.')
    results=search('site:open.spotify.com/playlist/ '+query[:200]);check()
    candidates=[]
    for line in results.splitlines():
        for link in re.findall(r'https?://\S+|//duckduckgo\.com/\S+',line):
            parsed=urlsplit('https:'+link if link.startswith('//') else link)
            if parsed.hostname in {'duckduckgo.com','www.duckduckgo.com'}:link=parse_qs(parsed.query).get('uddg',[''])[0]
            identifier=playlist_id(unquote(link).rstrip(').,'))
            if identifier and identifier not in candidates:candidates.append(identifier)
    for identifier in candidates[:5]:
        try:return validate(identifier,query,check)
        except spotify.SpotifyError:continue
    raise spotify.SpotifyError('I could not verify a matching public Spotify playlist. Try a more specific name or provide its playlist link.')


def play(query,scope,search,check):
    if scope not in {'auto','mine','public'}:return 'Choose library, public, or automatic playlist search.'
    try:
        identifier,title,origin=resolve(query,scope,search,check)
        if not spotify.shutil.which('spotify'):return 'The Spotify desktop application is not installed.'
        check();uri='spotify:playlist:'+identifier
        spotify.subprocess.Popen(['spotify',uri],stdout=spotify.subprocess.DEVNULL,stderr=spotify.subprocess.DEVNULL,start_new_session=True)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            check();result=spotify.call('org.mpris.MediaPlayer2.Player.OpenUri',uri)
            if result.returncode==0:
                check();played=spotify.call('org.mpris.MediaPlayer2.Player.Play')
                if played.returncode==0:return f'Opened {"public " if origin=="public" else "your saved "}playlist {title} in Spotify and requested playback.'
                break
            time.sleep(.25)
        return f'Opened playlist {title} in Spotify, but playback controls are unavailable.'
    except spotify.SpotifyError as exc:return str(exc)
    except (OSError,spotify.subprocess.TimeoutExpired):return 'Spotify playlist playback control failed. Check that Spotify is signed in.'
