"""Unsampled, observation-clock counters and time-weighted resource gauges."""
from collections import Counter
import numpy as np


class Telemetry:
    def __init__(self,engine):
        self.e=engine;self.last=0.;self.gauges=Counter();self.rows=[Counter() for _ in engine.buckets]
        self.latencies=[[] for _ in engine.buckets];self.rtts=[[] for _ in engine.buckets]
        self.db_nodes={};self.neighbors={}
        for observer in engine.nodes:
            keys={('node',n,'both') for n,_ in engine.adj[observer]}
            for _,lid in engine.adj[observer]:
                keys.update([('link',lid,'forward'),('link',lid,'reverse'),('association',lid,'both')])
            for key in keys:self.neighbors[(observer,key)]=engine.control.known(observer,*key)
        self.gauges['neighbor_down']=sum(not up for up in self.neighbors.values())

    def advance(self,time):
        end=min(time,self.e.p.duration*1000);width=self.e.p.window*1000
        while self.last<end:
            index=min(len(self.rows)-1,int(self.last/width));stop=min(end,(index+1)*width)
            for key,value in self.gauges.items():self.rows[index][key+'_area']+=value*(stop-self.last)
            self.last=stop

    def count(self,key,value=1):
        if 0<=self.e.t<self.e.p.duration*1000:self.rows[self.e.window(self.e.t)][key]+=value

    def gauge(self,key,delta):self.gauges[key]+=delta

    def db_state(self,node):
        state=(sum(not self.e.storage.tasks[i]['closed'] for i in self.e.storage.queues[node]),len(self.e.storage.running[node]))
        old=self.db_nodes.get(node,(0,0));self.db_nodes[node]=state
        self.gauge('db_queue',state[0]-old[0]);self.gauge('db_active',state[1]-old[1])

    def view(self,observer,key,up):
        index=(observer,key)
        if index in self.neighbors:
            self.gauge('neighbor_down',int(not up)-int(not self.neighbors[index]));self.neighbors[index]=up

    def signal_finish(self,status,elapsed):
        self.count('signal_completed');self.count('signal_failed',status!='delivered');self.count('signal_timeouts',status=='timeout')
        if status=='delivered' and self.e.t<self.e.p.duration*1000:self.latencies[self.e.window(self.e.t)].append(elapsed)

    def data_finish(self,packet):
        self.gauge('data_inflight',-1);success=packet['status']=='delivered'
        self.count('data_completed');self.count('data_failed',not success);self.count('data_qos_violations',not packet['qos_met'])
        if success:
            self.count('data_bytes',packet['bytes'])
            if self.e.t<self.e.p.duration*1000:self.rtts[self.e.window(self.e.t)].append(packet['rtt_ms'])

    def report(self,result):
        self.advance(self.e.p.duration*1000)
        capacities=Counter()
        for name,store in self.e.storage.stores.items():
            kind='cache' if store.evict else 'context'
            capacities[kind]+=sum(store.limit(n) for n in self.e.nodes.values())
        db_capacity=sum(n.db_connections for n in self.e.nodes.values() if n.enabled)
        for i,(row,c) in enumerate(zip(result['windows'],self.rows)):
            width=min(self.e.p.window,self.e.p.duration-row['time']);ms=width*1000
            completed=max(1,c['signal_completed']);data=max(1,c['data_completed'])
            row.update(telemetry_contract='events-v1',window_seconds=width,
                signal_arrivals_s=c['signal_arrivals']/width,signal_completed_s=c['signal_completed']/width,
                signal_failure_ratio=c['signal_failed']/completed,signal_timeout_ratio=c['signal_timeouts']/completed,
                signal_latency_p95=float(np.percentile(self.latencies[i],95)) if self.latencies[i] else 0.,
                signal_retries_s=c['signal_retries']/width,transport_retries_s=c['transport_retries']/width,
                control_messages_s=c['control_messages']/width,control_reconnects_s=c['control_reconnects']/width,
                neighbor_unavailable_ratio=c['neighbor_down_area']/ms/max(1,len(self.neighbors)),
                data_sent_s=c['data_sent']/width,data_completed_s=c['data_completed']/width,
                data_loss_ratio=c['data_failed']/data,data_rtt_p95=float(np.percentile(self.rtts[i],95)) if self.rtts[i] else 0.,
                data_goodput_mbps=c['data_bytes']*8/width/1e6,data_qos_violation_ratio=c['data_qos_violations']/data,
                data_inflight_mean=c['data_inflight_area']/ms,
                db_requests_s=c['db_requests']/width,db_wait_mean_ms=c['db_wait_ms']/max(1,c['db_wait_samples']),
                db_overflow_s=c['db_overflow']/width,db_timeouts_s=c['db_timeouts']/width,
                db_queue_mean=c['db_queue_area']/ms,db_active_ratio=c['db_active_area']/ms/max(1,db_capacity),
                cache_hit_ratio=c['cache_hits']/max(1,c['db_requests']),cache_occupancy_ratio=c['cache_area']/ms/max(1,capacities['cache']),
                cache_expired_s=c['cache_expired']/width,cache_evicted_s=c['cache_evicted']/width,
                context_occupancy_ratio=c['context_area']/ms/max(1,capacities['context']),
                context_expired_s=c['context_expired']/width,context_rejected_s=c['context_rejected']/width)
        result['telemetry']=dict(contract='events-v1',clock='Actual event time; windows [start,end); tail excluded; gauges integrated in time.',
            capacities=dict(capacities),neighbor_observations=len(self.neighbors),window_count=len(self.rows))
