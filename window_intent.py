"""Conservative natural-language window commands and explicit follow-up references."""
import re

ALIASES={
    'terminal': {'org.gnome.terminal','org.gnome.console','kgx','kitty','alacritty','com.mitchellh.ghostty','ghostty','konsole','org.kde.konsole','xterm','uxterm','xfce4-terminal','tilix','com.gexperts.tilix','terminator','wezterm','org.wezfurlong.wezterm'},
    'browser': {'google-chrome','google-chrome-stable','chromium','chromium-browser','org.chromium.chromium','firefox','org.mozilla.firefox'},
    'chrome': {'google-chrome','google-chrome-stable','com.google.chrome'},
    'files': {'org.gnome.nautilus','nautilus','nemo','org.kde.dolphin','thunar'},
    'editor': {'org.gnome.texteditor','gedit','org.gnome.gedit','code','code-oss','sublime_text'},
}


def target_name(text):
    text=re.sub(r'^(?:(?:all|every|the|my|of|open|both)\s+)+','',text.strip())
    text=re.sub(r'\s+(?:windows?|apps?|applications?)$','',text)
    return {'terminals':'terminal','browsers':'browser','file manager':'files','file explorer':'files','text editor':'editor','code editor':'code','google chrome':'chrome','console':'terminal'}.get(text,text)


def matches(window,target):
    app=window['app'].casefold()
    if target in ALIASES:return app in ALIASES[target]
    return target==str(window['id']) or target==app or target in app.split('.') or target in window['title'].casefold()


def parse(text):
    text=text.casefold().strip().rstrip('.!?')
    text=re.sub(r'^(?:(?:and|then|also|please)\s+)+','',text)
    text=re.sub(r'\s+(?:please|for me)$','',text)
    text=re.sub(r'\s+',' ',text)
    m=re.fullmatch(r'(?:move|send|put|take) (.+?) (?:over )?(?:to|into|on|onto) (?:workspace|desktop) (one|two|three|four|five|six|seven|eight|nine|ten|\d+)',text)
    if m:
        number=m[2]
        numbers='one two three four five six seven eight nine ten'.split()
        args={'action':'workspace','workspace':int(number) if number.isdigit() else numbers.index(number)+1}; target=m[1]
    else:
        m=re.fullmatch(r'(close|quit|shut down|shut|minimize|hide|maximize|expand|restore|unminimize|focus|show|bring back) (.+)',text)
        if not m:return None
        verb,target=m.groups()
        args={'action':{'quit':'close','shut':'close','shut down':'close','hide':'minimize','expand':'maximize','unminimize':'restore','bring back':'restore','show':'focus'}.get(verb,verb)}
        # Leave ordinary application launching to the existing routes.
        if verb in {'show','focus'} and not re.search(r'\b(window|windows|it|them|those|that)\b',target):return None
    if target in {'it','that','that one','that window','the same window','them','those','those windows','these windows','the same windows'}:
        return dict(args,_reference='single' if target in {'it','that','that one','that window','the same window'} else 'group')
    group=bool(re.search(r'\b(all|every|both|everything)\b',target) or re.search(r'\b(windows|terminals|browsers)\b',target))
    name=target_name(target)
    if name in {'window','windows','everything'}:name=''
    if not name and not group:return None
    return dict(args,window=name,**({'all':True} if group else {}))
