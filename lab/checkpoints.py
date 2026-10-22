"""Closed-format state graph. No pickle, dynamic imports or executable callbacks."""
from collections import Counter,defaultdict,deque,OrderedDict
import hashlib
import json
import platform
import zlib
import numpy as np
import pydantic
from pydantic import BaseModel
from . import schema
from .procedures import ProcedureSimulation,ENGINE_VERSION

FORMAT='simulation-state-v1'
MAX_BYTES=128*1024*1024
MAX_NODES=2_000_000
FACTORIES={t.__name__:t for t in (list,dict,set,int,float,bool,deque,Counter)}
MODELS={name:cls for name,cls in vars(schema).items() if isinstance(cls,type) and issubclass(cls,BaseModel) and cls.__module__==schema.__name__}
RUNTIME={'progress','cancel','stream_sink'}
STORE_CALLBACKS={'limit','ttl','node_key'}


def environment():
    return dict(format=FORMAT,engine=ENGINE_VERSION,python=platform.python_version(),numpy=np.__version__,pydantic=pydantic.__version__)


def engine_for(project,progress=None,cancel=None):
    if any(f.operation.startswith('5G ') for f in project.flows):
        from .fiveg import FiveGSimulation
        return FiveGSimulation(project,progress,cancel)
    return ProcedureSimulation(project,progress,cancel)


def registry(engine):
    objects={'engine':engine}
    for name in ('storage','control','journal','telemetry','clusters','monitoring','user_plane','mobility','session_lifecycle'):
        if hasattr(engine,name):objects[name]=getattr(engine,name)
    if hasattr(engine,'user_plane'):objects['upf_policy']=engine.user_plane.policy
    objects.update({'store:'+name:store for name,store in engine.storage.stores.items()})
    return objects


class GraphEncoder:
    def __init__(self,objects):
        self.objects={id(obj):name for name,obj in objects.items()};self.seen={};self.nodes=[];self.keepalive=[]
    def encode(self,value):
        if value is None or type(value) in (str,int,float,bool):return value
        if isinstance(value,np.generic):return self.encode(value.item())
        if id(value) in self.objects:return {'object':self.objects[id(value)]}
        if id(value) in self.seen:return {'ref':self.seen[id(value)]}
        if len(self.nodes)>=MAX_NODES:raise ValueError('Контрольная точка слишком велика: уменьшите нагрузку или число окон')
        index=len(self.nodes);self.seen[id(value)]=index;self.nodes.append(None);self.keepalive.append(value)
        if isinstance(value,BaseModel):node=['model',type(value).__name__,{k:self.encode(v) for k,v in vars(value).items()}]
        elif isinstance(value,np.random.Generator):node=['rng',self.encode(value.bit_generator.state)]
        elif isinstance(value,np.ndarray):node=['array',value.dtype.str,self.encode(value.tolist())]
        elif isinstance(value,defaultdict):
            factory=value.default_factory
            if factory not in FACTORIES.values():raise ValueError('Неподдерживаемый тип словаря состояния')
            node=['defaultdict',factory.__name__,[[self.encode(k),self.encode(v)] for k,v in value.items()]]
        elif isinstance(value,dict):
            tag='counter' if isinstance(value,Counter) else 'ordered' if isinstance(value,OrderedDict) else 'dict'
            node=[tag,[[self.encode(k),self.encode(v)] for k,v in value.items()]]
        elif isinstance(value,(list,tuple,set,deque)):
            node=[type(value).__name__,[self.encode(v) for v in value]]
        else:raise ValueError('Неподдерживаемый объект состояния: '+type(value).__name__)
        self.nodes[index]=node
        return {'ref':index}


class GraphDecoder:
    def __init__(self,objects,nodes):
        if not isinstance(nodes,list) or len(nodes)>MAX_NODES:raise ValueError('Неверный размер графа состояния')
        self.objects=objects;self.nodes=nodes;self.values={};self.active=set()
    def decode(self,value):
        if value is None or type(value) in (str,int,float,bool):return value
        if not isinstance(value,dict) or len(value)!=1:raise ValueError('Неверная ссылка состояния')
        if 'object' in value:return self.objects[value['object']]
        index=value.get('ref')
        if type(index)!=int or not 0<=index<len(self.nodes):raise ValueError('Неверный номер объекта состояния')
        if index in self.values:return self.values[index]
        if index in self.active:raise ValueError('Цикл неизменяемых объектов в контрольной точке')
        self.active.add(index);node=self.nodes[index];tag=node[0]
        if tag in ('dict','counter','ordered','defaultdict'):
            obj=defaultdict(FACTORIES[node[1]]) if tag=='defaultdict' else {'dict':dict,'counter':Counter,'ordered':OrderedDict}[tag]()
            self.values[index]=obj
            for k,v in node[-1]:obj[self.decode(k)]=self.decode(v)
        elif tag in ('list','set','deque'):
            obj={'list':list,'set':set,'deque':deque}[tag]();self.values[index]=obj
            for v in node[1]:
                item=self.decode(v)
                if tag=='set':obj.add(item)
                else:obj.append(item)
        elif tag=='tuple':obj=tuple(self.decode(v) for v in node[1])
        elif tag=='model':
            cls=MODELS[node[1]]
            if set(node[2])-cls.model_fields.keys():raise ValueError('Неизвестное поле модели в состоянии')
            obj=cls.model_construct();self.values[index]=obj
            # Internal RPC flows intentionally carry protocols outside the public Flow literal.
            obj.__dict__.update({k:self.decode(v) for k,v in node[2].items()});obj.__pydantic_fields_set__=set(node[2])
        elif tag=='rng':
            state=self.decode(node[1])
            if state.get('bit_generator')!='PCG64':raise ValueError('Неподдерживаемый генератор случайных чисел')
            obj=np.random.default_rng();obj.bit_generator.state=state
        elif tag=='array':
            dtype=np.dtype(node[1])
            if dtype.kind not in 'biuf':raise ValueError('Неподдерживаемый массив состояния')
            obj=np.array(self.decode(node[2]),dtype=dtype)
        else:raise ValueError('Неизвестный тип состояния: '+str(tag))
        self.values[index]=obj;self.active.remove(index);return obj


def dumps(engine):
    objects=registry(engine);encoder=GraphEncoder(objects);states={}
    for name,obj in objects.items():
        excluded=RUNTIME if name=='engine' else STORE_CALLBACKS if name.startswith('store:') else set()
        states[name]={key:encoder.encode(value) for key,value in vars(obj).items() if key not in excluded}
    payload=dict(environment=environment(),project=engine.original_project.model_dump(),states=states,nodes=encoder.nodes)
    data=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    if len(data)>MAX_BYTES:raise ValueError('Контрольная точка превышает 128 МБ; уменьшите нагрузку или число окон')
    blob=zlib.compress(data,3)
    return blob,hashlib.sha256(blob).hexdigest()


def loads(blob,digest,progress=None,cancel=None):
    if len(blob)>MAX_BYTES or hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Повреждена контрольная точка')
    try:
        decompressor=zlib.decompressobj();data=decompressor.decompress(blob,MAX_BYTES+1)
        if len(data)>MAX_BYTES or decompressor.unconsumed_tail or not decompressor.eof or decompressor.unused_data:raise ValueError('Неверный размер контрольной точки')
        payload=json.loads(data)
        if payload['environment']!=environment():raise ValueError('Версии движка, Python, NumPy или Pydantic отличаются; продолжение этой точки недоступно')
        project=schema.Project.model_validate(payload['project']);engine=engine_for(project,progress,cancel);objects=registry(engine)
        if set(payload['states'])!=set(objects):raise ValueError('Изменился состав объектов движка')
        decoder=GraphDecoder(objects,payload['nodes'])
        for name,obj in objects.items():
            excluded=RUNTIME if name=='engine' else STORE_CALLBACKS if name.startswith('store:') else set()
            allowed=set(vars(obj))-excluded
            if name=='engine':allowed.update(('event_limit','reporting'))
            state=payload['states'][name]
            if set(state)-allowed:raise ValueError('Неизвестные поля состояния: '+name)
            obj.__dict__.update({key:decoder.decode(value) for key,value in state.items()})
        return engine
    except (KeyError,TypeError,IndexError,AttributeError,zlib.error,json.JSONDecodeError,RecursionError) as error:
        raise ValueError('Неверный формат контрольной точки') from error
