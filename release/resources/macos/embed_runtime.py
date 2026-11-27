#!/usr/bin/env python3
# Copyright 2026 Free Software Foundation, Inc.
#
# This file is part of GNU Radio
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Make "GNU Radio Companion.app" self-contained.

Run at install time (see cmake/Modules/GrMacOSBundle.cmake) on an installed
bundle.  It copies into <app>/Contents/Resources/grc-runtime:

  bin/gnuradio-companion   the program the launcher runs
  python/gnuradio/...      the GRC Python package of this build
  blocks/                  the block definitions GRC loads
  site-packages/PyQt6      PyQt6 with the Qt frameworks and plugins it needs

and then ad-hoc code signs the whole bundle, so the application does not
depend on the install tree or on a PyQt6 installation on the user's machine.
The Python interpreter and the GNU Radio runtime it runs flowgraphs with are
still taken from the GNU Radio installation that the program's "#!" line
(or the interpreter the bundle was built with) points to.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

# PyQt6 modules used by GRC (grc/gui_qt) plus what Qt itself needs on macOS.
PYQT_MODULES = {
    "QtCore", "QtGui", "QtWidgets", "QtSvg", "QtSvgWidgets", "QtPrintSupport",
    "QtDBus", "QtNetwork", "QtOpenGL", "QtOpenGLWidgets", "QtXml",
}
QT_PLUGIN_DIRS = {
    "platforms", "styles", "imageformats", "iconengines", "platforminputcontexts",
    "generic", "tls", "networkinformation",
}
RESOURCE_JUNK = ("__pycache__", ".DS_Store")


def run(cmd, check=True, **kwargs):
    return subprocess.run(cmd, check=check, **kwargs)


def ignore_junk(_directory, names):
    return [n for n in names if n in RESOURCE_JUNK or n.endswith(".pyc")]


def copy_tree(src, dst):
    shutil.copytree(src, dst, symlinks=True, ignore=ignore_junk, dirs_exist_ok=True)


def find_pyqt_site(python, hint):
    """Directory that contains the PyQt6 package."""
    candidates = [hint] if hint else []
    if not candidates:
        out = run([python, "-c",
                   "import os, PyQt6; print(os.path.dirname(os.path.dirname(PyQt6.__file__)))"],
                  check=False, capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            candidates.append(out.stdout.strip())
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "PyQt6")):
            return candidate
    return None


def macho_dependencies(path):
    out = run(["otool", "-L", path], check=False, capture_output=True, text=True).stdout
    return re.findall(r"@rpath/(Qt\w+)\.framework/", out)


def is_macho(path):
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    with open(path, "rb") as f:
        return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf",
                             b"\xbe\xba\xfe\xca")


def embed_pyqt(site_src, site_dst):
    src = os.path.join(site_src, "PyQt6")
    dst = os.path.join(site_dst, "PyQt6")
    qt_src = os.path.join(src, "Qt6")
    if not os.path.isdir(qt_src):
        raise SystemExit("PyQt6 at %s has no bundled Qt6 (install the PyQt6 wheel)" % src)

    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        path = os.path.join(src, name)
        stem = name.split(".")[0]
        if name == "Qt6" or name in RESOURCE_JUNK:
            continue
        if os.path.isdir(path):
            # sip bindings and lupdate are development-only; uic loads .ui files.
            if name in ("bindings", "lupdate"):
                continue
            copy_tree(path, os.path.join(dst, name))
        elif name.endswith(".pyi") or name == "py.typed":
            continue
        elif stem.startswith("Qt") and stem not in PYQT_MODULES:
            continue
        else:
            shutil.copy2(path, os.path.join(dst, name), follow_symlinks=False)

    qt_dst = os.path.join(dst, "Qt6")
    os.makedirs(qt_dst)

    plugins_src = os.path.join(qt_src, "plugins")
    for name in sorted(os.listdir(plugins_src)):
        if name in QT_PLUGIN_DIRS:
            copy_tree(os.path.join(plugins_src, name), os.path.join(qt_dst, "plugins", name))

    # Keep exactly the frameworks that the kept PyQt modules and plugins link.
    needed = set()
    queue = []
    for root in (dst, os.path.join(qt_dst, "plugins")):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                if is_macho(path):
                    queue.extend(macho_dependencies(path))
    lib_src = os.path.join(qt_src, "lib")
    while queue:
        name = queue.pop()
        if name in needed:
            continue
        binary = os.path.join(lib_src, name + ".framework", "Versions", "A", name)
        if not os.path.exists(binary):
            continue
        needed.add(name)
        queue.extend(macho_dependencies(binary))
    for name in sorted(needed):
        copy_tree(os.path.join(lib_src, name + ".framework"),
                  os.path.join(qt_dst, "lib", name + ".framework"))
    for extra in ("qt.conf",):
        if os.path.exists(os.path.join(qt_src, extra)):
            shutil.copy2(os.path.join(qt_src, extra), os.path.join(qt_dst, extra))
    return sorted(needed)


def ad_hoc_sign(app):
    """Sign nested code first, then the bundle, all ad hoc ("-")."""
    resources = os.path.join(app, "Contents", "Resources")
    frameworks = []
    binaries = []
    for dirpath, dirs, files in os.walk(resources):
        for d in list(dirs):
            if d.endswith(".framework"):
                frameworks.append(os.path.join(dirpath, d))
                dirs.remove(d)
        for name in files:
            path = os.path.join(dirpath, name)
            if is_macho(path):
                binaries.append(path)
    for path in binaries + frameworks:
        run(["codesign", "--force", "--sign", "-", path], capture_output=True)
    run(["codesign", "--force", "--deep", "--sign", "-", app], capture_output=True)
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", required=True, help="installed .app bundle")
    parser.add_argument("--python", default=sys.executable, help="interpreter the app runs with")
    parser.add_argument("--gnuradio-python-dir", required=True,
                        help="site-packages directory (in the install tree) holding gnuradio/grc")
    parser.add_argument("--program", required=True, help="installed gnuradio-companion script")
    parser.add_argument("--blocks", action="append", default=[],
                        help="block definition directory; later ones override earlier ones")
    parser.add_argument("--pyqt-site", default="", help="site-packages directory holding PyQt6")
    parser.add_argument("--no-sign", action="store_true")
    args = parser.parse_args()

    app = os.path.abspath(args.app)
    runtime = os.path.join(app, "Contents", "Resources", "grc-runtime")
    if os.path.isdir(runtime):
        shutil.rmtree(runtime)

    gr_pkg = os.path.join(args.gnuradio_python_dir, "gnuradio")
    for sub in ("grc",):
        if not os.path.isdir(os.path.join(gr_pkg, sub)):
            raise SystemExit("%s not found; install GRC before the bundle" % os.path.join(gr_pkg, sub))
    py_dst = os.path.join(runtime, "python", "gnuradio")
    os.makedirs(py_dst)
    shutil.copy2(os.path.join(gr_pkg, "__init__.py"), os.path.join(py_dst, "__init__.py"))
    copy_tree(os.path.join(gr_pkg, "grc"), os.path.join(py_dst, "grc"))

    os.makedirs(os.path.join(runtime, "bin"))
    shutil.copy2(args.program, os.path.join(runtime, "bin", os.path.basename(args.program)))

    blocks_dst = os.path.join(runtime, "blocks")
    os.makedirs(blocks_dst)
    for blocks in args.blocks:
        if os.path.isdir(blocks):
            copy_tree(blocks, blocks_dst)
        else:
            print("warning: block directory %s not found" % blocks, file=sys.stderr)

    site = find_pyqt_site(args.python, args.pyqt_site)
    if site:
        frameworks = embed_pyqt(site, os.path.join(runtime, "site-packages"))
        print("Bundled PyQt6 from %s (%d Qt frameworks)" % (site, len(frameworks)))
    else:
        raise SystemExit("PyQt6 not found: install the PyQt6 wheel for %s or pass --pyqt-site "
                         "(cmake: -DGR_MACOS_APP_PYQT_SITE=<site-packages>)" % args.python)

    if not args.no_sign and shutil.which("codesign"):
        ad_hoc_sign(app)
        print("Ad-hoc signed %s" % app)


if __name__ == "__main__":
    main()
