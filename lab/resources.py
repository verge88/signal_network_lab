"""Finite caches, expiring context stores and an independent database pool."""
from collections import OrderedDict,Counter,deque
from collections.abc import MutableMapping
import heapq


class CapacityError(ValueError):pass


class TimedStore(MutableMapping):
    def __init__(self,engine,name,limit,ttl,node_key=lambda key:key[0],evict=False):
        self.e=engine;self.name=name;self.limit=limit;self.ttl=ttl;self.node_key=node_key;self.evict=evict
        self.data={};self.orders={};self.stats=Counter();self.serial=0;self.expirations=[]
    def __getitem__(self,key):
        item=self.data.get(key)
        if item is None:raise KeyError(key)
        if item[1]<=self.e.t:self.remove(key,'expired');raise KeyError(key)
        self.orders[self.node_key(key)].move_to_end(key)
        return item[0]
    def __setitem__(self,key,value):
        node=self.node_key(key);self.purge(node);order=self.orders.setdefault(node,OrderedDict());limit=self.limit(self.e.nodes[node])
        if not limit:return
        if key not in self.data and len(order)>=limit:
            if not self.evict:self.stats['rejected']+=1;raise CapacityError('context_limit')
            self.remove(next(iter(order)),'evicted')
        self.serial+=1;deadline=self.e.t+self.ttl(self.e.nodes[node])*1000
        self.data[key]=(value,deadline,self.serial);order[key]=None;order.move_to_end(key);self.stats['writes']+=1
        heapq.heappush(self.expirations,(deadline,self.serial,key))
        if len(self.expirations)>4*len(self.data)+1024:
            self.expirations=[(v[1],v[2],k) for k,v in self.data.items()];heapq.heapify(self.expirations)
        if deadline<=self.e.p.duration*1000:self.e.push(deadline,'resource_expire',(self.name,key,self.serial),0)
    def __delitem__(self,key):
        if key not in self.data:raise KeyError(key)
        self.remove(key,'released')
    def __iter__(self):
        self.purge();return iter(list(self.data))
    def __len__(self):self.purge();return len(self.data)
    def remove(self,key,reason):
        if key in self.data:self.data.pop(key);self.orders[self.node_key(key)].pop(key,None);self.stats[reason]+=1
    def purge(self,node=None):
        while self.expirations and self.expirations[0][0]<=self.e.t:
            _,token,key=heapq.heappop(self.expirations);self.expire(key,token)
    def clear_node(self,node):
        for key in list(self.orders.get(node,{})):self.remove(key,'restart_cleared')
    def expire(self,key,token):
        if key in self.data and self.data[key][2]==token and self.data[key][1]<=self.e.t:self.remove(key,'expired')
    def report(self):
        self.purge();return dict(entries=len(self.data),by_node={n:len(o) for n,o in self.orders.items()},**self.stats)


class StorageResources:
    def __init__(self,engine):
        self.e=engine;self.stores={};self.tasks={};self.serial=0;self.queues={n:deque() for n in engine.nodes};self.running={n:set() for n in engine.nodes}
        self.stats={n:dict(requests=0,hits=0,misses=0,deduplicated=0,completed=0,rejected=0,timeouts=0,max_queue=0,max_active=0,busy_ms=0.,wait_ms=0.) for n in engine.nodes}
        self.data_cache=self.store('data_cache',lambda n:n.cache_entries,lambda n:n.cache_ttl_s,evict=True)
    def store(self,name,limit,ttl,node_key=lambda key:key[0],evict=False):
        store=TimedStore(self.e,name,limit,ttl,node_key,evict);self.stores[name]=store;return store
    def submit(self,n,msg,arrival):
        e=self.e;node=e.nodes[n];stat=self.stats[n];stat['requests']+=1
        pr=e.procedures[msg['procedure']];key=(n,pr['flow'].source,pr['subscriber'],msg['operation'])
        transaction=('5g',n,pr['id'],msg['rpc_id']) if msg.get('rpc_id') is not None else (n,pr['id'],msg['step'])
        if transaction in e.cache:stat['deduplicated']+=1;e.complete_node(n,msg,arrival);return
        if key in self.data_cache:stat['hits']+=1;e.complete_node(n,msg,arrival);return
        stat['misses']+=1;self.serial+=1;ident=self.serial
        task=dict(id=ident,node=n,msg=msg,arrival=arrival,key=key,queued=e.t,epoch=e.node_epoch[n],closed=False,started=None)
        self.tasks[ident]=task
        self.clean_queue(n)
        if len(self.running[n])>=node.db_connections and len(self.queues[n])>=node.db_queue_limit:
            stat['rejected']+=1;self.close(task,'database_queue_overflow');return
        self.queues[n].append(ident);stat['max_queue']=max(stat['max_queue'],max(0,len(self.queues[n])-max(0,node.db_connections-len(self.running[n]))))
        e.push(e.t+node.db_timeout_ms,'resource_db_timeout',ident,0);self.pump(n)
    def clean_queue(self,n):self.queues[n]=deque(i for i in self.queues[n] if not self.tasks[i]['closed'])
    def pump(self,n):
        e=self.e;node=e.nodes[n];self.clean_queue(n)
        while self.queues[n] and len(self.running[n])<node.db_connections and e.node_up(n,e.t):
            ident=self.queues[n].popleft();task=self.tasks[ident];task['started']=e.t;self.running[n].add(ident)
            stat=self.stats[n];stat['wait_ms']+=e.t-task['queued'];stat['max_active']=max(stat['max_active'],len(self.running[n]))
            e.push(e.t+max(.0001,node.database_ms*e.effect_value('node_delay',n,e.t)),'resource_db_done',ident)
    def close(self,task,error=None):
        if task['closed']:return
        task['closed']=True;n=task['node'];e=self.e
        if task['started'] is not None:self.stats[n]['busy_ms']+=e.t-task['started']
        self.running[n].discard(task['id'])
        if task['epoch']==e.node_epoch[n] and e.node_up(n,e.t) and e.usable(task['msg']):
            if error:task['msg']['resource_error']=error
            else:self.data_cache[task['key']]=True;self.stats[n]['completed']+=1
            e.complete_node(n,task['msg'],task['arrival'])
        self.pump(n)
    def event(self,kind,data):
        if kind=='resource_expire':
            name,key,token=data;self.stores[name].expire(key,token);return
        task=self.tasks[data]
        if task['closed']:return
        if kind=='resource_db_timeout':self.stats[task['node']]['timeouts']+=1;self.close(task,'database_timeout')
        else:self.close(task)
    def node_fault(self,n,restart):
        for ident in set(self.running[n])|set(self.queues[n]):
            task=self.tasks[ident]
            if not task['closed']:
                task['closed']=True
                if task['started'] is not None:self.stats[n]['busy_ms']+=self.e.t-task['started']
                self.e.fail_message(task['msg'],'database_node_down')
        self.queues[n].clear();self.running[n].clear()
        if restart:
            for store in self.stores.values():store.clear_node(n)
    def report(self):return dict(database=self.stats,stores={name:s.report() for name,s in self.stores.items()})
