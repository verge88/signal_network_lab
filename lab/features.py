"""Versioned detector inputs. Legacy telemetry remains a separate contract."""
import hashlib
import json
import math

LEGACY = ('rate','delivery_ratio','latency_mean','latency_p95','loss_ratio','timeout_ratio','queue_mean','queue_max','utilization','route_changes')
SIGNALING = ('signal_arrivals_s','signal_completed_s','signal_failure_ratio','signal_timeout_ratio','signal_latency_p95','signal_retries_s','transport_retries_s','control_messages_s','control_reconnects_s','neighbor_unavailable_ratio')
USERPLANE = ('data_sent_s','data_completed_s','data_loss_ratio','data_rtt_p95','data_goodput_mbps','data_qos_violation_ratio','data_inflight_mean')
RESOURCES = ('db_requests_s','db_wait_mean_ms','db_overflow_s','db_timeouts_s','db_queue_mean','db_active_ratio','cache_hit_ratio','cache_occupancy_ratio','cache_expired_s','cache_evicted_s','context_occupancy_ratio','context_expired_s','context_rejected_s')
SETS = {
    'legacy-v1': dict(title='Общие · v1 (совместимость)', features=LEGACY, telemetry=None),
    'signaling-v1': dict(title='Сигнализация · v1', features=SIGNALING, telemetry='events-v1'),
    'userplane-v1': dict(title='Пользовательские данные · v1', features=USERPLANE, telemetry='events-v1'),
    'resources-v1': dict(title='Ресурсы · v1', features=RESOURCES, telemetry='events-v1'),
    'combined-v1': dict(title='Все подсистемы · v1', features=SIGNALING+USERPLANE+RESOURCES, telemetry='events-v1'),
}


def specification(key):
    if key not in SETS:raise ValueError('Неизвестная версия набора признаков: '+str(key))
    return SETS[key]


def matrix(rows, key):
    spec=specification(key)
    if not rows:raise ValueError('Нет окон телеметрии')
    result=[]
    for row in rows:
        if spec['telemetry'] and row.get('telemetry_contract')!=spec['telemetry']:
            raise ValueError('Нужна телеметрия events-v1. Выполните новый расчёт; старые окна нельзя дополнить нулями.')
        if spec['telemetry'] and row.get('feature_contract')!='procedures-v2':raise ValueError('events-v1 поддерживается только для procedures-v2')
        try:values=[float(row[f]) for f in spec['features']]
        except (KeyError,TypeError,ValueError) as error:raise ValueError('Отсутствуют или повреждены признаки '+key) from error
        if not all(math.isfinite(v) for v in values):raise ValueError('Признаки должны быть конечными числами')
        result.append(values)
    return result


def topology_id(project):
    """Canonical IDs and positions do not create a new structural topology."""
    labels={n['id']:n['kind']+str(n.get('enabled',True)) for n in project['nodes']}
    for _ in range(4):
        neighbors={n:[] for n in labels}
        for link in project['links']:
            edge=(link['protocol'],link.get('enabled',True))
            neighbors[link['source']].append((edge,labels[link['target']]))
            neighbors[link['target']].append((edge,labels[link['source']]))
        labels={n:hashlib.sha256(json.dumps((labels[n],sorted(v))).encode()).hexdigest() for n,v in neighbors.items()}
    data=dict(nodes=sorted(labels.values()),links=sorted((tuple(sorted((labels[l['source']],labels[l['target']]))),l['protocol'],l.get('enabled',True)) for l in project['links']))
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:16]


def operational_metrics(rows, scores, threshold):
    """Window-end decisions; contiguous alarms and merged fault intervals per run."""
    groups={}
    for row,score in zip(rows,scores):groups.setdefault(row['run_id'],[]).append((row,float(score)))
    delays=[];episodes=missed=false_alarms=0;normal_seconds=0.;available=True
    for values in groups.values():
        values.sort(key=lambda pair:pair[0]['time']);intervals=[]
        previous_end=None;identity=None
        for row,_ in values:
            if row.get('window_seconds'):
                width=row['window_seconds'];start=row['time']
                if not math.isfinite(width) or width<=0 or not math.isfinite(start) or start<0:raise ValueError('Некорректные границы окна ML')
                if previous_end is not None and start<previous_end-1e-9:raise ValueError('Окна одного run_id не должны перекрываться')
                previous_end=start+width
            if 'fault_intervals' in row:
                current=tuple(tuple(v) for v in row['fault_intervals'])
                if identity is not None and identity!=current:raise ValueError('Расписание отказов должно совпадать во всех окнах прогона')
                identity=current
        for row,_ in values:
            for lo,hi in row.get('fault_intervals',[]):
                if hi>lo:intervals.append((float(lo),float(hi)))
        merged=[]
        for lo,hi in sorted(set(intervals)):
            if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
            else:merged.append((lo,hi))
        if any(not row.get('window_seconds') or 'fault_intervals' not in row for row,_ in values):available=False;continue
        previous=False;prior_end=None
        for row,score in values:
            lo=float(row['time']);hi=lo+row['window_seconds'];alarm=score>=threshold
            if prior_end is None or abs(prior_end-lo)>1e-9:previous=False
            overlap=sum(max(0.,min(hi,b)-max(lo,a)) for a,b in merged)
            # Only wholly normal windows are eligible for false alarms/exposure.
            if not overlap and not row['label']:
                normal_seconds+=row['window_seconds']
                if alarm and not previous:false_alarms+=1
            previous=alarm;prior_end=hi
        for lo,hi in merged:
            episodes+=1
            decisions=[row['time']+row['window_seconds'] for row,score in values
                       if score>=threshold and lo<row['time']+row['window_seconds']<=hi and row['time']<hi]
            if decisions:delays.append(min(decisions)-lo)
            else:missed+=1
    return dict(available=available,episodes=episodes,detected=episodes-missed,missed=missed,
                delay_mean_s=sum(delays)/len(delays) if delays else None,delay_max_s=max(delays) if delays else None,
                delays_s=delays,false_alarm_episodes=false_alarms,normal_exposure_s=normal_seconds,
                false_alarms_per_hour=false_alarms*3600/normal_seconds if available and normal_seconds else None,
                policy='Decisions at window end; merged scheduled faults; wholly normal windows; contiguous alarms count once. Missing detections are censored, not zero.')
