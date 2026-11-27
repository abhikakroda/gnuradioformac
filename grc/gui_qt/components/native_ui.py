"""Native macOS presentation helpers for the Qt front end.

Copyright 2026 Free Software Foundation, Inc.
This file is part of GNU Radio

SPDX-License-Identifier: GPL-3.0-or-later

On macOS file panels and alerts are presented as sheets that slide out of the
window they belong to.  Qt does this for window-modal dialogs, but the blocking
static helpers (QFileDialog.getOpenFileName, QMessageBox.question, ...) are
application-modal.  The functions here wrap the window-modal form in a local
event loop so callers keep their simple synchronous code, and fall back to the
regular static helpers on every other platform.
"""

import sys

from PyQt6.QtCore import QEventLoop, Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox

IS_MAC = sys.platform == "darwin"


def _run_sheet(dialog):
    loop = QEventLoop()
    dialog.finished.connect(loop.quit)
    dialog.open()
    loop.exec()
    return dialog.result()


def _sheet_parent(parent):
    return parent.window() if parent is not None else None


def open_file(parent, caption, directory="", name_filter=""):
    """Choose an existing file.  Returns the path or an empty string."""
    sheet_parent = _sheet_parent(parent)
    if not (IS_MAC and sheet_parent is not None and sheet_parent.isVisible()):
        path, _ = QFileDialog.getOpenFileName(parent, caption, directory, name_filter)
        return path
    dialog = QFileDialog(sheet_parent, caption, directory, name_filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    if _run_sheet(dialog) == QFileDialog.DialogCode.Accepted and dialog.selectedFiles():
        return dialog.selectedFiles()[0]
    return ""


def save_file(parent, caption, directory="", name_filter="", default_suffix=""):
    """Choose a file name to write.  Returns the path or an empty string."""
    sheet_parent = _sheet_parent(parent)
    if not (IS_MAC and sheet_parent is not None and sheet_parent.isVisible()):
        dialog = QFileDialog(parent, caption, directory, name_filter)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        if default_suffix:
            dialog.setDefaultSuffix(default_suffix)
        if dialog.exec() == QFileDialog.DialogCode.Accepted and dialog.selectedFiles():
            return dialog.selectedFiles()[0]
        return ""
    dialog = QFileDialog(sheet_parent, caption, directory, name_filter)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    if default_suffix:
        dialog.setDefaultSuffix(default_suffix)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    if _run_sheet(dialog) == QFileDialog.DialogCode.Accepted and dialog.selectedFiles():
        return dialog.selectedFiles()[0]
    return ""


def ask(parent, title, text, buttons, default=None, icon=QMessageBox.Icon.Question,
        informative_text=""):
    """Show an alert and return the StandardButton that was pressed.

    On macOS the alert is a sheet attached to the parent window: title is the
    bold message, text goes into the informative text underneath it.
    """
    sheet_parent = _sheet_parent(parent)
    box = QMessageBox(sheet_parent)
    box.setIcon(icon)
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    if IS_MAC:
        box.setText(title)
        box.setInformativeText(informative_text or text)
    else:
        box.setWindowTitle(title)
        box.setText(text)
    if IS_MAC and sheet_parent is not None and sheet_parent.isVisible():
        box.setWindowModality(Qt.WindowModality.WindowModal)
        _run_sheet(box)
        return box.standardButton(box.clickedButton()) if box.clickedButton() else QMessageBox.StandardButton.NoButton
    box.exec()
    return box.standardButton(box.clickedButton()) if box.clickedButton() else QMessageBox.StandardButton.NoButton


def info(parent, title, text, rich_text=False):
    """Show an informational dialog, as a window-attached sheet on macOS."""
    sheet_parent = _sheet_parent(parent)
    box = QMessageBox(sheet_parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    if rich_text:
        box.setTextFormat(Qt.TextFormat.RichText)
    if IS_MAC:
        box.setText(title)
        box.setInformativeText(text)
    else:
        box.setWindowTitle(title)
        box.setText(text)
    if IS_MAC and sheet_parent is not None and sheet_parent.isVisible():
        box.setWindowModality(Qt.WindowModality.WindowModal)
        _run_sheet(box)
        return
    box.exec()
