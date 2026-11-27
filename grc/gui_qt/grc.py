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
import importlib.abc
import logging
import textwrap
import os
import sys

from PyQt6.QtCore import QEvent, QSettings, QTimer, Qt, QT_VERSION_STR, PYQT_VERSION_STR
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

# Custom modules
from ..core.utils import macos
from . import components
from .helpers.profiling import StopWatch

# Logging
# Setup the logger to use a different name than the file name
log = logging.getLogger("grc.application")

APP_DISPLAY_NAME = "GNU Radio Companion"


class _ForeignQtBlocker(importlib.abc.MetaPathFinder):
    """Refuse to load a second Qt major version into this PyQt6 process.

    Block definitions run their `imports` (for example "from PyQt5 import Qt"
    of blocks written for a Qt5 GNU Radio) inside GRC to evaluate parameters.
    Qt5 and Qt6 in one process register the same Objective-C classes on macOS
    and crash; GRC treats a failed import there as a harmless hier-block
    import, so the failure is safe.
    """

    PREFIXES = ("PyQt5", "PySide2")

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.PREFIXES:
            raise ImportError(f"{fullname} cannot be loaded into the PyQt6 based GNU Radio Companion")
        return None


class Application(QApplication):
    """
    This is the main QT application for GRC.
    It handles setting up the application components and actions and handles communication between different components in the system.
    """

    def __init__(self, settings, platform, file_path):
        # Note. Logger must have the correct naming convention to share handlers
        log.debug("__init__")
        self.settings = settings
        self.platform = platform
        config = platform.config

        self.qsettings = QSettings(config.gui_prefs_file, QSettings.Format.IniFormat)
        log.debug(f"Using QSettings from {config.gui_prefs_file}")
        os.environ["QT_SCALE_FACTOR"] = self.qsettings.value('appearance/qt_scale_factor', "1.0", type=str)

        if sys.platform == "darwin" and not any(isinstance(f, _ForeignQtBlocker) for f in sys.meta_path):
            sys.meta_path.insert(0, _ForeignQtBlocker())

        log.debug("Creating QApplication instance")
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        self.is_macos = sys.platform == "darwin"
        if self.is_macos:
            macos.set_process_name(APP_DISPLAY_NAME)
        QApplication.__init__(self, settings.argv)
        QApplication.setApplicationName(APP_DISPLAY_NAME)
        QApplication.setApplicationDisplayName(APP_DISPLAY_NAME)
        QApplication.setOrganizationDomain("gnuradio.org")
        macos.setup_application_identity(settings.path.ICON)

        self.theme = "light"
        if self.is_macos:
            # Native look: the macOS widget style and system palette follow the
            # Light/Dark appearance chosen in System Settings.
            self.setStyle("macos")
            self.theme = self.system_theme()
            self.styleHints().colorSchemeChanged.connect(self.system_theme_changed)
        elif self.qsettings.value("appearance/theme", "dark") == "dark":
            try:
                import qdarkstyle

                self.setStyleSheet(qdarkstyle.load_stylesheet())
                self.theme = "dark"
            except ImportError:
                log.warning("Did not find QDarkstyle. Dark mode disabled")

        # Load the main view class and initialize QMainWindow
        log.debug("ARGV - {0}".format(settings.argv))
        log.debug("INSTALL_DIR - {0}".format(settings.path.INSTALL))

        # Global signals
        self.signals = {}

        # Setup the main application window
        log.debug("Creating main application window")
        stopwatch = StopWatch()
        self.MainWindow = components.MainWindow(file_path)
        stopwatch.lap("mainwindow")
        level_str = self.qsettings.value('grc/console_log_level', "info", type=str)
        if level_str == "info":
            self.Console = components.Console(logging.INFO)
        else:  # level_str == "debug"
            self.Console = components.Console(logging.DEBUG)
        stopwatch.lap("console")
        self.BlockLibrary = components.BlockLibrary()
        stopwatch.lap("blocklibrary")
        # self.DocumentationTab = components.DocumentationTab()
        # stopwatch.lap('documentationtab')
        self.WikiTab = components.WikiTab()
        stopwatch.lap("wikitab")
        self.VariableEditor = components.VariableEditor()
        stopwatch.lap("variable_editor")
        self.VariableEditor.set_scene(self.MainWindow.currentFlowgraphScene)
        self.VariableEditor.all_editor_actions.connect(self.MainWindow.handle_editor_action)
        self.MainWindow.ExampleBrowser.set_library(self.BlockLibrary)

        # Debug times
        log.debug(
            "Loaded MainWindow controller - {:.4f}s".format(
                stopwatch.elapsed("mainwindow")
            )
        )
        log.debug(
            "Loaded Console component - {:.4f}s".format(stopwatch.elapsed("console"))
        )
        log.debug(
            "Loaded BlockLibrary component - {:.4}s".format(
                stopwatch.elapsed("blocklibrary")
            )
        )
        # log.debug("Loaded DocumentationTab component - {:.4}s".format(stopwatch.elapsed("documentationtab")))

        # Print Startup information once everything has loaded
        self.Console.enable()

        block_paths = "\n\t".join(config.block_paths)
        log_files = ''
        for handler in logging.getLogger("grc").handlers:
            if type(handler) == logging.FileHandler:
                log_files = log_files + f"\n\t{handler.baseFilename} ({logging.getLevelName(handler.level)})"

        welcome = (
            f"<<< Welcome to {config.name} {config.version} >>>\n\n"
            f"{'PyQt ' + PYQT_VERSION_STR}\n"
            f"GUI preferences file: {self.qsettings.fileName()}\n"
            f"Log files: {'None' if not log_files else log_files}\n"
            f"Block paths:\n\t{block_paths}\n"
            f"Using {QIcon.themeName()} icon theme\n"
        )
        log.info(textwrap.dedent(welcome))

        log.debug(f'devicePixelRatio {self.MainWindow.screen().devicePixelRatio()}')

        if (not self.is_macos) and (self.qsettings.value("appearance/theme", "dark") == "dark") and (self.theme == "light"):
            log.warning("Could not apply dark theme. Is QDarkStyle installed?")

    def system_theme(self):
        """"dark" or "light", following the macOS appearance"""
        if self.styleHints().colorScheme() == Qt.ColorScheme.Dark:
            return "dark"
        return "light"

    def system_theme_changed(self, *_):
        self.theme = self.system_theme()
        log.debug("System appearance changed to %s", self.theme)
        tabs = self.MainWindow.tabWidget
        for index in range(tabs.count()):
            tabs.widget(index).apply_theme()

    def event(self, event):
        # Finder (double-click / "Open With" / drop on the Dock icon) and
        # `open file.grc` deliver files as FileOpen events, not as arguments.
        if event.type() == QEvent.Type.FileOpen and event.file():
            tabs = self.MainWindow.tabWidget
            scene = self.MainWindow.currentFlowgraphScene
            replace_blank = (tabs.count() == 1 and not scene.filename
                             and not scene.undoStack.canUndo())
            self.MainWindow.open_triggered(event.file())
            if replace_blank and tabs.count() == 2:
                tabs.removeTab(0)
            self.MainWindow.raise_()
            self.MainWindow.activateWindow()
            return True
        return super().event(event)

    # Global registration functions
    #  - Handles the majority of child controller interaciton

    def registerSignal(self, signal):
        pass

    def registerDockWidget(self, widget, location=0):
        """Allows child controllers to register a widget that can be docked in the main window"""
        # TODO: Setup the system to automatically add new "Show <View Name>" menu items when a new
        # dock widget is added.
        log.debug(
            "Registering widget ({0}, {1})".format(widget.__class__.__name__, location)
        )
        self.MainWindow.registerDockWidget(location, widget)

    def registerMenu(self, menu):
        """Allows child controllers to register an a menu rather than just a single action"""
        # TODO: Need to have several capabilities:
        #  - Menu's need the ability to have a priority for ordering
        #  - When registering, the controller needs to specific target menu
        #  - Automatically add seporators (unless it is the first or last items)
        #  - Have the ability to add it as a sub menu
        #  - MainWindow does not need to call register in the app controller. It can call directly
        #  - Possibly view sidebars and toolbars as submenu
        #  - Have the ability to create an entirely new menu
        log.debug("Registering menu ({0})".format(menu.title()))
        self.MainWindow.registerMenu(menu)

    def registerAction(self, action, menu):
        """Allows child controllers to register a global action shown in the main window"""
        pass

    def run(self):
        """Launches the main QT event loop"""
        # Show the main window after everything is initialized.
        self.MainWindow.show()
        self.MainWindow.raise_()
        self.MainWindow.activateWindow()
        if self.is_macos:
            # LaunchServices may still be finishing the launch (for example
            # after `open`), so bring the window forward again once the event
            # loop runs, for a short while only so later focus is not stolen.
            for delay_ms in (0, 250, 750):
                QTimer.singleShot(delay_ms, self._bring_to_front)
        return self.exec()

    def _bring_to_front(self):
        window = self.MainWindow
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()
        macos.activate_application()
