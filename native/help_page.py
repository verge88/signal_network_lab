"""Offline handbook with chapter navigation and full-text search, using Qt text."""
import re
import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices,QTextCursor,QTextBlockFormat,QPalette
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QListWidget,QListWidgetItem,QTextBrowser,QSplitter,QTextEdit
from .widgets import label,button
from .style import themed_color


def handbook_path():
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    return root/'docs'/'user-guide.md'


def load_chapters(path=None):
    source=(path or handbook_path()).read_text(encoding='utf-8')
    parts=re.split(r'^## (.+)\s*$',source,flags=re.MULTILINE)
    if len(parts)<3:raise ValueError('В руководстве нет разделов')
    return [dict(title=parts[i].strip(),text='## '+parts[i].strip()+'\n\n'+parts[i+1].strip()) for i in range(1,len(parts),2)]


class HelpPage(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.chapters=load_chapters();self.matches=[];self.match_index=-1
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(9)
        top=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('Поиск по всему руководству: например, AMF, канал, порог…');self.search.setClearButtonEnabled(True);self.search.setAccessibleName('Поиск по справке');top.addWidget(self.search,1)
        self.previous=button('Назад',lambda:self.next_match(-1));self.next=button('Далее',lambda:self.next_match(1));self.previous.setToolTip('Предыдущее совпадение в разделе');self.next.setToolTip('Следующее совпадение в разделе');top.addWidget(self.previous);top.addWidget(self.next);layout.addLayout(top)
        split=QSplitter(Qt.Orientation.Horizontal);layout.addWidget(split,1)
        self.contents=QListWidget();self.contents.setAccessibleName('Оглавление руководства');self.contents.setMinimumWidth(220);split.addWidget(self.contents)
        self.reader=QTextBrowser();self.reader.setAccessibleName('Руководство пользователя');self.reader.setOpenLinks(False);self.reader.anchorClicked.connect(self.open_reference)
        self.reader.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth);font=self.reader.font();font.setPointSize(11);self.reader.setFont(font);split.addWidget(self.reader);split.setSizes([285,900]);split.setStretchFactor(1,1)
        self.status=label('','Muted');layout.addWidget(self.status)
        for index,chapter in enumerate(self.chapters):
            item=QListWidgetItem(chapter['title']);item.setData(Qt.ItemDataRole.UserRole,index);item.setToolTip(chapter['title']);self.contents.addItem(item)
        self.contents.currentItemChanged.connect(self.render_selected);self.search.textChanged.connect(self.filter_chapters);self.search.returnPressed.connect(lambda:self.next_match(1))
        self.refresh_theme();self.contents.setCurrentRow(0)
    def refresh_theme(self):
        ink=themed_color('#e5ebf5').name();link=themed_color('#8fe0c2').name();line=themed_color('#344153').name();surface=themed_color('#191f29').name()
        self.reader.document().setDefaultStyleSheet(f'body {{ color:{ink}; }} h2 {{ font-size:22px; margin-top:4px; margin-bottom:18px; }} h3 {{ font-size:17px; margin-top:22px; }} p,li {{ margin-top:6px; margin-bottom:10px; }} a {{ color:{link}; }} table {{ border-collapse:collapse; }} th,td {{ border:1px solid {line}; padding:7px; }} th {{ background-color:{surface}; }} code {{ font-family:Consolas; }}')
        self.reader.setStyleSheet('QTextBrowser { background:'+themed_color('#11151c').name()+'; color:'+ink+'; font-size:15px; border:1px solid '+line+'; border-radius:8px; padding:18px; }')
        if self.contents.currentItem():self.render_selected()
    def open_reference(self,url):
        host=url.host().lower()
        if url.scheme()=='https' and any(host==domain or host.endswith('.'+domain) for domain in ('rfc-editor.org','etsi.org','itu.int','3gpp.org')):QDesktopServices.openUrl(url)
    def open_section(self,title=None):
        self.search.clear()
        if title:
            for index,chapter in enumerate(self.chapters):
                if chapter['title']==title:self.contents.setCurrentRow(index);break
    def filter_chapters(self):
        query=self.search.text().strip().casefold();visible=[]
        for index,chapter in enumerate(self.chapters):
            item=self.contents.item(index);shown=not query or query in chapter['text'].casefold();item.setHidden(not shown)
            if shown:visible.append(item)
        current=self.contents.currentItem()
        if not visible:
            self.reader.clear();self.matches=[];self.match_index=-1;self.previous.setEnabled(False);self.next.setEnabled(False);self.status.setText('Ничего не найдено. Попробуйте полное название или другую часть слова.');return
        if current not in visible:self.contents.setCurrentItem(visible[0])
        else:self.render_selected()
    def render_selected(self,*args):
        current=self.contents.currentItem()
        if current is None:return
        chapter=self.chapters[current.data(Qt.ItemDataRole.UserRole)];self.reader.setMarkdown(chapter['text']);self.matches=[];self.match_index=-1
        block=self.reader.document().begin()
        while block.isValid():
            cursor=QTextCursor(block);format=block.blockFormat();table=cursor.currentTable()
            format.setLineHeight(125,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            format.setTopMargin(18 if format.headingLevel()>=3 else 4 if not table and not block.textList() else 0)
            format.setBottomMargin(8 if not table and not block.textList() else 2);cursor.setBlockFormat(format);block=block.next()
        query=self.search.text().strip();cursor=QTextCursor(self.reader.document())
        if query:
            while True:
                cursor=self.reader.document().find(query,cursor)
                if cursor.isNull():break
                self.matches.append(QTextCursor(cursor))
        highlights=[]
        for cursor in self.matches:
            selection=QTextEdit.ExtraSelection();selection.cursor=cursor;selection.format.setBackground(self.palette().color(QPalette.ColorRole.Highlight));selection.format.setForeground(self.palette().color(QPalette.ColorRole.HighlightedText));highlights.append(selection)
        self.reader.setExtraSelections(highlights);self.previous.setEnabled(bool(self.matches));self.next.setEnabled(bool(self.matches))
        found=sum(not self.contents.item(i).isHidden() for i in range(self.contents.count()))
        self.status.setText(f'Разделов: {found} из {len(self.chapters)}. Совпадений в разделе: {len(self.matches)}.' if query else f'{len(self.chapters)} разделов · руководство доступно без интернета · F1 — открыть справку')
        if self.matches:self.next_match(1)
    def next_match(self,direction):
        if self.matches:
            self.match_index=(self.match_index+direction)%len(self.matches);self.reader.setTextCursor(self.matches[self.match_index]);self.reader.ensureCursorVisible()
