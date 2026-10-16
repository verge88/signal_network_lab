import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lab.schema import Project
from native.graph import NetworkView


class GraphInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.project=Project(name='Mouse fixture',nodes=[dict(id='a',name='A',kind='STP',x=200,y=180),dict(id='b',name='B',kind='HLR',x=650,y=180),dict(id='c',name='C',kind='MSC',x=200,y=430)],links=[],flows=[dict(id='f',source='c',target='b',rate=0)]).model_dump()
        self.view=NetworkView();self.view.resize(900,600);self.view.set_project(self.project);self.view.show();self.view.activateWindow();self.view.setFocus();self.app.processEvents()
        self.connections=[];self.moves=[];self.view.connection_requested.connect(lambda a,b:self.connections.append((a,b)));self.view.node_moved.connect(lambda *args:self.moves.append(args))

    def tearDown(self):self.view.close();self.view.deleteLater();self.app.processEvents()

    def point(self,nid,port=False):
        return self.view.mapFromScene(self.view.node_items[nid].mapToScene(QPointF(78 if port else 0,4 if port else 0)))

    def drag(self,start,end):
        QTest.mousePress(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        QTest.mouseMove(self.view.viewport(),end)
        QTest.mouseRelease(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end)

    def test_port_drag_connects_nodes_without_moving_them(self):
        before={key:item.pos() for key,item in self.view.node_items.items()}
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('a',True))
        self.assertIsNone(self.view.connection_source);self.assertIsNone(self.view.connection_preview)
        self.drag(self.point('a',True),self.point('b'))
        self.assertEqual(self.connections,[('a','b')]);self.assertFalse(self.moves)
        self.assertEqual(before,{key:item.pos() for key,item in self.view.node_items.items()});self.assertIsNone(self.view.connection_preview)

    def test_click_mode_connects_two_nodes_and_exits(self):
        self.view.set_connection_mode(True)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('a'))
        self.assertEqual(self.view.connection_source,'a');self.assertTrue(self.view.connection_mode)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('c'))
        self.assertEqual(self.connections,[('a','c')]);self.assertFalse(self.view.connection_mode);self.assertFalse(self.moves)

    def test_mode_also_accepts_drag_between_node_bodies(self):
        self.view.set_connection_mode(True);self.drag(self.point('b'),self.point('c'))
        self.assertEqual(self.connections,[('b','c')]);self.assertFalse(self.moves)

    def test_blank_drop_and_self_drop_do_not_create_a_channel(self):
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('a'))
        self.drag(self.point('a',True),self.view.mapFromScene(QPointF(450,340)))
        self.drag(self.point('a',True),self.point('a'))
        self.assertEqual(self.connections,[]);self.assertIsNone(self.view.connection_source);self.assertIsNone(self.view.connection_preview)

    def test_escape_cancels_before_global_shortcuts(self):
        triggered=[];action=QAction(self.view);action.setShortcut('Escape');action.triggered.connect(lambda:triggered.append(True));self.view.addAction(action)
        self.view.set_connection_mode(True)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('a'))
        QTest.keyClick(self.view,Qt.Key.Key_Escape)
        self.assertFalse(self.view.connection_mode);self.assertIsNone(self.view.connection_source);self.assertFalse(triggered);self.assertFalse(self.connections)

    def test_project_reload_clears_preview_before_deleting_items(self):
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('a'))
        QTest.mousePress(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('a',True))
        self.assertIsNotNone(self.view.connection_preview)
        self.view.set_project(self.project)
        QTest.mouseRelease(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('b'))
        self.assertIsNone(self.view.connection_source);self.assertIsNone(self.view.connection_preview);self.assertFalse(self.connections)

    def test_zoomed_view_uses_scene_coordinates(self):
        self.view.fit_mode=False;self.view.scale(.65,.65);self.view.translate(50,-20)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point('c'))
        self.drag(self.point('c',True),self.point('b'))
        self.assertEqual(self.connections,[('c','b')]);self.assertFalse(self.moves)

    def test_node_body_drag_still_moves_instead_of_connecting(self):
        start=self.point('a');self.drag(start,start+QPoint(50,35))
        self.assertTrue(self.moves);self.assertFalse(self.connections)
