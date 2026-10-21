"""Offline asynchronous state replication and heartbeat-based local failover.

This is an explicit LWW/lease approximation, not Raft or a linearizable database.
No data is copied by an election: only delivered replication messages carry state.
"""
from collections import Counter
from copy import deepcopy
import json

STORES={'subscriber_contexts','fiveg_contexts','pdu_contexts','repository_contexts'}


class StateClusters:
    def __init__(self,engine):
        self.e=engine;self.groups={c.id:c for c in engine.p.clusters};self.members={n:c for c in engine.p.clusters for n in c.members}
        self.views={};self.entries={n:{} for n in self.members};self.acked={};self.pending={};self.requests={};self.seq=0;self.revision=0;self.suppress=0
        self.stats={c.id:Counter() for c in engine.p.clusters};self.events=[];self.event_count=0;self.last_sync={};self.snapshots={}
        for node,c in self.members.items():
            self.views[node]=dict(leader=c.members[0],term=0,role='LEADER' if node==c.members[0] else 'FOLLOWER',candidate=None,since=0,
                peers={p:dict(time=-c.heartbeat_timeout_ms if c.partition_policy=='quorum' else 0,term=0,leader=c.members[0],epoch=0) for p in c.members if p!=node})

    def emit(self,group,kind,**fields):
        self.event_count+=1;self.stats[group][kind]+=1
        event=dict(time=self.e.t/1000,cluster_id=group,event=kind,**fields)
        if len(self.events)<self.e.p.trace_limit:self.events.append(event)
        pid=fields.get('procedure_id')
        if pid is not None:self.e.journal.emit(pid,'cluster_state',state=kind,**{k:v for k,v in fields.items() if k!='procedure_id'})

    def prepare(self):
        for c in self.groups.values():self.e.push(0,'cluster_tick',c.id,0)

    def live(self,node):
        c=self.members[node];view=self.views[node]
        return [n for n in c.members if n==node or self.e.t-view['peers'][n]['time']<c.heartbeat_timeout_ms]

    def can_serve(self,node):
        if node not in self.members:return True
        c=self.members[node];v=self.views[node]
        return bool(self.e.node_up(node,self.e.t) and v['role']=='LEADER' and v['leader']==node and (c.partition_policy=='availability' or len(self.live(node))>=len(c.members)//2+1))

    def resolve(self,anchor,observer,protocol):
        if anchor not in self.members:return anchor
        c=self.members[anchor];f=self.e.p.flows[0].model_copy(update=dict(protocol=protocol,global_title=''))
        candidates=[n for n in c.members if self.can_serve(n) and self.e.route(observer,n,f) is not None]
        return max(candidates,key=lambda n:(self.views[n]['term'],-c.members.index(n))) if candidates else None

    def procedure(self,src,dst):
        e=self.e;pid=-10000-list(self.members).index(src)*100-list(self.members).index(dst)
        if pid not in e.procedures:
            f=e.p.flows[0].model_copy(update=dict(id=f'cluster:{src}:{dst}',source=src,target=dst,priority=0,global_title='',message_size_cv=0))
            e.procedures[pid]=dict(id=pid,flow=f,subscriber=0,objects=set(),affected=set(),transport_retries=0,retries=0,control=True,last_cause=None,step=0,attempts=0,active=False,routes=[])
        return e.procedures[pid]

    def send(self,src,dst,kind,entry=None):
        e=self.e;c=self.members[src];self.seq+=1;ident=self.seq
        payload=dict(id=ident,src=src,dst=dst,kind=kind,entry=deepcopy(entry),start=e.t,src_epoch=e.node_epoch[src],dst_epoch=self.views[src]['peers'][dst]['epoch'])
        self.requests[ident]=payload
        size=128+len(json.dumps(entry,ensure_ascii=False).encode()) if entry is not None else 128
        protocol='Diameter' if e.nodes[src].kind=='HSS' else 'SBI'
        self.stats[c.id]['wire_requests']+=1;e.telemetry.count('control_messages')
        e.send_message(self.procedure(src,dst),src,dst,'request','STATE_REPLICATE' if kind=='update' else 'CLUSTER_HEARTBEAT',size,
            protocol=protocol,category='replication',context=ident)
        return ident

    def capture(self,store,key,value,deadline,reason='write',owner=None):
        if self.suppress or store not in STORES or key[0] not in self.members or getattr(self.e,'reporting',False):return
        node=key[0]
        if reason=='restart_cleared':return
        self.revision+=1;canonical=(store,tuple(key[1:]));previous=self.entries[node].get(canonical,{})
        owner=value.get('owner') if isinstance(value,dict) else owner if owner is not None else previous.get('pdu_owner')
        entry=dict(store=store,key=list(key[1:]),value=deepcopy(value),deadline=deadline,pdu_owner=owner,stamp=[self.e.t,self.revision,node],origin=node)
        self.entries[node][canonical]=entry
        for peer in self.members[node].members:
            if peer!=node and self.e.node_up(node,self.e.t):self.publish(node,peer,canonical,entry)

    def publish(self,src,dst,key,entry):
        pair=(src,dst,key);version=tuple(entry['stamp']);pending=self.pending.get(pair);c=self.members[src]
        if self.acked.get(pair)==version or (pending and pending[0]==version and pending[1]>self.e.t):return
        if pending and pending[1]<=self.e.t:self.stats[c.id]['replication_timeouts']+=1
        self.pending[pair]=(version,self.e.t+c.request_timeout_ms)
        self.send(src,dst,'update',entry)

    def arrival(self,msg):
        e=self.e;request=self.requests[msg['context']];src,dst=request['src'],request['dst'];c=self.members[src]
        if msg['role']=='answer':
            if request['src_epoch']!=e.node_epoch[src]:return
            meta=request['reply_meta'];peer=self.views[src]['peers'][dst];epoch=meta['epoch']
            if peer['epoch']!=epoch:
                for key in list(self.acked):
                    if key[:2]==(src,dst):self.acked.pop(key,None)
            peer.update(time=e.t,epoch=epoch,term=meta['term'],leader=meta['leader'])
            if request['kind']=='update':
                entry=request['entry'];key=(entry['store'],tuple(entry['key']));pair=(src,dst,key)
                if msg['result']=='success':self.acked[pair]=max(self.acked.get(pair,tuple(entry['stamp'])),tuple(entry['stamp']))
                if self.pending.get(pair,(None,))[0]==tuple(entry['stamp']):self.pending.pop(pair,None)
            return
        if request['dst_epoch']!=e.node_epoch[dst] and request['kind']=='update':return
        if request['kind']=='update' and msg.get('resource_error'):
            self.stats[c.id]['database_rejections']+=1;self.answer(request,msg['resource_error']);return
        if request['kind']=='update':e.push(e.t+c.replication_delay_ms,'cluster_apply',(request['id'],e.node_epoch[dst]))
        else:self.answer(request,'success')

    def answer(self,request,result):
        e=self.e;src,dst=request['src'],request['dst'];protocol='Diameter' if e.nodes[src].kind=='HSS' else 'SBI'
        request['reply_meta']=dict(epoch=e.node_epoch[dst],term=self.views[dst]['term'],leader=self.views[dst]['leader'])
        self.stats[self.members[src].id]['wire_answers']+=1;e.telemetry.count('control_messages')
        e.send_message(self.procedure(src,dst),dst,src,'answer','STATE_REPLICATE_ACK' if request['kind']=='update' else 'CLUSTER_HEARTBEAT_ACK',128,result,
            protocol=protocol,category='replication',context=request['id'])

    def apply(self,ident,epoch):
        from .resources import CapacityError
        e=self.e;r=self.requests[ident];dst=r['dst'];entry=r['entry'];c=self.members[dst]
        if epoch!=e.node_epoch[dst] or not e.node_up(dst,e.t):return
        canonical=(entry['store'],tuple(entry['key']));current=self.entries[dst].get(canonical)
        if current and tuple(current['stamp'])>=tuple(entry['stamp']):self.answer(r,'success');return
        store=e.storage.stores.get(entry['store'])
        if store is None:self.answer(r,'unknown_store');return
        key=(dst,*entry['key']);value=entry['value'];owner=value.get('owner') if isinstance(value,dict) else None
        # A completed generation cannot be recreated by a delayed snapshot.
        fenced=owner is not None and hasattr(e,'session_lifecycle') and e.session_lifecycle.records.get(owner,{}).get('state')=='RELEASED'
        deleted=value is None or (entry['deadline'] is not None and entry['deadline']<=e.t) or fenced
        self.suppress+=1
        if hasattr(e,'session_lifecycle'):e.session_lifecycle.suppress+=1
        try:
            if deleted:store.pop(key,None)
            else:store.put(key,deepcopy(value),entry['deadline'])
        except CapacityError:
            self.stats[c.id]['capacity_rejections']+=1;self.answer(r,'replica_context_limit');return
        finally:
            self.suppress-=1
            if hasattr(e,'session_lifecycle'):e.session_lifecycle.suppress-=1
        self.entries[dst][canonical]=deepcopy(entry)
        if deleted:self.entries[dst][canonical]['value']=None
        lag=e.t-entry['stamp'][0];self.stats[c.id]['applied_updates']+=1;self.stats[c.id]['lag_sum_ms']+=lag
        self.stats[c.id]['lag_max_ms']=max(self.stats[c.id]['lag_max_ms'],lag)
        if deleted and entry['store']=='pdu_contexts' and hasattr(e,'session_lifecycle'):
            previous_owner=entry.get('pdu_owner',owner);record=e.session_lifecycle.records.get(previous_owner)
            if record and record['state'] in ('RELEASING','RECONCILING','ORPHANED'):e.session_lifecycle.complete_release(previous_owner)
        self.answer(r,'success')

    def tick(self,group):
        e=self.e;c=self.groups[group]
        for node in c.members:
            if not e.node_up(node,e.t):continue
            v=self.views[node];live=self.live(node)
            claims={n:(v['term'] if n==node else v['peers'][n]['term']) for n in live}
            quorum=c.partition_policy=='availability' or len(live)>=len(c.members)//2+1
            desired=max(live,key=lambda n:(claims[n],-c.members.index(n))) if quorum else None
            if desired!=v['leader'] or v['role']=='OFFLINE':
                if v['candidate']!=desired or v['role']=='OFFLINE':v.update(candidate=desired,since=e.t,role='WAITING')
                if e.t-v['since']>=c.failover_ms:
                    previous=v['leader'];v['leader']=desired;v['role']='LEADER' if desired==node else 'FOLLOWER' if desired else 'NO_QUORUM';v['candidate']=None
                    if desired==node and previous!=node:v['term']=max([v['term'],*[p['term'] for p in v['peers'].values()]])+1
                    self.emit(group,'leader_changed',node=node,previous=previous,leader=desired,term=v['term'])
            elif v['role']=='WAITING':v['role']='LEADER' if desired==node else 'FOLLOWER'
            for peer in c.members:
                if peer!=node:self.send(node,peer,'heartbeat')
            if e.t-self.last_sync.get(node,-c.sync_ms)>=c.sync_ms:
                self.last_sync[node]=e.t
                for key,entry in list(self.entries[node].items()):
                    for peer in c.members:
                        if peer!=node:self.publish(node,peer,key,entry)
        leaders=[n for n in c.members if self.can_serve(n)]
        if len(leaders)>1:self.stats[group]['split_brain_ticks']+=1
        self.rebind_sessions()
        self.snapshots[group]=self.snapshot(c)
        if e.t<e.p.duration*1000:e.push(min(e.t+c.heartbeat_ms,e.p.duration*1000),'cluster_tick',group,0)

    def node_fault(self,node,restart):
        if node not in self.members:return
        c=self.members[node];v=self.views[node];v.update(role='OFFLINE',candidate=None,since=self.e.t)
        if restart:
            missing=[entry for key,entry in self.entries[node].items() if entry['value'] is not None and not any(other.get(key,{}).get('stamp')==entry['stamp'] for n,other in self.entries.items() if n!=node and n in c.members)]
            lost=len(missing)
            for owner in {entry['value']['owner'] for entry in missing if entry['store']=='pdu_contexts' and 'owner' in entry['value']}:
                self.emit(c.id,'session_state_lost',procedure_id=owner,node=node,reason='unreplicated_restart')
            self.emit(c.id,'restart_state_loss',node=node,unreplicated_entries=lost)
            self.stats[c.id]['unreplicated_entries_lost']+=lost;self.entries[node].clear()
            for pair in list(self.acked):
                if pair[0]==node:self.acked.pop(pair,None)

    def recoverable(self,store,key,value):
        if key[0] not in self.members or store not in STORES:return False
        resource=self.e.storage.stores[store];c=self.members[key[0]]
        return any((item:=resource.data.get((peer,*key[1:]))) and item[1]>self.e.t and item[0]==value for peer in c.members if peer!=key[0])

    def rebind_sessions(self):
        e=self.e
        if not hasattr(e,'session_lifecycle'):return
        for owner,record in list(e.session_lifecycle.records.items()):
            if record['state']!='ACTIVE':continue
            pr=e.procedures[owner];f=pr['flow']
            for kind,old,proto in [('AMF',f.target,'NGAP'),('SMF',pr['nf_selected'].get('SMF'),'SBI')]:
                if old not in self.members:continue
                node=self.resolve(old,f.source if kind=='AMF' else f.target,proto)
                if not node or node==old:continue
                e.touch(pr,old);e.touch(pr,node)
                context=e.session_lifecycle.context(owner,node);flag='pdu_active' if kind=='AMF' else 'sm_context'
                if context.get('owner')!=owner or not context.get(flag) or (kind=='AMF' and not e.contexts.get(e.context_key(pr,node),{}).get('registered')):
                    self.emit(self.members[old].id,'session_state_lost',procedure_id=owner,node=node,previous=old,pdu_session_id=record['pdu_session_id'])
                    e.session_lifecycle.start_cleanup(owner,'replica_state_missing');break
                if kind=='AMF':pr['flow']=f.model_copy(update=dict(target=node));pr['destination']=node;f=pr['flow']
                else:
                    pr['nf_selected']['SMF']=node
                    if owner in e.user_plane.sessions:e.user_plane.sessions[owner]['smf']=node
                self.emit(self.members[old].id,'session_rebound',procedure_id=owner,node=node,previous=old,pdu_session_id=record['pdu_session_id'])

    def event(self,kind,data):
        if kind=='cluster_tick':self.tick(data)
        else:self.apply(*data)

    def repository_read(self,pr,node,key):
        canonical=('repository_contexts',tuple(key[1:]));entry=self.entries[node].get(canonical)
        required=pr.get('repository_tokens',{}).get(tuple(key[1:]))
        stamp=entry['stamp'] if entry and entry['value'] is not None and entry['deadline']>self.e.t else None
        stale=required is not None and (stamp is None or tuple(stamp)<tuple(required))
        self.emit(self.members[node].id,'repository_read',procedure_id=pr['id'],node=node,read_version=stamp,required_version=required,result='stale' if stale else 'local' if stamp else 'initial_profile')
        if stale:self.stats[self.members[node].id]['stale_reads_rejected']+=1
        return not stale

    def snapshot(self,c):
        views=[]
        for n in c.members:
            v=self.views[n];live=self.live(n);role=v['role'] if self.e.node_up(n,self.e.t) else 'OFFLINE'
            if role!='OFFLINE' and c.partition_policy=='quorum' and len(live)<len(c.members)//2+1:role='NO_QUORUM'
            views.append(dict(cluster_id=c.id,node=n,role=role,leader=v['leader'],term=v['term'],live=live,entries=len(self.entries[n]),peers=deepcopy(v['peers']),observed_ms=self.e.t))
        return views

    def report(self):
        groups=[];views=[]
        for c in self.groups.values():
            keys=set().union(*(self.entries[n] for n in c.members));divergent=sum(len({json.dumps(self.entries[n].get(k,{}).get('stamp')) for n in c.members})>1 for k in keys)
            groups.append(dict(id=c.id,name=c.name,members=c.members,policy=c.partition_policy,divergent_keys=divergent,**self.stats[c.id]))
            views.extend(deepcopy(self.snapshots.get(c.id,self.snapshot(c))))
        return dict(version='async-state-v1',groups=groups,views=views,events=self.events,event_count=self.event_count,truncated=self.event_count>len(self.events),
            consistency='asynchronous last-writer-wins; heartbeat views may be stale; no consensus or linearizability guarantee')
