"""Resource-bearing management exchanges and per-router link-state views.

Offline protocol event model; no sockets, codecs or external peers.
"""
from collections import Counter

PHASES={'Diameter':[('CER','CEA')],'M3UA':[('ASPUP','ASPUP_ACK'),('ASPAC','ASPAC_ACK')],
        'SS7':[('LINK_ALIGNMENT','ALIGNMENT_ACK')],'NGAP':[('NG_SETUP','NG_SETUP_RESPONSE')],
        'SBI':[('HTTP2_SETTINGS','HTTP2_SETTINGS_ACK')],'PFCP':[('PFCP_ASSOCIATION','PFCP_ASSOCIATION_RESPONSE')]}
HEARTBEATS={'Diameter':('DWR','DWA'),'M3UA':('BEAT','BEAT_ACK'),'SS7':('LINK_TEST','LINK_TEST_ACK'),
            'NGAP':('SCTP_HEARTBEAT','SCTP_HEARTBEAT_ACK'),'SBI':('HTTP2_PING','HTTP2_PING_ACK'),'PFCP':('PFCP_HEARTBEAT','PFCP_HEARTBEAT_RESPONSE')}


class ControlPlane:
    def __init__(self,engine):
        self.e=engine;self.states={};self.requests={};self.seq=0;self.sent=0;self.received=0;self.traces=[];self.counts=Counter()
        self.views={n:{} for n in engine.nodes};self.view_events=[];self.view_seen=0;self.revision=0
        for observer in self.views:
            for n,node in engine.nodes.items():self.views[observer][('node',n,'both')]=dict(up=node.enabled,version=-1,time=0,origin='configuration')
            for lid,link in engine.links.items():
                for direction in ('forward','reverse'):self.views[observer][('link',lid,direction)]=dict(up=link.enabled,version=-1,time=0,origin='configuration')
                self.views[observer][('association',lid,'both')]=dict(up=not engine.p.control_plane or link.protocol not in PHASES,version=-1,time=0,origin='configuration')
        for lid,link in engine.links.items():
            if link.protocol in PHASES:self.states[lid]=dict(state='CLOSED',epoch=0,pending=None,phase=0)

    def enabled(self,lid):
        link=self.e.links[lid]
        return link.enabled and (link.protocol!='M3UA' or link.asp_state=='ACTIVE') and (link.protocol not in ('Diameter','NGAP','SBI','PFCP') or link.peer_state=='OPEN')

    def prepare(self):
        e=self.e
        if e.p.control_plane:
            for lid,state in self.states.items():
                if self.enabled(lid):e.push(e.links[lid].startup_ms,'ctrl_start',lid)
                if e.links[lid].shutdown_at_s is not None:e.push(e.links[lid].shutdown_at_s*1000,'ctrl_close',lid,0)
        if e.p.routing_knowledge!='distributed':return
        if e.p.route_refresh_ms<=e.p.duration*1000:e.push(e.p.route_refresh_ms,'ctrl_sync',0)
        updates=[]
        for incident in e.p.incidents:
            if incident.kind not in ('node_down','node_restart','link_down'):continue
            resource='link' if incident.kind=='link_down' else 'node';target=incident.target
            adjacent={target} if resource=='node' else {e.links[target].source,e.links[target].target}
            if resource=='node':adjacent.update(n for n,_ in e.adj[target])
            for observer in sorted(adjacent):
                configured=e.nodes[observer].observer_detection_ms
                delay=incident.detection_ms if incident.detection_ms is not None else configured if configured is not None else (e.links[target].detection_ms if resource=='link' else e.nodes[target].detection_ms)
                switch=e.links[target].switch_ms if resource=='link' else e.nodes[target].switch_ms
                recovery=incident.recovery_ms if incident.recovery_ms is not None else (e.links[target].recovery_ms if resource=='link' else e.nodes[target].recovery_ms)
                down=incident.start*1000+delay+switch;up=(incident.start+incident.duration)*1000+recovery
                if incident.duration*1000>delay and down<up:
                    updates.append((down,(observer,resource,target,incident.direction,False)))
                updates.append((up,(observer,resource,target,incident.direction,True)))
        # Versions follow observation time, including recovery delays. An older
        # physical incident can otherwise recover last with an obsolete version.
        for version,(time,payload) in enumerate(sorted(updates,key=lambda item:(item[0],item[1])),1):
            e.push(time,'ctrl_view',(*payload,version))

    def can_restore(self,observer,resource,target,direction):
        """A local recovery cannot release another active fault on the same key."""
        e=self.e;item=(e.links if resource=='link' else e.nodes)[target]
        if not item.enabled:return False
        for incident in e.p.incidents:
            if incident.target!=target or incident.kind not in (('link_down',) if resource=='link' else ('node_down','node_restart')):continue
            if resource=='link' and incident.direction not in ('both',direction):continue
            start=incident.start*1000;end=(incident.start+incident.duration)*1000
            if start<=e.t<end:return False
            configured=e.nodes[observer].observer_detection_ms
            delay=incident.detection_ms if incident.detection_ms is not None else configured if configured is not None else item.detection_ms
            recovery=incident.recovery_ms if incident.recovery_ms is not None else item.recovery_ms
            if end-start>delay and start+delay+item.switch_ms<=e.t<end+recovery:return False
        return True

    def known(self,observer,kind,target,direction='both'):
        return self.views.get(observer,{}).get((kind,target,direction),dict(up=True))['up']

    def record_view(self,observer,key,up,version,origin,previous=None):
        e=self.e;old=self.views[observer].get(key)
        if old and old['version']>=version:return
        self.views[observer][key]=dict(up=up,version=version,time=e.t/1000,origin=origin)
        e.telemetry.view(observer,key,up)
        self.view_seen+=1;e.reservoir(self.view_events,dict(time=e.t/1000,observer=observer,resource=key[0],target=key[1],direction=key[2],up=up,version=version,origin=origin),self.view_seen)
        if not e.node_up(observer,e.t):return
        for nxt,lid in sorted(e.adj[observer]):
            if nxt==previous or e.links[lid].protocol in ('GTPU','IP'):continue
            e.push(e.t+e.nodes[observer].advertisement_delay_ms,'ctrl_advertise',(observer,nxt,lid,key,up,version,origin))

    def advertise(self,data):
        src,dst,lid,key,up,version,origin=data
        self.enqueue(lid,src,dst,'ROUTE_UPDATE',dict(key=key,up=up,version=version,origin=origin))

    def enqueue(self,lid,src,dst,operation,payload):
        e=self.e;link=e.links[lid];pid=-1-list(e.links).index(lid)*2-int(src!=link.source)
        if pid not in e.procedures:
            flow=e.p.flows[0].model_copy(update=dict(id=f'control:{lid}:{src}',source=src,target=dst,priority=0,message_size_cv=0))
            e.procedures[pid]=dict(id=pid,flow=flow,subscriber=0,objects=set(),affected=set(),transport_retries=0,retries=0,control=True,last_cause=None)
        self.sent+=1;self.counts[operation]+=1;e.message_seq+=1
        e.telemetry.count('control_messages')
        msg=dict(id=e.message_seq,procedure=pid,step=0,source=src,target=dst,role='control',operation=operation,size=link.control_bytes,result='success',path=[(dst,lid)],hop=0,start=e.t,valid=True,protocol=link.protocol,rpc_id=None,category='control',control=dict(lid=lid,**payload))
        e.messages[msg['id']]=msg;e.arrive_node(msg,src)

    def change(self,lid,state):
        entry=self.states[lid]
        if state=='CONNECTING' and entry['epoch']>1:self.e.telemetry.count('control_reconnects')
        entry['state']=state
        self.e.lifecycle.append(dict(time=self.e.t/1000,object=lid,state='CONTROL_'+state))
        if self.e.p.routing_knowledge=='distributed' and state in ('OPEN','DOWN','CLOSING','CLOSED','REJECTED'):
            self.revision+=1;link=self.e.links[lid]
            for observer in (link.source,link.target):self.record_view(observer,('association',lid,'both'),state=='OPEN',self.revision,link.source)

    def start(self,lid):
        if not self.enabled(lid):return
        state=self.states[lid]
        if state.get('administrative_closed') or state['state']=='REJECTED':return
        if state['state']=='OPEN' or state['pending'] is not None:return
        state['epoch']+=1;state['phase']=0;self.change(lid,'CONNECTING');self.request(lid,*PHASES[self.e.links[lid].protocol][0])

    def node_restart(self,node):
        e=self.e
        end=max([(incident.start+incident.duration)*1000 for incident in e.p.incidents if incident.target==node and incident.kind=='node_restart' and incident.start*1000<=e.t<(incident.start+incident.duration)*1000]+[e.t])
        for lid,state in self.states.items():
            link=e.links[lid]
            if node not in (link.source,link.target):continue
            if state['pending'] is not None:self.requests[state['pending']]['done']=True
            state['pending']=None;state['epoch']+=1;self.change(lid,'DOWN')
            if end+link.reconnect_ms<=e.p.duration*1000:e.push(end+link.reconnect_ms,'ctrl_start',lid)

    def request(self,lid,operation,answer):
        e=self.e;state=self.states[lid];self.seq+=1
        request=dict(id=self.seq,lid=lid,operation=operation,answer=answer,epoch=state['epoch'],attempt=0,done=False,token=0)
        self.requests[self.seq]=request;state['pending']=self.seq;self.transmit(request)

    def transmit(self,request):
        e=self.e;link=e.links[request['lid']];request['token']+=1
        self.enqueue(link.id,link.source,link.target,request['operation'],dict(request=request['id'],epoch=request['epoch'],answer=False))
        e.push(e.t+link.control_timeout_ms,'ctrl_timeout',(request['id'],request['token']),0)

    def arrival(self,msg):
        e=self.e;self.received+=1;c=msg['control'];lid=c['lid'];self.e.reservoir(self.traces,dict(time=msg['start']/1000,end=e.t/1000,source=msg['source'],target=msg['target'],link=lid,operation=msg['operation'],protocol=msg['protocol'],bytes=msg['size']),self.received)
        if msg['operation']=='ROUTE_UPDATE':
            self.record_view(msg['target'],tuple(c['key']),c['up'],c['version'],c['origin'],msg['source']);return
        request=self.requests.get(c.get('request'))
        if not request or request['done'] or request['epoch']!=self.states[lid]['epoch']:return
        if not c['answer']:
            error=None;link=e.links[lid]
            if request['operation']=='CER':
                a=set(e.nodes[link.source].applications);b=set(e.nodes[link.target].applications)
                if not a&b and 4294967295 not in a|b:error='no_common_application'
            elif request['operation']=='ASPAC' and link.peer_routing_context is not None and link.peer_routing_context!=link.routing_context:error='routing_context_mismatch'
            self.enqueue(lid,msg['target'],msg['source'],request['answer'],dict(request=request['id'],epoch=request['epoch'],answer=True,error=error));return
        request['done']=True;state=self.states[lid];state['pending']=None
        if c.get('error'):
            self.counts[c['error']]+=1;state['rejection_reason']=c['error'];self.change(lid,'REJECTED');return
        if request['operation']=='ASPIA':self.request(lid,'ASPDN','ASPDN_ACK');return
        if request['operation'] in ('DPR','ASPDN'):self.change(lid,'CLOSED');return
        if request['operation'] in {pair[0] for pair in PHASES[e.links[lid].protocol]}:
            state['phase']+=1;phases=PHASES[e.links[lid].protocol]
            if state['phase']<len(phases):self.change(lid,'ASP_UP');self.request(lid,*phases[state['phase']]);return
            self.change(lid,'OPEN')
        if e.t+e.links[lid].heartbeat_ms<=e.p.duration*1000:e.push(e.t+e.links[lid].heartbeat_ms,'ctrl_heartbeat',(lid,state['epoch']))

    def timeout(self,ident,token):
        request=self.requests[ident];state=self.states[request['lid']];e=self.e;link=e.links[request['lid']]
        if request['done'] or token!=request['token'] or request['epoch']!=state['epoch']:return
        if request['attempt']<link.control_retries:request['attempt']+=1;self.transmit(request);return
        request['done']=True;state['pending']=None;self.counts['timeouts']+=1;self.change(link.id,'DOWN')
        if state.get('administrative_closed'):self.change(link.id,'CLOSED');return
        if e.t+link.reconnect_ms<=e.p.duration*1000:e.push(e.t+link.reconnect_ms,'ctrl_start',link.id)

    def event(self,kind,data):
        e=self.e
        if kind=='ctrl_start':self.start(data)
        elif kind=='ctrl_close':
            state=self.states[data];state['administrative_closed']=True;state['epoch']+=1
            if state['pending'] is not None:self.requests[state['pending']]['done']=True
            state['pending']=None;self.change(data,'CLOSING')
            self.request(data,* (('DPR','DPA') if e.links[data].protocol=='Diameter' else ('ASPIA','ASPIA_ACK')))
        elif kind=='ctrl_timeout':self.timeout(*data)
        elif kind=='ctrl_heartbeat':
            lid,epoch=data;state=self.states[lid]
            if state['state']=='OPEN' and state['epoch']==epoch and state['pending'] is None:self.request(lid,*HEARTBEATS[e.links[lid].protocol])
        elif kind=='ctrl_advertise':self.advertise(data)
        elif kind=='ctrl_sync':
            for observer,entries in self.views.items():
                if not e.node_up(observer,e.t):continue
                changed=sorted([(key,value) for key,value in entries.items() if value['version']>=0],key=lambda item:item[0])
                if not changed:continue
                offset=(data*16)%len(changed)
                for key,value in (changed+changed)[offset:offset+min(16,len(changed))]:
                    for nxt,lid in sorted(e.adj[observer]):
                        if e.links[lid].protocol not in ('GTPU','IP'):self.advertise((observer,nxt,lid,key,value['up'],value['version'],value['origin']))
            if e.t+e.p.route_refresh_ms<=e.p.duration*1000:e.push(e.t+e.p.route_refresh_ms,'ctrl_sync',data+1)
        elif kind=='ctrl_view':
            observer,resource,target,direction,up,version=data
            directions=('forward','reverse') if resource=='link' and direction=='both' else (direction,)
            for direction in directions:
                if up and not self.can_restore(observer,resource,target,direction):continue
                self.record_view(observer,(resource,target,direction),up,version,observer)

    def report(self):
        return dict(enabled=self.e.p.control_plane,mode=self.e.p.routing_knowledge,sent=self.sent,received=self.received,counts=dict(self.counts),messages=self.traces,associations={lid:state['state'] for lid,state in self.states.items()},view_events=self.view_events,views=[dict(observer=observer,resource=key[0],target=key[1],direction=key[2],**value) for observer,entries in self.views.items() for key,value in entries.items()])
