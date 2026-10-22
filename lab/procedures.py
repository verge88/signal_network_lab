"""Offline behavioural procedure model. No packet codecs or network connections.

All clocks use milliseconds. Timer events have priority over arrivals at the
same timestamp: a response must arrive strictly before its deadline.
"""
import hashlib
import heapq
import json
import math
import platform
from collections import Counter, defaultdict, deque
from concurrent.futures import CancelledError
import numpy as np

ENGINE_VERSION = '2.11.0'
CONTRACT = 'procedures-v2'
ANSWERS = {'MAP UpdateLocation': 'MAP UpdateLocationResult',
           'MAP SendAuthenticationInfo': 'MAP AuthenticationInfoResult',
           'Diameter ULR': 'Diameter ULA', 'Diameter AIR': 'Diameter AIA',
           'Diameter CCR': 'Diameter CCA', 'ISUP IAM': 'ISUP ANM'}


def percentile(values, q):
    return float(np.percentile(values, q)) if values else 0.


class ProcedureSimulation:
    def __init__(self, project, progress=None, cancel=None):
        from .attacks import compile_attacks
        self.original_project=project
        effects,origins=compile_attacks(project)
        self.attack_origins={len(project.incidents)+i:origin for i,origin in enumerate(origins)}
        project=project.model_copy(update={'incidents':[*project.incidents,*effects]})
        self.p = project
        self.progress, self.cancel = progress, cancel
        self.rng = np.random.default_rng(project.seed)
        # Sampling must not change simulated physics when the trace limit changes.
        self.sample_rng = np.random.default_rng(project.seed ^ 0x51A9)
        self.metric_rng = np.random.default_rng(project.seed ^ 0xC4A1)
        self.direction_latencies = defaultdict(list)
        self.direction_delivered = Counter()
        self.nodes = {n.id: n for n in project.nodes}
        self.links = {l.id: l for l in project.links}
        self.adj = defaultdict(list)
        for l in project.links:
            self.adj[l.source].append((l.target, l.id))
            self.adj[l.target].append((l.source, l.id))
        self.events, self.serial, self.t = [], 0, 0.
        self.procedures, self.messages, self.transfers = {}, {}, {}
        self.message_seq, self.transfer_seq = 0, 0
        self.message_finished = 0
        self.node_queue = defaultdict(list)
        self.running = defaultdict(dict)
        self.node_epoch = Counter()
        self.next_admission = defaultdict(float)
        self.last_admission = {}
        self.wake = {}
        from .resources import StorageResources
        self.storage=StorageResources(self)
        self.cache=self.storage.store('transactions',lambda n:n.transaction_cache_limit,lambda n:n.transaction_cache_ttl_s,node_key=lambda k:k[1] if len(k)==4 and k[0]=='5g' else k[0],evict=True)
        self.subscribers=self.storage.store('subscriber_contexts',lambda n:n.context_limit,lambda n:n.context_ttl_s)
        self.wire, self.wire_queue = defaultdict(float), defaultdict(deque)
        self.loss_last = defaultdict(bool)
        self.associations = {}
        self.previous_routes = {}
        self.traces, self.message_traces = [], []
        self.finished = self.total = self.steps = 0
        self.max_outstanding = self.outstanding = 0
        self.reasons = Counter()
        self.in_observation = 0
        self.node_stats = {n: dict(processed=0, dropped=0, max_queue=0, busy_ms=0., errors=0, duplicates=0, latency=[]) for n in self.nodes}
        self.link_stats = {l: dict(messages=0, dropped=0, bytes=0, packets=0, retransmissions=0, acks=0, duplicates=0, buffer_drops=0, directions={}) for l in self.links}
        self.count = math.ceil(project.duration / project.window)
        self.buckets = [dict(sent=0, delivered=0, lost=0, timeouts=0, latency=[], queue=[], changes=0, retries=0, transport_retries=0, affected=set(), objects=set(), completed=0, outstanding=0) for _ in range(self.count)]
        self.busy_windows = [defaultdict(float) for _ in range(self.count)]
        self.lifecycle = []
        self.incident_stats = [dict(index=i, kind=e.kind, target=e.target, direction=e.direction, start=e.start, end=e.start+e.duration, affected_procedures=0, first_success_after_end=None) for i, e in enumerate(project.incidents)]
        from .control import ControlPlane
        self.control=ControlPlane(self)
        from .procedure_journal import ProcedureJournal
        self.journal=ProcedureJournal(self)
        from .telemetry import Telemetry
        self.telemetry=Telemetry(self)
        from .clusters import StateClusters
        self.clusters=StateClusters(self)
        self.prepared=False
        from .monitoring import MonitoringOverlay
        self.monitoring=MonitoringOverlay(self)

    def check(self, value=None):
        if self.cancel and self.cancel():
            raise CancelledError('Вычисление отменено')
        if self.progress and value is not None:
            self.progress(value)

    def push(self, t, kind, payload=None, priority=1):
        self.serial += 1
        heapq.heappush(self.events, (t, priority, self.serial, kind, payload))

    def window(self, t):
        return min(self.count-1, max(0, int(t / (1000*self.p.window))))

    def incident_matches(self, e, kind, target, t, direction='both', known=False, link=None):
        if e.kind not in kind or e.target != target or (e.direction != 'both' and e.direction != direction):
            return False
        lo, hi = e.start*1000, (e.start+e.duration)*1000
        if known:
            resource=link or self.nodes[target]
            detection = e.detection_ms if e.detection_ms is not None else resource.detection_ms
            recovery = e.recovery_ms if e.recovery_ms is not None else resource.recovery_ms
            # Outages shorter than detection do not publish a DOWN route state.
            if hi <= lo+detection:
                return False
            lo += detection + resource.switch_ms
            hi += recovery
        return lo <= t < hi

    def node_up(self, n, t, known=False,observer=None):
        if known and self.p.routing_knowledge=='distributed':return self.nodes[n].enabled and self.control.known(observer or self.p.flows[0].source,'node',n)
        return self.nodes[n].enabled and not any(self.incident_matches(e, ('node_down', 'node_restart'), n, t, known=known) for e in self.p.incidents)

    def effect_value(self,kind,target,t,baseline=1.,minimum=False):
        values=[baseline]+[e.magnitude for e in self.p.incidents if self.incident_matches(e,(kind,),target,t)]
        return min(values) if minimum else max(values)

    def application_error(self,target):
        return self.effect_value('node_error',target,self.t,self.nodes[target].error_rate)

    def attack_boundary(self,n):
        if n in self.last_admission:self.next_admission[n]=self.last_admission[n]+1000/self.admission_capacity(n)
        self.dispatch(n)

    def admission_capacity(self,n):
        node=self.nodes[n];ramp=1.
        for e in self.p.incidents:
            if e.target==n and e.kind in ('node_down','node_restart') and node.recovery_ramp_ms:
                start=(e.start+e.duration)*1000+(e.recovery_ms if e.recovery_ms is not None else node.recovery_ms)
                if start<=self.t<start+node.recovery_ramp_ms:ramp=min(ramp,max(.1,(self.t-start)/node.recovery_ramp_ms))
        return node.capacity*ramp*self.effect_value('node_capacity',n,self.t,minimum=True)

    def link_up(self, lid, src, t, known=False,observer=None,management=False):
        l = self.links[lid]
        direction = 'forward' if src == l.source else 'reverse'
        if not l.enabled or (l.protocol == 'M3UA' and l.asp_state != 'ACTIVE') or (l.protocol in ('Diameter','NGAP','SBI','PFCP') and l.peer_state != 'OPEN'):
            return False
        if t < l.startup_ms:
            return False
        if known and self.p.routing_knowledge=='distributed':return self.control.known(observer or src,'link',lid,direction) and self.control.known(observer or src,'association',lid)
        if self.p.control_plane and not management and lid in self.control.states and self.control.states[lid]['state']!='OPEN':return False
        return not any(self.incident_matches(e, ('link_down',), lid, t, direction, known, l) for e in self.p.incidents)

    def touch(self, pr, target, direction='both',resource=None):
        from .attacks import NODE_EFFECTS
        resource=resource or ('node' if direction=='both' else 'link')
        pr['objects'].add(target)
        for i, e in enumerate(self.p.incidents):
            if resource!=('node' if e.kind in NODE_EFFECTS else 'flow' if e.kind=='surge' else 'link'):continue
            if e.target == target and (e.direction == 'both' or e.direction == direction) and e.start*1000 <= self.t < (e.start+e.duration)*1000:
                pr['affected'].add(i)
                if self.t<self.p.duration*1000 and not pr.get('control'):
                    b = self.buckets[self.window(self.t)]
                    b['affected'].add(pr['id']); b['objects'].add(target)

    def destination(self, f, node=None):
        if f.protocol == 'Diameter':
            # Explicit host/realm selects the application endpoint, not a relay.
            wanted_realm = f.destination_realm or self.nodes[f.target].realm
            candidates = [n.id for n in self.nodes.values() if n.kind in ('HSS', 'SCP') and n.realm == wanted_realm and f.application_id in n.applications and (not f.destination_host or (n.host or n.id) == f.destination_host)]
            if f.destination_host or f.destination_realm:
                return f.target if f.target in candidates else min(candidates, default=None)
            return f.target if f.application_id in self.nodes[f.target].applications else None
        pc = f.destination_pc
        if f.global_title:
            # Translation is deferred until this node has processed the request.
            if node is None or not self.node_up(node,self.t):return None
            translations = [(len(prefix), value) for prefix, value in self.nodes[node].gtt.items() if f.global_title.startswith(prefix)]
            if not translations: return None
            pc = max(translations)[1]
        if pc:
            return next((n.id for n in self.nodes.values() if n.point_code == pc), None)
        return f.target

    def gt_next_hop(self, msg, n, ignore_faults=False):
        f=self.procedures[msg['procedure']]['flow'];visited={msg['source'],*(node for node,_ in msg['path'])}
        rules=[(len(key[3:]),value) for key,value in self.nodes[n].routing_table.items() if key.startswith('GT:') and f.global_title.startswith(key[3:])]
        rule=max(rules,key=lambda item:item[0])[1] if rules else self.nodes[n].routing_table.get('*')
        options=[]
        for nxt,lid in self.adj[n]:
            l=self.links[lid]
            if nxt in visited or l.protocol not in ('SS7','M3UA') or (l.protocol=='M3UA' and l.routing_context!=f.routing_context):continue
            if rule is not None and nxt not in rule:continue
            if rule is None and self.nodes[nxt].kind not in ('STP','SG'):continue
            if not l.enabled or not self.nodes[nxt].enabled:continue
            if not ignore_faults and (not self.link_up(lid,n,self.t,True,observer=n) or not self.node_up(nxt,self.t,True,observer=n)):continue
            rank=rule.index(nxt) if rule else 0
            if self.p.routing_mode=='loadshare':
                subscriber=self.procedures[msg['procedure']]['subscriber']
                rank=(rank-subscriber)%len(rule) if rule else int(hashlib.sha256(f'{subscriber}:{lid}'.encode()).hexdigest()[:4],16)/65536
            latency=l.reverse_latency_ms if n!=l.source and l.reverse_latency_ms is not None else l.latency_ms
            options.append((rank,latency+self.nodes[nxt].processing_ms,nxt,lid))
        if not options:return None
        _,_,nxt,lid=min(options);return nxt,lid

    def note_route(self, f, role, operation, rpc_id, path):
        sig=tuple(lid for _,lid in path);key=(f.id,role,operation) if rpc_id is not None else (f.id,role)
        if key in self.previous_routes and self.previous_routes[key]!=sig and self.t<self.p.duration*1000:self.buckets[self.window(self.t)]['changes']+=1
        self.previous_routes[key]=sig

    def advance_gt(self, msg, n):
        pr=self.procedures[msg['procedure']];f=pr['flow'];dst=self.destination(f,n)
        if dst is not None:
            msg['gtt_pending']=False;msg['target']=dst;pr['destination']=dst;self.touch(pr,dst)
            path=self.route(n,dst,f,pr['subscriber'])
            self.journal.message(msg,'gtt_translation',node=n,translated_target=dst,global_title=f.global_title,
                matching_prefix=max((prefix for prefix in self.nodes[n].gtt if f.global_title.startswith(prefix)),key=len,default=None))
            self.journal.route(pr,n,dst,f,path,cause=msg.get('_journal_event'))
            unaffected=self.route(n,dst,f,pr['subscriber'],True)
            if unaffected is not None and unaffected!=path:
                prev=n
                for node,lid in unaffected:self.touch(pr,node);self.touch(pr,lid,'forward' if prev==self.links[lid].source else 'reverse');prev=node
            if path is None:self.fail_message(msg,'no_route');return
            msg['path'].extend(path);msg['route_record'].extend(node for node,_ in path)
            self.note_route(f,msg['role'],msg['operation'],msg['rpc_id'],msg['path'])
        else:
            hop=self.gt_next_hop(msg,n);preferred=self.gt_next_hop(msg,n,True)
            if preferred is not None and preferred!=hop:
                node,lid=preferred;self.touch(pr,node);self.touch(pr,lid,'forward' if n==self.links[lid].source else 'reverse')
            self.journal.message(msg,'gtt_forward',node=n,next_hop=list(hop) if hop else None,global_title=f.global_title)
            if hop is None:self.fail_message(msg,'gtt_not_found');return
            msg['path'].append(hop);msg['route_record'].append(hop[0])

    def route(self, src, dst, f, selector=0, ignore_faults=False):
        if dst is None or (not ignore_faults and (not self.node_up(src, self.t, True,observer=src) or not self.node_up(dst, self.t, True,observer=src))):
            return None
        key = (self.nodes[dst].host or self.nodes[dst].id) if f.protocol in ('GTPU','IP') else (self.nodes[dst].host or self.nodes[dst].kind) if f.protocol in ('NGAP','SBI','PFCP') else self.nodes[dst].realm if f.protocol == 'Diameter' else self.nodes[dst].point_code
        q, seen = [(0., src, ())], set()
        while q:
            cost, n, path = heapq.heappop(q)
            if n in seen: continue
            seen.add(n)
            if n == dst: return list(path)
            rule = self.nodes[n].routing_table.get(key, self.nodes[n].routing_table.get('*'))
            for nxt, lid in self.adj[n]:
                l = self.links[lid]
                if nxt in seen or (rule is not None and nxt not in rule): continue
                if f.protocol in ('SS7','M3UA'):
                    if l.protocol not in ('SS7','M3UA'):continue
                elif l.protocol!=f.protocol:continue
                if f.protocol in ('NGAP','SBI','PFCP') and nxt!=dst and self.nodes[nxt].kind!='SCP5G':continue
                if l.protocol == 'M3UA' and l.routing_context != f.routing_context: continue
                if not ignore_faults and (not self.link_up(lid, n, self.t, True,observer=src) or not self.node_up(nxt, self.t, True,observer=src)): continue
                if ignore_faults and (not l.enabled or not self.nodes[nxt].enabled): continue
                # Configured next hops are ordered failover, or deterministic SLS-style loadsharing.
                rank = rule.index(nxt) if rule else 0
                if self.p.routing_mode == 'loadshare':
                    rank = ((rank-selector) % len(rule)) if rule else int(hashlib.sha256(f'{selector}:{lid}'.encode()).hexdigest()[:4], 16) / 65536
                latency = l.reverse_latency_ms if n != l.source and l.reverse_latency_ms is not None else l.latency_ms
                penalty = rank * 100000 if rule else rank * 100 if self.p.routing_mode == 'loadshare' else 0
                heapq.heappush(q, (cost+latency+self.nodes[nxt].processing_ms+penalty, nxt, path+((nxt, lid),)))
        return None

    def prepare(self):
        self.check(0)
        self.control.prepare()
        self.clusters.prepare()
        self.monitoring.prepare()
        for lid, l in self.links.items():
            if l.startup_ms and not self.p.control_plane:
                handshake={'Diameter':'CER / CEA','M3UA':'ASPUP / ASPUP_ACK','NGAP':'SCTP_SETUP','SBI':'HTTP2_SETUP','PFCP':'ASSOCIATION_SETUP','SS7':'LINK_ALIGNMENT'}
                states = [('CONNECTING', 0), (handshake.get(l.protocol,'DATA_PATH_START'), l.startup_ms/2), ('ASP_ACTIVE / AS_ACTIVE' if l.protocol=='M3UA' else 'OPEN', l.startup_ms)]
                for state, t in states: self.lifecycle.append(dict(time=t/1000, object=lid, state=state))
        for i, e in enumerate(self.p.incidents):
            if e.kind=='node_capacity' and e.magnitude<1:
                self.push(e.start*1000,'attack_boundary',e.target,0)
                self.push((e.start+e.duration)*1000,'attack_boundary',e.target,0)
            if e.kind not in ('node_down', 'node_restart', 'link_down'): continue
            l = self.links.get(e.target)
            resource=l or self.nodes[e.target]
            detection = e.detection_ms if e.detection_ms is not None else resource.detection_ms
            recovery = e.recovery_ms if e.recovery_ms is not None else resource.recovery_ms
            self.lifecycle.append(dict(time=e.start, object=e.target, state='FAULT', direction=e.direction))
            if e.duration*1000 > detection:
                self.lifecycle.extend([dict(time=e.start+detection/1000, object=e.target, state='DETECTED'), dict(time=e.start+(detection+resource.switch_ms)/1000, object=e.target, state='ROUTE_DOWN')])
            directions=('forward','reverse') if l and e.direction=='both' else (e.direction,)
            def clear(time,known=False):
                kinds=('link_down',) if l else ('node_down','node_restart')
                return all(not any(self.incident_matches(other,kinds,e.target,time,direction,known,l) for other in self.p.incidents) for direction in directions)
            physical_end=(e.start+e.duration)*1000;recovery_end=physical_end+recovery
            physical_state='PHYSICAL_UP' if clear(physical_end) else 'FAULT_END_STILL_DOWN'
            recovered=clear(recovery_end) and clear(recovery_end,True)
            self.lifecycle.extend([dict(time=physical_end/1000, object=e.target, state=physical_state), dict(time=recovery_end/1000, object=e.target, state=('OPEN' if l and l.protocol=='Diameter' else 'AVAILABLE') if recovered else 'RECOVERY_END_STILL_DOWN')])
            if l is None and resource.recovery_ramp_ms:
                self.lifecycle.append(dict(time=e.start+e.duration+(recovery+resource.recovery_ramp_ms)/1000, object=e.target, state='FULL_CAPACITY'))
            if e.kind in ('node_down', 'node_restart'):
                self.push(e.start*1000, 'node_fault', (e.target, e.kind), 0)
                self.push((e.start+e.duration)*1000, 'wake', e.target)
        for f in self.p.flows:
            self.check()
            boundaries = {0., float(self.p.duration)}
            boundaries.update(max(0., min(float(self.p.duration), t)) for e in self.p.incidents if e.kind=='surge' and e.target==f.id for t in (e.start, e.start+e.duration))
            # Periodic intensity is sampled in 100 ms bins; incident boundaries remain exact.
            if f.traffic_profile != 'poisson' or f.profile_amplitude:
                boundaries.update(np.arange(0, self.p.duration, f.profile_step_s).tolist())
            times = sorted(boundaries)
            for index,(lo, hi) in enumerate(zip(times, times[1:])):
                if index%1024==0:self.check()
                mid = (lo+hi)/2
                factor = max((e.magnitude for e in self.p.incidents if e.kind=='surge' and e.target==f.id and e.start<=mid<e.start+e.duration), default=1)
                wave = 1+f.profile_amplitude*math.sin(2*math.pi*mid/f.profile_period_s)
                if f.traffic_profile == 'bursty': wave *= 3 if (mid % f.profile_period_s) < f.profile_period_s/5 else .5
                for t in sorted(lo+self.rng.random(self.rng.poisson(f.rate*factor*wave*(hi-lo)))*(hi-lo)):
                    self.total += 1
                    self.push(float(t)*1000, 'new', (self.total, f))
        for wi in range(self.count):
            self.push(min(self.p.duration, (wi+1)*self.p.window)*1000, 'snapshot', wi, 2)

    def new(self, ident, f):
        self.telemetry.count('signal_arrivals')
        subscriber = int(self.rng.integers(f.subscribers))
        steps = [f.operation]
        if f.procedure == 'registration':
            steps = ['Diameter AIR', 'Diameter ULR'] if f.protocol=='Diameter' else ['MAP SendAuthenticationInfo', 'MAP UpdateLocation']
        pr = dict(id=ident, flow=f, start=self.t, subscriber=subscriber, session=f'{f.id}:{subscriber}:{ident}', steps=steps, step=0, attempt=0, attempts=0, active=True, affected=set(), objects=set(), routes=[], retries=0, transport_retries=0, last_cause=None, destination=self.destination(f))
        self.procedures[ident] = pr
        self.journal.begin(pr)
        self.outstanding += 1; self.max_outstanding = max(self.max_outstanding, self.outstanding)
        self.buckets[self.window(self.t)]['sent'] += 1
        self.touch(pr, f.id,resource='flow')
        self.send_attempt(pr)

    def send_attempt(self, pr):
        if not pr['active']: return
        f = pr['flow']; pr['attempts'] += 1
        if pr['attempt']:
            pr['retries'] += 1
            self.telemetry.count('signal_retries')
            if self.t<self.p.duration*1000:self.buckets[self.window(self.t)]['retries'] += 1
        timer=f'procedure:{pr["id"]}:{pr["step"]}:{pr["attempt"]}'
        pr['_journal_event']=self.journal.emit(pr['id'],'timer_set',cause=pr.get('_journal_event'),timer_id=timer,timer_type='procedure',deadline_ms=self.t+f.timeout_ms,attempt=pr['attempt'],step=pr['step'])
        pr['_journal_timer']=pr['_journal_event']
        self.push(self.t+f.timeout_ms, 'timeout', (pr['id'], pr['step'], pr['attempt']), 0)
        if f.target in self.clusters.members:
            destination=self.clusters.resolve(f.target,f.source,f.protocol)
            if destination is None:pr['last_cause']='cluster_unavailable';return
            pr['destination']=destination
        self.send_message(pr, f.source, pr['destination'], 'request', pr['steps'][pr['step']], f.message_bytes)

    def send_message(self, pr, src, dst, role, operation, size, result='success', step=None, protocol=None, rpc_id=None,category='signalling',context=None):
        f = pr['flow'] if protocol is None else pr['flow'].model_copy(update={'protocol':protocol})
        pending_gt=role=='request' and f.protocol in ('SS7','M3UA') and bool(f.global_title)
        if pending_gt:dst=None
        path = [] if pending_gt else self.route(src, dst, f, pr['subscriber'])
        route_event=self.journal.route(pr,src,dst,f,path,cause=pr.get('_journal_event'),rpc_id=rpc_id,pending_gtt=pending_gt)
        self.touch(pr,src)
        if dst is not None:self.touch(pr,dst)
        # Attribution follows the unaffected path, never every incident in the lab.
        unaffected = self.route(src,dst,f,pr['subscriber'],True)
        if unaffected is not None and unaffected!=path:
            prev=src
            for n,lid in unaffected:
                self.touch(pr,n);self.touch(pr,lid,'forward' if prev==self.links[lid].source else 'reverse');prev=n
        if path is None:
            pr['last_cause'] = 'no_route'
            return
        if not pending_gt and category=='signalling':self.note_route(f,role,operation,rpc_id,path)
        route_record=[src]+[n for n,_ in path]
        if role == 'request' and category=='signalling': pr['routes'].append(route_record)
        if f.message_size_cv:
            sigma = math.sqrt(math.log1p(f.message_size_cv**2))
            size = int(np.clip(self.rng.lognormal(math.log(size)-sigma*sigma/2, sigma), 32, 65536))
        self.message_seq += 1
        msg = dict(id=self.message_seq, procedure=pr['id'], step=pr['step'] if step is None else step, source=src, target=dst, role=role, operation=operation, size=size, result=result, path=path, hop=0, start=self.t, valid=True,protocol=f.protocol,rpc_id=rpc_id,gtt_pending=pending_gt,route_record=route_record)
        self.messages[msg['id']] = msg
        msg['category']=category;msg['context']=context
        self.journal.message(msg,'message_created',cause=route_event,bytes=size,category=category)
        if category=='userplane':msg['priority']=f.data_priority
        self.arrive_node(msg, src)
        return msg

    def usable(self, msg):
        # A client timeout cannot withdraw already queued/on-wire traffic.
        # Late requests still consume server resources and may produce answers.
        return msg['valid']

    def fail_message(self, msg, cause):
        event=self.journal.message(msg,'message_failed',reason=cause)
        if event:self.procedures[msg['procedure']]['_journal_event']=event
        msg['valid'] = False
        self.procedures[msg['procedure']]['last_cause'] = cause

    def arrive_node(self, msg, n):
        if not self.usable(msg): return
        pr = self.procedures[msg['procedure']]; self.touch(pr, n)
        if not self.node_up(n, self.t): self.fail_message(msg, 'node_down'); return
        self.dispatch(n)
        stat = self.node_stats[n]
        queue = self.node_queue[n]
        queue[:] = [entry for entry in queue if self.usable(entry[2])];heapq.heapify(queue)
        # A free worker and admission token can accept a job even with queue_limit=0.
        immediate = len(self.running[n])<self.nodes[n].workers and self.next_admission[n]<=self.t and not queue
        if not immediate and len(queue)>=self.nodes[n].queue_limit:
            stat['dropped'] += 1; self.fail_message(msg, 'queue_overflow'); return
        self.serial += 1
        heapq.heappush(queue, (msg.get('priority',pr['flow'].priority), self.serial, msg, self.t))
        self.journal.message(msg,'cpu_queued',node=n,queue_depth=len(queue)-int(immediate),running=len(self.running[n]),priority=msg.get('priority',pr['flow'].priority))
        stat['max_queue'] = max(stat['max_queue'], len(queue)-int(immediate))
        if self.t<self.p.duration*1000:self.buckets[self.window(self.t)]['queue'].append(len(queue)-int(immediate))
        self.dispatch(n)

    def dispatch(self, n):
        if not self.node_up(n, self.t): return
        node = self.nodes[n]
        queue = self.node_queue[n]
        while queue and len(self.running[n])<node.workers:
            while queue and not self.usable(queue[0][2]): heapq.heappop(queue)
            if not queue: return
            if self.next_admission[n]>self.t:
                if self.wake.get(n) != self.next_admission[n]:
                    self.wake[n] = self.next_admission[n]; self.push(self.wake[n], 'wake', n)
                return
            _, _, msg, arrival = heapq.heappop(queue)
            cost = 1.
            application_work=n==msg['target'] and ((msg.get('category','signalling')=='signalling' and node.kind in ('HLR','HSS','SCP','UDM','UDR','AUSF','PCF')) or msg['operation']=='STATE_REPLICATE')
            if application_work:
                cost = 1.5 if 'Authentication' in msg['operation'] or 'AIR' in msg['operation'] else 2.
            mean = node.processing_ms * cost
            if node.service_distribution == 'exponential': service = self.rng.exponential(mean)
            elif node.service_distribution == 'lognormal' and mean:
                sigma = math.sqrt(math.log1p(node.service_cv**2)); service = self.rng.lognormal(math.log(mean)-sigma*sigma/2, sigma)
            else: service = mean
            embedded_database=0.
            if application_work and node.storage_model=='probabilistic' and (msg['operation']=='STATE_REPLICATE' or self.rng.random()>=node.cache_hit_ratio):embedded_database=node.database_ms;service+=embedded_database
            service = max(.0001, float(service))*self.effect_value('node_delay',n,self.t)
            self.touch(self.procedures[msg['procedure']],n)
            done = self.t+service
            self.last_admission[n]=self.t
            self.next_admission[n] = self.t+1000/self.admission_capacity(n)
            self.running[n][msg['id']] = (msg, self.t, done, self.node_epoch[n])
            started=self.journal.message(msg,'cpu_started',node=n,wait_ms=self.t-arrival,service_ms=service,done_ms=done,
                queue_depth=len(queue),workers=node.workers,storage_model=node.storage_model,probabilistic_database_in_cpu=bool(embedded_database),embedded_database_ms=embedded_database*self.effect_value('node_delay',n,self.t))
            if started:msg.setdefault('_journal_cpu',{})[n]=started
            self.push(done, 'node_done', (n, msg['id'], self.node_epoch[n], arrival))

    def record_busy(self, n, lo, hi):
        self.node_stats[n]['busy_ms'] += hi-lo
        first, last = max(0, self.window(lo)), self.window(hi)
        for wi in range(first, last+1):
            start = wi*self.p.window*1000; end = min(self.p.duration*1000, start+self.p.window*1000)
            self.busy_windows[wi][n] += max(0., min(hi,end)-max(lo,start))

    def node_done(self, n, mid, epoch, arrival):
        if epoch != self.node_epoch[n] or mid not in self.running[n]: return
        msg, lo, _, _ = self.running[n].pop(mid)
        self.journal.message(msg,'cpu_finished',cause=msg.get('_journal_cpu',{}).get(n),node=n,service_ms=self.t-lo)
        self.record_busy(n, lo, self.t)
        self.dispatch(n)
        if not self.usable(msg): return
        if not self.node_up(n, self.t): self.fail_message(msg, 'node_down'); return
        node=self.nodes[n]
        if node.storage_model=='queued' and node.database_ms>0 and (msg.get('category')=='signalling' or msg['operation']=='STATE_REPLICATE') and msg['role']=='request' and n==msg['target']:
            self.storage.submit(n,msg,arrival);return
        self.complete_node(n,msg,arrival)

    def complete_node(self,n,msg,arrival):
        if not self.usable(msg) or not self.node_up(n,self.t):return
        stat = self.node_stats[n]; stat['processed'] += 1; stat['latency'].append(self.t-arrival)
        self.monitoring.observe(n,msg,arrival)
        if msg.get('gtt_pending'):
            self.advance_gt(msg,n)
            if not self.usable(msg):return
        elif self.p.routing_knowledge=='distributed' and msg.get('category') not in ('control','monitoring') and n!=msg['target']:
            if msg['hop']>=max(8,2*len(self.nodes)):
                del msg['path'][msg['hop']:];del msg['route_record'][msg['hop']+1:]
                self.fail_message(msg,'routing_loop');return
            flow=self.procedures[msg['procedure']]['flow'].model_copy(update={'protocol':msg['protocol']})
            path=self.route(n,msg['target'],flow,self.procedures[msg['procedure']]['subscriber'])
            self.journal.route(self.procedures[msg['procedure']],n,msg['target'],flow,path,cause=msg.get('_journal_event'),message_id=msg['id'],forwarder=n)
            if path is None:
                del msg['path'][msg['hop']:];del msg['route_record'][msg['hop']+1:]
                self.fail_message(msg,'no_route');return
            msg['path'][msg['hop']:]=path;msg['route_record'][msg['hop']+1:]=[node for node,_ in path]
        if msg['hop'] == len(msg['path']): self.app_arrival(msg); return
        nxt, lid = msg['path'][msg['hop']]
        self.start_transfer(msg, n, nxt, lid)

    def node_fault(self, n, kind):
        fault_events={}
        for pid in self.journal.sessions:
            if n in self.procedures[pid]['objects']:
                fault_events[pid]=self.journal.emit(pid,'node_fault',node=n,reason=kind)
        self.clusters.node_fault(n,kind=='node_restart')
        self.storage.node_fault(n,kind=='node_restart',fault_events)
        if kind=='node_restart' and self.p.control_plane:self.control.node_restart(n)
        for msg, lo, _, _ in self.running[n].values():
            self.journal.message(msg,'cpu_aborted',cause=fault_events.get(msg['procedure']),node=n,service_ms=self.t-lo,reason=kind)
            self.record_busy(n, lo, self.t); self.touch(self.procedures[msg['procedure']], n); self.fail_message(msg, kind)
        self.node_stats[n]['dropped'] += len(self.running[n])+len(self.node_queue[n])
        self.running[n].clear(); self.node_epoch[n] += 1
        for _, _, msg, arrival in self.node_queue[n]:
            self.journal.message(msg,'cpu_queue_aborted',cause=fault_events.get(msg['procedure']),node=n,wait_ms=self.t-arrival,reason=kind)
            self.touch(self.procedures[msg['procedure']], n);self.fail_message(msg,kind)
        self.node_queue[n].clear(); self.next_admission[n] = self.t

    def association(self, lid, src):
        key = (lid, src)
        if key not in self.associations:
            l = self.links[lid]
            self.associations[key] = dict(queue=deque(), flight={}, cwnd=float(l.cwnd_packets), srtt=None, variance=0., rto=max(l.rto_min_ms,min(l.rto_max_ms,l.rto_ms)), streams=defaultdict(deque), completed=set())
        return self.associations[key]

    def start_transfer(self, msg, src, dst, lid):
        l = self.links[lid]; self.transfer_seq += 1
        paths=[other for other in self.links.values() if l.path_group and other.path_group==l.path_group and {other.source,other.target}=={src,dst} and other.protocol==l.protocol and other.routing_context==l.routing_context]
        payload=min([l.mtu-l.header_bytes]+[x.mtu-x.header_bytes for x in paths])
        chunks = math.ceil(msg['size']/payload)
        tr = dict(id=self.transfer_seq, msg=msg, src=src, dst=dst, lid=lid, chunks=chunks, received=set(), delivered=False, failed=False, sender_failed=False, pending_arrivals=0, stream=self.procedures[msg['procedure']]['subscriber'] % l.streams)
        self.transfers[tr['id']] = tr
        tr['_journal_queued']=self.journal.message(msg,'transport_queued',transfer_id=tr['id'],node=src,receiver=dst,link=lid,chunks=chunks,stream=tr['stream'])
        a = self.association(lid, src)
        a['streams'][tr['stream']].append(tr['id'])
        self.link_stats[lid]['messages'] += 1
        remaining = msg['size']
        for i in range(chunks):
            size = min(remaining,payload)+l.header_bytes; remaining -= size-l.header_bytes
            packet = dict(key=(tr['id'],i), transfer=tr['id'], index=i, size=size, retry=0, token=0, sent=0.,queued_at=self.t)
            a['queue'].append(packet)
        self.pump(lid, src)

    def pump(self, lid, src):
        a = self.association(lid, src)
        while a['queue'] and len(a['flight'])<max(1,int(a['cwnd'])):
            packet = a['queue'].popleft(); tr = self.transfers[packet['transfer']]
            if not self.usable(tr['msg']) or tr['failed'] or tr['sender_failed']: continue
            a['flight'][packet['key']] = packet
            self.packet_send(packet, lid, src)

    def wire_send(self, lid, src, size, pr, ack=False,management=False,message=None,packet=None,journal_cause=None):
        l = self.links[lid]; key = (lid, src)
        advance=not ack and not (packet is not None and self.transfers[packet['transfer']]['delivered'])
        journal_cause=journal_cause if journal_cause is not None else packet.get('_journal_send') if packet is not None and not ack else message.get('_journal_event') if message is not None else None
        direction = 'forward' if src==l.source else 'reverse'
        self.touch(pr, lid, direction)
        s = self.link_stats[lid]
        ds = s['directions'].setdefault(direction,dict(packets=0, bytes=0, dropped=0, retransmissions=0))
        s['packets'] += 1; ds['packets'] += 1
        if ack: s['acks'] += 1
        queue = self.wire_queue[key]
        while queue and queue[0][0]<=self.t: queue.popleft()
        if sum(x[1] for x in queue)+size > l.buffer_bytes:
            if message is not None:self.journal.message(message,'channel_drop',cause=journal_cause,advance=advance,node=src,link=lid,ack=ack,reason='buffer_overflow',bytes=size)
            s['buffer_drops'] += 1; s['dropped'] += 1; ds['dropped'] += 1; return None
        tx_start=max(self.t,self.wire[key]);tx = tx_start+size*8/(l.bandwidth_mbps*1000)
        self.wire[key] = tx; queue.append((tx,size))
        s['bytes'] += size; ds['bytes'] += size
        if message is not None and message.get('category')=='monitoring':self.monitoring.link_bytes[lid]+=size
        loss = l.reverse_loss if direction=='reverse' and l.reverse_loss is not None else l.loss
        factor = max((e.magnitude for e in self.p.incidents if self.incident_matches(e, ('loss',), lid, tx, direction)),default=1)
        loss = max([min(1.,loss*factor)]+[e.magnitude for e in self.p.incidents if self.incident_matches(e,('loss_absolute',),lid,tx,direction)])
        probability = loss+l.loss_correlation*(1-loss) if self.loss_last[key] else loss*(1-l.loss_correlation)
        dropped = self.rng.random()<probability
        self.loss_last[key] = dropped
        if not self.link_up(lid,src,tx,management=management) or not self.node_up(src,tx) or dropped:
            if message is not None:self.journal.message(message,'channel_drop',cause=journal_cause,advance=advance,node=src,link=lid,ack=ack,reason='sampled_loss' if dropped else 'link_or_source_unavailable',wait_ms=tx_start-self.t,serialization_ms=tx-tx_start,tx_start_ms=tx_start,tx_end_ms=tx,bytes=size)
            s['dropped'] += 1; ds['dropped'] += 1; return None
        delay = l.reverse_latency_ms if direction=='reverse' and l.reverse_latency_ms is not None else l.latency_ms
        factor = max((e.magnitude for e in self.p.incidents if self.incident_matches(e, ('latency',), lid, tx, direction)),default=1)
        arrival=tx+max(0.,float(self.rng.normal(delay*factor,l.jitter_ms)))
        if message is not None:
            event=self.journal.message(message,'ack_sent' if ack else 'channel_transmit',cause=journal_cause,advance=advance,node=src,receiver=l.target if src==l.source else l.source,link=lid,
                transfer_id=packet['transfer'] if packet else None,packet_index=packet['index'] if packet else None,
                wait_ms=tx_start-self.t,serialization_ms=tx-tx_start,propagation_ms=arrival-tx,tx_start_ms=tx_start,tx_end_ms=tx,arrival_ms=arrival,bytes=size)
            if ack and packet is not None:packet['_journal_ack']=event
            elif packet is not None:packet['_journal_wire']=event
        self.direction_delivered[key]+=1;samples=self.direction_latencies[key]
        if len(samples)<2000:samples.append(arrival-self.t)
        else:
            index=int(self.metric_rng.integers(self.direction_delivered[key]))
            if index<2000:samples[index]=arrival-self.t
        return arrival

    def packet_send(self, packet, lid, src):
        tr = self.transfers[packet['transfer']]; pr = self.procedures[tr['msg']['procedure']]
        packet['token'] += 1; packet['sent'] = self.t
        l = self.links[lid]; a = self.association(lid,src)
        sent_event=self.journal.message(tr['msg'],'packet_sent',cause=packet.get('_journal_retry',tr.get('_journal_queued')),advance=not tr['delivered'],node=src,receiver=tr['dst'],link=lid,
            transfer_id=tr['id'],packet_index=packet['index'],attempt=packet['retry'],flight=len(a['flight']),cwnd=a['cwnd'],admission_wait_ms=self.t-packet['queued_at'] if not packet['retry'] else 0.)
        packet['_journal_send']=sent_event
        actual=lid
        if packet['retry'] and l.path_group and not self.link_up(lid,src,self.t,True):
            candidates=[x.id for x in self.links.values() if x.path_group==l.path_group and {x.source,x.target}=={tr['src'],tr['dst']} and x.protocol==l.protocol and x.routing_context==l.routing_context and self.link_up(x.id,src,self.t,True)]
            if candidates:actual=min(candidates)
        if packet['retry']:
            s = self.link_stats[actual]; s['retransmissions'] += 1
            direction = 'forward' if src==self.links[actual].source else 'reverse'
            s['directions'].setdefault(direction,dict(packets=0,bytes=0,dropped=0,retransmissions=0))['retransmissions'] += 1
            pr['transport_retries'] += 1
            self.telemetry.count('transport_retries')
            if self.t<self.p.duration*1000:self.buckets[self.window(self.t)]['transport_retries'] += 1
        arrival = self.wire_send(actual,src,packet['size']-l.header_bytes+self.links[actual].header_bytes,pr,management=tr['msg'].get('category')=='control',message=tr['msg'],packet=packet)
        if arrival is not None:tr['pending_arrivals']+=1;self.push(arrival,'packet_rx',(packet,lid,src,actual,packet.get('_journal_wire'),packet['retry']))
        if l.protocol in ('PFCP','GTPU','IP'):
            # N4 datagrams use RPC response timers, not transport DATA ACKs.
            a['flight'].pop(packet['key'],None)
            if arrival is None:tr['failed']=True;self.fail_message(tr['msg'],'datagram_loss')
            return
        rto = a['rto']
        timer=f'transport:{tr["id"]}:{packet["index"]}:{packet["token"]}'
        timer_event=self.journal.emit(pr['id'],'timer_set',cause=sent_event,timer_id=timer,timer_type='transport',deadline_ms=self.t+rto,rto_ms=rto,
            message_id=tr['msg']['id'],transfer_id=tr['id'],packet_index=packet['index'],attempt=packet['retry'],node=src,link=lid)
        packet['_journal_timer_event']=timer_event;packet['_journal_timer_id']=timer
        self.push(self.t+rto,'packet_timeout',(packet,lid,src,packet['token'],timer_event),0)

    def packet_rx(self, packet, lid, src, actual,send_event=None,sent_attempt=None):
        tr = self.transfers[packet['transfer']]; msg = tr['msg']; pr = self.procedures[msg['procedure']]
        tr['pending_arrivals']-=1
        received_event=self.journal.message(msg,'packet_received',cause=send_event,advance=not tr['delivered'] and packet['index'] not in tr['received'],node=tr['dst'],link=actual,transfer_id=tr['id'],packet_index=packet['index'],attempt=sent_attempt,duplicate=packet['index'] in tr['received'])
        if not self.node_up(tr['dst'],self.t):self.retire_receiver(tr);return
        if self.links[lid].protocol not in ('PFCP','GTPU','IP'):
            ack_at = self.wire_send(actual,tr['dst'],self.links[actual].header_bytes,pr,True,management=msg.get('category')=='control',message=msg,packet=packet,journal_cause=received_event)
            if ack_at is not None:self.push(ack_at,'ack',(packet,lid,src,packet.get('_journal_ack')))
        if not self.usable(msg) or tr['failed']:self.retire_receiver(tr);return
        if packet['index'] in tr['received']:
            self.link_stats[lid]['duplicates'] += 1;self.retire_receiver(tr);return
        tr['received'].add(packet['index'])
        if len(tr['received'])==tr['chunks']:
            a = self.association(lid,src)
            if self.links[lid].protocol in ('PFCP','GTPU','IP'):
                if not tr['delivered']:
                    tr['delivered']=True;a['streams'][tr['stream']].remove(tr['id']);msg['hop']+=1;self.arrive_node(msg,tr['dst'])
            else:a['completed'].add(tr['id']);self.deliver_stream(a,tr['stream'])
        self.retire_receiver(tr)

    def retire_receiver(self, tr):
        # Sender exhaustion stops new DATA, but cannot recall DATA already on wire
        # or invalidate a message handed to the next node. An incomplete receive
        # can be retired only after all outstanding arrival events have drained.
        if tr['sender_failed'] and not tr['pending_arrivals'] and len(tr['received'])<tr['chunks']:
            tr['failed']=True;self.fail_message(tr['msg'],'transport_exhausted')
            self.deliver_stream(self.association(tr['lid'],tr['src']),tr['stream'])

    def deliver_stream(self, a, stream):
        queue = a['streams'][stream]
        while queue:
            tr = self.transfers[queue[0]]
            if tr['failed'] or not self.usable(tr['msg']): queue.popleft(); a['completed'].discard(tr['id']); continue
            if tr['id'] not in a['completed']: break
            queue.popleft(); a['completed'].discard(tr['id'])
            if not tr['delivered']:
                tr['delivered'] = True; tr['msg']['hop'] += 1; self.arrive_node(tr['msg'],tr['dst'])

    def ack(self, packet, lid, src,ack_event=None):
        a = self.association(lid,src)
        accepted=a['flight'].pop(packet['key'],None) is not None
        tr=self.transfers[packet['transfer']]
        self.journal.message(tr['msg'],'ack_received',cause=ack_event,advance=False,node=src,link=lid,transfer_id=tr['id'],packet_index=packet['index'],accepted=accepted)
        if not accepted:return
        self.journal.emit(tr['msg']['procedure'],'timer_cancelled',cause=packet.get('_journal_timer_event'),timer_id=packet.get('_journal_timer_id'),timer_type='transport',reason='ack_received',message_id=tr['msg']['id'],node=src,link=lid)
        l = self.links[lid]
        if not packet['retry']:
            rtt = self.t-packet['sent']
            if a['srtt'] is None: a['srtt'], a['variance'] = rtt,rtt/2
            else:
                a['variance'] = .75*a['variance']+.25*abs(a['srtt']-rtt)
                a['srtt'] = .875*a['srtt']+.125*rtt
            a['rto'] = max(l.rto_min_ms,min(l.rto_max_ms,a['srtt']+4*a['variance']))
        a['cwnd'] = min(128.,a['cwnd']+1/a['cwnd'])
        self.pump(lid,src)

    def packet_timeout(self, packet, lid, src, token,timer_event=None):
        a = self.association(lid,src)
        if packet['token'] != token or packet['key'] not in a['flight']: return
        tr = self.transfers[packet['transfer']]
        expired=self.journal.emit(tr['msg']['procedure'],'timer_expired',cause=timer_event,timer_type='transport',timer_id=f'transport:{tr["id"]}:{packet["index"]}:{token}',
            message_id=tr['msg']['id'],transfer_id=tr['id'],packet_index=packet['index'],receiver_delivered=tr['delivered'],pending_arrivals=tr['pending_arrivals'],node=src,link=lid)
        if not self.usable(tr['msg']) or tr['failed']:
            a['flight'].pop(packet['key']); self.deliver_stream(a,tr['stream']); self.pump(lid,src); return
        l = self.links[lid]
        if packet['retry'] >= (0 if l.protocol=='SS7' else l.transport_retries):
            self.journal.message(tr['msg'],'transport_exhausted',cause=expired,advance=False,transfer_id=tr['id'],receiver_delivered=tr['delivered'],pending_arrivals=tr['pending_arrivals'])
            tr['sender_failed']=True
            for key in [key for key in a['flight'] if key[0]==tr['id']]:
                pending=a['flight'].pop(key)
                if key!=packet['key']:self.journal.emit(tr['msg']['procedure'],'timer_cancelled',cause=pending.get('_journal_timer_event'),timer_id=pending.get('_journal_timer_id'),timer_type='transport',reason='sender_exhausted')
            self.retire_receiver(tr);self.deliver_stream(a,tr['stream']); self.pump(lid,src); return
        a['cwnd'] = max(1.,a['cwnd']/2);a['rto']=min(l.rto_max_ms,2*a['rto']);packet['retry'] += 1
        packet['_journal_retry']=self.journal.emit(tr['msg']['procedure'],'transport_retry',cause=expired,message_id=tr['msg']['id'],transfer_id=tr['id'],packet_index=packet['index'],attempt=packet['retry'],rto_ms=a['rto'],cwnd=a['cwnd'])
        self.packet_send(packet,lid,src)

    def app_arrival(self, msg):
        if msg.get('category')=='monitoring':self.monitoring.arrival(msg);return
        if msg.get('category')=='replication':self.clusters.arrival(msg);return
        event=self.journal.message(msg,'message_arrived',node=msg['target'],result=msg['result'],elapsed_ms=self.t-msg['start'])
        if event:self.procedures[msg['procedure']]['_journal_event']=event
        if msg.get('category')=='mobility':self.mobility.arrival(msg);return
        if msg.get('category')=='control':self.control.arrival(msg);return
        if msg.get('category')=='userplane':self.user_plane.arrival(msg);return
        pr = self.procedures[msg['procedure']]; f = pr['flow']; dst = msg['target']
        self.message_finished += 1
        self.reservoir(self.message_traces,dict(time=msg['start']/1000,end=self.t/1000,procedure_id=pr['id'],session_id=pr['session'],transaction_id=f'{pr["id"]}:{msg["step"]}',role=msg['role'],operation=msg['operation'],source=msg['source'],target=dst,bytes=msg['size']),self.message_finished)
        if msg['role']=='request':
            key = (dst,pr['id'],msg['step'])
            if key in self.cache:
                result = self.cache[key]; self.node_stats[dst]['duplicates'] += 1
            else:
                state_key = (dst, f.source, pr['subscriber'])
                state = self.subscribers.get(state_key,'new')
                result = msg.get('resource_error','success')
                kind=self.nodes[dst].kind
                if not self.clusters.can_serve(dst):result='cluster_not_leader'
                if result!='success':pass
                elif (msg['operation'].startswith('MAP') and kind not in ('HLR','SCP')) or (msg['operation'] in ('Diameter AIR','Diameter ULR') and kind!='HSS') or (msg['operation']=='ISUP IAM' and kind!='MSC'):result='unsupported_service'
                elif f.procedure=='registration' and msg['step']>0 and state not in ('authenticated','registered'): result='session_state_lost'
                elif self.rng.random()<self.application_error(dst): result='protocol_error'
                if result=='success':
                    from .resources import CapacityError
                    try:
                        if 'Authentication' in msg['operation'] or msg['operation']=='Diameter AIR': self.subscribers[state_key]='authenticated'
                        elif 'UpdateLocation' in msg['operation'] or msg['operation']=='Diameter ULR': self.subscribers[state_key]='registered'
                    except CapacityError:result='context_limit'
                if result!='success': self.node_stats[dst]['errors'] += 1
                self.cache[key] = result
            # ISUP ACM is an intermediate response; ANM determines call setup completion.
            if msg['operation']=='ISUP IAM': self.send_message(pr,dst,f.source,'intermediate','ISUP ACM',64,result,msg['step'])
            self.send_message(pr,dst,f.source,'answer',ANSWERS[msg['operation']],f.response_bytes,result,msg['step'])
        elif msg['role']=='answer':
            if not pr['active'] or msg['step']!=pr['step']:return
            self.journal.emit(pr['id'],'timer_cancelled',cause=pr.get('_journal_timer'),timer_id=f'procedure:{pr["id"]}:{pr["step"]}:{pr["attempt"]}',reason='answer_received',message_id=msg['id'])
            if msg['result']!='success': self.finish(pr,msg['result']); return
            if pr['step']+1<len(pr['steps']):
                pr['step'] += 1; pr['attempt'] = 0; self.send_attempt(pr)
            else: self.finish(pr,'delivered')

    def timeout(self, ident, step, attempt):
        pr = self.procedures[ident]
        if not pr['active'] or pr['step']!=step or pr['attempt']!=attempt: return
        pr['_journal_event']=self.journal.emit(ident,'timer_expired',cause=pr.get('_journal_timer'),timer_type='procedure',timer_id=f'procedure:{ident}:{step}:{attempt}',reason='no_answer_before_deadline',step=step,attempt=attempt)
        if attempt<pr['flow'].max_retries:
            pr['attempt'] += 1
            backoff = pr['flow'].retry_backoff_ms * 2**attempt
            self.push(self.t+backoff,'retry',(ident,step,pr['attempt']))
            pr['_journal_event']=self.journal.emit(ident,'procedure_retry',cause=pr.get('_journal_event'),step=step,attempt=pr['attempt'],backoff_ms=backoff,scheduled_ms=self.t+backoff)
        else: self.finish(pr,'timeout')

    def reservoir(self, target, record, seen):
        sink=getattr(self,'stream_sink',None)
        if sink:sink(target,record,seen)
        if len(target)<self.p.trace_limit: target.append(record)
        else:
            index = int(self.sample_rng.integers(max(seen,len(target)+1)))
            if index<len(target): target[index] = record

    def finish(self, pr, status):
        self.journal.finish(pr,status)
        pr['active'] = False; self.finished += 1; self.outstanding -= 1
        elapsed = self.t-pr['start']; b = self.buckets[self.window(pr['start'])]
        self.telemetry.signal_finish(status,elapsed)
        pr['status'] = status; pr['elapsed'] = elapsed
        if status=='delivered':
            b['delivered'] += 1; b['latency'].append(elapsed)
            if self.t<self.p.duration*1000: self.in_observation += 1
        else:
            b['lost'] += 1; b['timeouts'] += int(status=='timeout'); self.reasons[status] += 1
        if self.t<self.p.duration*1000: self.buckets[self.window(self.t)]['completed'] += 1
        for i in pr['affected']:
            stat = self.incident_stats[i]; stat['affected_procedures'] += 1
        if status=='delivered':
            for i, stat in enumerate(self.incident_stats):
                if stat['target'] in pr['objects'] and self.t>=stat['end']*1000 and stat['first_success_after_end'] is None: stat['first_success_after_end']=self.t/1000-stat['end']
        self.reservoir(self.traces,dict(time=round(pr['start']/1000,6),end=round(self.t/1000,6),source=pr['flow'].source,target=pr['destination'],operation=pr['flow'].operation,protocol=pr['flow'].protocol,status=status,latency_ms=round(elapsed,6),route=pr['routes'][-1] if pr['routes'] else [],routes=pr['routes'],procedure_id=pr['id'],session_id=pr['session'],subscriber_id=pr['subscriber'],steps_completed=pr['step']+int(status=='delivered'),attempts=pr['attempts'],retries=pr['retries'],transport_retries=pr['transport_retries'],cause=pr['last_cause'] if status!='delivered' else None,affected_incidents=sorted(pr['affected'])),self.finished)

    def run(self, event_hook=None):
        if getattr(self,'reporting',False):raise ValueError('Этот расчёт уже завершён')
        if event_hook is None and self.p.duration>600:
            # Dataset and sweep runs also need bounded transport history, even
            # when they are not durable jobs with a journal of their own.
            from .long_runs import compact
            event_hook=lambda engine:compact(engine) if engine.steps%512==0 else None
        if not self.prepared:self.prepare();self.prepared=True
        while self.events:
            if event_hook:event_hook(self)
            self.steps += 1
            if self.steps%2048==0: self.check(min(99,int(100*self.finished/max(1,self.total))))
            self.t, _, _, kind, data = heapq.heappop(self.events)
            self.telemetry.advance(self.t)
            self.journal.actor=self.journal.event_actor(kind,data)
            if kind=='new': self.new(*data)
            elif kind=='timeout': self.timeout(*data)
            elif kind=='retry':
                ident, step, attempt = data; pr=self.procedures[ident]
                if pr['active'] and pr['step']==step and pr['attempt']==attempt: self.send_attempt(pr)
            elif kind=='node_done': self.node_done(*data)
            elif kind=='node_fault': self.node_fault(*data)
            elif kind=='wake': self.wake.pop(data,None); self.dispatch(data)
            elif kind=='attack_boundary':self.attack_boundary(data)
            elif kind=='packet_rx': self.packet_rx(*data)
            elif kind=='packet_timeout': self.packet_timeout(*data)
            elif kind=='ack': self.ack(*data)
            elif kind=='snapshot': self.buckets[data]['outstanding']=self.outstanding
            elif kind.startswith('rpc_'):self.rpc_event(kind,data)
            elif kind.startswith('ctrl_'):self.control.event(kind,data)
            elif kind.startswith('monitor_'):self.monitoring.event(kind,data)
            elif kind.startswith('user_'):self.user_plane.event(kind,data)
            elif kind.startswith('resource_'):self.storage.event(kind,data)
            elif kind.startswith('mobility_'):self.mobility.event(kind,data)
            elif kind.startswith('session_'):self.session_lifecycle.event(kind,data)
            elif kind.startswith('cluster_'):self.clusters.event(kind,data)
            self.journal.actor=None
            if self.steps>getattr(self,'event_limit',12000000): raise ValueError('Превышен лимит событий; уменьшите нагрузку, размеры или число повторов')
        self.reporting=True
        result=self.report();result['control_plane']=self.control.report()
        result['resources']=self.storage.report()
        result['clusters']=self.clusters.report()
        result['monitoring']=self.monitoring.report()
        result['config_sha256']=hashlib.sha256(json.dumps(self.original_project.model_dump(),sort_keys=True).encode()).hexdigest()
        result['attacks']=self.report_attacks()
        for i,origin in self.attack_origins.items():result['incidents'][i].update(origin)
        for row in result['windows']:
            lo=row['time'];hi=min(self.p.duration,lo+self.p.window)
            row['scheduled_attacks']=sorted({o['attack_id'] for i,o in self.attack_origins.items() if self.p.incidents[i].start<hi and self.p.incidents[i].start+self.p.incidents[i].duration>lo})
        result['procedure_journal']=self.journal.report()
        self.telemetry.report(result)
        return result

    def report_attacks(self):
        reports=[]
        for a in self.original_project.attacks:
            indices={i for i,o in self.attack_origins.items() if o['attack_id']==a.id}
            impacted=[p for p in self.procedures.values() if not p.get('control') and p['affected'] & indices]
            steps=[]
            for s in a.steps:
                step_indices={i for i in indices if self.attack_origins[i]['step_id']==s.id}
                steps.append(dict(id=s.id,name=s.name,target=s.target,effect=s.effect,affected_procedures=sum(bool(p['affected'] & step_indices) for p in impacted)))
            reports.append(dict(id=a.id,name=a.name,enabled=a.enabled,start=a.start,end=a.start+(a.repeats-1)*a.interval+max(s.offset+s.duration for s in a.steps),intervals=len(indices),affected_procedures=len(impacted),successful=sum(p.get('status')=='delivered' for p in impacted),failed=sum(p.get('status')!='delivered' for p in impacted),retries=sum(p['retries'] for p in impacted),transport_retries=sum(p['transport_retries'] for p in impacted),steps=steps))
        for trace in self.traces:
            trace['affected_attacks']=sorted({self.attack_origins[i]['attack_id'] for i in trace['affected_incidents'] if i in self.attack_origins})
        for trace in self.message_traces:
            pr=self.procedures.get(trace['procedure_id'])
            trace['affected_attacks']=sorted({self.attack_origins[i]['attack_id'] for i in pr['affected'] if i in self.attack_origins}) if pr else []
        return reports

    def report(self):
        from .simulator import FEATURES
        for trace in self.traces:
            pr=self.procedures[trace['procedure_id']]
            trace['transport_retries']=pr['transport_retries'];trace['affected_incidents']=sorted(pr['affected'])
        for i,stat in enumerate(self.incident_stats):stat['affected_procedures']=sum(i in pr['affected'] for pr in self.procedures.values() if not pr.get('control'))
        rows = []
        for i,b in enumerate(self.buckets):
            start = i*self.p.window; width=min(self.p.window,self.p.duration-start); sent=max(1,b['sent'])
            scheduled=int(any(e.start<start+width and e.start+e.duration>start for e in self.p.incidents))
            affected=int(bool(b['affected']))
            degraded=int(b['lost']>0 or percentile(b['latency'],95)>self.p.degradation_latency_ms)
            label=dict(scheduled=scheduled,affected=affected,degradation=degraded)[self.p.label_policy]
            rows.append(dict(time=start,label=label,scheduled_event=scheduled,affected=affected,observed_degradation=degraded,affected_objects=sorted(b['objects']),sent=b['sent'],delivered=b['delivered'],rate=b['sent']/width,delivery_ratio=b['delivered']/sent,latency_mean=float(np.mean(b['latency'])) if b['latency'] else 0.,latency_p95=percentile(b['latency'],95),latency_p99=percentile(b['latency'],99),loss_ratio=b['lost']/sent,timeout_ratio=b['timeouts']/sent,queue_mean=float(np.mean(b['queue'])) if b['queue'] else 0.,queue_max=max(b['queue'],default=0),utilization=min(1.,max((v/self.nodes[n].workers for n,v in self.busy_windows[i].items()),default=0)/(width*1000)),route_changes=b['changes'],retries=b['retries'],transport_retries=b['transport_retries'],completion_rate=b['completed']/width,outstanding=b['outstanding'],feature_contract=CONTRACT))
        latencies=[x for b in self.buckets for x in b['latency']]
        delivered=sum(b['delivered'] for b in self.buckets)
        for n,s in self.node_stats.items():
            values=s.pop('latency'); s['latency_p95']=percentile(values,95); s['latency_p99']=percentile(values,99)
            s['utilization']=sum(b.get(n,0) for b in self.busy_windows)/(self.p.duration*1000*self.nodes[n].workers)
        for lid,s in self.link_stats.items():
            for direction,ds in s['directions'].items():
                src=self.links[lid].source if direction=='forward' else self.links[lid].target
                samples=self.direction_latencies[(lid,src)]
                ds['latency_p95']=percentile(samples,95);ds['latency_p99']=percentile(samples,99);ds['latency_sample_size']=len(samples)
        per_flow=[]
        for f in self.p.flows:
            group=[p for p in self.procedures.values() if p['flow'].id==f.id]
            values=[p['elapsed'] for p in group if p['status']=='delivered']
            per_flow.append(dict(flow_id=f.id,operation=f.operation,started=len(group),successful=len(values),success_ratio=len(values)/max(1,len(group)),latency_p95=percentile(values,95),latency_p99=percentile(values,99),attempts=sum(p['attempts'] for p in group),retries=sum(p['retries'] for p in group),failure_reasons=dict(Counter(p['status'] for p in group if p['status']!='delivered'))))
        self.check(100)
        return dict(seed=self.p.seed,duration=self.p.duration,feature_contract=CONTRACT,environment=dict(engine=ENGINE_VERSION,mode='procedures',python=platform.python_version(),numpy=np.__version__),config_sha256=hashlib.sha256(json.dumps(self.p.model_dump(),sort_keys=True).encode()).hexdigest(),summary=dict(sent=self.total,delivered=delivered,delivery_ratio=delivered/max(1,self.total),loss_ratio=(self.total-delivered)/max(1,self.total),latency_mean=float(np.mean(latencies)) if latencies else 0.,latency_p95=percentile(latencies,95),latency_p99=percentile(latencies,99),throughput=self.in_observation/self.p.duration,completed_in_observation=self.in_observation,completed_in_tail=delivered-self.in_observation,procedure_retries=sum(p['retries'] for p in self.procedures.values()),transport_retries=sum(p['transport_retries'] for p in self.procedures.values()),wire_messages=self.message_seq,max_outstanding=self.max_outstanding,outstanding_end=self.buckets[-1]['outstanding'],failure_reasons=dict(self.reasons)),windows=rows,nodes=self.node_stats,links=self.link_stats,traces=sorted(self.traces,key=lambda x:x['time']),message_traces=sorted(self.message_traces,key=lambda x:x['time']),trace_limit=self.p.trace_limit,trace_policy='uniform reservoir over all completed procedures; independent sampling RNG',lifecycle=sorted(self.lifecycle,key=lambda x:x['time']),incidents=self.incident_stats,procedures=per_flow,subscriber_states=dict(Counter(self.subscribers.values())),features=FEATURES)
