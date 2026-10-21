"""Offline PDU ownership, protocol cleanup and UE deregistration fencing."""
from collections import Counter
from copy import deepcopy


class SessionLifecycle:
    def __init__(self,engine):
        self.e=engine;self.records={};self.index={};self.epochs=Counter();self.blocked=set();self.deregistering=set()
        self.transactions={};self.seq=0;self.events=[];self.event_count=0;self.stats=Counter();self.suppress=0
        self.store=engine.storage.store('pdu_contexts',lambda n:n.pdu_session_limit,lambda n:n.context_ttl_s)

    def ue(self,pr):return (pr['flow'].source,pr['subscriber'],pr['flow'].plmn)
    def context_key(self,owner,node):
        pr=self.e.procedures[owner];return self.e.context_key(pr,node)+(self.records[owner]['pdu_session_id'],)
    def emit(self,record,state,reason=None):
        record['state']=state;record['updated_ms']=self.e.t
        if reason:record['reason']=reason
        self.event_count+=1;self.stats[state]+=1
        event=dict(time=self.e.t/1000,procedure_id=record['owner'],pdu_session_id=record['pdu_session_id'],state=state,reason=reason)
        if len(self.events)<self.e.p.trace_limit:self.events.append(event)
        self.e.journal.emit(record['owner'],'pdu_state',state=state,reason=reason,pdu_session_id=record['pdu_session_id'])

    def reserve(self,pr):
        if pr['id'] in self.records:return None
        ue=self.ue(pr);f=pr['flow']
        if ue in self.deregistering or ue in self.blocked:return 'not_registered'
        selected=next((i for i in range(f.pdu_session_id,f.pdu_session_id+f.pdu_session_count) if (ue,i) not in self.index),None)
        if selected is None:
            return 'pdu_session_in_use' if self.e.registration_context(pr,f.target).get('registered') else 'not_registered'
        pr['pdu_session_id']=selected;self.index[(ue,selected)]=pr['id']
        record=dict(owner=pr['id'],pdu_session_id=selected,ue=list(ue),state='ESTABLISHING',created_ms=self.e.t,updated_ms=self.e.t,cleanup_attempts=0,reason=None)
        self.records[pr['id']]=record
        for node in self.e.nodes:self.e.journal.watch_context(pr,'pdu_contexts',self.context_key(pr['id'],node))
        self.emit(record,'ESTABLISHING');return None

    def find(self,pr):return self.index.get((self.ue(pr),pr['flow'].pdu_session_id))
    def owned_keys(self,owner):
        return [key for key,item in self.store.data.items() if item[0].get('owner')==owner]
    def valid_write(self,pr):
        record=self.records.get(pr['id'])
        return bool(record and record['state']=='ESTABLISHING' and pr.get('ue_epoch')==self.epochs[self.ue(pr)] and self.ue(pr) not in self.deregistering)
    def write(self,pr,node,flag):
        key=self.context_key(pr['id'],node);item=self.store.get(key)
        if item and item.get('owner')!=pr['id']:raise ValueError('pdu_generation_conflict')
        value=dict(item or {},owner=pr['id']);value[flag]=True;self.store[key]=value
    def context(self,owner,node):
        return self.store.get(self.context_key(owner,node),{}) if owner in self.records and node else {}
    def delete(self,owner,node):
        if owner not in self.records or not node:return
        key=self.context_key(owner,node);item=self.store.data.get(key)
        if item and item[0].get('owner')==owner:
            self.suppress+=1
            try:self.store.pop(key,None)
            finally:self.suppress-=1
        elif item is None and node in self.e.clusters.members:
            record=self.records[owner]
            if self.index.get((tuple(record['ue']),record['pdu_session_id']),owner)==owner:
                self.e.clusters.capture('pdu_contexts',key,None,None,owner=owner)

    def activate(self,pr):
        record=self.records[pr['id']]
        if not self.valid_write(pr):return False
        self.emit(record,'ACTIVE')
        f=pr['flow']
        for kind,delay in [('session_release',f.session_lifetime_s),('session_deregister',f.deregister_after_s)]:
            if delay and self.e.t+delay*1000<=self.e.p.duration*1000:
                record[kind]=self.e.journal.emit(pr['id'],'timer_set',timer_id=f'{kind}:{pr["id"]}',timer_type=kind,deadline_ms=self.e.t+delay*1000,pdu_session_id=pr['pdu_session_id'])
                self.e.push(self.e.t+delay*1000,kind,pr['id'],0)
        return True

    def release_specs(self,owner,root=True):
        from .fiveg import call
        pr=self.e.procedures[owner];f=pr['flow'];nf=pr['nf_selected'];smf=nf.get('SMF');upf=nf.get('UPF');pcf=nf.get('PCF')
        def spec(src,dst,op,children=(),protocol='SBI'):
            value=call(src,dst,op,children,protocol);value['pdu_owner']=owner;return value
        children=[]
        if upf:children.append(spec(smf or f.target,upf,'PFCP Session Deletion',protocol='PFCP'))
        if pcf:children.append(spec(smf or f.target,pcf,'Npcf SMPolicyDelete'))
        steps=[spec(f.target,smf,'Nsmf ReleaseSMContext',children)] if smf else children
        steps.append(spec(f.target,f.source,'NGAP PDUResourceRelease',protocol='NGAP'))
        return spec(f.source,f.target,'5G PDU Session Release',steps,protocol='NGAP') if root else steps

    def begin_release(self,owner,reason):
        record=self.records.get(owner)
        if not record or record['state'] in ('RELEASED','RELEASING','RECONCILING'):return
        if 'session_release' in record:
            self.e.journal.emit(owner,'timer_cancelled',cause=record.pop('session_release'),timer_id=f'session_release:{owner}',timer_type='session_release',reason=reason)
        self.emit(record,'RELEASING',reason);self.e.user_plane.stop(owner)
    def complete_release(self,owner):
        record=self.records.get(owner)
        if not record:return
        if self.owned_keys(owner):return
        if record['state']=='RELEASED':return
        self.index.pop((tuple(record['ue']),record['pdu_session_id']),None);self.emit(record,'RELEASED')
        self.e.user_plane.stop(owner,completed=True)

    def start_cleanup(self,owner,reason):
        record=self.records.get(owner)
        if not record or record['state']=='RELEASED' or record.get('cleanup_pending'):return
        self.begin_release(owner,reason)
        if not self.owned_keys(owner):self.complete_release(owner);return
        record['cleanup_pending']=True
        self.start_transaction(self.e.procedures[owner],self.release_specs(owner),owner,'release')

    def start_transaction(self,pr,spec,owner,purpose):
        self.seq+=1;ident=self.seq
        self.transactions[ident]=dict(id=ident,owner=owner,procedure=pr['id'],purpose=purpose,closed=False)
        if owner is not None and owner in self.records:self.records[owner]['cleanup_attempts']+=1
        self.e.start_rpc(pr,spec,None,lifecycle_id=ident)

    def transaction_done(self,ident,result):
        tx=self.transactions[ident]
        if tx['closed']:return
        tx['closed']=True;owner=tx['owner'];pr=self.e.procedures[tx['procedure']]
        if tx['purpose']=='deregister':
            if result!='success':self.failed_deregister(pr)
            return
        record=self.records[owner];record['cleanup_pending']=False
        if not self.owned_keys(owner):self.complete_release(owner);return
        self.emit(record,'ORPHANED',result)
        f=pr['flow'];at=self.e.t+f.session_cleanup_retry_s*1000
        if record['cleanup_attempts']<=f.session_cleanup_retries and at<self.e.p.duration*1000:self.e.push(at,'session_reconcile',owner,0)

    def reconcile_specs(self,owner):
        from .fiveg import call
        pr=self.e.procedures[owner];f=pr['flow'];nf=pr['nf_selected'];steps=[]
        # Independent deletions let reachable peers reclaim state even when a
        # different peer is still unavailable. Every deletion still uses a wire RPC.
        for key in sorted(self.owned_keys(owner),key=lambda k:({'UPF':0,'PCF':1,'SMF':2,'AMF':3}.get(self.e.nodes[k[0]].kind,4),k)):
            node=key[0];kind=self.e.nodes[node].kind
            src,op,proto={'UPF':(nf.get('SMF') or f.target,'PFCP Session Deletion','PFCP'),
                          'PCF':(nf.get('SMF') or f.target,'Npcf SMPolicyDelete','SBI'),
                          'SMF':(f.target,'Nsmf ReleaseSMContext','SBI')}.get(kind,(f.source,'5G PDU Session Release','NGAP'))
            spec=call(src,node,op,protocol=proto);spec['pdu_owner']=owner;steps.append(spec)
        return steps

    def begin_deregister(self,pr):
        ue=self.ue(pr)
        if pr.get('deregistration_started') and pr.get('deregistration_epoch')!=self.epochs[ue]:return 'stale_deregistration'
        if not pr.get('deregistration_started') and ue in self.deregistering:return 'deregistration_in_progress'
        if not pr.get('deregistration_started'):
            pr['deregistration_started']=True;self.epochs[ue]+=1;self.blocked.add(ue);self.deregistering.add(ue)
            pr['deregistration_epoch']=self.epochs[ue]
            for owner,record in list(self.records.items()):
                if tuple(record['ue'])==ue and record['state']!='RELEASED':self.begin_release(owner,'ue_deregistration')

    def deregister_plan(self,pr):
        from .fiveg import call
        steps=[];ue=self.ue(pr)
        for owner,record in self.records.items():
            if tuple(record['ue'])==ue and record['state']!='RELEASED':steps.append(self.release_specs(owner))
        udm=self.e.resolve(pr,'@UDM','nudm-uecm')
        if udm:steps.append(call(pr['flow'].target,udm,'Nudm UECMDeregistration',[call(udm,'@UDR','Nudr Update')]))
        ausf=self.e.resolve(pr,'@AUSF','nausf-auth')
        if ausf:steps.append(call(pr['flow'].target,ausf,'Nausf AuthenticationContextRelease'))
        return steps

    def delete_registration(self,pr,node):
        ue=self.ue(pr);self.suppress+=1
        try:
            keys={key for key in self.e.contexts.orders.get(node,{}) if (key[1],key[2],key[3])==ue}
            keys.add(self.e.context_key(pr,node))
            for owner,record in self.records.items():
                if tuple(record['ue'])==ue:keys.add(self.e.context_key(self.e.procedures[owner],node))
            for key in keys:
                if key in self.e.contexts.data:self.e.contexts.pop(key,None)
                elif node in self.e.clusters.members:self.e.clusters.capture('fiveg_contexts',key,None,None)
        finally:self.suppress-=1
    def deregister_complete(self,pr,node=None):
        if pr.get('deregistration_completed'):return
        pr['deregistration_completed']=True
        self.delete_registration(pr,node or pr['flow'].target);self.deregistering.discard(self.ue(pr))
        for owner,record in self.records.items():
            if tuple(record['ue'])==self.ue(pr):self.complete_release(owner)
        self.stats['deregistered']+=1
    def retry_deregister(self,pr):
        if pr.get('deregistration_completed'):return
        count=pr.get('deregistration_retries',0);at=self.e.t+pr['flow'].session_cleanup_retry_s*1000
        if count<pr['flow'].session_cleanup_retries and at<self.e.p.duration*1000:
            pr['deregistration_retries']=count+1;self.e.push(at,'session_retry_deregister',pr['id'],0)
    def registered(self,pr):
        ue=self.ue(pr)
        if ue in self.blocked:self.epochs[ue]+=1;pr['ue_epoch']=self.epochs[ue]
        self.blocked.discard(ue);self.deregistering.discard(ue)

    def failed_deregister(self,pr):
        if pr.get('deregistration_completed') or pr.get('deregistration_epoch')!=self.epochs[self.ue(pr)]:return
        self.retry_deregister(pr)
        for owner,record in list(self.records.items()):
            if tuple(record['ue'])==self.ue(pr) and record['state']!='RELEASED':self.start_cleanup(owner,'deregistration_incomplete')

    def on_finish(self,pr,status):
        if status!='delivered' and pr['id'] in self.records:self.start_cleanup(pr['id'],status)
        if pr['flow'].operation=='5G Deregistration' and status!='delivered' and pr.get('deregistration_started'):self.failed_deregister(pr)
        if pr['flow'].operation=='5G PDU Session Release':
            owner=pr.get('release_owner')
            if owner is not None:
                self.complete_release(owner)
                if status!='delivered':self.start_cleanup(owner,status)

    def context_removed(self,store,key,value,reason):
        if self.suppress:return
        if reason=='restart_cleared' and self.e.clusters.recoverable(store,key,value):return
        if getattr(self.e,'reporting',False):
            record=self.records.get(value.get('owner')) if store=='pdu_contexts' else None
            if record and record['state']!='RELEASED':self.emit(record,'ORPHANED',reason+'_after_observation')
            return
        if store=='pdu_contexts':
            owner=value.get('owner')
            if owner in self.records and self.records[owner]['state']!='RELEASED':
                self.e.user_plane.stop(owner);self.emit(self.records[owner],'ORPHANED',reason)
                self.e.push(self.e.t,'session_reconcile',owner,0)
        elif store=='fiveg_contexts' and value.get('registered') and reason in ('expired','restart_cleared'):
            for owner,record in list(self.records.items()):
                if tuple(record['ue'])==(key[1],key[2],key[3]) and record['state']!='RELEASED':self.e.push(self.e.t,'session_release',owner,0)

    def event(self,kind,owner):
        from .fiveg import call
        record=self.records.get(owner)
        if record and kind in record:
            self.e.journal.emit(owner,'timer_expired',cause=record.pop(kind),timer_id=f'{kind}:{owner}',timer_type=kind,pdu_session_id=record['pdu_session_id'])
        if kind=='session_deregister' or kind=='session_retry_deregister':
            pr=self.e.procedures[owner]
            if kind=='session_deregister' and (pr.get('ue_epoch')!=self.epochs[self.ue(pr)] or self.ue(pr) in self.blocked):return
            spec=call(pr['flow'].source,pr['flow'].target,'5G Deregistration',protocol='NGAP')
            self.start_transaction(pr,spec,None,'deregister');return
        if kind=='session_release':self.start_cleanup(owner,'session_lifetime');return
        record=self.records[owner]
        if record['state']=='RELEASED' or record.get('cleanup_pending'):return
        specs=self.reconcile_specs(owner)
        if not specs:self.complete_release(owner);return
        # An independent RPC per remaining peer; a root at the gNB sequences them.
        record['cleanup_pending']=True;self.emit(record,'RECONCILING')
        root=call(self.e.procedures[owner]['flow'].source,self.e.procedures[owner]['flow'].source,'Session Reconciliation',specs,protocol='NGAP')
        root['continue_on_error']=True
        self.start_transaction(self.e.procedures[owner],root,owner,'reconcile')

    def rehome(self,old,new):
        for owner,record in self.records.items():
            flow=self.e.procedures[owner]['flow']
            if record['state']=='RELEASED' or (flow.source,flow.target,flow.plmn,flow.snssai.lower(),flow.dnn)!=(old.source,old.target,old.plmn,old.snssai.lower(),old.dnn):continue
            previous=tuple(record['ue']);current=(new.source,previous[1],previous[2])
            if self.index.get((previous,record['pdu_session_id']))==owner:
                self.index.pop((previous,record['pdu_session_id']));self.index[(current,record['pdu_session_id'])]=owner
            record['ue']=list(current)
            self.epochs[current]=max(self.epochs[current],self.epochs[previous])
            if previous in self.blocked:self.blocked.add(current)
            if previous in self.deregistering:self.deregistering.add(current)

    def report(self):
        states=Counter(r['state'] for r in self.records.values());records=list(self.records.values())
        if len(records)>self.e.p.trace_limit:
            import numpy as np
            rng=np.random.default_rng(self.e.p.seed^0x5E5510);records=[records[i] for i in sorted(rng.choice(len(records),self.e.p.trace_limit,replace=False))]
        sessions=[];owners={r['owner'] for r in records};contexts=[];nodes={}
        for key,(value,deadline,_) in self.store.data.items():
            if value['owner'] in owners:
                nodes.setdefault(value['owner'],[]).append(key[0])
                contexts.append(dict(node=key[0],owner=value['owner'],pdu_session_id=key[-1],flags=sorted(k for k,v in value.items() if k!='owner' and v),expires_ms=deadline))
        for record in records:
            pr=self.e.procedures[record['owner']];f=pr['flow']
            sessions.append(dict(record,gnb=f.source,subscriber_id=pr['subscriber'],amf=f.target,nf_selected=dict(pr['nf_selected']),dnn=f.dnn,snssai=f.snssai,
                context_nodes=sorted(nodes.get(record['owner'],[]))))
        return dict(version='pdu-lifecycle-v1',states=dict(states),total=len(self.records),remaining_contexts=len(self.store.data),
            pending_deregistrations=len(self.deregistering),transitions=dict(self.stats),sessions=deepcopy(sessions),contexts=contexts,events=self.events,
            event_count=self.event_count,events_truncated=self.event_count>len(self.events),session_sampled=len(records)<len(self.records))
