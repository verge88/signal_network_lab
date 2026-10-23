"""Standalone Linux worker. Shipped as source; invoked only for a selected lab profile.

No shell commands, system service restarts, database resets or kernel installation.
All process control is scoped to owned PID/start-time/config-file identities.
"""
import hashlib
import glob
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime,timezone

CORE=('nrf','scp','udr','udm','ausf','pcf','nssf','bsf','upf','smf','amf')
IPS=dict(nrf=10,scp=200,udr=20,udm=12,ausf=11,pcf=13,nssf=14,bsf=15,smf=4,upf=7,amf=5)
BASE=Path('/var/tmp')


def run(argv,timeout=15,check=True,data=None):
    r=subprocess.run([str(a) for a in argv],input=data,capture_output=True,text=True,timeout=timeout,env=dict(os.environ,LC_ALL='C'))
    if check and r.returncode:raise ValueError('Команда '+str(argv[0])+' завершилась с ошибкой: '+(r.stderr or r.stdout)[-1200:])
    return r


def names(profile):
    tag=profile['id'][:8]
    return dict(namespace='slran-'+tag,host_if='slh'+tag,ran_if='slr'+tag,upf_if='slu'+tag)


def addresses(profile):
    ran=list(ipaddress.ip_network(profile['ran_subnet']).hosts());ue=ipaddress.ip_network(profile['ue_subnet'])
    return str(ran[0]),str(ran[1]),str(next(ue.hosts()))


def fingerprint(profile):return hashlib.sha256(json.dumps({k:v for k,v in profile.items() if k!='name'},sort_keys=True).encode()).hexdigest()


def redact(value,profile):
    text=json.dumps(value,ensure_ascii=False)
    for sub in profile['subscribers']:
        for key in ('key','opc'):text=re.sub(re.escape(sub[key]),'[redacted]',text,flags=re.I)
    return json.loads(text)


def workspace(profile,create=False):
    ident=profile['id']
    if not re.fullmatch('[a-f0-9]{12}',ident):raise ValueError('Некорректный ID стенда')
    path=BASE/('signal-lab-'+ident)
    if path.is_symlink():raise ValueError('Каталог стенда не может быть символической ссылкой')
    if create:path.mkdir(mode=0o700,parents=False,exist_ok=True)
    if path.exists() and (path.stat().st_uid!=os.geteuid() or path.stat().st_mode&0o077):raise ValueError('Каталог стенда должен принадлежать текущему пользователю и иметь права 0700')
    return path


def write(path,data):
    if path.is_symlink():raise ValueError('Символическая ссылка вместо файла стенда')
    temp=path.with_name(path.name+'.new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(temp,path)


def read_state(root):
    path=root/'state.json'
    if path.is_symlink():raise ValueError('Неверный файл состояния')
    return json.loads(path.read_text()) if path.exists() else dict(processes={},resources=False,prepared=False)


def identity(pid):
    try:
        text=Path(f'/proc/{int(pid)}/stat').read_text();tail=text[text.rindex(')')+2:].split()
        if tail[0]=='Z':return None
        return dict(start=tail[19],argv=Path(f'/proc/{int(pid)}/cmdline').read_bytes().decode(errors='replace').split('\0')[:-1],exe=str(Path(f'/proc/{int(pid)}/exe').resolve()))
    except (OSError,ValueError):return None


def owned(entry):
    actual=identity(entry['pid'])
    return bool(actual and actual['start']==entry['start'] and actual['exe']==entry['exe'] and entry['config'] in actual['argv'])


def preflight(profile):
    tools={name:str(Path(profile['core_bin'])/('open5gs-'+name+'d')) for name in CORE}
    tools.update({name:str(Path(profile['ran_bin'])/name) for name in ('nr-gnb','nr-ue','nr-cli')})
    checks=[]
    def check(key,ok,detail):checks.append(dict(check=key,ok=bool(ok),detail=str(detail)))
    check('Linux',platform.system()=='Linux',platform.release())
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_STREAM,132);s.close();check('SCTP',True,'Сокет SCTP создан')
    except OSError as e:check('SCTP',False,str(e))
    check('TUN',Path('/dev/net/tun').exists(),'/dev/net/tun')
    check('root',os.geteuid()==0,'Для изолированных интерфейсов и UE/UPF требуется root')
    check('iproute2',shutil.which('ip') is not None,shutil.which('ip'))
    check('ping',shutil.which('ping') is not None,shutil.which('ping'))
    check('yaml',importlib.util.find_spec('yaml') is not None,'Python-модуль yaml')
    check('Mongo adapter',importlib.util.find_spec('pymongo') is not None or shutil.which('mongosh') is not None,'PyMongo либо mongosh')
    try:
        with socket.create_connection(('127.0.0.1',27017),timeout=.5):pass
        check('MongoDB',True,'127.0.0.1:27017')
    except OSError as error:check('MongoDB',False,str(error))
    for name,path in tools.items():check(name,os.path.isfile(path) and os.access(path,os.X_OK),path)
    for name in CORE:check(name+'.yaml',(Path(profile['config_dir'])/(name+'.yaml')).is_file(),'Исходная конфигурация Open5GS')
    if importlib.util.find_spec('yaml') is not None:
        import yaml
        for name in CORE:
            try:
                doc=yaml.safe_load((Path(profile['config_dir'])/(name+'.yaml')).read_text())
                valid=isinstance(doc[name].get('sbi',{}).get('server'),list) if name!='upf' else isinstance(doc[name].get('pfcp',{}).get('server'),list)
                check(name+' schema',valid,'sbi.server / pfcp.server')
            except (OSError,ValueError,KeyError,TypeError,yaml.YAMLError) as error:check(name+' schema',False,str(error)[:200])
    versions={}
    # Open5GS does not provide a universal --version switch: never launch a NF for inventory.
    if shutil.which('dpkg-query'):
        versions['open5gs_package']=run(['dpkg-query','-W','-f=${Version}','open5gs'],check=False).stdout[:100]
    if shutil.which('git') and Path(profile['ran_bin']).is_dir():
        versions['ueransim_source']=run(['git','-C',str(Path(profile['ran_bin']).parent),'describe','--tags','--always'],check=False).stdout.strip()[:100]
    if Path(tools['nr-ue']).is_file():versions['nr_ue_sha256']=hashlib.sha256(Path(tools['nr-ue']).read_bytes()).hexdigest()
    candidates=[]
    for pattern in ('/opt/UERANSIM/build/nr-ue','/opt/ueransim/build/nr-ue','/home/*/UERANSIM/build/nr-ue','/home/*/*/UERANSIM/build/nr-ue','/root/UERANSIM/build/nr-ue'):
        candidates.extend(str(Path(path).parent) for path in glob.glob(pattern) if os.access(path,os.X_OK))
    return dict(ready=all(c['ok'] for c in checks),checks=checks,kernel=platform.release(),python=platform.python_version(),versions=versions,suggested_ran_bins=sorted(set(candidates)),
        isolation=names(profile),source='linux-preflight',clock='wall')


def ue_configs(profile):
    host,ran,gateway=addresses(profile);slice_=dict(sst=profile['sst'],sd=int(profile['sd'],16))
    gnb=dict(mcc=profile['mcc'],mnc=profile['mnc'],nci='0x000000010',idLength=32,tac=profile['tac'],
        linkIp='127.0.0.1',ngapIp=ran,gtpIp=ran,amfConfigs=[dict(address=host,port=38412)],slices=[slice_],ignoreStreamIds=True)
    configs={'gnb.yaml':gnb}
    for sub in profile['subscribers']:
        configs['ue-'+sub['imsi']+'.yaml']=dict(supi='imsi-'+sub['imsi'],mcc=profile['mcc'],mnc=profile['mnc'],
            protectionScheme=0,homeNetworkPublicKey='0'*64,homeNetworkPublicKeyId=0,routingIndicator='0000',key=sub['key'],op=sub['opc'],opType='OPC',amf='8000',
            imei='356938035643803',imeiSv='4370816125816151',gnbSearchList=['127.0.0.1'],uacAic=dict(mps=False,mcs=False),
            uacAcc=dict(normalClass=0,class11=False,class12=False,class13=False,class14=False,class15=False),
            sessions=[dict(type='IPv4',apn=profile['dnn'],slice=slice_)],**{'configured-nssai':[slice_],'default-nssai':[slice_]},
            integrity=dict(IA1=True,IA2=True,IA3=True),ciphering=dict(EA1=True,EA2=True,EA3=True),integrityMaxRate=dict(uplink='full',downlink='full'))
    return configs


def rewrite_config(document,name,profile,root):
    if not isinstance(document,dict) or name not in document:raise ValueError('Неизвестная структура конфигурации '+name)
    def transform(value):
        if isinstance(value,dict):return {k:transform(v) for k,v in value.items()}
        if isinstance(value,list):return [transform(v) for v in value]
        if isinstance(value,str):
            return re.sub(r'127\.0\.0\.(\d+)(?!\d)',lambda m:'127.20.0.'+m[1] if int(m[1]) in IPS.values() else m[0],value)
        return value
    doc=transform(document);nf=doc[name];host,ran,gateway=addresses(profile)
    doc['logger']=dict(file=dict(path=str(root/(name+'.log'))),level='info')
    if name in ('udr','pcf'):doc['db_uri']='mongodb://127.0.0.1:27017/signal_lab_'+profile['id']
    if name!='upf':
        if not isinstance(nf.get('sbi',{}).get('server'),list):raise ValueError('Нужна конфигурация Open5GS с sbi.server: '+name)
        nf['sbi']['server']=[dict(address='127.20.0.'+str(IPS[name]),port=7777)]
    plmn=dict(mcc=profile['mcc'],mnc=profile['mnc']);slice_=dict(sst=profile['sst'],sd=profile['sd'])
    if name=='nrf':nf['serving']=[dict(plmn_id=plmn)]
    if name=='amf':
        nf['ngap']=dict(server=[dict(address=host)])
        nf['guami']=[dict(plmn_id=plmn,amf_id=dict(region=2,set=1))];nf['tai']=[dict(plmn_id=plmn,tac=profile['tac'])]
        nf['plmn_support']=[dict(plmn_id=plmn,s_nssai=[slice_])]
    if name in ('smf','upf'):
        nf['pfcp']=dict(server=[dict(address='127.20.0.'+str(IPS[name]))])
        nf['gtpu']=dict(server=[dict(address=host if name=='upf' else '127.20.0.4')])
        nf['session']=[dict(subnet=profile['ue_subnet'],gateway=gateway,**(dict(dev=names(profile)['upf_if']) if name=='upf' else {}))]
    if name=='smf':
        nf['pfcp']['client']=dict(upf=[dict(address='127.20.0.7')]);nf['info']=[dict(s_nssai=[dict(**slice_,dnn=[profile['dnn']])])]
    if name=='nssf':nf['sbi'].setdefault('client',{})['nsi']=[dict(uri='http://127.20.0.10:7777',s_nssai=slice_)]
    return doc


def prepare(profile,root,state):
    import yaml
    if any(owned(v) for v in state['processes'].values()):raise ValueError('Сначала остановите процессы этого стенда')
    if os.geteuid()!=0:raise ValueError('Подготовка изолированной сети требует root')
    if state.get('resources') and (not state.get('prepared') or state.get('fingerprint')!=fingerprint(profile)):raise ValueError('Для изменённого или частично подготовленного профиля сначала очистите его сеть')
    if not state.get('resources'):
        desired=[ipaddress.ip_network(profile[k]) for k in ('ran_subnet','ue_subnet')]
        routes=json.loads(run(['ip','-j','route','show']).stdout)
        for route in routes:
            destination=route.get('dst','default')
            if destination=='default':continue
            try:network=ipaddress.ip_network(destination,strict=False)
            except ValueError:continue
            if network.version==4 and any(network.overlaps(n) for n in desired):raise ValueError('Подсеть стенда пересекается с существующим маршрутом: '+destination)
    configs=ue_configs(profile)
    for name in CORE:
        source=Path(profile['config_dir'])/(name+'.yaml')
        configs[name+'.yaml']=rewrite_config(yaml.safe_load(source.read_text()),name,profile,root)
    fd_source=configs['smf.yaml']['smf'].get('freeDiameter')
    fd_text=None
    if fd_source:
        fd_text=Path(fd_source).read_text()
        fd_text=fd_text.replace('127.0.0.4','127.20.0.4')
        fd_text=fd_text.replace('127.0.0.9','127.20.0.9')
        if not re.search(r'(?m)^\s*ListenOn\s*=',fd_text):fd_text+='\nListenOn = "127.20.0.4";\n'
        configs['smf.yaml']['smf']['freeDiameter']=str(root/'smf-diameter.conf')
    # Validate every document before the first mutation of interfaces or configs.
    for name in CORE:
        if name=='upf':continue
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            try:sock.bind(('127.20.0.'+str(IPS[name]),7777))
            except OSError:raise ValueError('Адрес SBI уже занят: '+name+'. Остановите другой экземпляр стенда.')
    for name,doc in configs.items():write(root/name,doc)  # JSON is a YAML-compatible flow representation.
    if fd_text is not None:
        fd_path=root/'smf-diameter.conf'
        if fd_path.is_symlink():raise ValueError('Неверный файл Diameter')
        fd_path.write_text(fd_text);fd_path.chmod(0o600)
    n=names(profile);host,ran,gateway=addresses(profile)
    if not state.get('resources'):
        if run(['ip','netns','list'],check=False).stdout.find(n['namespace'])>=0:raise ValueError('Имя сетевого пространства занято вне этого профиля')
        for key in ('host_if','upf_if'):
            if run(['ip','link','show',n[key]],check=False).returncode==0:raise ValueError('Имя интерфейса занято вне этого профиля')
        state['resources']=True;write(root/'state.json',state)
        run(['ip','netns','add',n['namespace']])
        run(['ip','link','add',n['host_if'],'type','veth','peer','name',n['ran_if']])
        run(['ip','link','set',n['ran_if'],'netns',n['namespace']])
        run(['ip','addr','add',host+'/30','dev',n['host_if']]);run(['ip','link','set',n['host_if'],'up'])
        for args in (['link','set','lo','up'],['addr','add',ran+'/30','dev',n['ran_if']],['link','set',n['ran_if'],'up']):run(['ip','-n',n['namespace'],*args])
        run(['ip','tuntap','add',n['upf_if'],'mode','tun']);run(['ip','addr','add',gateway+'/'+profile['ue_subnet'].split('/')[1],'dev',n['upf_if']]);run(['ip','link','set',n['upf_if'],'up'])
    state.update(prepared=True,fingerprint=fingerprint(profile),files=sorted(configs));write(root/'state.json',state)
    return dict(prepared=True,files=sorted(configs),workspace=str(root),isolation=n)


def require_prepared(profile,state):
    if not state.get('prepared') or state.get('fingerprint')!=fingerprint(profile):raise ValueError('Профиль изменён или не подготовлен. Остановите стенд и выполните подготовку заново.')


def spawn(profile,root,state,name,executable,config,namespace=False):
    if name in state['processes'] and owned(state['processes'][name]):return
    argv=[executable,'-c',str(root/config)]
    if namespace:argv=['ip','netns','exec',names(profile)['namespace'],*argv]
    log=root/(name+'-console.log')
    if log.is_symlink():raise ValueError('Неверный файл журнала')
    offset=log.stat().st_size if log.exists() else 0
    with log.open('ab',buffering=0) as stream:
        process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True,env=dict(os.environ,DB_URI='mongodb://127.0.0.1:27017/signal_lab_'+profile['id']))
    actual=None
    for _ in range(25):
        time.sleep(.04);actual=identity(process.pid)
        if process.poll() is not None:raise ValueError(name+' завершился при запуске; откройте диагностику')
        if actual and str(root/config) in actual['argv'] and Path(actual['exe']).name==Path(executable).name:break
    if not actual or Path(actual['exe']).name!=Path(executable).name:
        process.kill();process.wait();raise ValueError('Не удалось подтвердить процесс '+name)
    state['processes'][name]=dict(pid=process.pid,start=actual['start'],exe=actual['exe'],config=str(root/config),started_at=time.time(),log_offset=offset)
    write(root/'state.json',state)


def core_ready(profile,state):
    result=[]
    for name in CORE:
        alive=name in state['processes'] and owned(state['processes'][name]);ready=alive
        if alive and name!='upf':
            try:
                with socket.create_connection(('127.20.0.'+str(IPS[name]),7777),timeout=.3):pass
            except OSError:ready=False
        result.append(dict(component=name,alive=bool(alive),ready=bool(ready)))
    return result


def cli(profile,node,command):
    return run(['ip','netns','exec',names(profile)['namespace'],str(Path(profile['ran_bin'])/'nr-cli'),node,'--exec',command],timeout=4,check=False)


def parse_ue(status,sessions):
    import yaml
    s=yaml.safe_load(status) or {};p=yaml.safe_load(sessions) or {}
    if not isinstance(s,dict) or not isinstance(p,dict):return dict(registered=None,sessions=[],error='Неизвестный формат nr-cli')
    registered=s.get('rm-state')=='RM-REGISTERED' or str(s.get('mm-state','')).startswith('MM-REGISTERED/')
    active=[]
    for name,value in p.items():
        if not isinstance(value,dict):continue
        if value.get('state') in ('PS-ACTIVE','ACTIVE') and value.get('address'):
            active.append(dict(name=name,address=value['address'],apn=value.get('apn'),state=value['state']))
    return dict(registered=registered if 'rm-state' in s or 'mm-state' in s else None,sessions=active,status=s)


def snapshot(profile,root,state):
    results=[];deadline=time.monotonic()+40
    for sub in profile['subscribers']:
        key='ue-'+sub['imsi'];alive=key in state['processes'] and owned(state['processes'][key])
        item=dict(imsi=sub['imsi'],alive=alive,registered=None,sessions=[])
        if alive and time.monotonic()<deadline:
            try:
                s=cli(profile,'imsi-'+sub['imsi'],'status');p=cli(profile,'imsi-'+sub['imsi'],'ps-list')
                if not s.returncode and not p.returncode:item.update(parse_ue(s.stdout,p.stdout))
                else:item['error']=(s.stderr or p.stderr or s.stdout)[:500]
            except (OSError,ValueError,subprocess.TimeoutExpired) as e:item['error']=str(e)[:500]
        results.append(item)
    logs={}
    for path in sorted(root.glob('*.log')):
        if not path.is_symlink():
            with path.open('rb') as stream:stream.seek(max(0,path.stat().st_size-12000));logs[path.name]=stream.read().decode(errors='replace')
    gnb_alive='gnb' in state['processes'] and owned(state['processes']['gnb']);gnb_connected=None
    if gnb_alive:
        try:
            import yaml
            dump=run(['ip','netns','exec',names(profile)['namespace'],str(Path(profile['ran_bin'])/'nr-cli'),'--dump'],timeout=4,check=False)
            nodes=[n.strip() for n in dump.stdout.splitlines() if n.strip().startswith('UERANSIM-gnb-')]
            if len(nodes)==1:
                status=yaml.safe_load(cli(profile,nodes[0],'status').stdout)
                if isinstance(status,dict) and isinstance(status.get('is-ngap-up'),bool):gnb_connected=status['is-ngap-up']
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    runtime_version=None
    for key,text in logs.items():
        if key.startswith(('gnb','ue-')) and (m:=re.search(r'UERANSIM v([\d.]+)',text)):runtime_version=m[1];break
    return redact(dict(source='open5gs-ueransim',clock='wall',observed_at=datetime.now(timezone.utc).isoformat(),prepared=state.get('prepared',False),
        profile_matches=state.get('fingerprint')==fingerprint(profile),core=core_ready(profile,state),
        gnb_alive=gnb_alive,gnb_connected=gnb_connected,ueransim_runtime_version=runtime_version,ues=results,logs=logs),profile)


def provision(profile):
    documents=[]
    for sub in profile['subscribers']:
            speed=dict(downlink=dict(value=1,unit=3),uplink=dict(value=1,unit=3))
            session=dict(name=profile['dnn'],type=1,qos=dict(index=9,arp=dict(priority_level=8,pre_emption_capability=1,pre_emption_vulnerability=2)),ambr=speed,pcc_rule=[])
            document=dict(imsi=sub['imsi'],schema_version=1,msisdn=[],imeisv=[],mme_host=[],mme_realm=[],purge_flag=[],
                slice=[dict(sst=profile['sst'],sd=profile['sd'],default_indicator=True,session=[session])],
                security=dict(k=sub['key'],op=None,opc=sub['opc'],amf='8000'),ambr=speed,access_restriction_data=32,
                network_access_mode=0,subscriber_status=0,operator_determined_barring=0,subscribed_rau_tau_timer=12,__v=0,_signal_lab=profile['id'])
            documents.append(document)
    if importlib.util.find_spec('pymongo') is not None:
        from pymongo import MongoClient
        with MongoClient('mongodb://127.0.0.1:27017',serverSelectionTimeoutMS=2000) as client:
            collection=client['signal_lab_'+profile['id']].subscribers
            for document in documents:
                old=collection.find_one(dict(imsi=document['imsi']))
                if old and old.get('_signal_lab')!=profile['id']:raise ValueError('Абонент не принадлежит профилю')
                collection.replace_one(dict(imsi=document['imsi']),document,upsert=True)
    else:
        script="const p=JSON.parse(require('fs').readFileSync(0,'utf8')); const c=new Mongo('mongodb://127.0.0.1:27017').getDB(p.database).subscribers; for(const d of p.documents){const old=c.findOne({imsi:d.imsi});if(old && old._signal_lab!==d._signal_lab){throw Error('Subscriber ownership mismatch');}c.replaceOne({imsi:d.imsi},d,{upsert:true});}print('ok');"
        run(['mongosh','--quiet','--nodb','--eval',script],data=json.dumps(dict(database='signal_lab_'+profile['id'],documents=documents)))
    return dict(database='signal_lab_'+profile['id'],subscribers=[s['imsi'] for s in profile['subscribers']])


def stop(root,state):
    for name,entry in reversed(list(state['processes'].items())):
        if not owned(entry):continue
        try:os.kill(entry['pid'],signal.SIGTERM)
        except ProcessLookupError:continue
        for _ in range(30):
            if not owned(entry):break
            time.sleep(.05)
        if owned(entry):
            try:os.kill(entry['pid'],signal.SIGKILL)
            except ProcessLookupError:pass
    state['processes']={};state.pop('pfcp_pair',None);write(root/'state.json',state)
    return dict(stopped=True)


def ping_samples(text):
    sent=re.search(r'(\d+) packets transmitted, (\d+) (?:packets )?received',text)
    values=[float(v) for v in re.findall(r'time=([0-9.]+)\s*ms',text)]
    return dict(sent=int(sent[1]) if sent else None,received=int(sent[2]) if sent else None,rtt_samples_ms=values,
        rtt_mean_ms=sum(values)/len(values) if values else None,rtt_p95_ms=sorted(values)[max(0,math.ceil(.95*len(values))-1)] if values else None)


def procedure_events(text):
    """Conservative adapter for paired UERANSIM NAS events, one outstanding procedure per UE."""
    pending={};events=[];ambiguous=set()
    starts={'registration':'Sending Initial Registration','pdu':'Sending PDU Session Establishment Request'}
    ends={'registration':('Initial Registration is successful','Registration Reject received'),
          'pdu':('PDU Session establishment is successful','PDU Session Establishment Reject received')}
    for line in text.splitlines():
        match=re.match(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\]\s+\[nas\]',line)
        if not match:continue
        stamp=datetime.strptime(match[1],'%Y-%m-%d %H:%M:%S.%f')
        for kind,marker in starts.items():
            if marker in line:
                if kind in pending:ambiguous.add(kind)
                pending[kind]=stamp
            success,failure=ends[kind]
            if success not in line and failure not in line:continue
            start=pending.pop(kind,None)
            if start is not None and kind not in ambiguous and stamp>=start:
                events.append(dict(procedure=kind,start=start.isoformat(),end=stamp.isoformat(),latency_ms=(stamp-start).total_seconds()*1000,status='success' if success in line else 'rejected'))
            ambiguous.discard(kind)
    return dict(events=events,incomplete=len(pending),adapter='ueransim-nas-paired-v1',boundary='UE NAS send to matching NAS success/reject log; wall clock at millisecond resolution')


def measurement(profile,root,state):
    before=snapshot(profile,root,state);data=[];gateway=addresses(profile)[2];procedures=[];deadline=time.monotonic()+90
    for sub in profile['subscribers']:
        entry=state['processes'].get('ue-'+sub['imsi']);path=root/('ue-'+sub['imsi']+'-console.log')
        if entry and path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:stream.seek(entry.get('log_offset',0));raw=stream.read(1_000_001)
            if len(raw)>1_000_000:procedures.append(dict(imsi=sub['imsi'],events=[],incomplete=None,error='Журнал больше 1 МБ: выборка процедур недоступна'))
            else:procedures.append(dict(imsi=sub['imsi'],**procedure_events(raw.decode(errors='replace'))))
    interfaces=json.loads(run(['ip','netns','exec',names(profile)['namespace'],'ip','-j','addr','show']).stdout)
    for ue in before['ues']:
        for session in ue['sessions']:
            if time.monotonic()>deadline:data.append(dict(imsi=ue['imsi'],error='Истекло общее время измерений'));continue
            text=json.dumps(session['address']);matches=re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b',text)
            ips={str(ipaddress.ip_address(v)) for v in matches if ipaddress.ip_address(v) in ipaddress.ip_network(profile['ue_subnet'])}
            devices=[i['ifname'] for i in interfaces if re.fullmatch(r'uesimtun\d+',i['ifname']) and any(a.get('local') in ips for a in i.get('addr_info',[]))]
            if len(devices)!=1:data.append(dict(imsi=ue['imsi'],error='Не удалось однозначно сопоставить PDU-адрес с UE TUN'));continue
            result=run(['ip','netns','exec',names(profile)['namespace'],'ping','-n','-c','5','-W','1','-I',devices[0],gateway],timeout=12,check=False)
            data.append(dict(imsi=ue['imsi'],interface=devices[0],target=gateway,exit_code=result.returncode,**ping_samples(result.stdout)))
    return dict(**before,data_tests=data,procedure_observations=procedures,procedure_samples=[],measurement_boundary='ICMP UE TUN to local UPF gateway; NAS procedure timings are separate paired log observations',
        provenance=dict(kernel=platform.release(),wall_time=datetime.now(timezone.utc).isoformat(),adapter='open5gs-sbi-server-v1',network_isolation=names(profile),environment=preflight(profile)))


def dispatch(request):
    p=request['profile'];action=request['action']
    if action=='preflight':return preflight(p)
    root=workspace(p,create=action=='prepare')
    if not root.exists():
        if action=='status':return dict(prepared=False,source='open5gs-ueransim',clock='wall')
        raise ValueError('Сначала подготовьте профиль')
    import fcntl
    with (root/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);state=read_state(root)
        if action=='status':return snapshot(p,root,state)
        if action=='stop':return stop(root,state)
        if action=='cleanup':
            if any(owned(v) for v in state['processes'].values()):raise ValueError('Сначала остановите процессы')
            if state.get('resources'):
                n=names(p)
                run(['ip','netns','del',n['namespace']],check=False)
                for key in ('host_if','upf_if'):run(['ip','link','del',n[key]],check=False)
                remaining=run(['ip','netns','list'],check=False).stdout
                if any(line.split()[0]==n['namespace'] for line in remaining.splitlines() if line.split()) or any(run(['ip','link','show',n[key]],check=False).returncode==0 for key in ('host_if','upf_if')):raise ValueError('Не все ресурсы удалены; проверьте права и повторите очистку')
            state.update(resources=False,prepared=False);write(root/'state.json',state);return dict(cleaned=True)
        if action=='prepare':
            checks=preflight(p)
            if not checks['ready']:raise ValueError('Среда не готова: '+', '.join(c['check'] for c in checks['checks'] if not c['ok']))
            return prepare(p,root,state)
        require_prepared(p,state)
        if action=='provision':return provision(p)
        if action=='start_core':
            offset=(root/'smf.log').stat().st_size if (root/'smf.log').exists() else 0
            for name in CORE:
                spawn(p,root,state,name,str(Path(p['core_bin'])/('open5gs-'+name+'d')),name+'.yaml')
                if name!='upf':
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        if next(c for c in core_ready(p,state) if c['component']==name)['ready']:break
                        time.sleep(.15)
                    else:raise ValueError(name+' не открыл SBI; откройте диагностику')
            pair=[state['processes'][key]['start'] for key in ('smf','upf')]
            if state.get('pfcp_pair')==pair:return snapshot(p,root,state)
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                path=root/'smf.log'
                if path.exists():
                    with path.open('rb') as stream:stream.seek(offset);data=stream.read(256000).decode(errors='replace')
                    if 'PFCP associated [127.20.0.7]' in data:break
                time.sleep(.15)
            else:raise ValueError('SBI открыт, но связь SMF↔UPF по PFCP не подтверждена; проверьте журнал SMF')
            state['pfcp_pair']=pair;write(root/'state.json',state)
            return snapshot(p,root,state)
        if action=='start_ran':
            if not all(c['ready'] for c in core_ready(p,state)):raise ValueError('Не все процессы ядра готовы')
            spawn(p,root,state,'gnb',str(Path(p['ran_bin'])/'nr-gnb'),'gnb.yaml',True);return snapshot(p,root,state)
        if action=='start_ues':
            if 'gnb' not in state['processes'] or not owned(state['processes']['gnb']):raise ValueError('Сначала запустите gNB')
            for sub in p['subscribers']:spawn(p,root,state,'ue-'+sub['imsi'],str(Path(p['ran_bin'])/'nr-ue'),'ue-'+sub['imsi']+'.yaml',True)
            return snapshot(p,root,state)
        if action=='measure':return measurement(p,root,state)
        if action=='remove_subscriber':
            imsi=request.get('imsi')
            if imsi not in {s['imsi'] for s in p['subscribers']}:raise ValueError('Неизвестный тестовый IMSI')
            if importlib.util.find_spec('pymongo') is not None:
                from pymongo import MongoClient
                with MongoClient('mongodb://127.0.0.1:27017',serverSelectionTimeoutMS=2000) as client:
                    result=client['signal_lab_'+p['id']].subscribers.delete_one(dict(imsi=imsi,_signal_lab=p['id']));count=result.deleted_count
            else:
                script="const p=JSON.parse(require('fs').readFileSync(0,'utf8'));const c=new Mongo('mongodb://127.0.0.1:27017').getDB(p.database).subscribers;print(c.deleteOne({imsi:p.imsi,_signal_lab:p.owner}).deletedCount);"
                count=int(run(['mongosh','--quiet','--nodb','--eval',script],data=json.dumps(dict(database='signal_lab_'+p['id'],imsi=imsi,owner=p['id']))).stdout.strip())
            return dict(removed=count,imsi=imsi)
        raise ValueError('Неизвестная операция')


def main(request):
    try:result=dict(ok=True,result=dispatch(request))
    except Exception as error:result=dict(ok=False,error=str(error)[:2000])
    print(json.dumps(redact(result,request['profile']),ensure_ascii=False),flush=True)
