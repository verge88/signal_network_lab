"""Bounded discrete-event model. Does not open signaling sockets or encode packets."""
import heapq
import math
import hashlib
import json
import platform
from concurrent.futures import CancelledError
from collections import defaultdict, deque
import numpy as np
from .schema import Project

FEATURES = ['rate', 'delivery_ratio', 'latency_mean', 'latency_p95', 'loss_ratio', 'timeout_ratio', 'queue_mean', 'queue_max', 'utilization', 'route_changes']


def simulate(project: Project, progress=None, cancel=None):
    if project.engine == 'procedures':
        if any(f.operation.startswith('5G ') for f in project.flows):
            from .fiveg import FiveGSimulation
            return FiveGSimulation(project, progress, cancel).run()
        from .procedures import ProcedureSimulation
        return ProcedureSimulation(project, progress, cancel).run()
    result = simulate_messages(project, progress, cancel)
    result['feature_contract'] = 'messages-v1'
    return result


def simulate_messages(project: Project, progress=None, cancel=None):
    def checkpoint(value=None):
        if cancel is not None and cancel():
            raise CancelledError('Вычисление отменено')
        if progress is not None and value is not None:
            progress(value)
    checkpoint(0)
    rng = np.random.default_rng(project.seed)
    nodes = {n.id: n for n in project.nodes}
    links = {l.id: l for l in project.links}
    adjacency = defaultdict(list)
    for l in project.links:
        adjacency[l.source].append((l.target, l))
        adjacency[l.target].append((l.source, l))
    def active(kind, target, t):
        return [e for e in project.incidents if e.kind == kind and e.target == target and e.start <= t < e.start + e.duration]
    def up(n, t):
        return nodes[n].enabled and not active('node_down', n, t)
    def route(src, dst, proto, t):
        if not up(src,t) or not up(dst,t): return None
        q = [(0., src, [])]
        seen = set()
        while q:
            cost, n, path = heapq.heappop(q)
            if n in seen: continue
            seen.add(n)
            if n == dst: return path
            for nxt, l in adjacency[n]:
                # SS7 and M3UA belong to the same abstract SS7 transport domain.
                if not l.enabled or active('link_down',l.id,t) or not up(nxt,t): continue
                if proto in ('SS7','M3UA'):
                    if l.protocol not in ('SS7','M3UA'):continue
                elif l.protocol!=proto:continue
                penalty = max((e.magnitude for e in active('latency',l.id,t)), default=1)
                if nxt not in seen:
                    heapq.heappush(q,(cost+l.latency_ms*penalty+nodes[nxt].processing_ms,nxt,path+[(nxt,l.id)]))
        return None
    count = math.ceil(project.duration/project.window)
    buckets = [dict(sent=0, delivered=0, lost=0, timeouts=0, latency=[], queue=[], changes=0) for _ in range(count)]
    node_stats = {n:dict(processed=0,dropped=0,max_queue=0,busy_ms=0) for n in nodes}
    link_stats = {l:dict(messages=0,dropped=0,bytes=0) for l in links}
    busy = defaultdict(float)
    pending = defaultdict(deque)
    wire = defaultdict(float)
    busy_windows = [defaultdict(float) for _ in range(count)]
    traces = []
    previous = {}
    events = []
    serial = 0
    for f in project.flows:
        checkpoint()
        # Sample each constant-intensity segment, including fractional boundaries.
        boundaries=sorted({0.,float(project.duration)} | {max(0.,min(float(project.duration),t)) for e in project.incidents if e.kind=='surge' and e.target==f.id for t in (e.start,e.start+e.duration)})
        for lo,hi in zip(boundaries,boundaries[1:]):
            factor = max((e.magnitude for e in active('surge',f.id,(lo+hi)/2)),default=1)
            for t in sorted(lo+rng.random(rng.poisson(f.rate*factor*(hi-lo)))*(hi-lo)):
                serial += 1
                heapq.heappush(events,(float(t)*1000,serial,'start',f,None,0,0.))
    total_messages=len(events)
    finished=0
    steps=0
    def finish(f, start, end, status, path, bucket):
        nonlocal finished
        finished+=1
        b = buckets[bucket]
        if status == 'delivered':
            b['delivered'] += 1
            b['latency'].append(end-start)
        else:
            b['lost'] += 1
            b['timeouts'] += int(status == 'timeout')
        if len(traces) < 1500:
            traces.append(dict(time=round(start/1000,3), source=f.source,target=f.target,operation=f.operation,protocol=f.protocol,status=status,latency_ms=round(end-start,2),route=[f.source]+[n for n,_ in (path or [])]))
    while events:
        steps+=1
        if steps%2048==0:
            checkpoint(min(99,int(100*finished/max(1,total_messages))))
        t, seq, stage, f, path, hop, start = heapq.heappop(events)
        if stage == 'start':
            start=t
            bucket=min(count-1,int(start/1000/project.window))
            buckets[bucket]['sent'] += 1
            path=route(f.source,f.target,f.protocol,t/1000)
            if path is None:
                finish(f,start,t,'no_route',path,bucket)
                continue
            sig=tuple(l for _,l in path)
            if f.id in previous and previous[f.id] != sig: buckets[bucket]['changes'] += 1
            previous[f.id]=sig
            hop=0
        bucket=min(count-1,int(start/1000/project.window))
        if t-start > f.timeout_ms:
            finish(f,start,t,'timeout',path,bucket)
            continue
        if hop > len(path):
            finish(f,start,t,'delivered',path,bucket)
            continue
        n=f.source if hop==0 else path[hop-1][0]
        if not up(n,t/1000):
            finish(f,start,t,'node_down',path,bucket)
            continue
        p=pending[n]
        while p and p[0] <= t: p.popleft()
        waiting=max(0,len(p)-1)
        buckets[bucket]['queue'].append(waiting)
        stat=node_stats[n]
        stat['max_queue']=max(stat['max_queue'],waiting)
        # One job in service; queue_limit applies only to waiting jobs.
        if p and waiting >= nodes[n].queue_limit:
            stat['dropped']+=1
            finish(f,start,t,'queue_overflow',path,bucket)
            continue
        service=max(1000/nodes[n].capacity,nodes[n].processing_ms)
        done=max(t,busy[n])+service
        service_start=max(t,busy[n])
        first=max(0,int(service_start/1000/project.window))
        last=min(count-1,int(done/1000/project.window))
        for wi in range(first,last+1):
            lo=wi*project.window*1000
            hi=min(project.duration*1000,lo+project.window*1000)
            busy_windows[wi][n]+=max(0,min(done,hi)-max(service_start,lo))
        busy[n]=done
        p.append(done)
        stat['processed']+=1
        stat['busy_ms']+=service
        if hop == len(path):
            heapq.heappush(events,(done,seq,'arrival',f,path,hop+1,start))
            continue
        nxt,lid=path[hop]
        l=links[lid]
        ls=link_stats[lid]
        ls['messages']+=1
        loss=min(1.,l.loss*max((e.magnitude for e in active('loss',lid,done/1000)),default=1))
        if not l.enabled or active('link_down',lid,done/1000) or rng.random()<loss:
            ls['dropped']+=1
            finish(f,start,done,'link_loss',path,bucket)
            continue
        ls['bytes']+=f.message_bytes
        # Independent full-duplex serialization per direction.
        key=(lid,n)
        tx=max(done,wire[key])+f.message_bytes*8/(l.bandwidth_mbps*1000)
        wire[key]=tx
        factor=max((e.magnitude for e in active('latency',lid,done/1000)),default=1)
        arrival=tx+max(0,rng.normal(l.latency_ms*factor,l.jitter_ms))
        heapq.heappush(events,(arrival,seq,'arrival',f,path,hop+1,start))
    rows=[]
    for i,b in enumerate(buckets):
        start=i*project.window
        width=min(project.window,project.duration-start)
        sent=max(1,b['sent'])
        label=int(any(e.start<start+width and e.start+e.duration>start for e in project.incidents))
        rows.append(dict(time=start,label=label,sent=b['sent'],delivered=b['delivered'],rate=b['sent']/width,delivery_ratio=b['delivered']/sent,latency_mean=float(np.mean(b['latency'])) if b['latency'] else 0.,latency_p95=float(np.percentile(b['latency'],95)) if b['latency'] else 0.,loss_ratio=b['lost']/sent,timeout_ratio=b['timeouts']/sent,queue_mean=float(np.mean(b['queue'])) if b['queue'] else 0.,queue_max=max(b['queue'],default=0),utilization=min(1.,max(busy_windows[i].values(),default=0)/(width*1000)),route_changes=b['changes']))
    all_lat=[v for b in buckets for v in b['latency']]
    sent=sum(b['sent'] for b in buckets)
    delivered=sum(b['delivered'] for b in buckets)
    checkpoint(100)
    return dict(seed=project.seed,duration=project.duration,environment=dict(engine='1.0.0',python=platform.python_version(),numpy=np.__version__),config_sha256=hashlib.sha256(json.dumps(project.model_dump(),sort_keys=True).encode()).hexdigest(),summary=dict(sent=sent,delivered=delivered,delivery_ratio=delivered/max(1,sent),loss_ratio=(sent-delivered)/max(1,sent),latency_mean=float(np.mean(all_lat)) if all_lat else 0.,latency_p95=float(np.percentile(all_lat,95)) if all_lat else 0.,throughput=delivered/project.duration),windows=rows,nodes=node_stats,links=link_stats,traces=traces,trace_limit=1500,features=FEATURES)
