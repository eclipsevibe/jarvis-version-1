"""Resolve close requests against live objects, never process-name guesses."""
import re
from urllib.parse import urlsplit

import desktop_tools as desktop

BROWSERS={'chrome':('google-chrome','google-chrome-stable','google-chrome-beta','google-chrome-unstable','com.google.chrome','chrome'),
          'chromium':('chromium','chromium-browser','org.chromium.chromium'),
          'firefox':('firefox','firefox-esr','org.mozilla.firefox','librewolf','io.gitlab.librewolf-community'),
          'edge':('microsoft-edge','microsoft-edge-beta','com.microsoft.edge'),
          'brave':('brave','brave-browser','com.brave.browser'),
          'opera':('opera','com.opera.opera'),'vivaldi':('vivaldi','vivaldi-stable','com.vivaldi.vivaldi')}


def parse(request):
    match=re.fullmatch(r'(?:please )?(?:close|dismiss|quit|exit)\s+(.+?)[.!?]*',request.strip(),re.I)
    if not match:return None
    target=re.sub(r'\s+please$','',match[1],flags=re.I).strip()
    bulk=bool(re.match(r'^(?:all|every)\b',target,re.I))
    target=re.sub(r'^(?:all(?: of)?|every)\s+','',target,flags=re.I)
    target=re.sub(r'^(?:the|my|that|this|a|an)\s+','',target,flags=re.I).strip()
    if re.search(r'\bnotifications?\b',target,re.I):
        query=re.sub(r'\bnotifications?\b','',target,flags=re.I).strip()
        query=re.sub(r'^(?:from|about|for)\s+','',query,flags=re.I)
        query=re.sub(r'\b(?:pop-?up|banner|window|toast)\b','',query,flags=re.I).strip()
        return dict(kind='notifications',query=query,all=bulk)
    if re.search(r'\btabs?\b',target,re.I):
        query=re.sub(r'\btabs?\b','',target,flags=re.I).strip()
        browser=None
        for name in ('chrome','chromium','firefox','edge','brave','opera','vivaldi'):
            if re.search(r'\b'+name+r'\b',query,re.I):
                browser=name;query=re.sub(r'\b(?:google )?'+name+r'\b','',query,flags=re.I);break
        query=re.sub(r'\b(?:in|on|from|the|my|browser)\b','',query,flags=re.I).strip()
        return dict(kind='tabs',query=query,browser=browser or 'chrome',all=bulk)
    target=re.sub(r'\bwindows?\b','',target,flags=re.I).strip()
    target=re.sub(r'^(?:the|my)\s+','',target,flags=re.I)
    return dict(kind='windows',query=target,all=bulk)


def browser_kind(app):
    app=app.casefold().removesuffix('.desktop').replace(' ','-')
    return next((name for name,aliases in BROWSERS.items() if app in aliases),None)


def tab_matches(tab,query):
    if not query:return True
    try:host=(urlsplit(tab.get('url','')).hostname or '').lower()
    except ValueError:host=''
    query=query.casefold().strip()
    if query==str(tab.get('id')):return True
    if query in {'youtube','you tube'}:
        return host in {'youtube.com','youtu.be'} or host.endswith('.youtube.com')
    if '.' in query and ' ' not in query:
        return host==query or host.endswith('.'+query)
    return query in tab.get('title','').casefold() or query in host


def choose(items,bulk,kind):
    if not items:raise desktop.DesktopError('No matching '+kind+' found. Nothing was closed.')
    if not bulk and len(items)>1:
        candidates='\n'.join(str(item.get('id',''))+': '+item.get('title',item.get('app','')) for item in items[:15])
        raise desktop.DesktopError('More than one '+kind+' matches. Choose one by name/ID or explicitly say “all”:\n'+candidates)
    return items if bulk else items[:1]


def close(request,confirm,check):
    intent=parse(request)
    if not intent:return 'Please say what you want closed.'
    try:
        query=intent['query'].casefold()
        if intent['kind']=='notifications':
            items=desktop.shell_call('notifications',check)['notifications']
            matches=[n for n in items if not query or query==n['id'].casefold() or query in (' '.join(str(n.get(k,'')) for k in ('title','body','app'))).casefold()]
            if not intent['all'] and not query:
                visible=[n for n in matches if n.get('visible')]
                if len(visible)==1:matches=visible
            selected=choose(matches,intent['all'],'notifications')
            result=desktop.shell_call('dismiss_notifications',check,ids=[n['id'] for n in selected])
            return f"Dismissed {result['dismissed']} notification(s)."
        if intent['kind']=='tabs':
            from browser_control import get_broker
            if intent['browser'] not in {'chrome','chromium'}:
                return 'Tab-level controls currently support Chrome and Chromium. I will not close a whole browser window in place of its tabs.'
            broker=get_broker()
            listed=broker.request(intent['browser'],'list',{},check)
            candidates=[t for t in listed if tab_matches(t,query)]
            # “the tab” is safe only if a unique active tab exists.
            if not query and not intent['all']:
                active=[t for t in candidates if t.get('active')]
                if len(active)==1:candidates=active
            selected=choose(candidates,intent['all'],'tabs')
            summary='\n'.join(t.get('title','Untitled') for t in selected[:25])
            desktop.confirm_change(confirm,f'Close {len(selected)} matching {intent["browser"]} tab(s)?\n{summary}'+('\n…and more matching tabs.' if len(selected)>25 else ''))
            check()
            # IDs, URLs and titles are frozen for confirmation. The extension
            # rechecks each tab and will skip any that navigated in the meantime.
            responses=broker.request(intent['browser'],'close',{'tabs':selected},check)
            closed=sum(r.get('closed',0) for r in responses)
            skipped=sum(r.get('skipped',0) for r in responses)
            return f'Closed {closed} matching tab(s). Skipped {skipped} changed or unavailable tab(s).'
        items=desktop.shell_call('list',check)['windows']
        if query in {'browser','browsers','web browser','web browsers'}:
            matches=[w for w in items if browser_kind(w.get('app',''))]
        else:
            query=re.sub(r'^(?:google )?(chrome|chromium|firefox|edge|brave|opera|vivaldi)(?: browsers?)?$',r'\1',query)
            if query in BROWSERS:matches=[w for w in items if browser_kind(w.get('app',''))==query]
            elif query in {'everything','all',''}:
                return 'Please specify the app or window group to close. Nothing was closed.'
            else:
                from window_intent import target_name,matches as matches_window
                alias=target_name(query)
                exact=[w for w in items if query==str(w['id']) or query==w.get('app','').casefold() or query==w.get('title','').casefold()]
                matches=exact or [w for w in items if matches_window(w,alias)]
        selected=choose(matches,intent['all'],'windows')
        desktop.confirm_change(confirm,'Close these '+str(len(selected))+' window(s)? Unsaved work or tabs may be affected.\n'+'\n'.join(w['title'] for w in selected[:25]))
        count=0
        for w in selected:
            check();desktop.shell_call('close',check,window=w['id']);count+=1
        return f'Requested closure of {count} matching window(s). Apps may still show an unsaved-work prompt.'
    except desktop.DesktopError as exc:return str(exc)
