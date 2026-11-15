"""Resolve a content request, then open its URL in the requested browser."""
import json
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlencode, urlsplit
import urllib.request

import codex_bridge


class ContentError(Exception):
    pass


def request_browser(text):
    match=re.search(r'\b(?:in|using|with|on)\s+(?:google\s+)?(chrome|chromium|firefox|browser)\s*[.!?]*$',text,re.I)
    return match[1].lower() if match else 'browser'


def is_content_request(text):
    if not re.match(r'^(?:open|show|watch|play|find|search)\b',text,re.I):return False
    if re.match(r'^play\b',text,re.I) and re.search(r'\bspotify\b|open\.spotify\.com',text,re.I) and not re.search(r'\byoutube\b',text,re.I):return False
    return bool(re.search(r'https?://|\b(youtube|video|videos|website|webpage|article|web site)\b|\b(?:in|using|with) (?:google )?(?:chrome|chromium|firefox|browser)\b',text,re.I))


def open_url(url,browser='browser',check=lambda:None):
    from tools import _validate_public_web_url
    try:_validate_public_web_url(url)
    except ValueError as exc:raise ContentError(str(exc)) from None
    commands={'chrome':('google-chrome','google-chrome-stable'),
              'chromium':('chromium','chromium-browser'), 'firefox':('firefox',),
              'browser':('xdg-open',)}
    if browser not in commands:raise ContentError('Choose Chrome, Chromium, Firefox, or your default browser.')
    executable=next((command for command in commands[browser] if shutil.which(command)),None)
    if executable is None:raise ContentError(f'{browser.title()} is not installed.')
    check()
    try:
        subprocess.Popen([executable,url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    except OSError:raise ContentError(f'Could not start {browser.title()}.') from None


def resolve(query,history,check,allow_codex=True):
    direct=re.search(r'https?://[^\s<>]+',query)
    if direct:return direct[0].rstrip('.!?'), 'the requested page'
    text=re.sub(r'\s+(?:in|using|with|on)\s+(?:google\s+)?(?:chrome|chromium|firefox|browser)\s*[.!?]*$','',query,flags=re.I)
    if text.lower().strip() in {'open youtube','show youtube'}:
        return 'https://www.youtube.com/','YouTube'
    if re.match(r'^search(?: for)?\s+',text,re.I):
        term=re.sub(r'^search(?: for)?\s+','',text,flags=re.I)
        return 'https://www.google.com/search?'+urlencode({'q':term}), 'your search'
    if not allow_codex:
        raise ContentError('Local mode cannot use Codex to find this page. Provide a direct URL or ask for a browser search.')
    class Cancellation:
        def is_set(self):check(); return False
    prompt=('Resolve the following request to a direct public webpage URL. Use web search for current/latest '
            'content. For a YouTube video request return the direct watch URL, not a search-results page. '
            'For latest videos verify the requested creator/channel and upload date, prefer the main channel '
            'and regular videos over Shorts unless Shorts were requested. Do not invent URLs or dates. '
            'Use recent conversation to resolve references such as "that video". Do not open anything. '
            'Return only JSON with url and title, or {} if a confident match cannot be found. Request: '+json.dumps(query))
    try:
        result=codex_bridge.answer(prompt,history,[],Cancellation(),timeout=25,effort_override='low')
        match=re.search(r'\{.*\}',result,re.S)
        data=json.loads(match[0]) if match else {}
    except (codex_bridge.CodexError,codex_bridge.CodexCancelled,ValueError):
        raise ContentError('I could not find a verified page for that request.') from None
    if not isinstance(data,dict):raise ContentError('I could not find a verified page for that request.')
    url=data.get('url',''); title=data.get('title','the requested page')
    if not isinstance(url,str) or not isinstance(title,str):
        raise ContentError('I could not find a verified page for that request.')
    if not url:raise ContentError('I could not find a verified page for that request.')
    if 'youtube' in query.lower() or re.search(r'\bvideo\b',query,re.I):
        parsed=urlsplit(url)
        if parsed.hostname in {'www.youtube.com','youtube.com','m.youtube.com'}:
            identifier=parse_qs(parsed.query).get('v',[''])[0] if parsed.path=='/watch' else ''
        elif parsed.hostname=='youtu.be':identifier=parsed.path.strip('/')
        else:identifier=''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',identifier):
            raise ContentError('I found a page, but could not verify a direct YouTube video link.')
        url='https://www.youtube.com/watch?v='+identifier
        check()
        try:
            request=urllib.request.Request('https://www.youtube.com/oembed?'+urlencode({'url':url,'format':'json'}))
            with urllib.request.urlopen(request,timeout=4) as response:metadata=json.loads(response.read(64000))
            if 'mrbeast' in query.lower().replace(' ','') and metadata.get('author_name','').lower().replace(' ','')!='mrbeast':
                raise ContentError('The video does not appear to be from MrBeast’s official channel.')
            title=metadata.get('title') or title
        except (OSError,ValueError):
            raise ContentError('I found a video link, but YouTube could not verify it right now.') from None
    check()
    return url,title


def open_content(query,browser='browser',history=None,check=lambda:None,allow_codex=True):
    try:
        url,title=resolve(query,history or [],check) if allow_codex else resolve(query,history or [],check,allow_codex=False)
        open_url(url,browser,check)
        destination='your browser' if browser=='browser' else browser.title()
        return f'Opened {title} in {destination}.\n{url}'
    except ContentError as exc:return str(exc)
