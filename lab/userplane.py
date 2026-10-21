"""Offline bidirectional N3/N6 probe traffic after a successful PDU procedure."""
from collections import Counter
from .procedures import percentile
import numpy as np


class UserPlane:
    def __init__(self,engine):
        self.e=engine;self.sessions={};self.packets={};self.seq=0;self.traces=[];self.completed=0
        self.windows=[dict(sent=0,delivered=0,rtt=[]) for _ in engine.buckets]
        from .upf import UpfPolicy
        self.policy=UpfPolicy(self);self.released=0

    def start(self,pr):
        f=pr['flow']
        if not f.user_plane_enabled:return
        upf=pr['nf_selected'].get('UPF') or self.e.resolve(pr,'@UPF')
        dn=f.data_network or next((n.id for n in self.e.nodes.values() if n.kind=='DN' and f.dnn in n.dnns),'')
        self.sessions[pr['id']]=dict(upf=upf,dn=dn,smf=pr['nf_selected'].get('SMF'),pdu_session_id=pr['pdu_session_id'])
        rng=np.random.default_rng(self.e.p.seed^pr['id']^0xDA7A);offset=0.
        for index in range(f.data_packets):
            if index:
                interval=f.data_interval_ms/10 if f.application_profile=='bulk' else rng.exponential(f.data_interval_ms) if f.application_profile=='interactive' else f.data_interval_ms
                offset+=interval
                if f.application_profile=='video':
                    cycle=(f.video_on_s+f.video_off_s)*1000;phase=offset%cycle
                    if phase>=f.video_on_s*1000:offset+=cycle-phase
            at=self.e.t+offset
            if at<self.e.p.duration*1000:self.e.push(at,'user_send',pr['id'])

    def finish(self,packet,status):
        if packet['closed']:return
        e=self.e;packet['closed']=True;packet['status']=status;elapsed=e.t-packet['start'];packet['rtt_ms']=elapsed
        ended=e.journal.emit(packet['procedure'],'timer_expired' if status=='user_plane_timeout' else 'timer_cancelled',cause=packet.get('_journal_timer'),timer_id=f'userplane:{packet["id"]}',timer_type='userplane',reason=status,user_packet_id=packet['id'])
        e.journal.emit(packet['procedure'],'user_data_finished',cause=ended,user_packet_id=packet['id'],status=status,rtt_ms=elapsed)
        if status=='delivered':self.windows[e.window(packet['start'])]['delivered']+=1;self.windows[e.window(packet['start'])]['rtt'].append(elapsed)
        budget=e.procedures[packet['procedure']]['flow'].qos_budget_ms;packet['qos_met']=status=='delivered' and (not budget or elapsed<=budget)
        e.telemetry.data_finish(packet)
        self.completed+=1;e.reservoir(self.traces,dict(time=packet['start']/1000,end=e.t/1000,packet_id=packet['id'],procedure_id=packet['procedure'],pdu_session_id=packet['pdu_session_id'],status=status,rtt_ms=elapsed,bytes=packet['bytes'],upf=packet['upf'],dn=packet['dn'],qos_met=packet['qos_met'],rules=packet.get('rules',[])),self.completed)

    def send_leg(self,packet,src,dst,protocol,leg):
        if packet['closed']:return
        pr=self.e.procedures[packet['procedure']]
        if not src or not dst or dst not in self.e.nodes:self.finish(packet,'data_network_unavailable');return
        msg=self.e.send_message(pr,src,dst,'data',leg,packet['bytes'],protocol=protocol,category='userplane',context=packet['id'])
        if msg is None:self.finish(packet,'no_user_plane_route')

    def context_valid(self,packet):
        e=self.e;pr=e.procedures[packet['procedure']];f=pr['flow']
        upf=e.session_lifecycle.context(pr['id'],packet['upf']);amf=e.session_lifecycle.context(pr['id'],f.target)
        record=e.session_lifecycle.records[pr['id']]
        return bool(record['state']=='ACTIVE' and not e.mobility.busy(f.id) and not self.sessions[pr['id']].get('released') and upf.get('n4_session') and amf.get('pdu_active') and all(ctx.get('owner')==pr['id'] for ctx in (upf,amf)))

    def release(self,ident):
        self.e.session_lifecycle.start_cleanup(ident,'explicit_release')

    def stop(self,ident,completed=False):
        session=self.sessions.get(ident)
        if session is None:return
        session['released']=True
        if completed and not session.get('release_confirmed'):
            session['release_confirmed']=True;self.released+=1
        for packet in list(self.packets.values()):
            if packet['procedure']==ident and not packet['closed']:self.finish(packet,'session_released')

    def event(self,kind,data):
        e=self.e
        if kind=='user_qos_wake':self.policy.wake(data);return
        if kind=='user_release':self.release(data);return
        if kind=='user_timeout':
            if not self.packets[data]['closed']:self.finish(self.packets[data],'user_plane_timeout')
            return
        pr=e.procedures[data];f=pr['flow'];session=self.sessions[data];self.seq+=1
        if session.get('released'):return
        packet=dict(id=self.seq,procedure=data,start=e.t,deadline=e.t+f.data_timeout_ms,bytes=f.data_bytes,closed=False,mobility_epoch=e.mobility.epochs.get(f.id,0),**session)
        self.packets[self.seq]=packet;self.windows[e.window(e.t)]['sent']+=1
        e.telemetry.count('data_sent');e.telemetry.gauge('data_inflight',1)
        packet['_journal_timer']=e.journal.emit(pr['id'],'timer_set',cause=pr.get('_journal_event'),timer_id=f'userplane:{self.seq}',timer_type='userplane',deadline_ms=packet['deadline'],user_packet_id=self.seq,node=f.source)
        e.push(e.t+f.data_timeout_ms,'user_timeout',self.seq,0)
        if not self.context_valid(packet):self.finish(packet,'user_plane_context_lost');return
        self.send_leg(packet,f.source,session['upf'],'GTPU','N3 uplink')

    def arrival(self,msg):
        packet=self.packets[msg['context']];e=self.e;pr=e.procedures[packet['procedure']]
        if packet['closed']:return
        if packet['mobility_epoch']!=e.mobility.epochs.get(pr['flow'].id,0):self.finish(packet,'mobility_interruption');return
        if msg['target']==packet['upf'] and not self.context_valid(packet):self.finish(packet,'user_plane_context_lost');return
        if msg['operation']=='N3 uplink':self.policy.submit(packet,'uplink',packet['dn'],'IP','N6 uplink')
        elif msg['operation']=='N6 uplink':self.send_leg(packet,packet['dn'],packet['upf'],'IP','N6 downlink')
        elif msg['operation']=='N6 downlink':self.policy.submit(packet,'downlink',pr['flow'].source,'GTPU','N3 downlink')
        else:self.finish(packet,'delivered')

    def report(self,result):
        packets=list(self.packets.values());delivered=[p for p in packets if p.get('status')=='delivered']
        for row,window in zip(result['windows'],self.windows):row.update(user_sent=window['sent'],user_delivered=window['delivered'],user_loss_ratio=(window['sent']-window['delivered'])/max(1,window['sent']),user_rtt_p95=percentile(window['rtt'],95))
        result['mobility']=self.e.mobility.traces
        result['user_plane']=dict(sessions=len(self.sessions),released_sessions=self.released,sent=len(packets),delivered=len(delivered),delivery_ratio=len(delivered)/max(1,len(packets)),qos_met=sum(p.get('qos_met',False) for p in packets),upf_policy=dict(self.policy.stats),rtt_p95=percentile([p['rtt_ms'] for p in delivered],95),payload_bytes=sum(p['bytes'] for p in delivered),goodput_mbps=sum(p['bytes']*8 for p in delivered)/(self.e.p.duration*1_000_000),failure_reasons=dict(Counter(p['status'] for p in packets if p['status']!='delivered')),traces=self.traces)
