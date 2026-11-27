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
