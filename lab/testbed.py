"""Windows-side controller for an explicitly selected, prepared Linux 5G lab."""
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid
from concurrent.futures import CancelledError
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from . import storage

CONTRACT='testbed-v1'
BOOTSTRAP="import json,sys; p=json.load(sys.stdin); ns={'__name__':'signal_lab_agent'}; exec(compile(p.pop('agent'),'signal_lab_agent','exec'),ns); ns['main'](p)"


class Subscriber(BaseModel):
    model_config=ConfigDict(extra='forbid')
    imsi:str=Field(default='999700000000001',pattern=r'^\d{15}$')
    key:str=Field(default_factory=lambda:secrets.token_hex(16).upper(),pattern=r'^[0-9a-fA-F]{32}$')
    opc:str=Field(default_factory=lambda:secrets.token_hex(16).upper(),pattern=r'^[0-9a-fA-F]{32}$')


class Profile(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id:str=Field(default_factory=lambda:uuid.uuid4().hex[:12],pattern=r'^[a-f0-9]{12}$')
    name:str=Field(default='Локальный 5G SA',min_length=1,max_length=100)
    distro:str=Field(default='Ubuntu',pattern=r'^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$')
    root:bool=False
    core_bin:str='/usr/bin'
    config_dir:str='/etc/open5gs'
    ran_bin:str='/opt/UERANSIM/build'
    mcc:str=Field(default='999',pattern=r'^\d{3}$')
    mnc:str=Field(default='70',pattern=r'^\d{2,3}$')
    tac:int=Field(default=1,ge=1,le=65535)
    sst:int=Field(default=1,ge=1,le=255)
    sd:str=Field(default='000001',pattern=r'^[0-9a-fA-F]{6}$')
    dnn:str=Field(default='internet',pattern=r'^[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}$')
    ran_subnet:str='10.203.0.0/30'
    ue_subnet:str='10.84.0.0/24'
    subscribers:list[Subscriber]=Field(default_factory=lambda:[Subscriber()],min_length=1,max_length=16)

    @model_validator(mode='after')
    def valid(self):
        for path in (self.core_bin,self.config_dir,self.ran_bin):
            if not re.fullmatch(r'/[A-Za-z0-9_./-]+',path) or '..' in Path(path).parts:raise ValueError('Укажите абсолютный Linux-путь без переходов к родителю')
        ran=ipaddress.ip_network(self.ran_subnet,strict=True);ue=ipaddress.ip_network(self.ue_subnet,strict=True)
        private=[ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
        if ran.version!=4 or ran.prefixlen!=30 or ue.version!=4 or not 16<=ue.prefixlen<=28 or ran.overlaps(ue) or not all(any(n.subnet_of(r) for r in private) for n in (ran,ue)):raise ValueError('Нужны непересекающиеся частные IPv4-сети: /30 для RAN и /16…/28 для UE')
        if any(not s.imsi.startswith(self.mcc+self.mnc) for s in self.subscribers):raise ValueError('IMSI должен начинаться с MCC/MNC стенда')
        if len({s.imsi for s in self.subscribers})!=len(self.subscribers):raise ValueError('IMSI должны быть уникальны')
        return self


def public_profile(profile):
    d=profile.model_dump() if isinstance(profile,Profile) else dict(profile)
    return dict(d,subscribers=[dict(imsi=s['imsi'],key='[скрыто]',opc='[скрыто]') for s in d['subscribers']])


def decode_output(data):
    return data.decode('utf-16-le' if b'\0' in data[:100] else 'utf-8-sig',errors='replace').lstrip('\ufeff')


def execute(argv,payload=None,timeout=30,cancel=None):
    """No command shell. communicate() drains both streams and allows cancellation."""
    options=dict(stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    process=subprocess.Popen(argv,**options);started=time.monotonic();first=True
    try:
        while True:
            if cancel and cancel():raise CancelledError('Операция отменена; состояние Linux-процессов проверяется отдельно')
            if time.monotonic()-started>timeout:raise TimeoutError('Истекло время ожидания Linux. Откройте «Состояние», прежде чем повторять запуск.')
            try:out,err=process.communicate(input=payload if first else None,timeout=.2);break
            except subprocess.TimeoutExpired:first=False
    except BaseException:
        process.kill();process.communicate();raise
    return process.returncode,decode_output(out),decode_output(err)


def distributions(cancel=None):
    code,out,err=execute(['wsl.exe','--list','--verbose'],cancel=cancel)
    if code:raise ValueError((err or out or 'Не удалось получить список WSL')[:2000])
    rows=[]
    for line in out.splitlines():
        match=re.match(r'^\s*\*?\s*(.+?)\s{2,}(\S+)\s+(\d+)\s*$',line)
        if match:rows.append(dict(name=match[1].strip(),state=match[2],version=int(match[3])))
    return rows


def agent_source():
    path=Path(sys._MEIPASS)/'linux'/'testbed_agent.py' if getattr(sys,'frozen',False) else Path(__file__).with_name('testbed_agent.py')
    return path.read_text(encoding='utf-8')


def request(profile,action,progress=None,cancel=None,**kwargs):
    p=Profile.model_validate(profile.model_dump() if isinstance(profile,Profile) else profile)
    allowed={'preflight','prepare','provision','start_core','start_ran','start_ues','status','stop','measure','remove_subscriber','cleanup'}
    if action not in allowed:raise ValueError('Неизвестная операция стенда')
    argv=['wsl.exe','--distribution',p.distro]
    if p.root:argv+=['--user','root']
    argv+=['--exec','python3','-c',BOOTSTRAP]
    payload=json.dumps(dict(agent=agent_source(),profile=p.model_dump(),action=action,**kwargs)).encode()
    if progress:progress(0)
    code,out,err=execute(argv,payload,timeout=180 if action=='measure' else 90,cancel=cancel)
    try:result=json.loads(out)
    except ValueError:raise ValueError((err or out or 'Linux не вернул отчёт JSON')[:2000])
    if code or not result.get('ok'):raise ValueError(result.get('error') or err[:2000] or 'Ошибка стенда')
    if progress:progress(100)
    return result['result']


def measure(profile,progress=None,cancel=None):
    p=Profile.model_validate(profile.model_dump() if isinstance(profile,Profile) else profile)
    result=request(p,'measure',progress,cancel)
    if cancel and cancel():raise CancelledError()
    return storage.save('testbed_run',p.name+' · измерение',dict(contract=CONTRACT,source='open5gs-ueransim',clock='wall',profile=public_profile(p),**result))


