"""Offline bidirectional N3/N6 probe traffic after a successful PDU procedure."""
from collections import Counter
from .procedures import percentile


class UserPlane:
    def __init__(self,engine):
        self.e=engine;self.sessions={};self.packets={};self.seq=0;self.traces=[];self.completed=0
        self.windows=[dict(sent=0,delivered=0,rtt=[]) for _ in engine.buckets]

    def start(self,pr):
        f=pr['flow']
        if not f.user_plane_enabled:return
        upf=pr['nf_selected'].get('UPF') or self.e.resolve(pr,'@UPF')
        dn=f.data_network or next((n.id for n in self.e.nodes.values() if n.kind=='DN' and f.dnn in n.dnns),'')
        self.sessions[pr['id']]=dict(upf=upf,dn=dn,smf=pr['nf_selected'].get('SMF'))
        for index in range(f.data_packets):
            at=self.e.t+index*f.data_interval_ms
            if at<self.e.p.duration*1000:self.e.push(at,'user_send',pr['id'])

    def finish(self,packet,status):
        if packet['closed']:return
        e=self.e;packet['closed']=True;packet['status']=status;elapsed=e.t-packet['start'];packet['rtt_ms']=elapsed
        if status=='delivered':self.windows[e.window(packet['start'])]['delivered']+=1;self.windows[e.window(packet['start'])]['rtt'].append(elapsed)
        self.completed+=1;e.reservoir(self.traces,dict(time=packet['start']/1000,end=e.t/1000,packet_id=packet['id'],procedure_id=packet['procedure'],status=status,rtt_ms=elapsed,bytes=packet['bytes'],upf=packet['upf'],dn=packet['dn']),self.completed)

    def send_leg(self,packet,src,dst,protocol,leg):
        if packet['closed']:return
        pr=self.e.procedures[packet['procedure']]
        if not src or not dst or dst not in self.e.nodes:self.finish(packet,'data_network_unavailable');return
        msg=self.e.send_message(pr,src,dst,'data',leg,packet['bytes'],protocol=protocol,category='userplane',context=packet['id'])
        if msg is None:self.finish(packet,'no_user_plane_route')

    def context_valid(self,packet):
        e=self.e;pr=e.procedures[packet['procedure']];f=pr['flow']
        return bool(packet['upf'] and e.contexts.get(e.context_key(pr,packet['upf']),{}).get('n4_session') and
                    e.contexts.get(e.context_key(pr,f.target),{}).get('pdu_active'))

    def event(self,kind,data):
        e=self.e
        if kind=='user_timeout':
            if not self.packets[data]['closed']:self.finish(self.packets[data],'user_plane_timeout')
            return
        pr=e.procedures[data];f=pr['flow'];session=self.sessions[data];self.seq+=1
        packet=dict(id=self.seq,procedure=data,start=e.t,bytes=f.data_bytes,closed=False,**session)
        self.packets[self.seq]=packet;self.windows[e.window(e.t)]['sent']+=1
        e.push(e.t+f.data_timeout_ms,'user_timeout',self.seq,0)
        if not self.context_valid(packet):self.finish(packet,'user_plane_context_lost');return
        self.send_leg(packet,f.source,session['upf'],'GTPU','N3 uplink')

    def arrival(self,msg):
        packet=self.packets[msg['context']];e=self.e;pr=e.procedures[packet['procedure']]
        if packet['closed']:return
        if msg['target']==packet['upf'] and not self.context_valid(packet):self.finish(packet,'user_plane_context_lost');return
        if msg['operation']=='N3 uplink':self.send_leg(packet,packet['upf'],packet['dn'],'IP','N6 uplink')
        elif msg['operation']=='N6 uplink':self.send_leg(packet,packet['dn'],packet['upf'],'IP','N6 downlink')
        elif msg['operation']=='N6 downlink':self.send_leg(packet,packet['upf'],pr['flow'].source,'GTPU','N3 downlink')
        else:self.finish(packet,'delivered')

    def report(self,result):
        packets=list(self.packets.values());delivered=[p for p in packets if p.get('status')=='delivered']
        for row,window in zip(result['windows'],self.windows):row.update(user_sent=window['sent'],user_delivered=window['delivered'],user_loss_ratio=(window['sent']-window['delivered'])/max(1,window['sent']),user_rtt_p95=percentile(window['rtt'],95))
        result['user_plane']=dict(sessions=len(self.sessions),sent=len(packets),delivered=len(delivered),delivery_ratio=len(delivered)/max(1,len(packets)),rtt_p95=percentile([p['rtt_ms'] for p in delivered],95),payload_bytes=sum(p['bytes'] for p in delivered),goodput_mbps=sum(p['bytes']*8 for p in delivered)/(self.e.p.duration*1_000_000),failure_reasons=dict(Counter(p['status'] for p in packets if p['status']!='delivered')),traces=self.traces)
