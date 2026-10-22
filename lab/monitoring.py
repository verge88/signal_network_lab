"""Configurable, resource-bearing monitoring overlay on the offline event engine."""
import hashlib
import heapq
import json
import math
import statistics
from collections import Counter
from .monitor_expressions import evaluate


def default_features():
    return [dict(name='reported_rate',expression='reported_processed_s',group='local'),
            dict(name='witness_rate',expression='witness_messages_s',group='hierarchy'),
            dict(name='missing',expression='report_missing',group='hierarchy'),
            dict(name='age',expression='report_age_s',group='hierarchy'),
            dict(name='rtt',expression='report_rtt_ms',group='local'),
            dict(name='zone_deviation',expression='safe_div(abs(reported_processed_s-zone_median_processed_s),zone_mad_processed_s+1)',group='hierarchy'),
            dict(name='coverage',expression='zone_coverage',group='hierarchy'),
            dict(name='rate_change',expression='temporal_delta_processed_s',group='temporal')]


def schema_spec(config):
    definitions=[f.model_dump() for f in config.features]
    semantics=dict(contract='monitoring-v1',features=definitions,poll=config.poll_s,decision=config.decision_s,
        ttl=config.ttl_s,history=config.history,missing='zero-with-mask; history uses decision-time values')
    ident=hashlib.sha256(json.dumps(semantics,sort_keys=True).encode()).hexdigest()
    return dict(id=ident,features=[f.name for f in config.features],definitions=definitions,semantics=semantics)


class MonitoringOverlay:
    def __init__(self,engine):
        self.e=engine;self.rows=[];self.log=[];self.cache={};self.pending={};self.seq=0
        self.local={n:[0,0.] for n in engine.nodes};self.witness=Counter();self.previous={};self.history={}
        self.samples={};self.last_payload={};self.totals=Counter();self.link_bytes=Counter()
        self.traffic={n:Counter() for n in engine.nodes};self.traffic_previous={}
        self.last_decision=0.

    @property
    def config(self):return self.e.p.monitoring

    def prepare(self):
        if not self.config.enabled:return
        self.e.push(0,'monitor_poll')
        self.e.push(min(self.config.decision_s,self.e.p.duration)*1000,'monitor_decide',priority=2)

    def observe(self,node,msg,arrival):
        if not self.config.enabled or msg.get('category','signalling')!='signalling':return
        self.local[node][0]+=1;self.local[node][1]+=self.e.t-arrival
        self.witness[(node,msg['source'])]+=1
        traffic=self.traffic[node];traffic['sent']+=int(msg['source']==node);traffic['received']+=int(msg['target']==node)
        traffic['operation:'+msg['operation']]+=1
        traffic['peer:'+(msg['target'] if msg['source']==node else msg['source'])]+=1

    def route(self,src,dst):
        e=self.e
        if not e.node_up(src,e.t,True,observer=src) or not e.node_up(dst,e.t,True,observer=src):return None
        q=[(0,src,())];seen=set()
        while q:
            cost,node,path=heapq.heappop(q)
            if node in seen:continue
            seen.add(node)
            if node==dst:return list(path)
            for nxt,lid in e.adj[node]:
                link=e.links[lid]
                if nxt in seen or link.protocol not in self.config.protocols:continue
                if not e.node_up(nxt,e.t,True,observer=src) or not e.link_up(lid,node,e.t,True,observer=src):continue
                latency=link.reverse_latency_ms if node!=link.source and link.reverse_latency_ms is not None else link.latency_ms
                heapq.heappush(q,(cost+latency+e.nodes[nxt].processing_ms,nxt,path+((nxt,lid),)))
        return None

    def send(self,src,dst,kind,context,size):
        e=self.e;self.totals['messages']+=1
        path=self.route(src,dst)
        if path is None:self.totals['no_route']+=1;return
        pid=-100000-list(e.nodes).index(src)*len(e.nodes)-list(e.nodes).index(dst)
        if pid not in e.procedures:
            flow=e.p.flows[0].model_copy(update=dict(id=f'monitor:{src}:{dst}',source=src,target=dst,priority=self.config.priority,message_size_cv=0))
            e.procedures[pid]=dict(id=pid,flow=flow,subscriber=0,step=0,objects=set(),affected=set(),transport_retries=0,retries=0,control=True,last_cause=None)
        e.message_seq+=1
        msg=dict(id=e.message_seq,procedure=pid,step=0,source=src,target=dst,role='monitor',operation='MONITOR_'+kind,
            size=size,result='success',path=path,hop=0,start=e.t,valid=True,protocol=e.links[path[0][1]].protocol,
            rpc_id=None,category='monitoring',context=dict(kind=kind,**context),route_record=[src]+[n for n,_ in path],priority=self.config.priority)
        e.messages[msg['id']]=msg;e.arrive_node(msg,src)

    def poll(self):
        e=self.e;c=self.config
        for m in c.members:
            self.seq+=1;ident=self.seq;self.pending[ident]=dict(node=m.node,collector=m.collector,sent=e.t,done=False)
            self.send(m.collector,m.node,'QUERY',dict(request=ident),c.query_bytes)
            e.push(e.t+c.timeout_s*1000,'monitor_timeout',ident,0)
        if e.t+c.poll_s*1000<e.p.duration*1000:e.push(e.t+c.poll_s*1000,'monitor_poll')

    def arrival(self,msg):
        e=self.e;ctx=msg['context'];kind=ctx['kind'];self.totals['delivered']+=1
        if kind=='QUERY':
            request=self.pending.get(ctx['request'])
            if request is None:return
            node=request['node'];count,total_ms=self.local[node];old=self.samples.get(node,(0,0.,0.))
            width=max(.001,(e.t-old[2])/1000)
            payload=dict(processed_s=(count-old[0])/width,node_ms=(total_ms-old[1])/max(1,count-old[0]),sampled=e.t)
            traffic=self.traffic[node];delta=traffic-self.traffic_previous.get(node,Counter());self.traffic_previous[node]=traffic.copy()
            def entropy(prefix):
                values=[v for k,v in delta.items() if k.startswith(prefix)];total=sum(values)
                return -sum((v/total)*math.log2(v/total) for v in values) if total else 0.
            payload.update(operation_entropy=entropy('operation:'),peer_entropy=entropy('peer:'),io_ratio=delta['sent']/max(1,delta['received']))
            self.samples[node]=(count,total_ms,e.t)
            faults=[f for f in self.config.faults if f.node==node and f.start*1000<=e.t<(f.start+f.duration)*1000]
            for f in faults:
                if f.mode=='suppress':self.totals['suppressed']+=1;return
                if f.mode=='scale':payload['processed_s']*=f.factor
                if f.mode=='freeze':payload=dict(self.last_payload.get(node,payload),sampled=e.t)
            self.last_payload[node]=dict(payload)
            data=dict(request=ctx['request'],payload=payload)
            delay=max([f.delay_ms for f in faults if f.mode=='delay']+[0])
            if delay:e.push(e.t+delay,'monitor_reply',(node,request['collector'],data))
            else:self.send(node,request['collector'],'REPORT',data,self.config.report_bytes)
        elif kind=='REPORT':
            request=self.pending.get(ctx['request'])
            if request is None or request['done']:self.totals['late_reports']+=1;return
            request['done']=True
            payload=dict(ctx['payload'],received=e.t,rtt=e.t-request['sent'])
            key=(request['collector'],request['node']);previous=self.cache.get(key)
            if previous and previous['sampled']>payload['sampled']:self.totals['out_of_order_reports']+=1;return
            self.cache[key]=payload;self.totals['reports']+=1
            self.log.append(dict(time=e.t/1000,node=request['node'],collector=request['collector'],rtt_ms=payload['rtt'],processed_s=payload['processed_s']))
            if self.config.sync:
                for other in sorted({m.collector for m in self.config.members}-{request['collector']}):
                    self.send(request['collector'],other,'SYNC',dict(node=request['node'],payload=payload),self.config.report_bytes)
        elif kind=='SYNC':
            key=(msg['target'],ctx['node']);old=self.cache.get(key)
            if old is None or old['sampled']<ctx['payload']['sampled']:self.cache[key]=dict(ctx['payload'],received=e.t)

    def fresh(self,collector,node):
        item=self.cache.get((collector,node))
        return item if item and 0<=self.e.t-item['sampled']<=self.config.ttl_s*1000 else None

    def decide(self):
        e=self.e;c=self.config;lo=self.last_decision;hi=e.t/1000;self.last_decision=hi
        for m in c.members:
            if not e.node_up(m.collector,e.t):self.totals['unavailable_decisions']+=1;continue
            item=self.fresh(m.collector,m.node);rate=item['processed_s'] if item else 0.
            zone=[peer for peer in c.members if peer.zone==m.zone and peer.node!=m.node]
            peers=[p['processed_s'] for peer in zone if (p:=self.fresh(m.collector,peer.node))]
            median=statistics.median(peers) if peers else 0.;mad=statistics.median(abs(p-median) for p in peers) if peers else 0.
            witness=m.witness or m.collector;key=(witness,m.node);count=self.witness[key];previous=self.previous.get(key,0);self.previous[key]=count
            history=self.history.setdefault(m.node,[])
            values=dict(reported_processed_s=rate,reported_cpu_ms=item['node_ms'] if item else 0.,
                witness_messages_s=(count-previous)/(hi-lo),report_missing=float(item is None),
                report_age_s=(e.t-item['sampled'])/1000 if item else c.ttl_s,report_rtt_ms=item['rtt'] if item else 0.,
                zone_median_processed_s=median,zone_mad_processed_s=mad,zone_coverage=len(peers)/len(zone) if zone else 0.,
                temporal_delta_processed_s=rate-history[-1] if history else 0.,temporal_mean_processed_s=statistics.mean(history) if history else 0.)
            values.update(reported_operation_entropy=item.get('operation_entropy',0.) if item else 0.,reported_peer_entropy=item.get('peer_entropy',0.) if item else 0.,reported_io_ratio=item.get('io_ratio',0.) if item else 0.)
            features={f.name:evaluate(f.expression,values) for f in c.features}
            history.append(rate);del history[:-c.history]
            # Ground truth stays in metadata; expressions only see OBSERVABLES.
            intervals=[];types=set()
            for fault in c.faults:
                if fault.node==m.node:intervals.append([fault.start,fault.start+fault.duration]);types.add('report_'+fault.mode)
            for fault in e.p.incidents:
                target=fault.target
                relevant=target==m.node or (target in e.links and m.node in (e.links[target].source,e.links[target].target)) or (target in {f.id for f in e.p.flows} and any(f.id==target and m.node in (f.source,f.target) for f in e.p.flows))
                if relevant:intervals.append([fault.start,min(e.p.duration,fault.start+fault.duration)]);types.add(fault.kind)
            self.rows.append(dict(time=lo,window_seconds=hi-lo,entity_id=m.node,collector=m.collector,zone=m.zone,
                label=int(any(a<hi and b>lo for a,b in intervals)),fault_types=sorted(types),fault_intervals=intervals,
                feature_contract='monitoring-v1',schema_id=schema_spec(c)['id'],**features))
        if e.t<e.p.duration*1000:e.push(min(e.t+c.decision_s*1000,e.p.duration*1000),'monitor_decide',priority=2)

    def event(self,kind,data):
        if kind=='monitor_poll':self.poll()
        elif kind=='monitor_decide':self.decide()
        elif kind=='monitor_reply':self.send(data[0],data[1],'REPORT',data[2],self.config.report_bytes)
        elif kind=='monitor_timeout':
            request=self.pending.get(data)
            if request and not request['done']:request['done']=True;self.totals['timeouts']+=1
            self.pending.pop(data,None)

    def report(self):
        return dict(enabled=self.config.enabled,contract='monitoring-v1',schema=schema_spec(self.config),rows=self.rows,
            reports=self.log,counts=dict(self.totals),wire_bytes=sum(self.link_bytes.values()),link_wire_bytes=dict(self.link_bytes),
            transport='Configured monitoring overlay routes, real shared CPU/links/ACK/retry queues; fixed route per message.')
