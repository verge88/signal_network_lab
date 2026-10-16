"""Vector topology rendering from an immutable data snapshot, independent of UI state."""
import math
from collections import defaultdict
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QColor,QFont,QFontMetricsF,QPainter,QPainterPath,QPainterPathStroker,QPen
from .node_icons import draw_node_icon

COLORS={'SS7':'#9c6720','M3UA':'#187a5d','Diameter':'#3d6db4','NGAP':'#7855a8','SBI':'#267f8b','PFCP':'#ab5d34','GTPU':'#466cb4','IP':'#5b7554'}
PATTERNS={'SS7':Qt.PenStyle.DashLine,'M3UA':Qt.PenStyle.SolidLine,'Diameter':Qt.PenStyle.DotLine,'NGAP':Qt.PenStyle.DashDotLine,'SBI':Qt.PenStyle.SolidLine,'PFCP':Qt.PenStyle.DashDotDotLine,'GTPU':Qt.PenStyle.DashLine,'IP':Qt.PenStyle.DotLine}
WRAP=Qt.TextFlag.TextWrapAnywhere|Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter


def validate_topology(data):
    if not data.nodes:raise ValueError('Нет узлов для экспорта. Выберите узлы или каналы либо экспортируйте всю топологию.')
    if len(data.nodes)>60 or len(data.links)>150:raise ValueError('Схема превышает ограничения проекта: 60 узлов / 150 каналов')
    ids=[node.get('id') for node in data.nodes]
    if any(not isinstance(nid,str) or not nid for nid in ids) or len(set(ids))!=len(ids):raise ValueError('Некорректные идентификаторы узлов схемы')
    for node in data.nodes:
        if any(not isinstance(node.get(key),(int,float)) or not math.isfinite(node[key]) or abs(node[key])>1_000_000 for key in ('x','y')):raise ValueError('Некорректные координаты узла')
    for link in data.links:
        if link.get('source') not in ids or link.get('target') not in ids or link['source']==link['target']:raise ValueError('Канал ссылается на отсутствующий узел')
        if not isinstance(link.get('latency_ms',0),(int,float)) or not math.isfinite(link.get('latency_ms',0)):raise ValueError('Некорректная задержка канала')


def font_for(style,size=None,bold=False):
    font=QFont(style.font_family);font.setPixelSize(size or style.font_size);font.setBold(bold);return font


def line_color(protocol,style):
    color=QColor(COLORS.get(protocol,style.accent) if style.protocol_colors else style.accent)
    return color.lighter(160) if style.protocol_colors and QColor(style.background).lightness()<80 else color


def line_pattern(protocol,style,enabled=True):
    if not enabled:return Qt.PenStyle.DashLine
    return PATTERNS.get(protocol,Qt.PenStyle.SolidLine) if style.protocol_patterns else {'solid':Qt.PenStyle.SolidLine,'dash':Qt.PenStyle.DashLine,'dot':Qt.PenStyle.DotLine}[style.line_style]


def geometry(data,style):
    font=font_for(style);fm=QFontMetricsF(font);nodes={};bounds=QRectF()
    for node in data.nodes:
        name=str(node.get('name') or node['id']);width=max(180,min(340,fm.horizontalAdvance(name)+22)) if style.node_names else 180
        rows=[];height=20
        if style.node_icons or style.node_types:rows.append(('header',str(node.get('kind','')),max(28,style.font_size+8)));height+=rows[-1][2]
        if style.node_names:
            text_height=max(fm.height(),fm.boundingRect(QRectF(0,0,width-20,10000),WRAP,name).height())+5
            rows.append(('name',name,text_height));height+=text_height
        if style.node_ids:
            rows.append(('id',node['id'],max(fm.height(),fm.boundingRect(QRectF(0,0,width-20,10000),WRAP,node['id']).height())+4));height+=rows[-1][2]
        if style.node_details:
            detail=f'{node.get("capacity",0):g} msg/s · q {node.get("queue_limit",0)}'
            rows.append(('detail',detail,max(fm.height(),fm.boundingRect(QRectF(0,0,width-20,10000),WRAP,detail).height())+4));height+=rows[-1][2]
        height=max(50,height);rect=QRectF(node['x']-width*style.node_scale/2,node['y']-height*style.node_scale/2,width*style.node_scale,height*style.node_scale)
        nodes[node['id']]=dict(node=node,rect=rect,width=width,height=height,rows=rows);bounds=rect if bounds.isNull() else bounds.united(rect)
    groups=defaultdict(list)
    for link in data.links:groups[tuple(sorted((link['source'],link['target'])))].append(link)
    edges=[];placed=[];label_fm=QFontMetricsF(font_for(style,max(8,style.font_size-2)))
    def anchor(rect,towards):
        center=rect.center();delta=towards-center
        if abs(delta.x())+abs(delta.y())<.001:delta=QPointF(1,0)
        ratio=min(rect.width()/2/max(.0001,abs(delta.x())),rect.height()/2/max(.0001,abs(delta.y())))
        return center+delta*ratio
    for pair,links in sorted(groups.items()):
        a=nodes[pair[0]]['rect'].center();b=nodes[pair[1]]['rect'].center();delta=b-a;length=math.hypot(delta.x(),delta.y());normal=QPointF(-delta.y()/length,delta.x()/length) if length else QPointF(0,-1)
        for index,link in enumerate(sorted(links,key=lambda x:x.get('id',''))):
            shift=(index-(len(links)-1)/2)*48*style.node_scale
            source=nodes[link['source']]['rect'];target=nodes[link['target']]['rect']
            obstacles=[item['rect'].adjusted(-7,-7,7,7) for nid,item in nodes.items() if nid not in (link['source'],link['target'])]
            clearance=max(item['rect'].height() for item in nodes.values());candidates=[];stroker=QPainterPathStroker();stroker.setWidth(4)
            for offset in [shift]+[shift+sign*clearance*factor for factor in (2,3.5,5) for sign in (1,-1)]:
                control=(a+b)/2+normal*offset if length>=1 else a+QPointF(0,-140-index*35)
                start=anchor(source,control if offset or length<1 else target.center());end=anchor(target,control if offset or length<1 else source.center())
                path=QPainterPath(start);path.quadTo(control,end);stroke=stroker.createStroke(path)
                collisions=sum(stroke.intersects(obstacle) for obstacle in obstacles);candidates.append(((collisions,abs(offset-shift)),path))
                if not collisions:break
            path=min(candidates,key=lambda candidate:candidate[0])[1];bounds=bounds.united(path.boundingRect())
            parts=[]
            if style.link_protocol:parts.append(str(link.get('protocol','')))
            if style.link_latency:parts.append(f'{link.get("latency_ms",0):g} ms')
            label=' · '.join(parts) if style.link_labels else '';box=None
            if label:
                candidates=[]
                for fraction in (.5,.35,.65,.25,.75):
                    mid=path.pointAtPercent(fraction);candidate=QRectF(mid.x()-label_fm.horizontalAdvance(label)/2-6,mid.y()-label_fm.height()/2-3,label_fm.horizontalAdvance(label)+12,label_fm.height()+6)
                    intersections=[candidate.intersected(other) for other in [*(item['rect'] for item in nodes.values()),*placed]]
                    score=sum(max(0,r.width())*max(0,r.height()) for r in intersections);candidates.append((score,candidate))
                box=min(candidates,key=lambda candidate:candidate[0])[1];placed.append(box.adjusted(-3,-3,3,3));bounds=bounds.united(box)
            edges.append(dict(link=link,path=path,label=label,label_rect=box))
    return nodes,edges,bounds


def paint_topology(p,rect,data,style):
    nodes,edges,bounds=geometry(data,style)
    p.save()
    try:
        p.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.TextAntialiasing)
        if not style.transparent_background:p.fillRect(rect,QColor(style.background))
        p.translate(rect.topLeft());p.scale(rect.width()/style.width,rect.height()/style.height)
        width,height=style.width,style.height;margin=style.canvas_margin;top=margin
        if style.topology_title and data.title:
            font=font_for(style,style.font_size+7,True);p.setFont(font);p.setPen(QColor(style.foreground));fm=QFontMetricsF(font)
            title_height=min(height*.2,fm.boundingRect(QRectF(0,0,width-2*margin,10000),WRAP,data.title).height()+8)
            p.save();p.setClipRect(QRectF(margin,top,width-2*margin,title_height));p.drawText(QRectF(margin,top,width-2*margin,title_height),WRAP,data.title);p.restore();top+=title_height+12
        if style.legend and edges:
            p.setFont(font_for(style));fm=QFontMetricsF(p.font());x=margin;y=top;row_height=max(24,fm.height()+8)
            for protocol in sorted({edge['link'].get('protocol','') for edge in edges}):
                entry_width=fm.horizontalAdvance(protocol)+60
                if x+entry_width>width-margin and x>margin:x=margin;y+=row_height
                p.setPen(QPen(line_color(protocol,style),style.line_width,line_pattern(protocol,style)));p.drawLine(QPointF(x,y+row_height/2),QPointF(x+28,y+row_height/2))
                p.setPen(QColor(style.foreground));p.drawText(QRectF(x+36,y,entry_width-36,row_height),Qt.AlignmentFlag.AlignVCenter,protocol);x+=entry_width
            top=y+row_height+12
        area=QRectF(margin,top,width-2*margin,height-top-margin)
        if area.height()<60:raise ValueError('Для выбранного оформления недостаточно места: увеличьте высоту изображения')
        if style.grid:
            p.save();p.setClipRect(area);p.setPen(QPen(QColor(style.grid_color),.6,Qt.PenStyle.DotLine))
            for x in range(math.ceil(area.left()),math.ceil(area.right()),28):p.drawLine(QPointF(x,area.top()),QPointF(x,area.bottom()))
            for y in range(math.ceil(area.top()),math.ceil(area.bottom()),28):p.drawLine(QPointF(area.left(),y),QPointF(area.right(),y))
            p.restore()
        scale=min(area.width()/max(1,bounds.width()),area.height()/max(1,bounds.height()))*.97
        p.translate(area.center());p.scale(scale,scale);p.translate(-bounds.center())
        for edge in edges:
            link=edge['link'];p.save();p.setOpacity(1 if link.get('enabled',True) else .4)
            p.setPen(QPen(line_color(link.get('protocol',''),style),style.line_width/scale,line_pattern(link.get('protocol',''),style,link.get('enabled',True))));p.setBrush(Qt.BrushStyle.NoBrush);p.drawPath(edge['path']);p.restore()
        for edge in edges:
            if edge['label_rect'] is None:continue
            p.setBrush(QColor(style.background));p.setPen(Qt.PenStyle.NoPen);p.drawRoundedRect(edge['label_rect'],3,3)
            p.setFont(font_for(style,max(8,style.font_size-2)));p.setPen(QColor(style.foreground));p.drawText(edge['label_rect'],Qt.AlignmentFlag.AlignCenter,edge['label'])
        for item in nodes.values():
            node=item['node'];p.save();p.translate(item['rect'].topLeft());p.scale(style.node_scale,style.node_scale)
            p.setOpacity(1 if node.get('enabled',True) else .45);p.setBrush(QColor(style.node_background));p.setPen(QPen(QColor(style.node_border),max(.5,style.line_width*.75)/(scale*style.node_scale),Qt.PenStyle.SolidLine if node.get('enabled',True) else Qt.PenStyle.DashLine))
            p.drawRoundedRect(QRectF(0,0,item['width'],item['height']),8,8);y=10
            for kind,text,row_height in item['rows']:
                p.setPen(QColor(style.foreground));p.setFont(font_for(style,bold=kind=='header'))
                if kind=='header':
                    left=10
                    if style.node_icons:draw_node_icon(p,node.get('kind',''),QRectF(10,y,24,24),QColor(style.foreground));left=42
                    if style.node_types:p.drawText(QRectF(left,y,item['width']-left-10,row_height),Qt.AlignmentFlag.AlignVCenter,text)
                else:p.drawText(QRectF(10,y,item['width']-20,row_height),WRAP,text)
                y+=row_height
            p.restore()
    finally:p.restore()
