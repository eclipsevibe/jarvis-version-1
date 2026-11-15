"""User-session app budgets using runtime systemd cgroup controls.

CPU is distributed across app units so their combined quotas respect the budget.
RAM uses MemoryHigh (reclaim/throttle), never an OOM-inducing MemoryMax.
Desktop infrastructure and recovery terminals are always protected.
"""
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import psutil

ROOT=Path('/sys/fs/cgroup')
_manager=None
_manager_lock=threading.Lock()


def fan_status():
    values=[]
    for base in Path('/sys/class/hwmon').glob('hwmon*'):
        for path in base.glob('fan*_input'):
            try:
                rpm=int(path.read_text().strip())
                if rpm>=0:values.append(rpm)
            except (OSError,ValueError):pass
    return ', '.join(str(v)+' RPM' for v in values) if values else 'RPM not reported by the laptop hardware/driver'


def run(*argv):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=12,stdin=subprocess.DEVNULL)
    if result.returncode:raise RuntimeError((result.stderr or result.stdout).strip()[:1000])
    return result.stdout.strip()


def protected(name):
    if name in {'jarvis.service','init.scope'}:return True
    return any(part in name.lower() for part in ('gnome-shell','gnome-session','org.gnome.shell','sessionmanager','settingsdaemon',
        'settings-daemon','dbus','pipewire','pulseaudio','wireplumber','xdg-desktop','portal',
        'terminal','konsole','kitty','codex','at-spi','keyring','polkit'))


def scan_units():
    rows=json.loads(run('systemctl','--user','list-units','--type=service,scope','--state=running','--output=json','--no-pager'))
    found=[]
    prefix=f'/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/'
    for row in rows:
        name=row['unit']
        if protected(name):continue
        detail=run('systemctl','--user','show',name,'-p','ControlGroup','-p','InvocationID')
        properties=dict(line.split('=',1) for line in detail.splitlines() if '=' in line)
        group=properties.get('ControlGroup','')
        if protected(group):continue
        if not group.startswith(prefix):continue
        path=(ROOT/group.lstrip('/')).resolve()
        if not path.is_relative_to(ROOT.resolve()):continue
        try:
            cpu=(path/'cpu.max').read_text().strip()
            memory=(path/'memory.high').read_text().strip()
            current=int((path/'memory.current').read_text())
            pids=[]
            for file in path.rglob('cgroup.procs'):
                pids.extend(int(v) for v in file.read_text().split())
        except (OSError,ValueError):continue
        found.append(dict(unit=name,invocation=properties.get('InvocationID',''),path=str(path),
            cpu=cpu,memory=memory,current=current,pids=sorted(set(pids)),description=row.get('description',name)))
    return found


def quota(cpu):
    amount,period=cpu.split()
    return None if amount=='max' else int(amount)/int(period)*100


def allocation(units,cpu_percent,ram_gib):
    if not 25<=cpu_percent<=95:raise ValueError('CPU budget must be 25–95% of total machine capacity, leaving recovery headroom.')
    if not units:return {}
    per_cpu=cpu_percent*(psutil.cpu_count() or 1)/len(units)
    if per_cpu<5:raise ValueError('Too many app units for this CPU budget; raise it or exempt some apps.')
    target=None
    if ram_gib:
        total=psutil.virtual_memory().total
        target=int(float(ram_gib)*1024**3)
        if target<2*1024**3 or target>min(total-2*1024**3,total*.8):
            raise ValueError('RAM budget must be at least 2 GiB and leave at least 2 GiB / 20% for the desktop and system.')
        required=sum(max(128*1024**2,int(u['current']*1.25)) for u in units)
        if target<required:raise ValueError('That RAM budget is below current managed-app use plus safety headroom. Close apps or choose a larger RAM budget.')
    result={}
    weights=[max(128*1024**2,int(u['current']*1.25)) for u in units]
    for u,weight in zip(units,weights):
        old_cpu=quota(u['cpu'])
        cpu=min(per_cpu,old_cpu) if old_cpu is not None else per_cpu
        memory=int(target*weight/sum(weights)) if target else None
        if memory and u['memory']!='max':memory=min(memory,int(u['memory']))
        result[u['unit']]=(round(cpu,3),memory)
    return result


class ResourceManager:
    def __init__(self,directory=None):
        self.directory=Path(directory or Path.home()/'.config/jarvis/resources')
        self.lock=threading.RLock();self.wake=threading.Event();self.stop_event=threading.Event()
        self.thread=None;self.exemptions={};self.units=[];self.original={};self.fingerprint=None
        self.config={'enabled':False,'cpu_percent':75,'ram_gib':None}
        self.message='Limits are off. Kernel/system services and protected desktop processes are outside user-app budgets.'
        try:self.config.update(json.loads((self.directory/'limits.json').read_text()))
        except (OSError,ValueError,TypeError):pass
        try:self.original=json.loads((self.directory/'original.json').read_text())
        except (OSError,ValueError,TypeError):pass
        if self.config['enabled'] or self.original:self.refresh()

    def _save(self,name,value):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.directory.chmod(0o700)
        target=self.directory/name;tmp=target.with_suffix('.tmp')
        tmp.write_text(json.dumps(value));tmp.chmod(0o600);tmp.replace(target)

    def snapshot(self):
        with self.lock:
            return dict(**self.config,message=self.message,exemptions=list(self.exemptions),
                apps=[dict(unit=u['unit'],description=u['description'],pids=u['pids'],memory_mib=round(u['current']/1024**2)) for u in self.units],
                controlled=len(self.original),fan=fan_status())

    def refresh(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread=threading.Thread(target=self._loop,daemon=True,name='jarvis-resource-budgets');self.thread.start()
            self.wake.set()

    def _set(self,unit,cpu,memory):
        args=['systemctl','--user','set-property','--runtime',unit,'CPUQuota='+('' if cpu is None else str(cpu)+'%')]
        args.append('MemoryHigh='+('infinity' if memory in {None,'max'} else str(memory)))
        run(*args)

    def _restore(self,unit,live):
        old=self.original[unit]
        if live and live['invocation']==old['invocation']:
            self._set(unit,quota(old['cpu']),old['memory'])
        del self.original[unit]
        self._save('original.json',self.original)

    def _sync(self):
        units=scan_units()
        with self.lock:
            self.units=units;live={u['unit']:u for u in units}
            self.exemptions={name:inv for name,inv in self.exemptions.items() if name in live and live[name]['invocation']==inv}
            for name in list(self.original):
                if not self.config['enabled'] or name not in live or name in self.exemptions or self.original[name]['invocation']!=live[name]['invocation']:
                    self._restore(name,live.get(name))
            if not self.config['enabled']:
                self.message='Limits are off; original app limits restored.';self.fingerprint=None;return
            controlled=[dict(u,**{k:self.original[u['unit']][k] for k in ('cpu','memory')}) if u['unit'] in self.original else dict(u) for u in units if u['unit'] not in self.exemptions]
            mark=(tuple((u['unit'],u['invocation']) for u in controlled),self.config['cpu_percent'],self.config['ram_gib'])
            if mark==self.fingerprint:return
            plan=allocation(controlled,self.config['cpu_percent'],self.config['ram_gib'])
            for u in controlled:
                name=u['unit']
                if name not in self.original:
                    self.original[name]={k:u[k] for k in ('cpu','memory','invocation')}
                    self._save('original.json',self.original)
                cpu,memory=plan[name]
                # If RAM limiting is off, retain any original user-supplied limit.
                memory=self.original[name]['memory'] if memory is None else memory
                self._set(name,cpu,memory)
                actual=quota((Path(u['path'])/'cpu.max').read_text().strip())
                if actual is None or abs(actual-cpu)>.1:raise RuntimeError('CPU limit could not be verified for '+name)
                expected='max' if memory in {None,'max'} else str(memory)
                if (Path(u['path'])/'memory.high').read_text().strip()!=expected:raise RuntimeError('RAM pressure limit could not be verified for '+name)
            self.fingerprint=mark
            self.message=f"Verified limits on {len(controlled)} user-app units; {len(self.exemptions)} temporary exemptions. RAM uses a soft pressure limit, not a hard kill limit."

    def _loop(self):
        while not self.stop_event.is_set():
            self.wake.clear()
            try:self._sync()
            except Exception as exc:
                with self.lock:
                    self.message='Resource limits not fully applied: '+str(exc)
                    self.config['enabled']=False
                    try:self._save('limits.json',self.config)
                    except OSError:pass
                # Roll back partial changes; retain the recovery journal if that fails.
                try:
                    live={u['unit']:u for u in scan_units()}
                    for name in list(self.original):self._restore(name,live.get(name))
                except Exception:pass
            with self.lock:active=self.config['enabled'] or bool(self.original)
            if not active:
                with self.lock:self.thread=None
                return
            self.wake.wait(3)

    def command(self,args,confirm):
        action=args.get('action','status')
        if action in {'status','processes'}:
            self.refresh();return json.dumps(self.snapshot())
        if action=='set':
            cpu=int(args.get('cpu_percent',self.config['cpu_percent']))
            ram=args.get('ram_gib',self.config['ram_gib'])
            # Validate before enabling. A second live validation happens before apply.
            if not 25<=cpu<=95:return 'CPU budget must be 25–95 percent.'
            if not confirm(f'Apply {cpu}% combined CPU capacity and '+(str(ram)+' GiB soft RAM budget' if ram else 'no additional RAM budget')+' to managed user apps? Desktop infrastructure, system services and exempt apps are excluded.'):return 'Cancelled.'
            with self.lock:
                self.config.update(enabled=True,cpu_percent=cpu,ram_gib=ram)
                self.message='Applying limits; not active until verified.'
                self._save('limits.json',self.config);self.fingerprint=None
        elif action=='disable':
            with self.lock:
                self.config['enabled']=False;self.message='Restoring original app limits.';self._save('limits.json',self.config)
        elif action in {'exempt','unexempt'}:
            pid=int(args.get('pid',0))
            with self.lock:
                unit=next((u for u in self.units if pid in u['pids']),None)
                if not unit:return 'Refresh the running apps list and select a current process. Protected/system processes cannot be changed.'
                if action=='exempt':self.exemptions[unit['unit']]=unit['invocation']
                else:self.exemptions.pop(unit['unit'],None)
                self.fingerprint=None
        else:return 'Unknown resource-limit operation.'
        self.refresh();return self.message


def get_manager():
    global _manager
    with _manager_lock:
        if _manager is None:_manager=ResourceManager()
    return _manager
