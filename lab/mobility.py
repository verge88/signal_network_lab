"""Resource-bearing handover transactions, with explicit model boundaries."""
from copy import deepcopy


class Mobility:
    def __init__(self,engine):
        self.e=engine;self.current={f.id:f for f in engine.p.flows};self.pending={};self.transactions={};self.seq=0;self.epochs={};self.traces=[]
    def prepare(self):
        for f in self.e.p.flows:
            for m in f.mobility:self.e.push(m.time*1000,'mobility_begin',(f.id,m),0)
    def flow(self,f):return self.current.get(f.id,f)
    def busy(self,flow_id):
        flow=self.current[flow_id];key=lambda f:(f.source,f.target,f.plmn,f.snssai.lower(),f.dnn)
        return any(key(flow)==key(self.transactions[ident]['old']) for ident in self.pending.values())
    def begin(self,flow_id,m):
        e=self.e;f=self.current[flow_id]
        if self.busy(flow_id):self.traces.append(dict(time=e.t/1000,flow_id=flow_id,status='mobility_busy'));return
        self.seq+=1;ident=self.seq;self.epochs[flow_id]=self.epochs.get(flow_id,0)+1
        amf=m.amf or f.target;bindings=dict(f.nf_bindings)
        if m.upf:bindings['UPF']=m.upf
        new=f.model_copy(update=dict(source=m.gnb,target=amf,nf_bindings=bindings))
        steps=[(m.gnb,amf,'NGAP','Handover Request')]
        if amf!=f.target:steps.append((f.target,amf,'SBI','Context Transfer'))
        if m.upf:
            smf=f.nf_bindings.get('SMF') or next((n.id for n in e.nodes.values() if n.kind=='SMF'),None)
            if smf:steps.append((smf,m.upf,'PFCP','Session Relocation'))
        pid=-1000000-ident;e.procedures[pid]=dict(id=pid,flow=f.model_copy(update=dict(id='mobility:'+str(ident),priority=0,message_size_cv=0)),subscriber=0,step=0,active=True,objects=set(),affected=set(),transport_retries=0,retries=0,control=True,last_cause=None)
        tx=dict(id=ident,pid=pid,flow_id=flow_id,old=f,new=new,settings=m,steps=steps,index=0,closed=False,start=e.t,token=0)
        self.transactions[ident]=tx;self.pending[flow_id]=ident;e.push(e.t+m.timeout_ms,'mobility_timeout',ident,0);self.transmit(tx)
    def transmit(self,tx):
        if tx['index']==len(tx['steps']):
            ready=max(self.e.t,tx['start']+tx['settings'].interruption_ms);self.e.push(ready,'mobility_commit',tx['id']);return
        src,dst,proto,op=tx['steps'][tx['index']];tx['epochs']=(self.e.node_epoch[src],self.e.node_epoch[dst]);tx['token']+=1
        self.e.send_message(self.e.procedures[tx['pid']],src,dst,'request',op,256,protocol=proto,category='mobility',context=(tx['id'],tx['token']))
    def arrival(self,msg):
        ident,token=msg['context'];tx=self.transactions[ident]
        if tx['closed'] or token!=tx['token']:return
        src,dst,proto,op=tx['steps'][tx['index']]
        if tx['epochs']!=(self.e.node_epoch[src],self.e.node_epoch[dst]):self.finish(tx,'peer_restarted');return
        if msg['role']=='request':self.e.send_message(self.e.procedures[tx['pid']],dst,src,'answer',op+' Response',192,protocol=proto,category='mobility',context=msg['context'])
        else:tx['index']+=1;self.transmit(tx)
    def commit(self,tx):
        e=self.e;old,new,m=tx['old'],tx['new'],tx['settings']
        if not all(e.node_up(n,e.t) for n in (new.source,new.target)) or (m.upf and not e.node_up(m.upf,e.t)):self.finish(tx,'peer_unavailable');return
        if not all(e.profile_matches(e.nodes[n],new) for n in [new.source,new.target]+([m.upf] if m.upf else [])):self.finish(tx,'profile_not_supported');return
        family={fid:flow for fid,flow in self.current.items() if (flow.source,flow.target,flow.plmn,flow.snssai.lower(),flow.dnn)==(old.source,old.target,old.plmn,old.snssai.lower(),old.dnn)}
        cohort_size=max(flow.subscribers for flow in family.values());moves=[]
        if m.transfer_context:
            lifecycle=e.session_lifecycle
            for owner,record in lifecycle.records.items():
                flow=e.procedures[owner]['flow']
                if record['state']=='RELEASED' or flow.id not in family:continue
                destination=((new.source,record['ue'][1],record['ue'][2]),record['pdu_session_id'])
                if lifecycle.index.get(destination,owner)!=owner:self.finish(tx,'context_conflict');return
            for record in lifecycle.records.values():
                if record['ue'][0]==old.source and record['state'] in ('RELEASING','RECONCILING','ORPHANED'):
                    self.finish(tx,'session_cleanup_in_progress');return
            for store in (e.contexts,lifecycle.store):
                group=[]
                for key,value in list(store.items()):
                    if key[1]!=old.source or key[3:6]!=(old.plmn,old.snssai.lower(),old.dnn) or key[2]>=cohort_size:continue
                    node=new.target if key[0]==old.target else m.upf if m.upf and e.nodes[key[0]].kind=='UPF' else key[0]
                    dest=(node,new.source,*key[2:]);group.append((store,key,dest,deepcopy(value)))
                counts={n:len(o) for n,o in store.orders.items()}
                old_keys={k for _,k,_,_ in group};new_keys={k for _,_,k,_ in group}
                if any(dest in store and dest not in old_keys and store[dest]!=value for _,_,dest,value in group):self.finish(tx,'context_conflict');return
                for key in old_keys-new_keys:counts[key[0]]=counts.get(key[0],0)-1
                for key in new_keys-old_keys:
                    if key not in store:counts[key[0]]=counts.get(key[0],0)+1
                if any(v>store.limit(e.nodes[n]) for n,v in counts.items()):self.finish(tx,'context_limit');return
                moves.extend(group)
            lifecycle.suppress+=1
            try:
                for store,key,_,_ in moves:store.pop(key,None)
                for store,_,key,value in moves:store[key]=value
            finally:lifecycle.suppress-=1
            lifecycle.rehome(old,new)
            e.initial_registration_cohorts=[(new.target,new.source,*c[2:]) if c[:5]==(old.target,old.source,old.plmn,old.snssai.lower(),old.dnn) else c for c in e.initial_registration_cohorts]
            e.seeded_initial.update(dest for store,_,dest,_ in moves if store is e.contexts)
        for fid,flow in family.items():
            binds=dict(flow.nf_bindings)
            if m.upf:binds['UPF']=m.upf
            self.current[fid]=flow.model_copy(update=dict(source=new.source,target=new.target,nf_bindings=binds))
            if fid!=old.id:self.epochs[fid]=self.epochs.get(fid,0)+1
        for pr in e.procedures.values():
            if pr.get('control') or pr['flow'].id not in family:continue
            pr.setdefault('origin_flow',pr['flow'])
            pr['flow']=self.current[pr['flow'].id]
            if 'destination' in pr:pr['destination']=new.target
            session=e.user_plane.sessions.get(pr['id'])
            if session and m.upf:session['upf']=m.upf
            if m.upf and 'nf_selected' in pr:pr['nf_selected']['UPF']=m.upf
            if pr['active']:pr['mobility_obsolete']=True;e.finish(pr,'mobility_relocation')
            elif pr['id'] not in e.user_plane.sessions:pr['mobility_obsolete']=True
            for node in e.nodes:
                if pr['id'] in e.session_lifecycle.records:e.journal.watch_context(pr,'pdu_contexts',e.session_lifecycle.context_key(pr['id'],node))
        self.finish(tx,'completed')
    def finish(self,tx,status):
        if tx['closed']:return
        tx['closed']=True;self.pending.pop(tx['flow_id'],None);self.e.procedures[tx['pid']]['active']=False
        self.traces.append(dict(time=tx['start']/1000,end=self.e.t/1000,flow_id=tx['flow_id'],status=status,old_gnb=tx['old'].source,new_gnb=tx['new'].source,old_amf=tx['old'].target,new_amf=tx['new'].target,new_upf=tx['settings'].upf,transferred=tx['settings'].transfer_context and status=='completed'))
    def event(self,kind,data):
        if kind=='mobility_begin':self.begin(*data);return
        tx=self.transactions[data]
        if tx['closed']:return
        if kind=='mobility_timeout':self.finish(tx,'handover_timeout')
        else:self.commit(tx)
