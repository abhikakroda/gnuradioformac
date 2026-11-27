"""macOS application identity helpers (Dock icon and process name).

Copyright 2026 Free Software Foundation, Inc.
This file is part of GNU Radio

SPDX-License-Identifier: GPL-3.0-or-later

When GNU Radio Companion is started from the "GNU Radio Companion.app" bundle
the launcher execs the Python interpreter, so Cocoa does not see the bundle's
Info.plist for the running process.  The functions here tell AppKit which icon
and name to use.  They only talk to the Objective-C runtime through ctypes, so
no extra Python packages are needed, and they do nothing on other platforms.
"""

import ctypes
import ctypes.util
import logging
import os
import sys

log = logging.getLogger(__name__)

ICON_ENV = "GRC_MACOS_APP_ICON"
NAME_ENV = "GRC_MACOS_APP_NAME"


def _objc():
    path = ctypes.util.find_library("objc")
    if not path:
        raise OSError("Objective-C runtime not found")
    lib = ctypes.cdll.LoadLibrary(path)
    lib.objc_getClass.restype = ctypes.c_void_p
    lib.objc_getClass.argtypes = [ctypes.c_char_p]
    lib.sel_registerName.restype = ctypes.c_void_p
    lib.sel_registerName.argtypes = [ctypes.c_char_p]
    return lib


def _send(lib, receiver, selector, *args):
    """objc_msgSend with pointer-sized arguments and a pointer-sized result."""
    proto = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             *([ctypes.c_void_p] * len(args)))
    func = proto(("objc_msgSend", lib))
    return func(receiver, lib.sel_registerName(selector), *args)


def _nsstring(lib, text):
    cls = lib.objc_getClass(b"NSString")
    return _send(lib, cls, b"stringWithUTF8String:", text.encode("utf-8"))


def set_dock_icon(icon_path):
    """Use the image at icon_path (.icns or .png) as the application's Dock icon.

    Returns True when AppKit accepted the icon.
    """
    if sys.platform != "darwin":
        return False
    if not icon_path or not os.path.isfile(icon_path):
        log.debug("Dock icon %r not found", icon_path)
        return False
    try:
        lib = _objc()
        image = _send(lib, lib.objc_getClass(b"NSImage"), b"alloc")
        image = _send(lib, image, b"initWithContentsOfFile:", _nsstring(lib, icon_path))
        if not image:
            log.debug("AppKit could not load %s", icon_path)
            return False
        app = _send(lib, lib.objc_getClass(b"NSApplication"), b"sharedApplication")
        _send(lib, app, b"setApplicationIconImage:", image)
        return True
    except Exception as err:
        log.debug("Could not set the Dock icon: %s", err)
        return False


def set_process_name(name):
    """Set the name AppKit shows for the process (application menu, Dock).

    The menu bar title of a process that was exec'd from a bundle launcher comes
    from the main bundle's CFBundleName, which is the Python interpreter's
    unless it is overridden here, before the first NSApplication is created.
    """
    if sys.platform != "darwin" or not name:
        return False
    try:
        lib = _objc()
        info = _send(lib, lib.objc_getClass(b"NSProcessInfo"), b"processInfo")
        _send(lib, info, b"setProcessName:", _nsstring(lib, name))
        bundle = _send(lib, lib.objc_getClass(b"NSBundle"), b"mainBundle")
        bundle_info = _send(lib, bundle, b"infoDictionary")
        if bundle_info:
            for key in ("CFBundleName", "CFBundleDisplayName"):
                _send(lib, bundle_info, b"setObject:forKey:",
                      _nsstring(lib, name), _nsstring(lib, key))
        return True
    except Exception as err:
        log.debug("Could not set the process name: %s", err)
        return False


def activate_application():
    """Bring the application to the front.

    A process started through the bundle launcher is not activated by
    LaunchServices once it has been exec'd into the interpreter, so its first
    window would otherwise open behind the application that was in front.
    """
    if sys.platform != "darwin":
        return False
    try:
        lib = _objc()
        app = _send(lib, lib.objc_getClass(b"NSApplication"), b"sharedApplication")
        _send(lib, app, b"setActivationPolicy:", 0)  # NSApplicationActivationPolicyRegular
        _send(lib, app, b"activateIgnoringOtherApps:", 1)
        return True
    except Exception as err:
        log.debug("Could not activate the application: %s", err)
        return False


def setup_application_identity(fallback_icon=None):
    """Apply the Dock icon and name given by the app bundle launcher.

    The launcher exports GRC_MACOS_APP_ICON and GRC_MACOS_APP_NAME.  When run
    outside the bundle (from a shell, say) fallback_icon is used instead so the
    Dock still shows the GNU Radio logo rather than the Python rocket.
    """
    if sys.platform != "darwin":
        return
    set_process_name(os.environ.get(NAME_ENV, ""))
    icon = os.environ.get(ICON_ENV, "")
    if not (icon and os.path.isfile(icon)):
        icon = fallback_icon
    set_dock_icon(icon)


# --- window chrome ---------------------------------------------------------
#
# The helpers below take the NSView pointer of a Qt top-level window (the value
# of QWidget.winId() on macOS) and adjust the NSWindow around it.  They return
# False instead of raising, so a missing or changed selector can never take the
# application down.

NS_WINDOW_STYLE_FULL_SIZE_CONTENT = 1 << 15
NS_WINDOW_TITLE_HIDDEN = 1
NS_WINDOW_DOCUMENT_ICON_BUTTON = 4


def _typed_send(lib, receiver, selector, argtypes, restype, *args):
    proto = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
    func = proto(("objc_msgSend", lib))
    return func(receiver, lib.sel_registerName(selector), *args)


def _window_of(lib, view):
    if not view:
        return None
    return _send(lib, ctypes.c_void_p(int(view)).value, b"window") or None


def configure_unified_window(view):
    """Let the content run under a transparent, title-less title bar.

    The window keeps its traffic-light buttons, but toolbar and sidebars start
    at the very top (full-size content view), as in Finder, Xcode or Notes.
    """
    if sys.platform != "darwin":
        return False
    try:
        lib = _objc()
        window = _window_of(lib, view)
        if not window:
            return False
        mask = _typed_send(lib, window, b"styleMask", [], ctypes.c_ulong)
        _typed_send(lib, window, b"setStyleMask:", [ctypes.c_ulong], None,
                    mask | NS_WINDOW_STYLE_FULL_SIZE_CONTENT)
        _typed_send(lib, window, b"setTitlebarAppearsTransparent:", [ctypes.c_bool], None, True)
        _typed_send(lib, window, b"setTitleVisibility:", [ctypes.c_long], None, NS_WINDOW_TITLE_HIDDEN)
        proxy_icon = _typed_send(lib, window, b"standardWindowButton:", [ctypes.c_long], ctypes.c_void_p,
                                 NS_WINDOW_DOCUMENT_ICON_BUTTON)
        if proxy_icon:
            _typed_send(lib, proxy_icon, b"setHidden:", [ctypes.c_bool], None, True)
        return True
    except Exception as err:
        log.debug("Could not configure the unified window: %s", err)
        return False


def toggle_full_screen(view):
    """Enter or leave the native full screen space of the window."""
    if sys.platform != "darwin":
        return False
    try:
        lib = _objc()
        window = _window_of(lib, view)
        if not window:
            return False
        _send(lib, window, b"toggleFullScreen:", None)
        return True
    except Exception as err:
        log.debug("Could not toggle full screen: %s", err)
        return False


def zoom_window(view):
    """Zoom (green button behaviour without full screen) the window."""
    if sys.platform != "darwin":
        return False
    try:
        lib = _objc()
        window = _window_of(lib, view)
        if not window:
            return False
        _send(lib, window, b"zoom:", None)
        return True
    except Exception as err:
        log.debug("Could not zoom the window: %s", err)
        return False


def bring_all_to_front():
    """Window > Bring All to Front."""
    if sys.platform != "darwin":
        return False
    try:
        lib = _objc()
        app = _send(lib, lib.objc_getClass(b"NSApplication"), b"sharedApplication")
        _send(lib, app, b"arrangeInFront:", None)
        return True
    except Exception as err:
        log.debug("Could not bring windows to the front: %s", err)
        return False


def show_about_panel(name, version, build="", copyright_text="", credits=""):
    """Show the standard Cocoa About panel with the application icon and version."""
    if sys.platform != "darwin":
        return False
    try:
        lib = _objc()
        options = _send(lib, lib.objc_getClass(b"NSMutableDictionary"), b"dictionary")
        entries = {"ApplicationName": name, "ApplicationVersion": version}
        if build:
            entries["Version"] = build
        if copyright_text:
            entries["Copyright"] = copyright_text
        for key, value in entries.items():
            _send(lib, options, b"setObject:forKey:", _nsstring(lib, value), _nsstring(lib, key))
        if credits:
            attributes = _send(lib, lib.objc_getClass(b"NSMutableDictionary"), b"dictionary")
            font = _typed_send(lib, lib.objc_getClass(b"NSFont"), b"systemFontOfSize:",
                               [ctypes.c_double], ctypes.c_void_p, 11.0)
            color = _send(lib, lib.objc_getClass(b"NSColor"), b"secondaryLabelColor")
            style = _send(lib, lib.objc_getClass(b"NSMutableParagraphStyle"), b"new")
            _typed_send(lib, style, b"setAlignment:", [ctypes.c_long], None, 2)  # NSTextAlignmentCenter
            for key, value in (("NSFont", font), ("NSColor", color), ("NSParagraphStyle", style)):
                if value:
                    _send(lib, attributes, b"setObject:forKey:", value, _nsstring(lib, key))
            attributed = _send(lib, lib.objc_getClass(b"NSAttributedString"), b"alloc")
            attributed = _send(lib, attributed, b"initWithString:attributes:",
                               _nsstring(lib, credits), attributes)
            _send(lib, options, b"setObject:forKey:", attributed, _nsstring(lib, "Credits"))
        app = _send(lib, lib.objc_getClass(b"NSApplication"), b"sharedApplication")
        icon = _send(lib, app, b"applicationIconImage")
        if icon:
            _send(lib, options, b"setObject:forKey:", icon, _nsstring(lib, "ApplicationIcon"))
        _send(lib, app, b"orderFrontStandardAboutPanelWithOptions:", options)
        return True
    except Exception as err:
        log.debug("Could not show the About panel: %s", err)
        return False


class _NSRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                ("width", ctypes.c_double), ("height", ctypes.c_double)]


NS_VISUAL_EFFECT_MATERIAL_SIDEBAR = 7
NS_VISUAL_EFFECT_BLENDING_BEHIND_WINDOW = 0
NS_WINDOW_BELOW = -1
NS_VIEW_WIDTH_HEIGHT_SIZABLE = 2 | 16


def add_sidebar_vibrancy(view, width, height):
    """Put an NSVisualEffectView (sidebar material) behind the Qt content.

    The window's Qt content has to be translucent (WA_TranslucentBackground);
    wherever Qt paints nothing the blurred desktop shows through, which gives
    the translucent sidebar look of Finder and Mail.  Returns the effect view
    pointer, or None when it could not be installed.
    """
    if sys.platform != "darwin":
        return None
    try:
        lib = _objc()
        window = _window_of(lib, view)
        if not window:
            return None
        content = _send(lib, window, b"contentView")
        frame_view = _send(lib, content, b"superview")
        if not (content and frame_view):
            return None
        effect = _send(lib, lib.objc_getClass(b"NSVisualEffectView"), b"alloc")
        effect = _typed_send(lib, effect, b"initWithFrame:", [_NSRect], ctypes.c_void_p,
                             _NSRect(0, 0, float(width), float(height)))
        if not effect:
            return None
        _typed_send(lib, effect, b"setMaterial:", [ctypes.c_long], None, NS_VISUAL_EFFECT_MATERIAL_SIDEBAR)
        _typed_send(lib, effect, b"setBlendingMode:", [ctypes.c_long], None,
                    NS_VISUAL_EFFECT_BLENDING_BEHIND_WINDOW)
        _typed_send(lib, effect, b"setAutoresizingMask:", [ctypes.c_ulong], None, NS_VIEW_WIDTH_HEIGHT_SIZABLE)
        _typed_send(lib, frame_view, b"addSubview:positioned:relativeTo:",
                    [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p], None,
                    effect, NS_WINDOW_BELOW, content)
        return effect
    except Exception as err:
        log.debug("Could not install the vibrancy view: %s", err)
        return None
