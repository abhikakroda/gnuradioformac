# Copyright 2014-2020 Free Software Foundation, Inc.
# This file is part of GNU Radio
#
# GNU Radio Companion is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# GNU Radio Companion is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA

from __future__ import absolute_import, print_function

# Standard modules
import logging
import os
import sys
import subprocess
import cProfile
import pstats

from typing import Union

from PyQt6.QtCore import QEvent, QMetaObject, QPointF, QSize, QThreadPool, QTimer, QUrl, Qt, pyqtSlot
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QAction, QPainter, QPalette
from PyQt6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QLabel,
                             QMainWindow, QMenu, QMessageBox, QProgressBar, QSizePolicy, QStyle,
                             QTabWidget, QToolBar, QToolButton, QUndoView, QWidget)

# Custom modules
from . import native_ui
from .flowgraph_view import FlowgraphView
from .canvas.flowgraph import FlowgraphScene
from .example_browser import ExampleBrowser, ExampleBrowserDialog, Worker
from .executor import ExecFlowGraphThread
from .. import base, Constants, Utils
from .variable_editor import VariableEditorAction
from .undoable_actions import (
    RotateAction,
    EnableAction,
    DisableAction,
    BypassAction,
    MoveAction,
    NewElementAction,
    DeleteElementAction,
    BlockPropsChangeAction,
    BussifyAction,
    AutoconnectAction,
)
from .preferences import PreferencesDialog
from .oot_browser import OOTBrowser
from .dialogs import ErrorsDialog
from ...core.base import Element
from ...core.utils import macos
from ... import paths

# Bump when the default dock layout changes so stale saved layouts are dropped.
MACOS_LAYOUT_VERSION = 3

# Logging
log = logging.getLogger(f"grc.application.{__name__}")

# Shortcuts
Action = QAction
Menu = QMenu
Toolbar = QToolBar


def Icons(name):
    """Theme icon; on macOS Qt resolves these to SF Symbols, with a symbol for those it has no mapping for."""
    icon = QIcon.fromTheme(name)
    if icon.isNull() and name in MACOS_FALLBACK_ICONS:
        icon = QIcon.fromTheme(MACOS_FALLBACK_ICONS[name])
    return icon


MACOS_FALLBACK_ICONS = {
    "system-run": "hammer",
    "edit-select_all": "checkmark.square",
    "document-close": "xmark.circle",
}
Keys = QKeySequence.StandardKey
QStyle = QStyle


class SidebarHeader(QWidget):
    """Small secondary-colour section title that follows the current appearance."""

    def __init__(self, text):
        super().__init__()
        self.text = text
        font = self.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text) + 20, metrics.height() + 14)

    def paintEvent(self, event):
        painter = QPainter(self)
        color = self.palette().color(QPalette.ColorRole.WindowText)
        color.setAlpha(150)
        painter.setPen(color)
        painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(12, 4, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.text)


class MainWindow(QMainWindow, base.Component):
    def __init__(self, file_path=[]):
        QMainWindow.__init__(self)
        # base.Component.__init__(self)

        log.debug("Setting the main window")
        self.setObjectName("main")
        title = _("window-title")
        self.setWindowTitle(title if title != "window-title" else "GNU Radio Companion")
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks |
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.AnimatedDocks
        )
        self.progress_bar = QProgressBar()
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()

        # Setup the window icon
        icon = QIcon(self.settings.path.ICON)
        log.debug("Setting window icon - ({0})".format(self.settings.path.ICON))
        self.setWindowIcon(icon)

        monitor = self.screen().availableGeometry()
        log.debug(
            "Setting window size - ({}, {})".format(monitor.width(), monitor.height())
        )
        if sys.platform == "darwin":
            self.resize(int(monitor.width() * 0.85), int(monitor.height() * 0.9))
            self.move(monitor.x() + (monitor.width() - self.width()) // 2,
                      monitor.y() + (monitor.height() - self.height()) // 2)
        else:
            self.resize(int(monitor.width() * 0.50), monitor.height())

        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)

        # Get max length of recently opened files list to be displayed in the menu
        self.max_recent_files = self.app.qsettings.value('appearance/max_recent_files', 10, type=int)
        recent_files = list(self.app.qsettings.value('window/files_recent', []))
        for file in recent_files:
            if not os.path.exists(file):
                recent_files.remove(file)
        self.recent_files = recent_files[:self.max_recent_files]

        self.menuBar().setNativeMenuBar(self.settings.window.NATIVE_MENUBAR)

        # TODO: Not sure about document mode
        # self.setDocumentMode(True)

        # Generate the rest of the window
        self.createStatusBar()

        self.profiler = cProfile.Profile()

        # actions['Quit.triggered.connect(self.close)
        # actions['Report.triggered.connect(self.reportDock.show)
        # QMetaObject.connectSlotsByName(self)

        # Translation support

        # self.setWindowTitle(_translate("blockLibraryDock", "Library", None))
        # library.headerItem().setText(0, _translate("blockLibraryDock", "Blocks", None))
        # QMetaObject.connectSlotsByName(blockLibraryDock)

        # TODO: Move to the base controller and set actions as class attributes
        # Automatically create the actions, menus and toolbars.
        # Child controllers need to call the register functions to integrate into the mainwindow
        self.actions = {}
        self.menus = {}
        self.toolbars = {}
        self.createActions(self.actions)
        self.createMenus(self.actions, self.menus)
        self.createToolbars(self.actions, self.toolbars)
        self.connectSlots()

        # Rest of the GUI widgets

        # Map some of the view's functions to the controller class
        self.registerDockWidget = self.addDockWidget
        self.registerMenu = self.addMenu
        self.registerToolBar = self.addToolBar

        # Do other initialization stuff. View should already be allocated and
        # actions dynamically connected to class functions. Also, the log
        # functionality should be also allocated
        log.debug("__init__")

        # Add the menus from the view
        menus = self.menus
        self.registerMenu(menus["file"])
        self.registerMenu(menus["edit"])
        self.registerMenu(menus["view"])
        self.registerMenu(menus["build"])
        self.registerMenu(menus["tools"])
        self.registerMenu(menus["help"])

        toolbars = self.toolbars
        self.registerToolBar(toolbars["file"])
        self.registerToolBar(toolbars["edit"])
        self.registerToolBar(toolbars["run"])
        self.registerToolBar(toolbars["misc"])

        self.tabWidget = QTabWidget()
        self.tabWidget.setTabsClosable(True)
        if sys.platform == "darwin":
            self.tabWidget.setMovable(True)
            self.tabWidget.setUsesScrollButtons(True)
            self.tabWidget.setElideMode(Qt.TextElideMode.ElideMiddle)
            self.tabWidget.tabBar().setExpanding(True)
        self.tabWidget.tabCloseRequested.connect(
            lambda index: self.close_triggered(index)
        )
        self.tabWidget.currentChanged.connect(
            lambda index: self.tab_triggered(index)
        )
        self.setCentralWidget(self.tabWidget)

        self.clipboard = None
        self.undoView = None

        files_open = list(self.app.qsettings.value('window/files_open', [])) + file_path
        grc_file_found = False
        if files_open:
            for file in files_open:
                if os.path.isfile(file):
                    try:
                        self.open_triggered(file)
                        grc_file_found = True
                    except Exception as e:
                        log.error(f"failed to load flowgraph file {file} with exception {e}")
                        log.debug(f"file {file}:", exc_info=True)
        if not grc_file_found:
            self.new_triggered()

        restored_state = False
        try:
            self.restoreGeometry(self.app.qsettings.value("window/geometry"))
            if sys.platform != "darwin":
                restored_state = self.restoreState(self.app.qsettings.value("window/windowState"))
        except TypeError:
            log.warning("Could not restore window geometry and state.")

        self.examples_found = False
        self.ExampleBrowser = ExampleBrowser()
        self.ExampleBrowser.file_to_open.connect(self.open_example)
        self.OOTBrowser = OOTBrowser()

        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(1)
        ExampleFinder = Worker(self.ExampleBrowser.find_examples)
        ExampleFinder.signals.result.connect(self.populate_libraries_w_examples)
        ExampleFinder.signals.progress.connect(self.update_progress_bar)
        self.threadpool.start(ExampleFinder)

        if sys.platform == "darwin":
            self.apply_macos_native_chrome()

    """def show(self):
        log.debug("Showing main window")
        self.show()
    """

    @pyqtSlot(VariableEditorAction)
    def handle_editor_action(self, key):
        # Calculate the position to insert a new block
        # Perhaps we should add a random component, as we may add several blocks
        pos = (self.currentFlowgraphScene.sceneRect().width() / 2, self.currentFlowgraphScene.sceneRect().height() / 2)
        if key == VariableEditorAction.DELETE_BLOCK:
            self.delete_triggered()
        elif key == VariableEditorAction.DISABLE_BLOCK:
            self.disable_triggered()
        elif key == VariableEditorAction.ENABLE_BLOCK:
            self.enable_triggered()
        elif key == VariableEditorAction.ADD_VARIABLE:
            self.currentFlowgraphScene.add_block('variable', pos)
        elif key == VariableEditorAction.ADD_IMPORT:
            self.currentFlowgraphScene.add_block('import', pos)
        elif key == VariableEditorAction.OPEN_PROPERTIES:
            self.currentFlowgraphScene.selected_blocks()[0].open_properties()
        else:
            log.debug(f"{key} not implemented yet")
        self.currentFlowgraphScene.clearSelection()

    @property
    def currentView(self):
        return self.tabWidget.currentWidget()

    @property
    def currentFlowgraphScene(self):
        return self.tabWidget.currentWidget().scene()

    @property
    def currentFlowgraph(self):
        return self.tabWidget.currentWidget().scene().core

    @pyqtSlot(QPointF)
    def registerMove(self, diff):
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        action = MoveAction(self.currentFlowgraphScene, diff)
        self.currentFlowgraphScene.undoStack.push(action)
        self.updateActions()

    @pyqtSlot(Element)
    def registerNewElement(self, elem):
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        action = NewElementAction(self.currentFlowgraphScene, elem)
        self.currentFlowgraphScene.undoStack.push(action)
        self.updateActions()
        self.currentFlowgraphScene.update()

    @pyqtSlot(Element)
    def registerDeleteElement(self, elem):
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        action = DeleteElementAction(self.currentFlowgraphScene, elem)
        self.currentFlowgraphScene.undoStack.push(action)
        self.updateActions()
        self.currentFlowgraphScene.update()

    @pyqtSlot(Element)
    def registerBlockPropsChange(self, elem):
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        action = BlockPropsChangeAction(self.currentFlowgraphScene, elem)
        self.currentFlowgraphScene.undoStack.push(action)
        self.updateActions()
        self.currentFlowgraphScene.update()

    def apply_default_dock_layout(self):
        """Give the side and bottom panels a usable size (the dock separators stay draggable)."""
        docks = [dock for dock in self.findChildren(QDockWidget) if not dock.isFloating()]
        by_area = {}
        for dock in docks:
            by_area.setdefault(self.dockWidgetArea(dock), []).append(dock)
        for dock in docks:
            dock.setMinimumWidth(220)
        left = by_area.get(Qt.DockWidgetArea.LeftDockWidgetArea, [])
        right = by_area.get(Qt.DockWidgetArea.RightDockWidgetArea, [])
        bottom = by_area.get(Qt.DockWidgetArea.BottomDockWidgetArea, [])
        if left:
            self.resizeDocks(left, [300] * len(left), Qt.Orientation.Horizontal)
        if right:
            self.resizeDocks(right, [300] * len(right), Qt.Orientation.Horizontal)
        if bottom:
            self.resizeDocks(bottom, [200] * len(bottom), Qt.Orientation.Vertical)
            if len(bottom) > 1:
                available = max(self.width() - 300 * bool(left) - 300 * bool(right), 600)
                console = [d for d in bottom if d.windowTitle() == "Console"]
                weights = {id(d): (0.6 if d in console else 0.4) for d in bottom}
                self.resizeDocks(bottom, [int(available * weights[id(d)]) for d in bottom],
                                 Qt.Orientation.Horizontal)

    def apply_macos_native_chrome(self):
        """Make the main window and menus behave like a regular macOS document app."""
        actions = self.actions
        actions["preferences"].setMenuRole(QAction.MenuRole.PreferencesRole)
        actions["exit"].setMenuRole(QAction.MenuRole.QuitRole)
        actions["about"].setMenuRole(QAction.MenuRole.AboutRole)
        actions["about_qt"].setMenuRole(QAction.MenuRole.AboutQtRole)

        # Mac keyboards have a Delete key that sends Backspace, and function
        # keys that are taken by the system unless Fn is held.
        actions["delete"].setShortcuts([Keys.Delete, QKeySequence(Qt.Key.Key_Backspace)])
        actions["generate"].setShortcuts([QKeySequence("F5"), QKeySequence("Ctrl+B")])
        actions["execute"].setShortcuts([QKeySequence("F6"), QKeySequence("Ctrl+R")])
        actions["kill"].setShortcuts([QKeySequence("F7"), QKeySequence("Ctrl+.")])

        # One unified title bar + toolbar, no toolbar dragging, icon-only buttons.
        self.setUnifiedTitleAndToolBarOnMac(True)
        for toolbar in self.toolbars.values():
            toolbar.setMovable(False)
            toolbar.setFloatable(False)
            toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.tabWidget.setDocumentMode(True)
        self.setDocumentMode(True)

        self.setup_view_menu_items()
        self.setup_unified_title_bar()
        self.setup_window_menu()

    def restore_macos_layout(self):
        """Restore dock/toolbar state once all docks are registered, or apply defaults."""
        restored = False
        try:
            self.restoreGeometry(self.app.qsettings.value("window/geometry"))
            if self.app.qsettings.value("window/layout_version", 0, type=int) >= MACOS_LAYOUT_VERSION:
                state = self.app.qsettings.value("window/windowState")
                if state:
                    restored = self.restoreState(state)
        except TypeError:
            log.warning("Could not restore window geometry and state.")
        if not restored:
            QTimer.singleShot(0, self.apply_default_dock_layout)
        self.update_sidebar_action()

    vibrancy_wanted = os.environ.get("GRC_MACOS_NO_VIBRANCY") is None

    def setup_sidebar_vibrancy(self):
        """Blur-behind sidebars: Qt paints nothing there, AppKit's material shows through."""
        if getattr(self, "unified_title_bar", False):
            self.style_dock_headers()
        if not (self.vibrancy_wanted and getattr(self, "unified_title_bar", False)):
            return False
        if not macos.add_sidebar_vibrancy(self.winId(), self.width(), self.height()):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            return False
        self.sidebar_vibrancy = True
        clear = QColor(0, 0, 0, 0)

        def make_clear(widget):
            palette = widget.palette()
            for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase):
                palette.setColor(role, clear)
            widget.setPalette(palette)
            widget.setAutoFillBackground(False)

        make_clear(self)
        for dock in self.sidebar_docks():
            for widget in [dock] + dock.findChildren(QWidget):
                make_clear(widget)
        return True

    def style_dock_headers(self):
        """Replace the dock title bars by quiet section headers, as in a native sidebar."""
        for dock in self.findChildren(QDockWidget):
            if dock.titleBarWidget() is not None:
                continue
            header = SidebarHeader(dock.windowTitle().upper())
            dock.setTitleBarWidget(header)

    TRAFFIC_LIGHT_SPACE = 76
    TOOLBAR_HEIGHT = 52

    def setup_unified_title_bar(self):
        """Put the toolbar in the title bar row (full-size content view)."""
        if self.vibrancy_wanted:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if not macos.configure_unified_window(self.winId()):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.vibrancy_wanted = False
            return
        self.unified_title_bar = True
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        first = None
        for toolbar in self.toolbars.values():
            toolbar.setIconSize(QSize(22, 22))
            toolbar.setFixedHeight(self.TOOLBAR_HEIGHT)
            if self.vibrancy_wanted:
                toolbar.setStyleSheet("QToolBar { background: transparent; border: 0; }")
            toolbar.installEventFilter(self)
            if first is None:
                first = toolbar
        if first is not None:
            spacer = QWidget()
            spacer.setFixedWidth(self.TRAFFIC_LIGHT_SPACE)
            spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            first_action = first.actions()[0]
            first.insertWidget(first_action, spacer)
            if "toggle_sidebars" in self.actions:
                sidebar_icon = QIcon.fromTheme("sidebar.left")
                if not sidebar_icon.isNull():
                    self.actions["toggle_sidebars"].setIcon(sidebar_icon)
                first.insertAction(first_action, self.actions["toggle_sidebars"])
                first.insertSeparator(first_action)

        last = list(self.toolbars.values())[-1]
        zoom_group = QFrame()
        zoom_group.setObjectName("macos_zoom_segmented")
        zoom_group.setStyleSheet(
            "#macos_zoom_segmented { border: 1px solid palette(mid); border-radius: 6px; }"
            "#macos_zoom_segmented QToolButton { border: 0; padding: 2px 6px; background: transparent; }"
        )
        zoom_layout = QHBoxLayout(zoom_group)
        zoom_layout.setContentsMargins(2, 1, 2, 1)
        zoom_layout.setSpacing(0)
        for key, symbol, text in (
            ("zoom_out", "minus.magnifyingglass", None),
            ("zoom_original", None, "100%"),
            ("zoom_in", "plus.magnifyingglass", None),
        ):
            btn = QToolButton(zoom_group)
            btn.setDefaultAction(self.actions[key])
            btn.setAutoRaise(True)
            if symbol:
                sym = QIcon.fromTheme(symbol)
                if not sym.isNull():
                    btn.setIcon(sym)
            if text:
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                btn.setText(text)
            else:
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            zoom_layout.addWidget(btn)
        last.addWidget(zoom_group)
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        last.addWidget(stretch)
        self.title_label = QLabel()
        self.title_label.setContentsMargins(0, 0, 14, 0)
        font = self.title_label.font()
        font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(font)
        last.addWidget(self.title_label)
        self.update_window_document()

    def eventFilter(self, obj, event):
        if (getattr(self, "unified_title_bar", False) and isinstance(obj, QToolBar)
                and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
                and event.button() == Qt.MouseButton.LeftButton
                and obj.actionAt(event.position().toPoint()) is None):
            child = obj.childAt(event.position().toPoint())
            if child is None or isinstance(child, (QLabel,)) or child.testAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents) or child.sizePolicy().horizontalPolicy() == \
                    QSizePolicy.Policy.Expanding:
                if event.type() == QEvent.Type.MouseButtonDblClick:
                    macos.zoom_window(self.winId())
                elif self.windowHandle() is not None:
                    self.windowHandle().startSystemMove()
                return True
        return super().eventFilter(obj, event)

    def setup_view_menu_items(self):
        view = self.menus["view"]
        sidebar = QAction("Hide Sidebars", self)
        sidebar.setShortcut(QKeySequence("Ctrl+Alt+S"))
        sidebar.setStatusTip("Show or hide the block library and variable panels")
        sidebar.triggered.connect(self.toggle_sidebars)
        self.actions["toggle_sidebars"] = sidebar

        view.insertAction(self.menus["panels"].menuAction(), sidebar)
        self.hidden_sidebars = []

    def sidebar_docks(self):
        areas = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea)
        return [dock for dock in self.findChildren(QDockWidget)
                if not dock.isFloating() and self.dockWidgetArea(dock) in areas]

    def toggle_sidebars(self):
        visible = [dock for dock in self.sidebar_docks() if dock.isVisible()]
        if visible:
            self.hidden_sidebars = visible
            for dock in visible:
                dock.hide()
        else:
            for dock in self.hidden_sidebars or self.sidebar_docks():
                dock.show()
            self.hidden_sidebars = []
        self.update_sidebar_action()

    def update_sidebar_action(self):
        shown = any(dock.isVisible() for dock in self.sidebar_docks())
        self.actions["toggle_sidebars"].setText("Hide Sidebars" if shown else "Show Sidebars")

    def setup_window_menu(self):
        window = QMenu("&Window", self)
        minimize = QAction("Minimize", self)
        minimize.setShortcut(QKeySequence("Ctrl+M"))
        minimize.triggered.connect(self.showMinimized)
        zoom = QAction("Zoom", self)
        zoom.triggered.connect(lambda: macos.zoom_window(self.winId()))
        previous_tab = QAction("Show Previous Tab", self)
        previous_tab.setShortcuts([QKeySequence("Ctrl+Shift+["), QKeySequence("Ctrl+Alt+Left")])
        previous_tab.triggered.connect(lambda: self.step_tab(-1))
        next_tab = QAction("Show Next Tab", self)
        next_tab.setShortcuts([QKeySequence("Ctrl+Shift+]"), QKeySequence("Ctrl+Alt+Right")])
        next_tab.triggered.connect(lambda: self.step_tab(1))
        front = QAction("Bring All to Front", self)
        front.triggered.connect(macos.bring_all_to_front)
        for action in (minimize, zoom):
            window.addAction(action)
        window.addSeparator()
        window.addAction(previous_tab)
        window.addAction(next_tab)
        window.addSeparator()
        window.addAction(front)
        for action in (minimize, zoom, previous_tab, next_tab, front):
            action.setMenuRole(QAction.MenuRole.NoRole)
        self.menus["window"] = window
        self.menuBar().insertMenu(self.menus["help"].menuAction(), window)

    def step_tab(self, step):
        count = self.tabWidget.count()
        if count > 1:
            self.tabWidget.setCurrentIndex((self.tabWidget.currentIndex() + step) % count)

    def update_window_document(self):
        """Show the open flowgraph's file, proxy icon and edited dot in the title bar."""
        if sys.platform != "darwin" or self.tabWidget.count() == 0:
            return
        scene = self.currentFlowgraphScene
        filename = scene.filename or ""
        self.setWindowFilePath(filename)
        name = os.path.basename(filename) or "Untitled"
        self.setWindowTitle(f"{name}[*]")
        self.setWindowModified(not scene.saved)
        if getattr(self, "title_label", None) is not None:
            self.title_label.setText(name + ("" if scene.saved else " \u2014 Edited"))

    def createActions(self, actions):
        """
        Defines all actions for this view.
        Controller manages connecting signals to slots implemented in the controller
        """
        log.debug("Creating actions")

        # File Actions
        actions["new"] = Action(
            Icons("document-new"),
            _("new"),
            self,
            shortcut=Keys.New,
            statusTip=_("new-tooltip"),
        )

        actions["open"] = Action(
            Icons("document-open"),
            _("open"),
            self,
            shortcut=Keys.Open,
            statusTip=_("open-tooltip"),
        )

        actions["open_recent"] = Action(
            Icons("document-open"),
            _("open_recent"),
            self,
            statusTip=_("open-recent-file"),
        )
        actions["open_recent"].setText("Open recent file")

        actions["example_browser"] = Action(
            _("example_browser"),
            self,
            statusTip=_("example_browser-tooltip"),
        )

        actions["close"] = Action(
            Icons("window-close"),
            _("close"),
            self,
            shortcut=Keys.Close,
            statusTip=_("close-tooltip"),
        )

        actions["close_all"] = Action(
            Icons("window-close"),
            _("close_all"),
            self,
            statusTip=_("close_all-tooltip"),
        )
        actions["save"] = Action(
            Icons("document-save"),
            _("save"),
            self,
            shortcut=Keys.Save,
            statusTip=_("save-tooltip"),
        )

        actions["save_as"] = Action(
            Icons("document-save-as"),
            _("save_as"),
            self,
            shortcut=Keys.SaveAs,
            statusTip=_("save_as-tooltip"),
        )

        actions["save_copy"] = Action(_("save_copy"), self)

        actions["screen_capture"] = Action(
            Icons("camera-photo"),
            _("screen_capture"),
            self,
            shortcut=Keys.Print,
            statusTip=_("screen_capture-tooltip"),
        )

        actions["exit"] = Action(
            Icons("application-exit"),
            _("exit"),
            self,
            shortcut=Keys.Quit,
            statusTip=_("exit-tooltip"),
        )

        # Edit Actions
        actions["undo"] = Action(
            Icons("edit-undo"),
            _("undo"),
            self,
            shortcut=Keys.Undo,
            statusTip=_("undo-tooltip"),
        )

        actions["redo"] = Action(
            Icons("edit-redo"),
            _("redo"),
            self,
            shortcut=Keys.Redo,
            statusTip=_("redo-tooltip"),
        )

        actions["view_undo_stack"] = Action("View undo stack", self)

        actions["cut"] = Action(
            Icons("edit-cut"),
            _("cut"),
            self,
            shortcut=Keys.Cut,
            statusTip=_("cut-tooltip"),
        )

        actions["copy"] = Action(
            Icons("edit-copy"),
            _("copy"),
            self,
            shortcut=Keys.Copy,
            statusTip=_("copy-tooltip"),
        )

        actions["paste"] = Action(
            Icons("edit-paste"),
            _("paste"),
            self,
            shortcut=Keys.Paste,
            statusTip=_("paste-tooltip"),
        )

        actions["delete"] = Action(
            Icons("edit-delete"),
            _("delete"),
            self,
            shortcut=Keys.Delete,
            statusTip=_("delete-tooltip"),
        )

        actions["undo"].setEnabled(False)
        actions["redo"].setEnabled(False)
        actions["cut"].setEnabled(False)
        actions["copy"].setEnabled(False)
        actions["paste"].setEnabled(False)
        actions["delete"].setEnabled(False)

        actions["select_all"] = Action(
            Icons("edit-select_all"),
            _("select_all"),
            self,
            shortcut=Keys.SelectAll,
            statusTip=_("select_all-tooltip"),
        )

        actions["select_none"] = Action(
            _("Select None"), self, statusTip=_("select_none-tooltip")
        )

        actions["autoconnect"] = Action(
            _("Autoconnect"),
            self,
            shortcut="A",
            statusTip=_("Connect the ports of the selected blocks based on their position and type"),
        )
        actions["autoconnect"].setEnabled(False)

        actions["rotate_ccw"] = Action(
            Icons("object-rotate-left"),
            _("rotate_ccw"),
            self,
            shortcut=Keys.MoveToPreviousChar,
            statusTip=_("rotate_ccw-tooltip"),
        )

        actions["rotate_cw"] = Action(
            Icons("object-rotate-right"),
            _("rotate_cw"),
            self,
            shortcut=Keys.MoveToNextChar,
            statusTip=_("rotate_cw-tooltip"),
        )

        actions["rotate_cw"].setEnabled(False)
        actions["rotate_ccw"].setEnabled(False)

        actions["enable"] = Action(_("enable"), self, shortcut="E")
        actions["disable"] = Action(_("disable"), self, shortcut="D")
        actions["bypass"] = Action(_("bypass"), self, shortcut="B")

        # TODO
        actions["block_inc_type"] = Action(_("block_inc_type"), self)
        actions["block_dec_type"] = Action(_("block_dec_type"), self)

        actions["enable"].setEnabled(False)
        actions["disable"].setEnabled(False)
        actions["bypass"].setEnabled(False)

        # TODO
        actions["vertical_align_top"] = Action(_("vertical_align_top"), self)
        actions["vertical_align_middle"] = Action(_("vertical_align_middle"), self)
        actions["vertical_align_bottom"] = Action(_("vertical_align_bottom"), self)

        actions["vertical_align_top"].setEnabled(False)
        actions["vertical_align_middle"].setEnabled(False)
        actions["vertical_align_bottom"].setEnabled(False)

        actions["horizontal_align_left"] = Action(_("horizontal_align_left"), self)
        actions["horizontal_align_center"] = Action(_("horizontal_align_center"), self)
        actions["horizontal_align_right"] = Action(_("horizontal_align_right"), self)

        actions["horizontal_align_left"].setEnabled(False)
        actions["horizontal_align_center"].setEnabled(False)
        actions["horizontal_align_right"].setEnabled(False)

        actions["create_hier"] = Action(_("create_hier_block"), self)
        actions["open_hier"] = Action(_("open_hier_block"), self)
        actions["toggle_source_bus"] = Action(_("toggle_source_bus"), self)
        actions["toggle_sink_bus"] = Action(_("toggle_sink_bus"), self)

        actions["create_hier"].setEnabled(True)
        actions["open_hier"].setEnabled(True)
        actions["toggle_source_bus"].setEnabled(False)
        actions["toggle_sink_bus"].setEnabled(False)

        actions["properties"] = Action(
            Icons("document-properties"),
            _("flowgraph-properties"),
            self,
            statusTip=_("flowgraph-properties-tooltip"),
        )

        actions["properties"].setEnabled(False)

        # View Actions
        actions["zoom_in"] = Action(
            Icons("zoom-in"),
            _("Zoom &in"),
            self
        )
        actions["zoom_in"].setShortcuts([Keys.ZoomIn, "Ctrl+="])
        actions["zoom_out"] = Action(
            Icons("zoom-out"),
            _("Zoom &out"),
            self,
            shortcut=Keys.ZoomOut,
        )
        actions["zoom_original"] = Action(
            Icons("zoom-original"),
            _("O&riginal size"),
            self,
            shortcut="Ctrl+0",
        )

        actions["toggle_grid"] = Action(
            _("toggle_grid"), self, shortcut="G", statusTip=_("toggle_grid-tooltip")
        )

        actions["errors"] = Action(
            Icons("dialog-error"), _("errors"), self, statusTip=_("errors-tooltip")
        )

        actions["find"] = Action(
            Icons("edit-find"),
            _("find"),
            self,
            shortcut=Keys.Find,
            statusTip=_("find-tooltip"),
        )

        # Help Actions
        actions["about"] = Action(
            Icons("help-about"), _("about"), self, statusTip=_("about-tooltip")
        )

        actions["about_qt"] = Action(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMenuButton),
            _("about-qt"),
            self,
            statusTip=_("about-tooltip"),
        )

        actions["generate"] = Action(
            Icons("system-run"),
            _("process-generate"),
            self,
            shortcut="F5",
            statusTip=_("process-generate-tooltip"),
        )

        actions["execute"] = Action(
            Icons("media-playback-start"),
            _("process-execute"),
            self,
            shortcut="F6",
            statusTip=_("process-execute-tooltip"),
        )

        actions["kill"] = Action(
            Icons("process-stop"),
            _("process-kill"),
            self,
            shortcut="F7",
            statusTip=_("process-kill-tooltip"),
        )

        actions["help"] = Action(
            Icons("help-browser"),
            _("help"),
            self,
            shortcut=Keys.HelpContents,
            statusTip=_("help-tooltip"),
        )

        # Tools Actions

        actions["filter_design_tool"] = Action(_("&Filter Design Tool"), self)

        actions["module_browser"] = Action(_("&OOT Module Browser"), self)

        actions["start_profiler"] = Action(_("Start profiler"), self)
        actions["stop_profiler"] = Action(_("Stop profiler"), self)

        # Help Actions

        actions["types"] = Action(_("&Types"), self)

        actions["keys"] = Action(_("&Keys"), self)

        actions["guided_tutorials"] = Action(
            Icons("help-browser"),
            _("Guided Tutorials"),
            self,
            statusTip=_("Open the GNU Radio guided tutorials"),
        )

        actions["get_involved"] = Action(_("&Get Involved"), self)

        actions["preferences"] = Action(
            Icons("preferences-system"),
            _("preferences"),
            self,
            statusTip=_("preferences-tooltip"),
        )

        actions["reload"] = Action(
            Icons("view-refresh"), _("reload"), self, statusTip=_("reload-tooltip")
        )

        # Disable some actions, by default
        actions["save"].setEnabled(True)
        actions["errors"].setEnabled(False)

    def updateDocTab(self):
        pass
        """
        doc_txt = self._app().DocumentationTab._text
        blocks = self.currentFlowgraphScene.selected_blocks()
        if len(blocks) == 1:
            #print(blocks[0].documentation)
            doc_string = blocks[0].documentation['']
            doc_txt.setText(doc_string)
        """

    def updateActions(self):
        """Update the available actions based on what is selected"""

        blocks = self.currentFlowgraphScene.selected_blocks()
        conns = self.currentFlowgraphScene.selected_connections()
        undoStack = self.currentFlowgraphScene.undoStack
        canUndo = undoStack.canUndo()
        canRedo = undoStack.canRedo()
        self.currentFlowgraph.validate()
        valid_fg = self.currentFlowgraph.is_valid()
        saved_fg = self.currentFlowgraphScene.saved

        self.actions["save"].setEnabled(not saved_fg or not self.currentFlowgraphScene.filename)
        self.update_window_document()

        self.actions["undo"].setEnabled(canUndo)
        self.actions["redo"].setEnabled(canRedo)
        self.actions["generate"].setEnabled(valid_fg)
        self.actions["execute"].setEnabled(valid_fg)
        self.actions["errors"].setEnabled(not valid_fg)
        self.actions["kill"].setEnabled(self.currentView.process_is_done())

        self.actions["cut"].setEnabled(False)
        self.actions["copy"].setEnabled(False)
        self.actions["paste"].setEnabled(False)
        self.actions["delete"].setEnabled(False)
        self.actions["autoconnect"].setEnabled(len(blocks) > 1)
        self.actions["rotate_cw"].setEnabled(False)
        self.actions["rotate_ccw"].setEnabled(False)
        self.actions["enable"].setEnabled(False)
        self.actions["disable"].setEnabled(False)
        self.actions["bypass"].setEnabled(False)
        self.actions["properties"].setEnabled(False)
        self.actions["toggle_source_bus"].setEnabled(False)
        self.actions["toggle_sink_bus"].setEnabled(False)

        if self.clipboard:
            self.actions["paste"].setEnabled(True)

        if len(conns) > 0:
            self.actions["delete"].setEnabled(True)

        if len(blocks) > 0:
            self.actions["cut"].setEnabled(True)
            self.actions["copy"].setEnabled(True)
            self.actions["delete"].setEnabled(True)
            self.actions["rotate_cw"].setEnabled(True)
            self.actions["rotate_ccw"].setEnabled(True)
            self.actions["enable"].setEnabled(True)
            self.actions["disable"].setEnabled(True)
            self.actions["bypass"].setEnabled(True)
            self.actions["toggle_source_bus"].setEnabled(True)
            self.actions["toggle_sink_bus"].setEnabled(True)

            self.actions["vertical_align_top"].setEnabled(False)
            self.actions["vertical_align_middle"].setEnabled(False)
            self.actions["vertical_align_bottom"].setEnabled(False)

            self.actions["horizontal_align_left"].setEnabled(False)
            self.actions["horizontal_align_center"].setEnabled(False)
            self.actions["horizontal_align_right"].setEnabled(False)

            if len(blocks) == 1:
                self.actions["properties"].setEnabled(True)

            if len(blocks) > 1:
                self.actions["vertical_align_top"].setEnabled(True)
                self.actions["vertical_align_middle"].setEnabled(True)
                self.actions["vertical_align_bottom"].setEnabled(True)

                self.actions["horizontal_align_left"].setEnabled(True)
                self.actions["horizontal_align_center"].setEnabled(True)
                self.actions["horizontal_align_right"].setEnabled(True)

            for block in blocks:
                if not block.core.can_bypass():
                    self.actions["bypass"].setEnabled(False)
                    break

    def createMenus(self, actions, menus):
        """Setup the main menubar for the application"""
        log.debug("Creating menus")

        # Global menu options
        self.menuBar().setNativeMenuBar(True)

        open_recent = Menu("Open recent file")
        menus["open_recent"] = open_recent

        act_recent_len = len(self.recent_files)
        if act_recent_len > self.max_recent_files:
            act_recent_len = self.max_recent_files

        # Populate recent file list
        if act_recent_len == 0:
            for i in range(self.max_recent_files):
                # Setup invisible dummy entries, that can be filled later, if recently opened files are available
                action = open_recent.addAction("Dummy", self.open_recent_triggered)
                action.setVisible(False)
        else:
            for i in range(act_recent_len):
                open_recent.addAction(self.recent_files[i], self.open_recent_triggered)
            if act_recent_len < self.max_recent_files:
                for i in range(self.max_recent_files - act_recent_len):
                    action = open_recent.addAction("Dummy", self.open_recent_triggered)
                    action.setVisible(False)

        # Setup the file menu
        file = Menu("&File")
        file.addAction(actions["new"])
        file.addAction(actions["open"])
        file.addMenu(open_recent)
        file.addAction(actions["example_browser"])
        file.addAction(actions["close"])
        file.addAction(actions["close_all"])
        file.addSeparator()
        file.addAction(actions["save"])
        file.addAction(actions["save_as"])
        file.addAction(actions["save_copy"])
        file.addSeparator()
        file.addAction(actions["screen_capture"])
        file.addSeparator()
        file.addAction(actions["preferences"])
        file.addSeparator()
        file.addAction(actions["exit"])
        menus["file"] = file

        # Setup the edit menu
        edit = Menu("&Edit")
        edit.addAction(actions["undo"])
        edit.addAction(actions["redo"])
        edit.addAction(actions["view_undo_stack"])
        edit.addSeparator()
        edit.addAction(actions["cut"])
        edit.addAction(actions["copy"])
        edit.addAction(actions["paste"])
        edit.addAction(actions["delete"])
        edit.addAction(actions["select_all"])
        edit.addAction(actions["select_none"])
        edit.addSeparator()
        edit.addAction(actions["autoconnect"])
        edit.addSeparator()
        edit.addAction(actions["rotate_ccw"])
        edit.addAction(actions["rotate_cw"])

        align = Menu("&Align")
        menus["align"] = align
        align.addAction(actions["vertical_align_top"])
        align.addAction(actions["vertical_align_middle"])
        align.addAction(actions["vertical_align_bottom"])
        align.addSeparator()
        align.addAction(actions["horizontal_align_left"])
        align.addAction(actions["horizontal_align_center"])
        align.addAction(actions["horizontal_align_right"])

        edit.addMenu(align)
        edit.addSeparator()
        edit.addAction(actions["enable"])
        edit.addAction(actions["disable"])
        edit.addAction(actions["bypass"])
        edit.addSeparator()

        more = Menu("&More")
        menus["more"] = more
        more.addAction(actions["create_hier"])
        more.addAction(actions["open_hier"])
        more.addAction(actions["toggle_source_bus"])
        more.addAction(actions["toggle_sink_bus"])

        edit.addMenu(more)
        edit.addAction(actions["properties"])
        menus["edit"] = edit

        # Setup submenu
        panels = Menu("&Panels")
        menus["panels"] = panels
        panels.setEnabled(False)

        toolbars = Menu("&Toolbars")
        menus["toolbars"] = toolbars
        toolbars.setEnabled(False)

        # Setup the view menu
        view = Menu("&View")
        view.addMenu(panels)
        view.addMenu(toolbars)
        view.addSeparator()
        view.addAction(actions["zoom_in"])
        view.addAction(actions["zoom_out"])
        view.addAction(actions["zoom_original"])
        view.addSeparator()
        view.addAction(actions["toggle_grid"])
        view.addAction(actions["find"])
        menus["view"] = view

        # Setup the build menu
        build = Menu("&Build")
        build.addAction(actions["errors"])
        build.addAction(actions["generate"])
        build.addAction(actions["execute"])
        build.addAction(actions["kill"])
        menus["build"] = build

        # Setup the tools menu
        tools = Menu("&Tools")
        tools.addAction(actions["filter_design_tool"])
        tools.addAction(actions["module_browser"])
        tools.addSeparator()
        tools.addAction(actions["start_profiler"])
        tools.addAction(actions["stop_profiler"])
        menus["tools"] = tools

        # Setup the help menu
        help = Menu("&Help")
        help.addAction(actions["help"])
        help.addAction(actions["guided_tutorials"])
        help.addAction(actions["types"])
        help.addAction(actions["keys"])
        help.addSeparator()
        help.addAction(actions["get_involved"])
        help.addAction(actions["about"])
        help.addAction(actions["about_qt"])
        menus["help"] = help

    def createToolbars(self, actions, toolbars):
        log.debug("Creating toolbars")

        # Main toolbar
        file = Toolbar("File")
        file.setObjectName("_FileTb")
        file.addAction(actions["new"])
        file.addAction(actions["open"])
        file.addAction(actions["save"])
        file.addAction(actions["close"])
        toolbars["file"] = file

        # Edit toolbar
        edit = Toolbar("Edit")
        edit.setObjectName("_EditTb")
        edit.addAction(actions["undo"])
        edit.addAction(actions["redo"])
        edit.addSeparator()
        edit.addAction(actions["cut"])
        edit.addAction(actions["copy"])
        edit.addAction(actions["paste"])
        edit.addAction(actions["delete"])
        toolbars["edit"] = edit

        # Run Toolbar
        run = Toolbar("Run")
        run.setObjectName("_RunTb")
        run.addAction(actions["errors"])
        run.addAction(actions["generate"])
        run.addAction(actions["execute"])
        run.addAction(actions["kill"])
        toolbars["run"] = run

        # Misc Toolbar
        misc = Toolbar("Misc")
        misc.setObjectName("_MiscTb")
        misc.addAction(actions["reload"])
        toolbars["misc"] = misc

    def createStatusBar(self):
        log.debug("Creating status bar")
        self.statusBar().showMessage(_("ready-message"))

    @staticmethod
    def default_directory():
        """Where file panels start: the working directory, or the home folder for an app launched from Finder."""
        cwd = os.getcwd()
        if sys.platform == "darwin" and cwd in ("/", ""):
            return os.path.expanduser("~")
        return cwd

    def open(self):
        if self.currentFlowgraphScene.filename:
            dirname = os.path.dirname(self.currentFlowgraphScene.filename)
        else:
            dirname = self.default_directory()
        return native_ui.open_file(
            self,
            self.actions["open"].statusTip(),
            dirname,
            "Flow Graph Files (*.grc);;All files (*.*)",
        )

    def save(self):
        return native_ui.save_file(
            self,
            self.actions["save"].statusTip(),
            name_filter="Flow Graph Files (*.grc);;All files (*.*)",
        )

    # Overridden methods
    def addDockWidget(self, location, widget):
        """Adds a dock widget to the view."""
        # This overrides QT's addDockWidget so that a 'show' menu auto can automatically be
        # generated for this action.
        super().addDockWidget(location, widget)
        if "toggle_sidebars" in self.actions:
            widget.visibilityChanged.connect(lambda _visible: self.update_sidebar_action())
        # This is the only instance where a controller holds a reference to a view it does not
        # actually control.
        name = widget.__class__.__name__
        log.debug("Generating show action item for widget: {0}".format(name))

        # Create the new action and wire it to the show/hide for the widget
        self.menus["panels"].addAction(widget.toggleViewAction())
        self.menus["panels"].setEnabled(True)

    def addToolBar(self, toolbar):
        """Adds a toolbar to the main window"""
        # This is also overridden so a show menu item can automatically be added
        super().addToolBar(toolbar)
        name = toolbar.windowTitle()
        log.debug("Generating show action item for toolbar: {0}".format(name))

        # Create the new action and wire it to the show/hide for the widget
        self.menus["toolbars"].addAction(toolbar.toggleViewAction())
        self.menus["toolbars"].setEnabled(True)

    def addMenu(self, menu):
        """Adds a menu to the main window"""
        help = self.menus["help"].menuAction()
        self.menuBar().insertMenu(help, menu)

    def populate_libraries_w_examples(self, example_tuple):
        examples, examples_w_block, designated_examples_w_block = example_tuple
        self.ExampleBrowser.populate(examples)
        self.app.BlockLibrary.populate_w_examples(examples_w_block, designated_examples_w_block)
        self.progress_bar.reset()
        self.progress_bar.hide()
        self.examples_found = True

    @pyqtSlot(tuple)
    def update_progress_bar(self, progress_tuple):
        progress, msg = progress_tuple
        self.progress_bar.show()
        self.progress_bar.setValue(progress)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat(msg)

    def connect_fg_signals(self, scene: FlowgraphScene):
        scene.selectionChanged.connect(self.updateActions)
        scene.selectionChanged.connect(self.updateDocTab)
        scene.itemMoved.connect(self.registerMove)
        scene.newElement.connect(self.registerNewElement)
        scene.deleteElement.connect(self.registerDeleteElement)
        scene.blockPropsChange.connect(self.registerBlockPropsChange)

    # Action Handlers
    def new_triggered(self):
        log.debug("New")
        fg_view = FlowgraphView(self, self.platform)
        fg_view.centerOn(0, 0)
        initial_state = self.platform.parse_flow_graph("")
        fg_view.scene().import_data(initial_state)
        fg_view.scene().saved = sys.platform == "darwin"  # pristine documents close without a prompt
        self.connect_fg_signals(fg_view.scene())
        log.debug("Adding flowgraph view")
        self.tabWidget.addTab(fg_view, "Untitled")
        self.tabWidget.setCurrentIndex(self.tabWidget.count() - 1)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)

    def open_triggered(self, filename=None, save_allowed=True):
        log.debug("open")
        if not filename:
            filename = self.open()

        if filename:
            open_fgs = self.get_open_flowgraphs()
            if filename not in open_fgs:
                self.add_recent_file(filename)
                log.info("Opening flowgraph ({0})".format(filename))
                new_flowgraph = FlowgraphView(self, self.platform)
                initial_state = self.platform.parse_flow_graph(filename)
                self.tabWidget.addTab(new_flowgraph, os.path.basename(filename))
                self.tabWidget.setCurrentIndex(self.tabWidget.count() - 1)
                self.currentFlowgraphScene.import_data(initial_state)
                self.currentFlowgraphScene.filename = filename
                self.connect_fg_signals(self.currentFlowgraphScene)
                self.currentFlowgraphScene.saved = True
                self.currentFlowgraphScene.save_allowed = save_allowed
                self.currentFlowgraphScene.core.rewrite()
                self.currentFlowgraphScene.core.validate()
                for block in self.currentFlowgraphScene.core.blocks:
                    block.gui.create_shapes_and_labels()
                self.currentFlowgraphScene.update_elements_to_draw()
                if hasattr(self.app, 'VariableEditor'):
                    self.app.VariableEditor.set_scene(self.currentFlowgraphScene)
                self.updateActions()
            else:
                self.tabWidget.setCurrentIndex(open_fgs.index(filename))

    def open_example(self, example_path):
        log.debug("open example")
        if example_path:
            self.open_triggered(example_path, False)

    def save_triggered(self):
        if not self.currentFlowgraphScene.save_allowed:
            self.save_as_triggered()
            return
        log.debug("save")
        filename = self.currentFlowgraphScene.filename

        if filename:
            try:
                self.platform.save_flow_graph(filename, self.currentFlowgraph)
                self.currentFlowgraph.grc_file_path = filename
                self.add_recent_file(filename)
            except IOError:
                log.error("Save failed")
                return

            log.info(f"Saved {filename}")
            self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(),
                                                    self.palette().color(QPalette.ColorRole.WindowText))
            self.currentFlowgraphScene.set_saved(True)
        else:
            log.debug("Flowgraph does not have a filename")
            self.save_as_triggered()
        self.updateActions()

    def save_as_triggered(self):
        log.debug("Save As")
        if self.currentFlowgraphScene.filename:
            dirname = os.path.dirname(self.currentFlowgraphScene.filename)
        else:
            dirname = self.default_directory()
        filename = native_ui.save_file(
            self, self.actions["save"].statusTip(), dirname,
            "Flow Graph Files (*.grc)", default_suffix="grc")

        if filename:
            self.currentFlowgraphScene.filename = filename
            try:
                self.platform.save_flow_graph(filename, self.currentFlowgraph)
            except IOError:
                log.error("Save (as) failed")
                return

            log.info(f"Saved (as) {filename}")
            self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(),
                                                    self.palette().color(QPalette.ColorRole.WindowText))
            self.currentFlowgraphScene.set_saved(True)
            self.tabWidget.setTabText(self.tabWidget.currentIndex(), os.path.basename(filename))
            self.add_recent_file(filename)
        else:
            log.debug("Cancelled Save As action")
        self.updateActions()

    def save_copy_triggered(self):
        log.debug("Save Copy")
        filename = native_ui.save_file(
            self, self.actions["save"].statusTip(), "",
            "Flow Graph Files (*.grc)", default_suffix="grc")

        if filename:
            try:
                self.platform.save_flow_graph(filename, self.currentFlowgraph)
            except IOError:
                log.error("Save (copy) failed")

            log.info(f"Saved (copy) {filename}")
        else:
            log.debug("Cancelled Save Copy action")

    def tab_triggered(self, tab_index=None):
        """
        Switches to a tab.

        Parameters:
            tab_index: switches to tab(tab_index)
        """
        log.debug(f"Switching to tab (index {tab_index})")
        if tab_index < 0:
            return
        self.tabWidget.setCurrentIndex(tab_index)
        if hasattr(self.app, 'VariableEditor'):
            self.app.VariableEditor.set_scene(self.currentFlowgraphScene)
        self.updateActions()

    def close_triggered(self, tab_index=False) -> Union[str, bool]:
        """
        Closes a tab.

        Parameters:
            tab_index: specifies which tab to close. If none, close the open tab

        Returns:
            the file path OR True if a tab was closed (False otherwise)
        """
        log.debug(f"Closing a tab (index {tab_index})")

        file_path = self.currentFlowgraphScene.filename
        if tab_index == False:
            tab_index = self.tabWidget.currentIndex()

        if self.currentFlowgraphScene.saved:
            self.tabWidget.removeTab(tab_index)
        else:
            message = None
            if file_path:
                message = f"Save changes to {os.path.basename(file_path)} before closing? Your changes will be lost otherwise."
            else:
                message = "This flowgraph has not been saved"  # TODO: Revise text

            if sys.platform == "darwin":
                name = os.path.basename(file_path) if file_path else "Untitled"
                response = native_ui.ask(
                    self,
                    f"Do you want to save the changes made to \u201c{name}\u201d?",
                    message,
                    QMessageBox.StandardButton.Save |
                    QMessageBox.StandardButton.Discard |
                    QMessageBox.StandardButton.Cancel,
                    default=QMessageBox.StandardButton.Save,
                    icon=QMessageBox.Icon.Warning,
                    informative_text="Your changes will be lost if you don\u2019t save them.",
                )
            else:
                response = QMessageBox.question(
                    None,
                    "Save flowgraph?",
                    message,
                    QMessageBox.StandardButton.Discard |
                    QMessageBox.StandardButton.Cancel |
                    QMessageBox.StandardButton.Save,
                )

            if response == QMessageBox.StandardButton.Discard:
                file_path = self.currentFlowgraphScene.filename
                self.tabWidget.removeTab(tab_index)
            elif response == QMessageBox.StandardButton.Save:
                self.save_triggered()
                if self.currentFlowgraphScene.saved:
                    file_path = self.currentFlowgraphScene.filename
                    self.tabWidget.removeTab(tab_index)
                else:
                    return False
            else:  # Cancel
                return False

        if self.tabWidget.count() == 0:  # No tabs left
            self.new_triggered()
            return True
        else:
            return file_path

    def close_all_triggered(self):
        log.debug("close")

        while self.tabWidget.count() > 1:
            self.close_triggered()
        # Close the final tab
        self.close_triggered()

    def print_triggered(self):
        log.debug("print")

    def screen_capture_triggered(self):
        log.debug("screen capture")
        # TODO: Should be user-set somehow
        background_transparent = True

        if self.currentFlowgraphScene.filename:
            filename = self.currentFlowgraphScene.filename.split('.')[0] + '.pdf'
        else:
            filename = 'Untitled.pdf'
        file_path = native_ui.save_file(
            self,
            self.actions["screen_capture"].statusTip(),
            filename,
            "PDF files (*.pdf);;PNG files (*.png);;SVG files (*.svg)",
        )
        if file_path:
            try:
                Utils.make_screenshot(
                    self.currentView, file_path, background_transparent
                )
            except ValueError:
                log.error("Failed to generate screenshot")

    def undo_triggered(self):
        log.debug("undo")
        self.currentFlowgraphScene.undoStack.undo()
        self.currentFlowgraphScene.update()
        self.updateActions()

    def redo_triggered(self):
        log.debug("redo")
        self.currentFlowgraphScene.undoStack.redo()
        self.updateActions()

    def view_undo_stack_triggered(self):
        log.debug("view_undo_stack")
        self.undoView = QUndoView(self.currentFlowgraphScene.undoStack)
        self.undoView.setWindowTitle("Undo stack")
        self.undoView.show()

    def cut_triggered(self):
        log.debug("cut")
        self.copy_triggered()
        self.delete_triggered()
        self.updateActions()

    def copy_triggered(self):
        log.debug("copy")
        self.clipboard = self.currentFlowgraphScene.copy_to_clipboard()
        self.updateActions()

    def paste_triggered(self):
        log.debug("paste")
        if self.clipboard:
            self.currentFlowgraphScene.paste_from_clipboard(self.clipboard)
            self.currentFlowgraphScene.update()
        else:
            log.debug("clipboard is empty")

    def delete_triggered(self):
        log.debug("delete")
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        action = DeleteElementAction(self.currentFlowgraphScene)
        self.currentFlowgraphScene.undoStack.push(action)
        self.updateActions()
        self.currentFlowgraphScene.update()

    def select_all_triggered(self):
        log.debug("select_all")
        self.currentFlowgraphScene.select_all()
        self.updateActions()

    def select_none_triggered(self):
        log.debug("select_none")
        self.currentFlowgraphScene.clearSelection()
        self.updateActions()

    def rotate_ccw_triggered(self):
        # Pass to Undo/Redo
        log.debug("rotate_ccw")
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        rotateCommand = RotateAction(self.currentFlowgraphScene, -90)
        self.currentFlowgraphScene.undoStack.push(rotateCommand)
        self.updateActions()
        self.currentFlowgraphScene.update()

    def autoconnect_triggered(self):
        log.debug("autoconnect")
        port_pairs = self.currentFlowgraphScene.autoconnect_candidates()
        if not port_pairs:
            return
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        self.currentFlowgraphScene.undoStack.push(AutoconnectAction(self.currentFlowgraphScene, port_pairs))
        self.updateActions()

    def rotate_cw_triggered(self):
        # Pass to Undo/Redo
        log.debug("rotate_cw")
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        rotateCommand = RotateAction(self.currentFlowgraphScene, 90)
        self.currentFlowgraphScene.undoStack.push(rotateCommand)
        self.updateActions()
        self.currentFlowgraphScene.update()

    def toggle_source_bus_triggered(self):
        log.debug("toggle_source_bus")
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        bussifyCommand = BussifyAction(self.currentFlowgraphScene, 'source')
        self.currentFlowgraphScene.undoStack.push(bussifyCommand)
        self.updateActions()
        self.currentFlowgraphScene.update()

    def toggle_sink_bus_triggered(self):
        log.debug("toggle_sink_bus")
        self.currentFlowgraphScene.set_saved(False)
        self.tabWidget.tabBar().setTabTextColor(self.tabWidget.currentIndex(), Qt.GlobalColor.red)
        bussifyCommand = BussifyAction(self.currentFlowgraphScene, 'sink')
        self.currentFlowgraphScene.undoStack.push(bussifyCommand)
        self.updateActions()
        self.currentFlowgraphScene.update()

    def errors_triggered(self):
        log.debug("errors")
        err = ErrorsDialog(self.currentFlowgraph)
        err.exec()

    def module_browser_triggered(self):
        log.debug("oot browser")
        self.OOTBrowser.show()

    def zoom_in_triggered(self):
        log.debug("zoom in")
        self.currentView.zoom(1.1)

    def zoom_out_triggered(self):
        log.debug("zoom out")
        self.currentView.zoom(1.0 / 1.1)

    def zoom_original_triggered(self):
        log.debug("zoom to original size")
        self.currentView.zoomOriginal()

    def find_triggered(self):
        log.debug("find block")
        self._app().BlockLibrary._search_bar.clear()
        self._app().BlockLibrary._search_bar.setFocus()
        self._app().BlockLibrary.reset()

    def get_involved_triggered(self):
        log.debug("get involved")
        native_ui.info(
            self,
            "Get Involved Instructions",
            """\
            <b>Welcome to the GNU Radio Community!</b><br/><br/>
            For more details on contributing to GNU Radio and getting engaged with our great community visit <a href="https://wiki.gnuradio.org/index.php/HowToGetInvolved">here</a>.<br/><br/>
            You can also join our <a href="https://chat.gnuradio.org/">Matrix chat server</a>, IRC Channel (#gnuradio) or contact through our <a href="https://lists.gnu.org/mailman/listinfo/discuss-gnuradio">mailing list (discuss-gnuradio)</a>.
        """,
            rich_text=True,
        )

    def guided_tutorials_triggered(self):
        log.debug("guided tutorials")
        QDesktopServices.openUrl(
            QUrl("https://tutorials.gnuradio.org")
        )

    def about_triggered(self):
        log.debug("about")
        config = self.platform.config
        py_version = sys.version.split()[0]
        if sys.platform == "darwin" and macos.show_about_panel(
                "GNU Radio Companion", str(config.version), f"Python {py_version}",
                "Copyright \u00a9 Free Software Foundation, Inc.",
                "GNU Radio is free software released under the GNU General Public License.\n"
                "https://www.gnuradio.org"):
            return
        QMessageBox.about(
            self, "About GNU Radio", f"GNU Radio {config.version} (Python {py_version})"
        )

    def about_qt_triggered(self):
        log.debug("about_qt")
        QApplication.instance().aboutQt()

    def properties_triggered(self):
        log.debug("properties")
        if len(self.currentFlowgraphScene.selected_blocks()) != 1:
            log.warning("Opening Properties even though selected_blocks() != 1 ")
        self.currentFlowgraphScene.selected_blocks()[0].open_properties()

    def enable_triggered(self):
        log.debug("enable")
        self.currentFlowgraphScene.set_saved(False)
        all_enabled = True
        for block in self.currentFlowgraphScene.selected_blocks():
            if not block.core.state == "enabled":
                all_enabled = False
                break

        if not all_enabled:
            cmd = EnableAction(self.currentFlowgraphScene)
            self.currentFlowgraphScene.undoStack.push(cmd)

        self.currentFlowgraphScene.update()
        self.updateActions()

    def disable_triggered(self):
        log.debug("disable")
        self.currentFlowgraphScene.set_saved(False)
        all_disabled = True
        for g_block in self.currentFlowgraphScene.selected_blocks():
            if not g_block.core.state == "disabled":
                all_disabled = False
                break

        if not all_disabled:
            cmd = DisableAction(self.currentFlowgraphScene)
            self.currentFlowgraphScene.undoStack.push(cmd)

        self.currentFlowgraphScene.update()
        self.updateActions()

    def bypass_triggered(self):
        log.debug("bypass")
        all_bypassed = True
        for g_block in self.currentFlowgraphScene.selected_blocks():
            if not g_block.core.state == "bypassed":
                all_bypassed = False
                break

        if not all_bypassed:
            cmd = BypassAction(self.currentFlowgraphScene)
            self.currentFlowgraphScene.undoStack.push(cmd)

        self.currentFlowgraphScene.update()
        self.updateActions()

    def block_inc_type_triggered(self):
        log.debug("block_inc_type")

    def block_dec_type_triggered(self):
        log.debug("block_dec_type")

    def generate_triggered(self, called_from_exec=False):
        log.debug("generate")
        scene = self.currentFlowgraphScene
        if not scene.saved or not scene.filename:
            self.save_triggered()
        if not scene.saved or not scene.filename:  # The line above was cancelled
            log.error("Cannot generate a flowgraph without saving first")
            return

        filename = self.currentFlowgraphScene.filename
        self.currentFlowgraph.grc_file_path = filename

        if self.currentFlowgraph.get_option('generate_options').startswith('hb'):
            output_dir = paths.get_state_directory()
        else:
            output_dir = os.path.dirname(filename)

        generator = self.platform.Generator(
            self.currentFlowgraph, output_dir
        )
        generator.write(called_from_exec)
        self.currentView.generator = generator
        log.info(f"Generated {generator.file_path}")

    def execute_triggered(self):
        log.debug("execute")
        if self.currentView.process_is_done():
            self.generate_triggered(called_from_exec=True)
            if self.currentView.generator:
                xterm = self.app.qsettings.value("grc/xterm_executable", "")
                '''if self.config.xterm_missing() != xterm:
                    if not os.path.exists(xterm):
                        Dialogs.show_missing_xterm(main, xterm)
                    self.config.xterm_missing(xterm)'''
                if self.currentFlowgraphScene.saved and self.currentFlowgraphScene.filename:
                    # Save config before execution
                    # self.config.save()
                    ExecFlowGraphThread(
                        view=self.currentView,
                        flowgraph=self.currentFlowgraph,
                        xterm_executable=xterm,
                        update_gui_callback=self.updateActions
                    )

    def kill_triggered(self):
        log.debug("kill")

    def reload_triggered(self):
        log.debug("Reload hier blocks")
        self.app.BlockLibrary.reload_blocks()

        range_ = self.tabWidget.count()
        for idx in range(range_):
            self.rebuild_tab(idx)

        self.updateActions()

    def create_hier_triggered(self):

        selected_blocks = []

        source_fg = self.currentFlowgraphScene
        for block in source_fg.selected_blocks():
            selected_blocks.append(block.core)

        # Generate new page
        self.new_triggered()
        sink_fg = self.currentFlowgraphScene

        # Set flow graph to heir block type
        top_block = sink_fg.core.get_block(Constants.DEFAULT_FLOW_GRAPH_ID)
        # Check if hb or hb_qt is required
        gen_opts = 'hb'
        for block in selected_blocks:
            if block.label.upper().startswith('QT GUI'):
                gen_opts = 'hb_qt_gui'
                break
        top_block.params['generate_options'].set_value(gen_opts)

        # this needs to be a unique name
        top_block.params['id'].set_value('new_hier')
        # Remove default samp_rate
        remove_me = sink_fg.core.get_block("samp_rate")
        sink_fg.remove_element(remove_me.gui)

        self.clipboard = source_fg.copy_to_clipboard()
        self.paste_triggered()

        # For each variable generate a possible parameter block
        # The user has to decide if the hier block should use a variable or a paramter

        for variable in sink_fg.core.get_variables():
            id = sink_fg.add_block(
                'parameter',
                (variable.states['coordinate'][0] + variable.gui.width + 50, variable.states['coordinate'][1] + 50))
            param_block = sink_fg.core.get_block(id)
            param_block.params['id'].set_value(variable.name)

        for connection in source_fg.core.connections:
            # Get id of connected blocks
            source = connection.source_block
            sink = connection.sink_block
            if source not in selected_blocks and sink in selected_blocks:
                # Create pad source and add to canvas
                pad_source_key = int(connection.sink_port.key)
                pad_id = sink_fg.add_block(
                    'pad_source',
                    (source.states['coordinate'][0], source.states['coordinate'][1] + pad_source_key * 50))
                pad_block = sink_fg.core.get_block(pad_id)
                pad_source = pad_block.sources[0]
                sink_block = sink_fg.core.get_block(sink_fg.core.blocks[selected_blocks.index(sink) + 1].name)
                sink = sink_block.sinks[pad_source_key]
                # ensure the port types match
                if pad_source.dtype != sink.dtype:
                    if pad_source.dtype == 'complex' and sink.dtype == 'fc32':
                        pass
                    else:
                        pad_block.params['type'].value = sink.dtype
                        pad_source.dtype = sink.dtype
                new_connection = sink_fg.core.connect(pad_source, sink)
                sink_fg.addItem(new_connection.gui)

            elif sink not in selected_blocks and source in selected_blocks:
                # Create Pad Sink and add to canvas
                pad_sink_key = int(connection.source_port.key)
                pad_id = sink_fg.add_block(
                    'pad_sink', (sink.states['coordinate'][0], sink.states['coordinate'][1] + pad_sink_key * 50))
                pad_block = sink_fg.core.get_block(pad_id)
                pad_sink = pad_block.sinks[0]
                source_block = sink_fg.core.get_block(sink_fg.core.blocks[selected_blocks.index(source) + 1].name)
                source = source_block.sources[pad_sink_key]
                # ensure the port types match
                if pad_sink.dtype != source.dtype:
                    if pad_sink.dtype == 'complex' and source.dtype == 'fc32':
                        pass
                    else:
                        pad_block.params['type'].value = source.dtype
                        pad_sink.dtype = source.dtype
                new_connection = sink_fg.core.connect(source, pad_sink)
                sink_fg.addItem(new_connection.gui)

        sink_fg.clearSelection()
        sink_fg.update()
        self.updateActions()

    def open_hier_triggered(self):
        log.debug("Open hier block triggered")
        for block in self.currentFlowgraphScene.selected_blocks():
            grc_source = block.core.extra_data.get('grc_source', '')
            if grc_source:
                self.open_triggered(grc_source)

    def rebuild_tab(self, idx):
        fgscene = self.tabWidget.widget(idx).scene()
        log.info("Rebuilding flowgraph ({0})".format(fgscene.filename))
        data = fgscene.core.export_data()
        fgblocks = fgscene.core.blocks
        for block in fgblocks:
            fgscene.removeItem(block.gui)
        for conn in fgscene.core.connections:
            fgscene.removeItem(conn.gui)
        fgscene.import_data(data)

    def add_recent_file(self, file_name):
        # double check file_name
        if os.path.exists(file_name):
            recent_files = self.recent_files
            if file_name in recent_files:
                recent_files.remove(file_name)  # Attempt removal
            recent_files.insert(0, file_name)  # Insert at start
            self.recent_files = recent_files[:self.max_recent_files]

            # Now add files to menu entries
            actions_list = self.menus["open_recent"].actions()
            for i in range(len(self.recent_files)):
                actions_list[i].setText(self.recent_files[i])
                actions_list[i].setVisible(True)

    def show_help(parent):
        """Display basic usage tips."""
        message = """\
            <b>Usage Tips</b>
            \n\
            <u>Add block</u>: drag and drop or double click a block in the block
        selection window.
            <u>Rotate block</u>: Select a block, press left/right on the keyboard.
            <u>Change type</u>: Select a block, press up/down on the keyboard.
            <u>Edit parameters</u>: double click on a block in the flow graph.
            <u>Make connection</u>: click on the source port of one block, then
        click on the sink port of another block.
            <u>Remove connection</u>: select the connection and press delete, or
        drag the connection.
            \n\
            *Press Ctrl+K or see menu for Keyboard - Shortcuts
            \
        """

        native_ui.info(self, "Help", message, rich_text=True)

    def types_triggered(self):
        log.debug("types")
        colors = [(name, color) for name, key, sizeof, color in Constants.CORE_TYPES]

        message = """
        <table>
        <tbody>
        """

        message += "\n".join(
            '<tr bgcolor="{color}"><td><tt>{name}</tt></td></tr>'
            "".format(color=color, name=name)
            for name, color in colors
        )
        message += "</tbody></table>"
        native_ui.info(self, "Stream Types", message, rich_text=True)

    def keys_triggered(self):
        log.debug("keys")

        message = """\
            <b>Keyboard Shortcuts</b>
            <br><br>
            <u>Ctrl+N</u>: Create a new flowgraph.<br>
            <u>Ctrl+O</u>: Open an existing flowgraph.<br>
            <u>Ctrl+S</u>: Save the current flowgraph or save as for new.<br>
            <u>Ctrl+W</u>: Close the current flowgraph.<br>
            <u>Ctrl+Z</u>: Undo a change to the flowgraph.<br>
            <u>Ctrl+Y</u>: Redo a change to the flowgraph.<br>
            <u>Ctrl+A</u>: Selects all blocks and connections.<br>
            <u>Ctrl+P</u>: Screen Capture of the Flowgraph.<br>
            <u>Ctrl+Shift+P</u>: Save the console output to file.<br>
            <u>Ctrl+L</u>: Clear the console.<br>
            <u>Ctrl+E</u>: Show variable editor.<br>
            <u>Ctrl+F</u>: Search for a block by name.<br>
            <u>Ctrl+Q</u>: Quit.<br>
            <u>F1</u>    : Help menu.<br>
            <u>F5</u>    : Generate the Flowgraph.<br>
            <u>F6</u>    : Execute the Flowgraph.<br>
            <u>F7</u>    : Kill the Flowgraph.<br>
            <u>Ctrl+Shift+S</u>: Save as the current flowgraph.<br>
            <u>Ctrl+Shift+D</u>: Create a duplicate of current flow graph.<br>

            <u>Ctrl+X/C/V</u>: Edit-cut/copy/paste.<br>
            <u>Ctrl+D/B/R</u>: Toggle visibility of disabled blocks or
                    connections/block tree widget/console.<br>
            <u>Shift+T/M/B/L/C/R</u>: Vertical Align Top/Middle/Bottom and
                    Horizontal Align Left/Center/Right respectively of the
                    selected block.<br>
            <u>Ctrl+0</u>: Reset the zoom level<br>
            <u>Ctrl++/-</u>: Zoom in and out<br>
            \
            """

        native_ui.info(self, "Keyboard shortcuts", message, rich_text=True)

    def preferences_triggered(self):
        log.debug("preferences")
        prefs_dialog = PreferencesDialog(self.app.qsettings)
        if prefs_dialog.exec():  # User pressed Save
            prefs_dialog.save_all()
            self.currentFlowgraphScene.update()

    def open_recent_triggered(self):
        log.debug("open_recent")
        self.open_triggered(self.sender().text())

    def example_browser_triggered(self, key_filter: Union[str, None] = None):
        log.debug("example-browser")
        if self.examples_found:
            self.ExampleBrowser.reset()
            ex_dialog = ExampleBrowserDialog(self.ExampleBrowser)
            if len(ex_dialog.browser.examples_dict) == 0:
                native_ui.info(
                    self,
                    "GRC: No examples found",
                    "GRC did not find any examples. Please ensure that the example path in grc.conf is correct.",
                )
                return

            if isinstance(key_filter, str):
                if not ex_dialog.browser.filter_(key_filter):
                    native_ui.info(
                        self,
                        "GRC: No examples",
                        "There are no examples for this block.",
                    )
                    return
            else:
                ex_dialog.browser.reset()

            ex_dialog.exec()
        else:
            native_ui.info(
                self,
                "GRC still indexing examples",
                "GRC is still indexing examples, please try again shortly.",
            )

    def exit_triggered(self):
        log.debug("exit")

        files_open = []
        range_ = reversed(range(self.tabWidget.count()))
        for idx in range_:  # Close the rightmost first. It'll be the first element in files_open
            tab = self.tabWidget.widget(idx)
            file_path = self.currentFlowgraphScene.filename
            if file_path:
                files_open.append(file_path)
            closed = self.close_triggered()
            if closed == False:
                # We cancelled closing a tab. We don't want to close the application
                return

        # Save the panel settings
        # Console
        self.app.qsettings.setValue('appearance/display_console', not self.app.Console.isHidden())
        # Block Library
        self.app.qsettings.setValue('appearance/display_blocklibrary', not self.app.BlockLibrary.isHidden())
        # Wiki
        self.app.qsettings.setValue('appearance/display_wiki', not self.app.WikiTab.isHidden())
        # Variable Editor
        self.app.qsettings.setValue('appearance/display_variable_editor', not self.app.VariableEditor.isHidden())

        # Write the leftmost tab to file first
        self.app.qsettings.setValue('window/files_open', list(reversed(files_open)))
        self.app.qsettings.setValue('window/windowState', self.saveState())
        self.app.qsettings.setValue('window/layout_version', MACOS_LAYOUT_VERSION)
        self.app.qsettings.setValue('window/geometry', self.saveGeometry())
        self.app.qsettings.sync()

        # TODO: Make sure all flowgraphs have been saved
        self.app.exit()

    def closeEvent(self, evt):
        log.debug("Close Event")
        self.exit_triggered()
        evt.ignore()

    def help_triggered(self):
        log.debug("help")
        self.show_help()

    def report_triggered(self):
        log.debug("report")

    def library_triggered(self):
        log.debug("library_triggered")

    def library_toggled(self):
        log.debug("library_toggled")

    def filter_design_tool_triggered(self):
        log.debug("filter_design_tool")
        subprocess.Popen(
            "gr_filter_design",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def start_profiler_triggered(self):
        log.info("Starting profiler")
        self.profiler.enable()

    def stop_profiler_triggered(self):
        self.profiler.disable()
        log.info("Stopping profiler")
        stats = pstats.Stats(self.profiler)
        stats.dump_stats('stats.prof')

    def get_open_flowgraphs(self):
        index = self.tabWidget.count()
        if index == 0:
            return []
        result = []
        range_ = range(index)
        for fg_nr in range_:
            self.tabWidget.setCurrentIndex(fg_nr)
            result.append(self.currentFlowgraphScene.filename)
        return result
