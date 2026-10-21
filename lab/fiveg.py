"""Offline 5G SA control-plane workflows with nested, resource-bearing RPCs.

Models selected dependencies, not NAS/NGAP/HTTP2/PFCP wire compatibility.
UE cohorts are represented by a gNB; radio and user-plane data are omitted.
"""
from collections import Counter
from .procedures import ProcedureSimulation

SERVICES={
    'AMF':['namf-comm'], 'AUSF':['nausf-auth'],
    'UDM':['nudm-ueau','nudm-uecm','nudm-sdm'], 'UDR':['nudr-dr'],
    'SMF':['nsmf-pdusession'], 'UPF':['n4-pfcp'],
    'PCF':['npcf-smpolicycontrol'], 'NRF':['nnrf-disc'], 'NSSF':['nnssf-nsselection'],
}
OP_SERVICE={
    'Nnrf NFDiscovery':'nnrf-disc', 'Nausf UEAuthentication':'nausf-auth',
    'Nudm UEAuthentication':'nudm-ueau', 'Nudm UECMRegistration':'nudm-uecm',
    'Nudm SDMGet':'nudm-sdm', 'Nudr Query':'nudr-dr', 'Nudr Update':'nudr-dr',
    'Nnssf NSSelection':'nnssf-nsselection', 'Nsmf CreateSMContext':'nsmf-pdusession',
    'Nsmf UpdateSMContext':'nsmf-pdusession', 'Npcf SMPolicyCreate':'npcf-smpolicycontrol',
    'PFCP Session Establishment':'n4-pfcp', 'PFCP Session Modification':'n4-pfcp',
}


def call(source,target,operation,children=(),protocol='SBI',query=None):
    return dict(source=source,target=target,operation=operation,children=list(children),protocol=protocol,query=query)


class FiveGSimulation(ProcedureSimulation):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.rpc_seq=0;self.rpcs={};self.contexts=self.storage.store('fiveg_contexts',lambda n:n.context_limit,lambda n:n.context_ttl_s);self.service_stats={};self.seeded_initial=set()
        self.restarted_nodes=set()
        # Cohorts represent initial state at t=0 without allocating up to a
        # million subscriber dictionaries. A restart invalidates the whole cohort.
        self.initial_registration_cohorts=[(f.target,f.source,f.plmn,f.snssai.lower(),f.dnn,f.subscribers) for f in self.p.flows if f.operation=='5G PDU Session' and f.initial_registered]
        from .userplane import UserPlane
        self.user_plane=UserPlane(self)
        from .mobility import Mobility
        self.mobility=Mobility(self)

    def prepare(self):super().prepare();self.mobility.prepare()

    def new(self,ident,f):super().new(ident,self.mobility.flow(f))

    def profile_matches(self,n,f,service=None):
        return (n.enabled and n.nf_status=='REGISTERED' and n.plmn==f.plmn
                and f.snssai.lower() in [s.lower() for s in n.slices]
                and (n.kind not in ('SMF','UPF','PCF') or f.dnn in n.dnns)
                and (not service or service in (n.services or SERVICES.get(n.kind,[]))))

    def select_nf(self,pr,kind,service=None):
        f=pr['flow']
        pinned=f.nf_bindings.get(kind)
        profiles=sorted(n.id for n in self.nodes.values() if n.kind==kind and self.profile_matches(n,f,service))
        candidates=[nid for nid in profiles if self.node_up(nid,self.t,True,observer=f.target)]
        if pinned:
            if pinned in profiles and pinned not in candidates:self.touch(pr,pinned)
            return pinned if pinned in candidates else None
        if not candidates:
            for nid in profiles:self.touch(pr,nid)
            return None
        selected=candidates[pr['subscriber']%len(candidates)] if self.p.routing_mode=='loadshare' else candidates[0]
        preferred=profiles[pr['subscriber']%len(profiles)] if self.p.routing_mode=='loadshare' else profiles[0]
        if preferred!=selected:self.touch(pr,preferred)
        return selected

    def resolve(self,pr,role,service=None):
        if not role.startswith('@'):return role
        kind=role[1:]
        if kind=='gNB':return pr['flow'].source
        if kind=='AMF':return pr['flow'].target
        selected=pr['nf_selected'].get(kind)
        if selected is not None:return selected
        selected=self.select_nf(pr,kind,service)
        if selected is not None:pr['nf_selected'][kind]=selected
        self.journal.emit(pr['id'],'nf_selected',cause=pr.get('_journal_event'),nf_kind=kind,service=service,selected=selected,pinned=pr['flow'].nf_bindings.get(kind),selector=pr['subscriber'],mode=self.p.routing_mode)
        return selected

    def discovery(self,pr,source,kind,service):
        return call(source,'@NRF','Nnrf NFDiscovery',query=(kind,service)) if pr['flow'].use_nrf else None

    def plan(self,pr,operation):
        auth=call('@AMF','@AUSF','Nausf UEAuthentication',[
            self.discovery(pr,'@AUSF','UDM','nudm-ueau'),
            call('@AUSF','@UDM','Nudm UEAuthentication',[call('@UDM','@UDR','Nudr Query')])])
        if operation=='5G Registration':
            return [self.discovery(pr,'@AMF','AUSF','nausf-auth'),auth,
                call('@AMF','@gNB','NAS AuthenticationSecurity',protocol='NGAP'),
                call('@AMF','@NSSF','Nnssf NSSelection') if pr['flow'].use_nssf else None,
                call('@AMF','@UDM','Nudm UECMRegistration',[call('@UDM','@UDR','Nudr Update')]),
                call('@AMF','@UDM','Nudm SDMGet',[call('@UDM','@UDR','Nudr Query')])]
        return [self.discovery(pr,'@AMF','SMF','nsmf-pdusession'),
            call('@AMF','@SMF','Nsmf CreateSMContext',[
                call('@SMF','@UDM','Nudm SDMGet',[call('@UDM','@UDR','Nudr Query')]),
                call('@SMF','@PCF','Npcf SMPolicyCreate',[call('@PCF','@UDR','Nudr Query')]) if pr['flow'].use_pcf else None,
                call('@SMF','@UPF','PFCP Session Establishment',protocol='PFCP')]),
            call('@AMF','@gNB','NGAP PDUResourceSetup',protocol='NGAP'),
            call('@AMF','@SMF','Nsmf UpdateSMContext',[
                call('@SMF','@UPF','PFCP Session Modification',protocol='PFCP')])]

    def context_key(self,pr,n):
        f=pr['flow']
        return (n,f.source,pr['subscriber'],f.plmn,f.snssai.lower(),f.dnn)

    def registration_context(self,pr,n):
        key=self.context_key(pr,n)
        if key not in self.contexts and key not in self.seeded_initial and n not in self.restarted_nodes and self.t<self.nodes[n].context_ttl_s*1000:
            if any((n,key[1],key[3],key[4],key[5])==cohort[:5] and key[2]<cohort[5] for cohort in self.initial_registration_cohorts):
                self.contexts[key]={'registered':True};self.seeded_initial.add(key)
        return self.contexts.get(key,{})

    def send_attempt(self,pr):
        if not pr['flow'].operation.startswith('5G '):return super().send_attempt(pr)
        if 'nf_selected' not in pr:
            pr['nf_selected']={};pr['roots']=[]
            pr['steps']=['5G Registration','5G PDU Session'] if '+' in pr['flow'].operation else [pr['flow'].operation]
        operation=pr['steps'][pr['step']]
        spec=call(pr['flow'].source,pr['flow'].target,operation,self.plan(pr,operation),protocol='NGAP')
        frame=self.start_rpc(pr,spec,None);pr['roots'].append(frame['id'])

    def start_rpc(self,pr,spec,parent):
        self.rpc_seq+=1
        src=self.resolve(pr,spec['source']);dst=self.resolve(pr,spec['target'],OP_SERVICE.get(spec['operation']))
        frame=dict(id=self.rpc_seq,procedure=pr['id'],step=pr['step'],spec=spec,src=src,dst=dst,parent=parent,
            attempt=0,token=0,closed=False,server_started=False,server_epoch=None,index=0,reply=None)
        self.rpcs[frame['id']]=frame
        stat=self.service_stats.setdefault(spec['operation'],dict(requests=0,success=0,errors=0,timeouts=0))
        frame['stat']=stat
        self.transmit_rpc(frame)
        return frame

    def transmit_rpc(self,frame):
        if frame['closed']:return
        pr=self.procedures[frame['procedure']];f=pr['flow'];frame['token']+=1;pr['attempts']+=1;frame['stat']['requests']+=1
        if frame['attempt']:
            pr['retries']+=1
            self.telemetry.count('signal_retries')
            if self.t<self.p.duration*1000:self.buckets[self.window(self.t)]['retries']+=1
        timeout=f.timeout_ms if frame['parent'] is None else f.service_timeout_ms
        frame['_journal_timer']=self.journal.emit(pr['id'],'timer_set',cause=frame.get('_journal_retry',pr.get('_journal_event')),timer_id=f'rpc:{frame["id"]}:{frame["token"]}',timer_type='rpc',deadline_ms=self.t+timeout,rpc_id=frame['id'],parent_rpc=frame['parent'],operation=frame['spec']['operation'],attempt=frame['attempt'],node=frame['src'],source=frame['src'],target=frame['dst'])
        pr['_journal_event']=frame['_journal_timer']
        self.push(self.t+timeout,'rpc_timeout',(frame['id'],frame['token']),0)
        if frame['src'] is None or frame['dst'] is None:
            pr['last_cause']='nf_not_found';return
        spec=frame['spec']
        self.send_message(pr,frame['src'],frame['dst'],'request',spec['operation'],f.message_bytes,
            step=frame['step'],protocol=spec['protocol'],rpc_id=frame['id'])

    def validate_request(self,pr,frame):
        n=self.nodes[frame['dst']];f=pr['flow'];op=frame['spec']['operation']
        if n.plmn!=f.plmn:return 'plmn_not_supported'
        if f.snssai.lower() not in [s.lower() for s in n.slices]:return 'slice_not_supported'
        if n.kind in ('SMF','UPF','PCF') and f.dnn not in n.dnns:return 'dnn_not_supported'
        if n.kind!='gNB' and n.nf_status!='REGISTERED':return 'nf_suspended'
        service=OP_SERVICE.get(op)
        if service and service not in (n.services or SERVICES.get(n.kind,[])):return 'nf_service_unavailable'
        if op=='5G PDU Session' and not self.registration_context(pr,frame['dst']).get('registered'):return 'not_registered'
        if op=='Nsmf UpdateSMContext' and not self.contexts.get(self.context_key(pr,frame['dst']),{}).get('sm_context'):return 'session_context_lost'
        if self.rng.random()<self.application_error(n.id):return 'protocol_error'
        return 'success'

    def app_arrival(self,msg):
        if msg.get('category') in ('control','userplane','mobility'):return super().app_arrival(msg)
        if msg.get('rpc_id') is None:return super().app_arrival(msg)
        frame=self.rpcs[msg['rpc_id']];pr=self.procedures[frame['procedure']];spec=frame['spec']
        event=self.journal.message(msg,'message_arrived',node=msg['target'],result=msg['result'],elapsed_ms=self.t-msg['start'],parent_rpc=frame['parent'])
        if event:pr['_journal_event']=event
        if pr.get('mobility_obsolete'):return
        self.message_finished+=1
        self.reservoir(self.message_traces,dict(time=msg['start']/1000,end=self.t/1000,procedure_id=pr['id'],session_id=pr['session'],
            transaction_id=f'{pr["id"]}:rpc-{frame["id"]}',role=msg['role'],operation=msg['operation'],protocol=msg['protocol'],
            source=msg['source'],target=msg['target'],bytes=msg['size'],result=msg['result'],snssai=pr['flow'].snssai,dnn=pr['flow'].dnn),self.message_finished)
        if msg['role']=='answer':
            if frame['closed']:return
            self.journal.emit(pr['id'],'timer_cancelled',cause=frame.get('_journal_timer'),timer_id=f'rpc:{frame["id"]}:{frame["token"]}',timer_type='rpc',rpc_id=frame['id'],reason='answer_received',message_id=msg['id'])
            frame['closed']=True;result=msg['result']
            frame['stat']['success' if result=='success' else 'errors']+=1
            if frame['parent'] is None:
                if not pr['active'] or pr['step']!=frame['step']:return
                if result=='success' and spec['operation']=='5G PDU Session':self.user_plane.start(pr)
                if result!='success':pr['last_cause']=result;self.finish(pr,result)
                elif pr['step']+1<len(pr['steps']):pr['step']+=1;self.send_attempt(pr)
                else:self.finish(pr,'delivered')
            else:
                parent=self.rpcs[frame['parent']]
                if parent.get('waiting')!=frame['id'] or parent['server_epoch']!=self.node_epoch[parent['dst']] or parent['reply'] is not None:return
                if result!='success':self.respond(parent,result)
                else:self.next_child(parent)
            return
        key=('5g',frame['dst'],pr['id'],frame['id'])
        if key in self.cache:
            self.node_stats[frame['dst']]['duplicates']+=1;self.reply_wire(frame,self.cache[key]);return
        epoch=self.node_epoch[frame['dst']]
        if frame['server_started'] and frame['server_epoch']==epoch and frame['reply'] is None:
            self.node_stats[frame['dst']]['duplicates']+=1;return
        frame['server_started']=True;frame['server_epoch']=epoch;frame['index']=0;frame['reply']=None
        from .resources import CapacityError
        try:result=msg.get('resource_error') or self.validate_request(pr,frame)
        except CapacityError:result='context_limit'
        if result=='success' and spec['query']:
            kind,service=spec['query'];selected=self.select_nf(pr,kind,service)
            if selected is None:result='nf_not_found'
            else:pr['nf_selected'][kind]=selected
        if result!='success':self.respond(frame,result)
        else:self.next_child(frame)

    def next_child(self,frame):
        if self.procedures[frame['procedure']].get('mobility_obsolete'):return
        if frame['server_epoch']!=self.node_epoch[frame['dst']]:return
        specs=frame['spec']['children']
        while frame['index']<len(specs):
            spec=specs[frame['index']];frame['index']+=1
            if spec is not None:
                child=self.start_rpc(self.procedures[frame['procedure']],spec,frame['id']);frame['waiting']=child['id'];return
        self.respond(frame,'success')

    def respond(self,frame,result):
        if frame['server_epoch']!=self.node_epoch[frame['dst']]:return
        pr=self.procedures[frame['procedure']];op=frame['spec']['operation']
        if pr.get('mobility_obsolete'):return
        flags={'Nausf UEAuthentication':'authenticated','5G Registration':'registered','Nsmf CreateSMContext':'sm_context','PFCP Session Establishment':'n4_session','5G PDU Session':'pdu_active','Nudm UECMRegistration':'ue_registered','Npcf SMPolicyCreate':'policy_active'}
        if result=='success' and op=='5G PDU Session' and not self.contexts.get(self.context_key(pr,frame['dst']),{}).get('registered'):result='not_registered'
        if result=='success' and op=='PFCP Session Modification' and not self.contexts.get(self.context_key(pr,frame['dst']),{}).get('n4_session'):result='session_context_lost'
        if result=='success' and op in flags:
            from .resources import CapacityError
            try:ctx=self.contexts.setdefault(self.context_key(pr,frame['dst']),{})
            except CapacityError:ctx=None;result='context_limit'
        if result=='success' and op in flags:
            ctx[flags[op]]=True
            self.contexts[self.context_key(pr,frame['dst'])]=ctx
        if result!='success':self.node_stats[frame['dst']]['errors']+=1
        frame['reply']=result;self.cache[('5g',frame['dst'],pr['id'],frame['id'])]=result
        self.reply_wire(frame,result)

    def reply_wire(self,frame,result):
        pr=self.procedures[frame['procedure']];spec=frame['spec']
        answer={'5G Registration':'5G Registration Accept','5G PDU Session':'5G PDU Session Accept'}.get(spec['operation'],spec['operation']+' Response')
        if result!='success' and spec['operation'].startswith('5G '):answer=spec['operation']+' Reject'
        self.send_message(pr,frame['dst'],frame['src'],'answer',answer,pr['flow'].response_bytes,result,
            step=frame['step'],protocol=spec['protocol'],rpc_id=frame['id'])

    def rpc_event(self,kind,data):
        ident,token=data;frame=self.rpcs[ident]
        if frame['closed'] or frame['token']!=token:return
        pr=self.procedures[frame['procedure']]
        if frame['parent'] is None and (not pr['active'] or pr['step']!=frame['step']):return
        if kind=='rpc_retry':self.transmit_rpc(frame);return
        expired=self.journal.emit(pr['id'],'timer_expired',cause=frame.get('_journal_timer'),timer_id=f'rpc:{ident}:{token}',timer_type='rpc',rpc_id=ident,operation=frame['spec']['operation'],reason='no_answer_before_deadline')
        pr['_journal_event']=expired
        if frame['attempt']<pr['flow'].max_retries:
            frame['attempt']+=1
            self.push(self.t+pr['flow'].retry_backoff_ms*2**(frame['attempt']-1),'rpc_retry',(ident,token))
            frame['_journal_retry']=self.journal.emit(pr['id'],'procedure_retry',cause=expired,rpc_id=ident,attempt=frame['attempt'],scheduled_ms=self.t+pr['flow'].retry_backoff_ms*2**(frame['attempt']-1))
        else:
            frame['closed']=True;frame['stat']['timeouts']+=1
            if frame['parent'] is None:
                if pr['active']:self.finish(pr,'timeout')
            else:
                parent=self.rpcs[frame['parent']]
                if parent.get('waiting')==ident and parent['reply'] is None:self.respond(parent,'service_timeout')

    def node_fault(self,n,kind):
        super().node_fault(n,kind)
        self.user_plane.policy.node_fault(n)
        if kind=='node_restart':
            self.restarted_nodes.add(n)

    def report(self):
        result=super().report()
        self.user_plane.report(result)
        result['fiveg']=dict(profile='5G SA behavioural control plane',services=self.service_stats,
            contexts=dict(Counter(k for value in self.contexts.values() for k,v in value.items() if v and not k.startswith('_'))),
            nf_profiles=[dict(id=n.id,kind=n.kind,status=n.nf_status,services=n.services or SERVICES.get(n.kind,[]),plmn=n.plmn,slices=n.slices,dnns=n.dnns) for n in self.nodes.values() if n.kind in SERVICES])
        for trace in result['traces']:
            pr=self.procedures[trace['procedure_id']]
            if pr['flow'].operation.startswith('5G '):
                trace.update(snssai=pr['flow'].snssai,dnn=pr['flow'].dnn,nf_selected=pr['nf_selected'])
                origin=pr.get('origin_flow',pr['flow']);trace['route']=next((p for p in pr['routes'] if p[0]==origin.source and p[-1]==origin.target),[])
        return result
