"""Standalone styled exports. Rendering never depends on the application's theme."""
import csv
import html
import io
import json
import math
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QSizeF, QMarginsF
from PySide6.QtGui import (QColor,QFont,QFontMetricsF,QImage,QPainter,QPainterPath,
                          QPen,QPdfWriter,QPageSize,QPageLayout,QTextDocument)
from PySide6.QtSvg import QSvgGenerator


FORMATS={'chart':('png','jpeg','svg','pdf','csv','json'),
         'table':('csv','tsv','json','xlsx','html','pdf')}


@dataclass
class ExportStyle:
    background:str='#ffffff'
    foreground:str='#161616'
    accent:str='#111111'
    grid_color:str='#d8d8d8'
    header_background:str='#eeeeee'
    alternate_background:str='#f7f7f7'
    font_family:str='Segoe UI'
    font_size:int=14
    line_width:float=2.0
    line_style:str='solid'
    grid:bool=True
    markers:bool=True
    legend:bool=True
    events:bool=True
    alternating:bool=True
    padding:int=8
    width:int=1600
    height:int=900
    dpi:int=150
    landscape:bool=True

    @classmethod
    def load(cls,values):
        if not isinstance(values,dict):return cls()
        try:
            result=cls(**{key:value for key,value in values.items() if key in cls.__dataclass_fields__})
            result.validate();return result
        except (ValueError,TypeError):return cls()

    def validate(self):
        for key in ('background','foreground','accent','grid_color','header_background','alternate_background'):
            if not re.fullmatch(r'#[0-9a-fA-F]{6}',str(getattr(self,key))):raise ValueError('Некорректный цвет: '+key)
        for key,lo,hi in [('font_size',8,32),('line_width',.5,8),('padding',2,20),('width',640,4096),('height',360,4096),('dpi',72,600)]:
            value=getattr(self,key)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:raise ValueError('Некорректный параметр: '+key)
            if key!='line_width' and not isinstance(value,int):raise ValueError('Нужно целое число: '+key)
        for key in ('grid','markers','legend','events','alternating','landscape'):
            if not isinstance(getattr(self,key),bool):raise ValueError('Некорректный переключатель: '+key)
        if self.line_style not in ('solid','dash','dot'):raise ValueError('Неизвестный тип линии')
        if not isinstance(self.font_family,str) or not self.font_family.strip():raise ValueError('Не задан шрифт')


PRESETS={
    'Светлый монохром':ExportStyle(),
    'Pure Black':ExportStyle(background='#000000',foreground='#ffffff',accent='#ffffff',grid_color='#393939',header_background='#222222',alternate_background='#101010'),
    'Научный':ExportStyle(accent='#2457a7',header_background='#e8eff9',alternate_background='#f4f7fb',font_family='Arial',markers=False),
}


@dataclass
class ExportData:
    kind:str
    title:str
    headers:list=field(default_factory=list)
    rows:list=field(default_factory=list)
    series:list=field(default_factory=list)
    incidents:list=field(default_factory=list)
    x_label:str='Время, s'
    y_label:str=''
    metadata:dict=field(default_factory=dict)

    def validate(self):
        if self.kind not in FORMATS:raise ValueError('Неизвестный объект экспорта')
        if self.kind=='table':
            if not self.headers or not self.rows:raise ValueError('В выбранной таблице нет строк для экспорта')
            if any(len(row)!=len(self.headers) for row in self.rows):raise ValueError('Число ячеек не совпадает с числом столбцов')
        elif not any(s.get('points') for s in self.series):raise ValueError('У графика пока нет данных. Выполните расчёт или выберите модель.')


def table_data(widget,title,selected=False):
    header=widget.horizontalHeader()
    columns=[header.logicalIndex(i) for i in range(widget.columnCount()) if not widget.isColumnHidden(header.logicalIndex(i))]
    indices=sorted({i.row() for i in widget.selectedIndexes()}) if selected else range(widget.rowCount())
    rows=[[widget.item(r,c).text() if widget.item(r,c) else '' for c in columns] for r in indices if not widget.isRowHidden(r)]
    return ExportData('table',title,[widget.horizontalHeaderItem(c).text() if widget.horizontalHeaderItem(c) else str(c+1) for c in columns],rows)


def chart_data(widget,title,x_label='Время, s',y_label=''):
    series=[]
    for index,source in enumerate(widget.series):
        points=[(float(x),float(y)) for x,y in source.get('points',[]) if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))]
        if points:series.append(dict(name=source.get('name',f'Ряд {index+1}'),points=points))
    return ExportData('chart',title,series=series,incidents=list(widget.incidents),x_label=x_label,y_label=y_label)


def chart_rows(data):
    return [[s['name'],x,y] for s in data.series for x,y in s['points']]


def paint_chart(p,rect,data,style):
    p.save();p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(rect,QColor(style.background))
    p.translate(rect.topLeft());p.scale(rect.width()/style.width,rect.height()/style.height)
    width,height=style.width,style.height
    font=QFont(style.font_family);font.setPixelSize(style.font_size);p.setFont(font)
    fm=QFontMetricsF(font);ink=QColor(style.foreground)
    def text(box,value,align=Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter):
        p.setPen(ink);p.drawText(box,align,value)
    title_font=QFont(font);title_font.setBold(True);title_font.setPixelSize(style.font_size+5);p.setFont(title_font)
    text(QRectF(28,16,width-56,32),data.title);p.setFont(font)
    legend_height=0
    patterns={'solid':Qt.PenStyle.SolidLine,'dash':Qt.PenStyle.DashLine,'dot':Qt.PenStyle.DotLine}
    if style.legend:
        lx=30;ly=65
        for i,s in enumerate(data.series):
            label_width=min(width-110,fm.horizontalAdvance(s['name'])+52)
            if lx+label_width>width-28 and lx>30:lx=30;ly+=style.font_size+12
            p.setPen(QPen(QColor(style.accent),style.line_width,patterns[style.line_style] if i==0 else (Qt.PenStyle.DashLine if i%2 else Qt.PenStyle.DotLine)))
            p.drawLine(QPointF(lx,ly),QPointF(lx+24,ly));text(QRectF(lx+32,ly-12,label_width-32,24),fm.elidedText(s['name'],Qt.TextElideMode.ElideRight,label_width-32));lx+=label_width+18
        legend_height=ly-48+18
    points=[point for s in data.series for point in s['points']]
    if not points:p.restore();return
    xmin=min(x for x,y in points);xmax=max(x for x,y in points);ymin=min(0,min(y for x,y in points));ymax=max(y for x,y in points)
    if xmin==xmax:xmin-=.5;xmax+=.5
    if ymin==ymax:ymax=ymin+1
    else:ymax+=(ymax-ymin)*.06
    tick_width=max(fm.horizontalAdvance(f'{ymin+(ymax-ymin)*i/5:.4g}') for i in range(6))
    plot=QRectF(max(82,tick_width+42),58+legend_height,width-max(82,tick_width+42)-32,height-130-legend_height)
    px=lambda x:plot.left()+(x-xmin)/(xmax-xmin)*plot.width()
    py=lambda y:plot.bottom()-(y-ymin)/(ymax-ymin)*plot.height()
    if style.events:
        for event in data.incidents:
            start=max(xmin,event['start']);end=min(xmax,event['start']+event['duration'])
            if end>start:
                shade=QColor(style.accent);shade.setAlpha(24);p.fillRect(QRectF(px(start),plot.top(),px(end)-px(start),plot.height()),shade)
    for i in range(6):
        x=xmin+(xmax-xmin)*i/5;y=ymin+(ymax-ymin)*i/5
        if style.grid:
            p.setPen(QPen(QColor(style.grid_color),1,Qt.PenStyle.DotLine));p.drawLine(QPointF(plot.left(),py(y)),QPointF(plot.right(),py(y)));p.drawLine(QPointF(px(x),plot.top()),QPointF(px(x),plot.bottom()))
        text(QRectF(7,py(y)-12,plot.left()-18,24),f'{y:.4g}',Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        text(QRectF(px(x)-48,plot.bottom()+7,96,24),f'{x:.4g}',Qt.AlignmentFlag.AlignCenter)
    p.setPen(QPen(ink,1));p.drawLine(plot.bottomLeft(),plot.bottomRight());p.drawLine(plot.bottomLeft(),plot.topLeft())
    p.save();p.setClipRect(plot.adjusted(-4,-4,4,4))
    for i,s in enumerate(data.series):
        color=QColor(style.accent);path=QPainterPath();path.moveTo(px(s['points'][0][0]),py(s['points'][0][1]))
        for x,y in s['points'][1:]:path.lineTo(px(x),py(y))
        p.setPen(QPen(color,style.line_width,patterns[style.line_style] if i==0 else (Qt.PenStyle.DashLine if i%2 else Qt.PenStyle.DotLine)));p.setBrush(Qt.BrushStyle.NoBrush);p.drawPath(path)
        if style.markers:
            p.setBrush(color)
            for x,y in s['points']:p.drawEllipse(QPointF(px(x),py(y)),style.line_width+1,style.line_width+1)
    p.restore()
    text(QRectF(plot.left(),height-35,plot.width(),25),data.x_label,Qt.AlignmentFlag.AlignCenter)
    p.save();p.translate(18,plot.center().y());p.rotate(-90);text(QRectF(-plot.height()/2,-12,plot.height(),24),data.y_label,Qt.AlignmentFlag.AlignCenter);p.restore();p.restore()


def table_html(data,style,preview=False):
    esc=lambda value:html.escape(str(value),quote=True)
    rows=data.rows[:12] if preview else data.rows
    body=''.join('<tr>'+''.join('<td>'+esc(value).replace('\n','<br>')+'</td>' for value in row)+'</tr>' for row in rows)
    alt=f'tbody tr:nth-child(even){{background:{style.alternate_background};}}' if style.alternating else ''
    return (f'<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>{esc(data.title)}</title><style>'
            f'body{{font-family:"{esc(style.font_family)}";font-size:{style.font_size}px;background:{style.background};color:{style.foreground};margin:24px;}}'
            f'table{{border-collapse:collapse;width:100%;table-layout:fixed;}}th,td{{border:{1 if style.grid else 0}px solid {style.grid_color};padding:{style.padding}px;word-wrap:break-word;vertical-align:top;}}'
            f'th{{background:{style.header_background};text-align:left;}}thead{{display:table-header-group;}}tr{{page-break-inside:avoid;}}{alt}'
            '</style></head><body>'+f'<h2>{esc(data.title)}</h2><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in data.headers)+'</tr></thead><tbody>'+body+'</tbody></table></body></html>')


def table_document(data,style,width,preview=False):
    # Inline cell formats also work in Qt's rich-text HTML subset (no nth-child CSS).
    esc=lambda value:html.escape(str(value),quote=True)
    rows=data.rows[:12] if preview else data.rows
    border=1 if style.grid else 0
    cells=lambda values,tag,bg:'<tr>'+''.join(f'<{tag} bgcolor="{bg}" style="color:{style.foreground};">{esc(v).replace(chr(10),"<br>")}</{tag}>' for v in values)+'</tr>'
    content=f'<h2>{esc(data.title)}</h2><table width="100%" border="{border}" bordercolor="{style.grid_color}" cellspacing="0" cellpadding="{style.padding}"><thead>'+cells(data.headers,'th',style.header_background)+'</thead>'
    for i,row in enumerate(rows):content+=cells(row,'td',style.alternate_background if style.alternating and i%2 else style.background)
    content+='</table>'
    doc=QTextDocument();font=QFont(style.font_family);font.setPixelSize(style.font_size);doc.setDefaultFont(font);doc.setDocumentMargin(0)
    doc.setDefaultStyleSheet(f'body,h2,th,td {{color:{style.foreground};}}');doc.setHtml(content);doc.setTextWidth(width)
    return doc


def render_preview(data,style):
    image=QImage(900,510,QImage.Format.Format_ARGB32);image.fill(QColor(style.background));p=QPainter(image)
    try:
        if data.kind=='chart':paint_chart(p,chart_box(900,510,style),data,style)
        else:
            doc=table_document(data,style,872,True);p.translate(14,12);doc.drawContents(p,QRectF(0,0,872,486))
    finally:p.end()
    return image


def chart_box(width,height,style):
    scale=min(width/style.width,height/style.height)
    return QRectF((width-style.width*scale)/2,(height-style.height*scale)/2,style.width*scale,style.height*scale)


def write_pdf(path,data,style):
    writer=QPdfWriter(str(path));writer.setTitle(data.title);writer.setCreator('Signal Lab Desktop');writer.setResolution(96)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageOrientation(QPageLayout.Orientation.Landscape if style.landscape else QPageLayout.Orientation.Portrait);writer.setPageMargins(QMarginsF(12,12,12,12))
    p=QPainter(writer)
    if not p.isActive():raise OSError('Не удалось открыть PDF для записи')
    try:
        if data.kind=='chart':
            p.fillRect(QRectF(0,0,writer.width(),writer.height()),QColor(style.background));paint_chart(p,chart_box(writer.width(),writer.height(),style),data,style)
        else:
            width,height=writer.width(),writer.height()-32;doc=table_document(data,style,width);doc.setPageSize(QSizeF(width,height));count=doc.pageCount()
            for page in range(count):
                if page and not writer.newPage():raise OSError('Ошибка добавления страницы PDF')
                p.fillRect(QRectF(0,0,width,writer.height()),QColor(style.background));p.save();p.setClipRect(QRectF(0,0,width,height));p.translate(0,-page*height);doc.drawContents(p,QRectF(0,page*height,width,height));p.restore()
                p.setPen(QColor(style.foreground));font=QFont(style.font_family);font.setPixelSize(10);p.setFont(font);p.drawText(QRectF(0,height+9,width,18),Qt.AlignmentFlag.AlignRight,f'{page+1} / {count}')
    finally:p.end();del p,writer


def write_xlsx(path,data,style):
    if len(data.rows)>1_048_574 or len(data.headers)>16384:raise ValueError('Таблица превышает ограничения XLSX')
    def column(index):
        result='';index+=1
        while index:index,digit=divmod(index-1,26);result=chr(65+digit)+result
        return result
    def xmltext(value):
        text=str(value)
        if len(text)>32767:raise ValueError('Ячейка превышает ограничение XLSX: 32767 символов')
        return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]','',text))
    def cell(value,r,c,fmt):
        # Displayed strings always remain literal strings, including =, + and @.
        return f'<c r="{column(c)}{r}" s="{fmt}" t="inlineStr"><is><t xml:space="preserve">{xmltext(value)}</t></is></c>'
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main';last=column(len(data.headers)-1)
    content=[f'<row r="1">{cell(data.title,1,0,3)}</row>', '<row r="2">'+''.join(cell(v,2,c,1) for c,v in enumerate(data.headers))+'</row>']
    for r,row in enumerate(data.rows,3):content.append(f'<row r="{r}">'+''.join(cell(v,r,c,2 if style.alternating and r%2==0 else 0) for c,v in enumerate(row))+'</row>')
    widths=[]
    for c,heading in enumerate(data.headers):
        length=max([len(str(heading))]+[len(str(row[c])) for row in data.rows[:200] if c<len(row)])
        widths.append(f'<col min="{c+1}" max="{c+1}" width="{min(65,max(12,length+style.padding))}" customWidth="1"/>')
    sheet=f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+''.join(widths)+'</cols><sheetData>'+''.join(content)+f'</sheetData><autoFilter ref="A2:{last}{len(data.rows)+2}"/>'
    if len(data.headers)>1:sheet+=f'<mergeCells count="1"><mergeCell ref="A1:{last}1"/></mergeCells>'
    sheet+=f'<pageSetup paperSize="9" orientation="{"landscape" if style.landscape else "portrait"}"/></worksheet>'
    rgb=lambda color:'FF'+color[1:].upper()
    fonts=''.join(f'<font><sz val="{style.font_size*(1.25 if i==2 else 1)}"/><color rgb="{rgb(style.foreground)}"/><name val="{html.escape(style.font_family,quote=True)}"/>{"<b/>" if i else ""}</font>' for i in range(3))
    fills='<fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'+''.join(f'<fill><patternFill patternType="solid"><fgColor rgb="{rgb(color)}"/><bgColor indexed="64"/></patternFill></fill>' for color in (style.background,style.header_background,style.alternate_background))
    sides=''.join(f'<{side} style="thin"><color rgb="{rgb(style.grid_color)}"/></{side}>' if style.grid else f'<{side}/>' for side in ('left','right','top','bottom'))
    xfs=''.join(f'<xf numFmtId="0" fontId="{font}" fillId="{fill}" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1" indent="{min(3,style.padding//5)}"/></xf>' for font,fill in ((0,2),(1,3),(0,4),(2,2)))
    styles=f'<styleSheet xmlns="{ns}"><fonts count="3">{fonts}</fonts><fills count="5">{fills}</fills><borders count="2"><border/><border>{sides}</border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4">{xfs}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    relns='http://schemas.openxmlformats.org/package/2006/relationships';docrel='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    files={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels':f'<Relationships xmlns="{relns}"><Relationship Id="rId1" Type="{docrel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="{docrel}"><sheets><sheet name="Данные" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="{relns}"><Relationship Id="rId1" Type="{docrel}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{docrel}/styles" Target="styles.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml':sheet,'xl/styles.xml':styles}
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,content in files.items():archive.writestr(name,'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'+content)


def export_file(path,data,style,fmt):
    data.validate();style.validate();path=Path(path)
    if fmt not in FORMATS[data.kind]:raise ValueError('Формат не поддерживается для этого объекта')
    if path.suffix.lower()!='.'+fmt:raise ValueError('Расширение файла должно быть .'+fmt)
    # A failed render leaves an existing destination intact.
    with tempfile.TemporaryDirectory(prefix='.signal-export-',dir=path.parent) as directory:
        target=Path(directory)/path.name
        if fmt in ('csv','tsv'):
            stream=io.StringIO(newline='');writer=csv.writer(stream,delimiter='\t' if fmt=='tsv' else ';')
            writer.writerow(data.headers if data.kind=='table' else ['Ряд',data.x_label,data.y_label or 'Значение']);writer.writerows(data.rows if data.kind=='table' else chart_rows(data));target.write_text(stream.getvalue(),encoding='utf-8-sig',newline='')
        elif fmt=='json':target.write_text(json.dumps(asdict(data),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        elif fmt=='html':target.write_text(table_html(data,style),encoding='utf-8')
        elif fmt=='xlsx':write_xlsx(target,data,style)
        elif fmt=='pdf':write_pdf(target,data,style)
        elif fmt=='svg':
            svg=QSvgGenerator();svg.setFileName(str(target));svg.setSize(QSize(style.width,style.height));svg.setViewBox(QRectF(0,0,style.width,style.height));svg.setResolution(style.dpi);svg.setTitle(data.title);svg.setDescription(data.x_label+' / '+data.y_label)
            p=QPainter(svg)
            try:paint_chart(p,QRectF(0,0,style.width,style.height),data,style)
            finally:p.end();del p,svg
        else:
            image=QImage(style.width,style.height,QImage.Format.Format_RGB32);image.fill(QColor(style.background));image.setDotsPerMeterX(round(style.dpi/0.0254));image.setDotsPerMeterY(round(style.dpi/0.0254));p=QPainter(image)
            try:paint_chart(p,QRectF(0,0,style.width,style.height),data,style)
            finally:p.end()
            if not image.save(str(target),'JPEG' if fmt=='jpeg' else 'PNG',95 if fmt=='jpeg' else -1):raise OSError('Не удалось записать изображение')
        if not target.is_file() or not target.stat().st_size:raise OSError('Экспорт не создал файл')
        target.replace(path)
