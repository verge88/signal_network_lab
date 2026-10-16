from .schema import Project


def preset(name='hybrid'):
    if name=='5g':return fiveg_preset()
    nodes = [
        ('msc', 'MSC / VLR', 'MSC', 140, 190, 800),
        ('stp-a', 'STP Alpha', 'STP', 390, 130, 500),
        ('stp-b', 'STP Beta', 'STP', 390, 350, 500),
        ('hlr', 'HLR / AuC', 'HLR', 700, 190, 350),
        ('mme', 'MME Access', 'MME', 140, 520, 800),
        ('dra', 'DRA Core', 'DRA', 620, 500, 600),
        ('hss', 'HSS Database', 'HSS', 1000, 470, 400),
        ('sg', 'Signaling GW', 'SG', 950, 150, 600),
    ]
    links = [('msc', 'stp-a', 'SS7'), ('msc', 'stp-b', 'SS7'), ('stp-a', 'stp-b', 'M3UA'), ('stp-a', 'hlr', 'M3UA'), ('stp-b', 'hlr', 'M3UA'), ('hlr', 'sg', 'M3UA'), ('stp-a', 'sg', 'M3UA'), ('mme', 'dra', 'Diameter'), ('dra', 'hss', 'Diameter'), ('mme', 'hss', 'Diameter')]
    flows = [dict(id='map', source='msc', target='hlr', protocol='M3UA', operation='MAP UpdateLocation', procedure='registration', rate=60), dict(id='auth', source='msc', target='hlr', protocol='M3UA', operation='MAP SendAuthenticationInfo', rate=25), dict(id='ulr', source='mme', target='hss', protocol='Diameter', operation='Diameter ULR', procedure='registration', rate=80, message_bytes=640)]
    if name == 'ss7':
        nodes = nodes[:4] + [nodes[7]]
        links = links[:7]
        flows = flows[:2]
    elif name == 'diameter':
        nodes = nodes[4:7]
        links = links[7:]
        flows = flows[2:]
    return Project(name={'hybrid': 'Hybrid signaling core', 'ss7': 'SS7 / SIGTRAN core', 'diameter': 'Diameter EPC core'}.get(name, 'Hybrid signaling core'), nodes=[dict(id=i, name=n, kind=k, x=x, y=y, capacity=c, point_code=f'2-10-{j+1}') for j, (i,n,k,x,y,c) in enumerate(nodes)], links=[dict(id=f'l{j+1}', source=a, target=b, protocol=p, latency_ms=8+j%3*4) for j,(a,b,p) in enumerate(links)], flows=flows)


def fiveg_preset():
    from .fiveg import SERVICES
    specs=[('gnb','gNB / UE cohorts','gNB',100,330),('amf','AMF Access','AMF',350,330),
        ('scp5g','SBA Service Proxy','SCP5G',650,330),('ausf','AUSF Authentication','AUSF',580,80),
        ('udm','UDM Subscriber Data','UDM',900,80),('udr','UDR Repository','UDR',1170,80),
        ('nrf','NRF Discovery','NRF',650,600),('nssf','NSSF Slice Selection','NSSF',930,600),
        ('pcf','PCF Policy','PCF',1190,600),('smf','SMF Sessions','SMF',980,330),('upf','UPF / N3 · N4','UPF',1270,330),('dn','Data Network','DN',1540,330)]
    nodes=[dict(id=i,name=name,kind=kind,x=x,y=y,capacity=3000 if kind=='SCP5G' else 1500,
        workers=8,processing_ms=.5,point_code=f'5-1-{j+1}',host=f'{i}.5gc.lab',
        services=SERVICES.get(kind,[]),database_ms=2 if kind in ('UDM','UDR') else 0) for j,(i,name,kind,x,y) in enumerate(specs)]
    links=[dict(id='n2',source='gnb',target='amf',protocol='NGAP',latency_ms=3,jitter_ms=.2,loss=.0001,bandwidth_mbps=100),
        dict(id='n4',source='smf',target='upf',protocol='PFCP',latency_ms=1,jitter_ms=.1,loss=.0001,bandwidth_mbps=100),
        dict(id='n3',source='gnb',target='upf',protocol='GTPU',latency_ms=3,jitter_ms=.2,loss=.0001,bandwidth_mbps=100),
        dict(id='n6',source='upf',target='dn',protocol='IP',latency_ms=2,jitter_ms=.2,loss=.0001,bandwidth_mbps=100)]
    links.extend(dict(id='sbi-'+i,source=i,target='scp5g',protocol='SBI',latency_ms=1,jitter_ms=.1,loss=.0001,bandwidth_mbps=100) for i,_,kind,_,_ in specs if kind not in ('gNB','UPF','SCP5G','DN'))
    flows=[dict(id='registration-5g',source='gnb',target='amf',protocol='NGAP',operation='5G Registration',rate=3,message_bytes=800,response_bytes=512,timeout_ms=8000),
        dict(id='pdu-5g',source='gnb',target='amf',protocol='NGAP',operation='5G PDU Session',rate=2,message_bytes=900,response_bytes=640,timeout_ms=8000),
        dict(id='access-5g',source='gnb',target='amf',protocol='NGAP',operation='5G Registration + PDU Session',rate=4,message_bytes=800,response_bytes=512,timeout_ms=12000)]
    return Project(name='5G SA / Service-Based Core',nodes=nodes,links=links,flows=flows)
