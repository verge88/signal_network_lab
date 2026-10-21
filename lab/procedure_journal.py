"""Bounded, physics-neutral causal journals for a sample of admitted procedures."""
import hashlib
from collections import defaultdict
from copy import deepcopy
import numpy as np

VERSION='procedure-journal-v1'


class ProcedureJournal:
    def __init__(self,engine):
        self.e=engine;self.rng=np.random.default_rng(engine.p.seed^0x17CA53)
        self.sessions={};self.slots=[];self.seen=0;self.actor=None
        self.watchers=defaultdict(set);self.shadow={};self.context_timers={}
    def emit(self,pid,kind,cause=None,**details):
        session=self.sessions.get(pid)
        if session is None:return None
        session['event_count']+=1
        if len(session['events'])>=self.e.p.journal_events:
            session['dropped_events']+=1;return None
        ident=len(session['events'])+1
        if cause is not None and not 0<cause<ident:cause=None
        session['events'].append(dict(id=ident,time_ms=self.e.t,kind=kind,cause_event=cause,**details))
        return ident
    def begin(self,pr):
        self.seen+=1;p=self.e.p;pid=pr['id']
        if p.journal_procedure_id is not None:
            if pid!=p.journal_procedure_id:return
        else:
            if not p.journal_sessions:return
            slot=len(self.slots) if len(self.slots)<p.journal_sessions else int(self.rng.integers(self.seen))
            if slot>=p.journal_sessions:return
            if slot<len(self.slots):
                old=self.slots[slot];self.sessions.pop(old)
                self.context_timers={key:value for key,value in self.context_timers.items() if key[0]!=old}
                for key in list(self.watchers):
                    self.watchers[key].discard(old)
                    if not self.watchers[key]:self.watchers.pop(key);self.shadow.pop(key,None)
                self.slots[slot]=pid
            else:self.slots.append(pid)
        self.sessions[pid]=dict(procedure_id=pid,session_id=pr['session'],subscriber_id=pr['subscriber'],flow_id=pr['flow'].id,
            operation=pr['flow'].operation,protocol=pr['flow'].protocol,start_ms=self.e.t,end_ms=None,status='active',cause=None,
            event_count=0,dropped_events=0,events=[],terminal_event=None)
        pr['_journal_event']=self.emit(pid,'procedure_start',source=pr['flow'].source,target=pr['destination'],subscriber=pr['subscriber'])
        f=pr['flow']
        for node in self.e.nodes:
            for name,key in [('subscriber_contexts',(node,f.source,pr['subscriber'])),('fiveg_contexts',(node,f.source,pr['subscriber'],f.plmn,f.snssai.lower(),f.dnn))]:
                token=(name,key);self.watchers[token].add(pid)
                store=self.e.storage.stores.get(name);item=store.data.get(key) if store is not None else None
                if item is not None:
                    value=deepcopy(item[0]);self.shadow[token]=(value,item[1])
                    initial=self.emit(pid,'context_initial',node=node,store=name,context_key=list(key),after=value,deadline_ms=item[1])
                    self.context_timers[(pid,token)]=self.emit(pid,'timer_set',cause=initial,timer_id=f'context:{name}:{key}',timer_type='context',deadline_ms=item[1],node=node)
    def message(self,msg,kind,cause=None,advance=True,**details):
        pid=msg['procedure']
        if pid not in self.sessions:return None
        event=self.emit(pid,kind,cause=msg.get('_journal_event') if cause is None else cause,
            message_id=msg['id'],rpc_id=msg.get('rpc_id'),operation=msg['operation'],role=msg['role'],
            protocol=msg.get('protocol'),source=msg['source'],target=msg['target'],**details)
        if event is not None and advance:msg['_journal_event']=event
        return event
    def route(self,pr,src,dst,flow,path,cause=None,**extra):
        if pr['id'] not in self.sessions:return None
        hops=[];node=src
        key=None if dst is None else (self.e.nodes[dst].host or dst) if flow.protocol in ('GTPU','IP') else (self.e.nodes[dst].host or self.e.nodes[dst].kind) if flow.protocol in ('NGAP','SBI','PFCP') else self.e.nodes[dst].realm if flow.protocol=='Diameter' else self.e.nodes[dst].point_code
        for nxt,lid in path or []:
            rules=self.e.nodes[node].routing_table;rule=rules.get(key,rules.get('*'));rank=rule.index(nxt) if rule and nxt in rule else 0
            if self.e.p.routing_mode=='loadshare':rank=((rank-pr['subscriber'])%len(rule)) if rule else int(hashlib.sha256(f'{pr["subscriber"]}:{lid}'.encode()).hexdigest()[:4],16)/65536
            link=self.e.links[lid];latency=link.reverse_latency_ms if node!=link.source and link.reverse_latency_ms is not None else link.latency_ms
            penalty=rank*100000 if rule else rank*100 if self.e.p.routing_mode=='loadshare' else 0
            hops.append(dict(node=node,next_node=nxt,link=lid,lookup_key=key,matched_rule=key if key in rules else '*' if '*' in rules else None,
                configured_next_hops=rule,rank=rank,latency_ms=latency,processing_ms=self.e.nodes[nxt].processing_ms,cost=latency+self.e.nodes[nxt].processing_ms+penalty))
            node=nxt
        return self.emit(pr['id'],'route_selected' if path is not None else 'route_unavailable',cause=cause,
            source=src,target=dst,algorithm=self.e.p.routing_mode,knowledge=self.e.p.routing_knowledge,selector=pr['subscriber'],hops=hops,**extra)
    def store_change(self,name,key,value,deadline,reason):
        token=(name,key);watched=self.watchers.get(token)
        if not watched:return
        previous=self.shadow.get(token,(None,None));before=deepcopy(previous[0]);after=deepcopy(value)
        if value is None:self.shadow.pop(token,None)
        else:self.shadow[token]=(after,deadline)
        for pid in sorted(watched):
            pr=self.e.procedures[pid]
            cause=pr.get('_journal_event') if self.actor==pid else None
            timer=self.context_timers.pop((pid,token),None)
            if timer is not None:
                closed=self.emit(pid,'timer_expired' if reason=='expired' else 'timer_cancelled',cause=timer,timer_id=f'context:{name}:{key}',timer_type='context',reason=reason,node=key[0])
                if reason=='expired':cause=closed
            if reason=='restart_cleared':cause=next((event['id'] for event in reversed(self.sessions[pid]['events']) if event['kind']=='node_fault' and event.get('node')==key[0]),None)
            changed=self.emit(pid,'context_changed',cause=cause,node=key[0],store=name,context_key=list(key),before=before,after=after,
                deadline_ms=deadline,previous_deadline_ms=previous[1],reason=reason,actor_procedure=self.actor)
            if deadline is not None:self.context_timers[(pid,token)]=self.emit(pid,'timer_set',cause=changed,timer_id=f'context:{name}:{key}',timer_type='context',deadline_ms=deadline,node=key[0])
    def finish(self,pr,status):
        if pr['id'] not in self.sessions:return
        session=self.sessions[pr['id']];session.update(end_ms=self.e.t,status=status,cause=pr.get('last_cause') if status!='delivered' else None)
        session['terminal_event']=self.emit(pr['id'],'procedure_finished',cause=pr.get('_journal_event'),status=status,reason=session['cause'],elapsed_ms=self.e.t-pr['start'])
    def event_actor(self,kind,data):
        e=self.e
        if kind=='new':return data[0]
        if kind in ('timeout','retry'):return data[0]
        if kind=='node_done':return e.messages[data[1]]['procedure']
        if kind in ('packet_rx','packet_timeout','ack'):return e.transfers[data[0]['transfer']]['msg']['procedure']
        if kind.startswith('rpc_'):return e.rpcs[data[0]]['procedure']
        if kind in ('resource_db_done','resource_db_timeout'):return e.storage.tasks[data]['msg']['procedure']
        if kind=='user_release':return data
        return None
    def report(self):
        return dict(version=VERSION,policy='forced procedure' if self.e.p.journal_procedure_id is not None else 'uniform reservoir over admitted procedures; independent RNG',
            seen=self.seen,session_limit=self.e.p.journal_sessions,event_limit=self.e.p.journal_events,
            requested_procedure=self.e.p.journal_procedure_id,sessions=[deepcopy(self.sessions[key]) for key in sorted(self.sessions)],
            limitations=['Only sampled procedures have detailed journals. Old records are not reconstructed.',
                'Events after the procedural response may include late DATA/ACK, user traffic and context expiration.',
                'Causal edges link recorded scheduling, messages and timers; temporal order alone is not a causal claim.'])
