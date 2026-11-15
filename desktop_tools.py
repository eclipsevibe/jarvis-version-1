"""Non-administrator desktop operations. No shell interpolation or elevation."""
import ast
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

import psutil


class DesktopError(Exception):
    pass


def run(argv, timeout=12, check=lambda:None):
    check()
    if not shutil.which(argv[0]):raise DesktopError(argv[0]+' is not installed.')
    try:
        result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,stdin=subprocess.DEVNULL)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise DesktopError('Desktop command unavailable or timed out: '+argv[0]) from exc
    check()
    output=(result.stdout or result.stderr).strip()
    if result.returncode or re.search(r'(?im)^(?:failed to |error:|not authorized|permission denied)',output):
        raise DesktopError(output[:1500] or 'The desktop rejected this operation. No administrator access was requested.')
    return output[:18000]+('\n[Output truncated; request a narrower view.]' if len(output)>18000 else '')


def identifier(value):
    value=str(value)
    if not value or len(value)>200 or value.startswith('-') or any(ord(c)<32 for c in value):
        raise DesktopError('Invalid device or setting identifier.')
    return value


def confirm_change(confirm,description):
    if not confirm(description):raise DesktopError('Cancelled. No change was made.')


def open_settings(panel):
    panels={'bluetooth':'bluetooth','wifi':'wifi','sound':'sound','network':'network','system':'system'}
    if panel not in panels:raise DesktopError('Unknown settings panel.')
    if not shutil.which('gnome-control-center'):raise DesktopError('GNOME Settings is not installed.')
    subprocess.Popen(['gnome-control-center',panels[panel]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    return 'Requested the '+panel+' settings window.'


def nm_permission(permission,check):
    output=run(['nmcli','-t','-f','PERMISSION,VALUE','general','permissions'],check=check)
    permissions=dict(line.rsplit(':',1) for line in output.splitlines() if ':' in line)
    if permissions.get('org.freedesktop.NetworkManager.'+permission)!='yes':
        raise DesktopError('NetworkManager does not permit this change without additional authorization. Use the laptop Network settings; Jarvis will not request administrator access.')


def network(args,confirm,check):
    action=args.get('action','status')
    base=['nmcli','--wait','10']
    if action=='settings':return open_settings('wifi')
    if action=='status':return run(base+['general','status'],check=check)+'\n'+run(base+['device','status'],check=check)
    if action=='scan':
        nm_permission('wifi.scan',check)
        return run(base+['-f','IN-USE,SSID,SIGNAL,SECURITY,DEVICE','device','wifi','list','--rescan','yes'],check=check)
    if action=='profiles':return run(base+['-f','NAME,UUID,TYPE,DEVICE,AUTOCONNECT','connection','show'],check=check)
    if action=='device':return run(base+['device','show',identifier(args['device'])],check=check)
    if action=='power':
        value=args.get('value')
        if value not in {'on','off'}:raise DesktopError('Use on or off.')
        confirm_change(confirm,'Turn Wi-Fi '+value+'?')
        nm_permission('enable-disable-wifi',check)
        run(base+['radio','wifi',value],check=check)
        return 'Wi-Fi radio: '+run(base+['radio','wifi'],check=check)
    uid=str(args.get('profile',''))
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',uid):raise DesktopError('Choose a connection UUID from the saved profiles list.')
    if action=='profile':return run(base+['connection','show','uuid',uid],check=check)
    if action in {'connect','disconnect','forget'}:
        confirm_change(confirm,action.title()+' network profile '+uid+'?')
        nm_permission('network-control' if action!='forget' else 'settings.modify.system',check)
        return run(base+['connection',{'connect':'up','disconnect':'down','forget':'delete'}[action],'uuid',uid],check=check) or 'NetworkManager accepted the change.'
    if action=='modify':
        prop=str(args.get('property',''))
        allowed={'connection.autoconnect','connection.metered','connection.id',
                 'ipv4.method','ipv4.addresses','ipv4.gateway','ipv4.dns','ipv4.ignore-auto-dns','ipv4.never-default','ipv4.route-metric',
                 'ipv6.method','ipv6.addresses','ipv6.gateway','ipv6.dns','ipv6.ignore-auto-dns','ipv6.never-default','ipv6.route-metric',
                 '802-11-wireless.ssid','802-11-wireless.band','802-11-wireless.channel','802-11-wireless.powersave','802-11-wireless.hidden'}
        if prop not in allowed:raise DesktopError('Use the native Wi-Fi settings window for that property or password.')
        value=str(args.get('value',''))
        if len(value)>1000 or '\x00' in value:raise DesktopError('Invalid setting value.')
        confirm_change(confirm,f'Change network profile {uid}: {prop} = {value}? It takes effect when the connection is reapplied or reconnected.')
        own=run(base+['-g','connection.permissions','connection','show','uuid',uid],check=check)
        nm_permission('settings.modify.own' if own else 'settings.modify.system',check)
        run(base+['connection','modify','uuid',uid,prop,value],check=check)
        return prop+': '+run(base+['-g',prop,'connection','show','uuid',uid],check=check)
    raise DesktopError('Unknown Wi-Fi operation.')


def bluetooth(args,confirm,check):
    action=args.get('action','status')
    if action=='settings':return open_settings('bluetooth')
    if action=='status':return run(['bluetoothctl','show'],check=check)
    if action=='adapters':return run(['bluetoothctl','list'],check=check)
    if action=='devices':return run(['bluetoothctl','devices'],check=check)
    if action=='scan':
        run(['bluetoothctl','--timeout','6','scan','on'],timeout=9,check=check)
        return run(['bluetoothctl','devices'],check=check)
    if action in {'power','discoverable','pairable'}:
        value=args.get('value')
        if value not in {'on','off'}:raise DesktopError('Use on or off.')
        confirm_change(confirm,'Set Bluetooth '+action+' '+value+'?')
        run(['bluetoothctl',action,value],check=check)
        return run(['bluetoothctl','show'],check=check)
    mac=str(args.get('device',''))
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}',mac):raise DesktopError('Choose a Bluetooth device address from the device list.')
    if action=='info':return run(['bluetoothctl','info',mac],check=check)
    if action not in {'connect','disconnect','pair','trust','untrust','remove','block','unblock'}:raise DesktopError('Unknown Bluetooth operation.')
    confirm_change(confirm,action.title()+' Bluetooth device '+mac+'? Pairing may require an additional prompt on the laptop.')
    receipt=run(['bluetoothctl','--timeout','10',action,mac],timeout=12,check=check)
    return receipt if action=='remove' else receipt+'\n'+run(['bluetoothctl','info',mac],check=check)


def sound(args,confirm,check):
    action=args.get('action','status')
    if action=='settings':return open_settings('sound')
    if action=='status':return run(['pactl','info'],check=check)
    if action=='devices':return run(['pactl','--format=json','list','sinks'],check=check)+'\n'+run(['pactl','--format=json','list','sources'],check=check)
    if action=='cards':return run(['pactl','--format=json','list','cards'],check=check)
    if action=='streams':return run(['pactl','--format=json','list','sink-inputs'],check=check)+'\n'+run(['pactl','--format=json','list','source-outputs'],check=check)
    target=identifier(args.get('device','@DEFAULT_SOURCE@' if action.startswith('input_') else '@DEFAULT_SINK@'))
    value=args.get('value','')
    commands={'default_output':'set-default-sink','default_input':'set-default-source',
              'volume':'set-sink-volume','input_volume':'set-source-volume',
              'mute':'set-sink-mute','input_mute':'set-source-mute',
              'app_volume':'set-sink-input-volume','app_mute':'set-sink-input-mute',
              'move_playback':'move-sink-input','move_recording':'move-source-output',
              'profile':'set-card-profile','output_port':'set-sink-port','input_port':'set-source-port'}
    if action not in commands:raise DesktopError('Unknown sound operation.')
    if action in {'default_input','input_volume','input_mute','move_recording','profile'}:
        confirm_change(confirm,f'Change sound setting {action} for {target} to {value}?')
    argv=['pactl',commands[action],target]
    if action in {'volume','input_volume','app_volume'}:
        number=int(value)
        if not 0<=number<=100:raise DesktopError('Use a volume from 0 to 100 percent.')
        argv.append(str(number)+'%')
    elif action in {'mute','input_mute','app_mute'}:
        if str(value) not in {'0','1','toggle'}:raise DesktopError('Use 0, 1 or toggle for mute.')
        argv.append(str(value))
    elif action not in {'default_input','default_output'}:argv.append(identifier(value))
    run(argv,check=check)
    if action in {'volume','input_volume','mute','input_mute'}:
        suffix='volume' if 'volume' in action else 'mute'
        return run(['pactl','get-'+('source' if action.startswith('input_') else 'sink')+'-'+suffix,target],check=check)
    return 'Sound server accepted '+action+'.\n'+run(['pactl','info'],check=check)


def shell_call(action,check=lambda:None,**args):
    try:
        raw=run(['gdbus','call','--session','--dest','org.jarvis.DesktopControl',
            '--object-path','/org/jarvis/DesktopControl','--method','org.jarvis.DesktopControl.Call',
            json.dumps(dict(action=action,**args))],check=check)
    except DesktopError as exc:
        raise DesktopError('Wayland desktop controls are unavailable. Run ~/jarvis/scripts/install-desktop-controls in a laptop terminal, then follow its login instructions. '+str(exc)) from exc
    try:result=json.loads(ast.literal_eval(raw)[0])
    except (ValueError,SyntaxError,TypeError,IndexError) as exc:raise DesktopError('Desktop control extension returned an invalid response. Install or update it with scripts/install-desktop-controls.') from exc
    if not result.get('ok'):raise DesktopError(result.get('error','Desktop rejected the request.'))
    return result


def select_window(windows,target):
    from window_intent import target_name, matches as matches_target
    target=target_name(str(target).casefold())
    matches=[w for w in windows if str(w['id'])==target]
    if not matches and target:matches=[w for w in windows if matches_target(w,target)]
    if len(matches)!=1:raise DesktopError('Choose an unambiguous window ID from the window list.')
    return matches[0]


def windows(args,confirm,check):
    action=args.get('action','list')
    if action in {'list','workspaces','monitors'}:return json.dumps(shell_call(action,check),ensure_ascii=False)
    data=shell_call('list',check)
    if args.get('all'):
        if action not in {'workspace','close','minimize','maximize','restore'}:
            raise DesktopError('That operation requires a single window.')
        from window_intent import target_name, matches
        target=target_name(str(args.get('window','')).casefold())
        ids=args.get('windows')
        if ids is not None:
            if not isinstance(ids,list) or not ids:raise DesktopError('No previous windows are selected.')
            selected=[select_window(data['windows'],str(w)) for w in ids]
        else:selected=[w for w in data['windows'] if not target or matches(w,target)]
        if not selected:raise DesktopError('No windows match the requested group.')
        values={}
        if action=='workspace':
            workspace=int(args['workspace'])
            if workspace<1:raise DesktopError('Workspace numbers start at 1.')
            values['workspace']=workspace
        if action=='close':
            confirm_change(confirm,f'Close {len(selected)} windows? Unsaved work may be lost.\n'+ '\n'.join(w['title'] for w in selected))
        results=[]
        for window in selected:
            check()
            try:results.append(shell_call(action,check,window=window['id'],**values))
            except DesktopError as exc:
                raise DesktopError(f'Completed {action} for {len(results)} of {len(selected)} windows before stopping: {exc}') from exc
        verb={'workspace':'moved','close':'closed','minimize':'minimized','maximize':'maximized','restore':'restored'}[action]
        return json.dumps({verb:len(results),'action':action,'window_ids':[w['id'] for w in selected],**values,'results':results},ensure_ascii=False)
    if action=='arrange':
        layout=args.get('layout','grid')
        if layout not in {'grid','columns','rows'}:raise DesktopError('Choose grid, columns or rows.')
        targets=args.get('windows',[])
        if not isinstance(targets,list) or len(targets)>16:raise DesktopError('Arrange up to 16 named windows.')
        selected=[select_window(data['windows'],target)['id'] for target in targets]
        if len(set(selected))!=len(selected):raise DesktopError('Choose different windows for each position.')
        return json.dumps(shell_call('arrange',check,windows=selected,layout=layout),ensure_ascii=False)
    window=select_window(data['windows'],args.get('window',''))
    if action=='close':confirm_change(confirm,'Close '+window['title']+'? Unsaved work may be lost.')
    allowed={'focus','move','resize','geometry','workspace','monitor','maximize','restore','minimize','close','left','right','top','bottom','center','top-left','top-right','bottom-left','bottom-right'}
    if action not in allowed:raise DesktopError('Unknown window operation.')
    values={key:int(args[key]) for key in ('x','y','width','height','workspace','monitor') if key in args}
    result=shell_call(action,check,window=window['id'],**values)
    return json.dumps(dict(result,action=action),ensure_ascii=False)


def type_text(args,confirm,check):
    text=args.get('text','')
    if not isinstance(text,str) or not 0<len(text)<=2000 or any(ord(c)<32 or ord(c)==127 for c in text):
        raise DesktopError('Type up to 2000 printable characters. Return, Tab and other control keys are not sent automatically.')
    data=shell_call('list',check)
    target=str(args.get('window',''))
    matches=[w for w in data['windows'] if str(w['id'])==target]
    if len(matches)!=1:raise DesktopError('Select an exact window ID before typing.')
    w=matches[0]
    requested=str(args.get('_requested_target','')).casefold()
    if not requested or not (requested==str(w['id']) or requested in w['title'].casefold() or requested==w['app'].casefold()):
        raise DesktopError('The chosen window does not match the app you explicitly requested.')
    confirm_change(confirm,'[LAPTOP ONLY] Type this exact text into '+w['app']+' — '+w['title']+'?\n\n'+text+'\n\nNo Return or submit key will be sent. Put the cursor in the intended field before approving.')
    check()
    job=shell_call('type',check,window=w['id'],text=text)['job']
    try:
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            check()
            result=shell_call('typing_status',check,job=job)
            if result['state']!='typing':
                return 'Text input events delivered to '+w['title']+'. Check the app; its contents cannot be verified.' if result['state']=='completed' else result.get('message','Typing stopped; some text may already have been entered.')
            time.sleep(.05)
        raise DesktopError('Typing timed out; some text may already have been entered.')
    finally:
        try:shell_call('cancel_typing',job=job)
        except DesktopError:pass


def system_data(args,check):
    kind=args.get('kind','overview')
    if kind=='overview':
        return json.dumps(dict(os=platform.platform(),kernel=platform.release(),hostname=platform.node(),boot_time=psutil.boot_time(),cpu_count=psutil.cpu_count(),memory=psutil.virtual_memory()._asdict(),swap=psutil.swap_memory()._asdict()),default=str)
    if kind=='processes':
        rows=[]
        for p in psutil.process_iter(['pid','name','username','status','memory_percent','cpu_percent']):
            check();rows.append(p.info)
            if len(rows)>=500:break
        return json.dumps(rows,default=str)
    if kind=='sensors':
        return json.dumps(dict(temperatures=psutil.sensors_temperatures(),fans=psutil.sensors_fans(),battery=psutil.sensors_battery()),default=str)
    if kind=='network':return json.dumps(dict(addresses=psutil.net_if_addrs(),interfaces=psutil.net_if_stats(),traffic=psutil.net_io_counters(pernic=True)),default=str)
    if kind=='storage':return run(['lsblk','--json','-o','NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL'],check=check)
    if kind=='cpu':return run(['lscpu'],check=check)
    if kind=='pci':return run(['lspci','-nnk'],check=check)
    if kind=='usb':return run(['lsusb'],check=check)
    if kind=='services':return run(['systemctl']+(['--user'] if args.get('scope','user')=='user' else [])+['list-units','--all','--no-pager','--plain'],check=check)
    if kind=='logs':
        argv=['journalctl','--user' if args.get('scope','user')=='user' else '--system','-n','100','--no-pager']
        if args.get('unit'):
            unit=str(args['unit'])
            if not re.fullmatch(r'[A-Za-z0-9_.@\\-]+\.(service|scope)',unit):raise DesktopError('Invalid unit name.')
            argv+=['-u',unit]
        return run(argv,check=check)
    raise DesktopError('Unknown system information category.')


def dispatch(name,args,confirm,check):
    functions={'network_settings':network,'bluetooth_settings':bluetooth,'sound_settings':sound,'window_control':windows,'type_text':type_text}
    try:
        if name=='system_data':return system_data(args,check)
        return functions[name](args,confirm,check)
    except (DesktopError,ValueError,KeyError,psutil.Error) as exc:
        return 'Desktop action not completed: '+str(exc)
