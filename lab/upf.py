"""Behavioural ordered packet rules and a finite token-bucket shaper."""
from collections import deque,Counter


class UpfPolicy:
    def __init__(self,user_plane):self.u=user_plane;self.e=user_plane.e;self.buckets={};self.stats=Counter()
    def submit(self,packet,direction,dst,protocol,leg):
        e=self.e;n=e.nodes[packet['upf']];f=e.procedures[packet['procedure']]['flow'];selected=None;index=-1
        for i,r in sorted(enumerate(n.upf_rules),key=lambda pair:(pair[1].precedence,pair[0])):
            if (not r.flow_id or r.flow_id==f.id) and (not r.dnn or r.dnn==f.dnn) and (not r.snssai or r.snssai.lower()==f.snssai.lower()) and r.direction in ('both',direction):selected=r;index=i;break
        self.stats['matched' if selected else 'default']+=1
        packet.setdefault('rules',[]).append(dict(upf=n.id,rule=selected.name if selected else 'default',direction=direction))
        if (selected.action if selected else n.upf_default_action)=='drop':self.stats['rule_drops']+=1;self.u.finish(packet,'upf_rule_drop');return
        if selected and packet['bytes']>selected.max_packet_bytes:self.stats['size_drops']+=1;self.u.finish(packet,'upf_packet_size');return
        rate=selected.rate_mbps if selected else n.upf_rate_mbps;burst=selected.burst_bytes if selected else n.upf_burst_bytes
        if not rate:self.u.send_leg(packet,n.id,dst,protocol,leg);return
        if packet['bytes']>burst:self.stats['burst_drops']+=1;self.u.finish(packet,'qos_burst_exceeded');return
        key=(n.id,index,direction);state=self.buckets.setdefault(key,dict(tokens=float(burst),updated=e.t,queue=deque(),wake=None,rate=rate*1000/8,burst=burst))
        self.pump(key)
        task=(packet['id'],dst,protocol,leg,e.t)
        if not state['queue'] and state['tokens']>=packet['bytes']:
            state['tokens']-=packet['bytes'];self.u.send_leg(packet,n.id,dst,protocol,leg);return
        if len(state['queue'])>=n.upf_queue_limit:self.stats['queue_drops']+=1;self.u.finish(packet,'qos_queue_overflow');return
        state['queue'].append(task);self.stats['queued']+=1;self.stats['max_queue']=max(self.stats['max_queue'],len(state['queue']));self.pump(key)
    def pump(self,key):
        state=self.buckets[key];e=self.e;state['tokens']=min(state['burst'],state['tokens']+max(0,e.t-state['updated'])*state['rate']);state['updated']=e.t
        state['queue']=deque(t for t in state['queue'] if not self.u.packets[t[0]]['closed'])
        while state['queue']:
            task=state['queue'][0];packet=self.u.packets[task[0]]
            if state['tokens']+1e-9<packet['bytes']:
                wake=min(packet['deadline'],e.t+(packet['bytes']-state['tokens'])/state['rate'])
                if state['wake'] is None or wake<state['wake']-1e-9:state['wake']=wake;e.push(wake,'user_qos_wake',(key,wake))
                return
            state['queue'].popleft();state['tokens']=max(0,state['tokens']-packet['bytes']);self.stats['wait_ms']+=e.t-task[4]
            if not self.u.context_valid(packet):self.u.finish(packet,'user_plane_context_lost')
            else:self.u.send_leg(packet,key[0],task[1],task[2],task[3])
        state['wake']=None
    def wake(self,data):
        key,token=data
        if key in self.buckets and self.buckets[key]['wake']==token:self.buckets[key]['wake']=None;self.pump(key)
    def node_fault(self,n):
        for key in list(self.buckets):
            if key[0]!=n:continue
            for task in list(self.buckets.pop(key)['queue']):self.u.finish(self.u.packets[task[0]],'upf_unavailable')
