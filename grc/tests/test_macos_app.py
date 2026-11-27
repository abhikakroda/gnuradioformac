import os
import plistlib
import re
import struct

import pytest

from grc.core.utils import macos

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ICNS = os.path.join(ROOT, "release", "resources", "icons", "gnuradio-companion.icns")
DOCUMENT_ICNS = os.path.join(ROOT, "release", "resources", "icons", "gnuradio-companion-document.icns")
PLIST_TEMPLATE = os.path.join(ROOT, "release", "resources", "macos", "Info.plist.in")


def test_dock_helpers_do_nothing_off_macos(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "linux")
    assert macos.set_dock_icon(ICNS) is False
    assert macos.set_process_name("GNU Radio Companion") is False
    macos.setup_application_identity(ICNS)


def test_dock_icon_requires_existing_file(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    assert macos.set_dock_icon(os.path.join(ROOT, "does-not-exist.icns")) is False
    assert macos.set_dock_icon(None) is False


def icns_sizes(path):
    data = open(path, "rb").read()
    assert data[:4] == b"icns"
    assert struct.unpack(">I", data[4:8])[0] == len(data)
    sizes = set()
    pos = 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        payload = data[pos + 8:pos + length]
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        sizes.add(struct.unpack(">II", payload[16:24]))
        pos += length
    assert pos == len(data)
    return sizes


def test_icns_is_valid_and_has_retina_sizes():
    sizes = icns_sizes(ICNS)
    for size in (16, 32, 64, 128, 256, 512, 1024):
        assert (size, size) in sizes


def test_document_icns_has_every_size():
    sizes = icns_sizes(DOCUMENT_ICNS)
    for size in (16, 32, 64, 128, 256, 512, 1024):
        assert (size, size) in sizes


def test_info_plist_template_declares_dock_app_and_icon():
    text = open(PLIST_TEMPLATE, encoding="utf-8").read()
    values = {name: "x" for name in set(re.findall(r"@(\w+)@", text))}
    values["GR_MACOS_APP_ICON_NAME"] = "gnuradio-companion"
    for name, value in values.items():
        text = text.replace("@%s@" % name, value)
    info = plistlib.loads(text.encode("utf-8"))
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleIconFile"] == "gnuradio-companion"
    assert os.path.isfile(os.path.join(os.path.dirname(ICNS), info["CFBundleIconFile"] + ".icns"))
    assert info["LSUIElement"] is False
    for key in ("CFBundleName", "CFBundleExecutable", "CFBundleIdentifier"):
        assert info[key]


def test_info_plist_template_associates_grc_documents():
    text = open(PLIST_TEMPLATE, encoding="utf-8").read()
    values = {name: "x" for name in set(re.findall(r"@(\w+)@", text))}
    values["GR_MACOS_APP_ICON_NAME"] = "gnuradio-companion"
    values["GR_MACOS_APP_DOCUMENT_ICON_NAME"] = "gnuradio-companion-document"
    values["GR_MACOS_APP_IDENTIFIER"] = "org.gnuradio.grc"
    for name, value in values.items():
        text = text.replace("@%s@" % name, value)
    info = plistlib.loads(text.encode("utf-8"))
    doc_types = info["CFBundleDocumentTypes"]
    assert any("grc" in doc["CFBundleTypeExtensions"] for doc in doc_types)
    assert all(doc["CFBundleTypeRole"] == "Editor" for doc in doc_types)
    assert all(doc["CFBundleTypeIconFile"] == "gnuradio-companion-document" for doc in doc_types)
    exported = info["UTExportedTypeDeclarations"][0]
    assert exported["UTTypeIdentifier"] == "org.gnuradio.grc.flowgraph"
    assert "grc" in exported["UTTypeTagSpecification"]["public.filename-extension"]
    assert exported["UTTypeIconFile"] == "gnuradio-companion-document"
    assert os.path.isfile(os.path.join(os.path.dirname(DOCUMENT_ICNS), exported["UTTypeIconFile"] + ".icns"))


def test_launcher_templates_exist_and_keep_bundle_identity():
    resources = os.path.dirname(PLIST_TEMPLATE)
    native = open(os.path.join(resources, "launcher.cpp.in"), encoding="utf-8").read()
    assert "Py_InitializeFromConfig" in native
    assert "parse_argv = 0" in native
    assert os.path.isfile(os.path.join(resources, "launcher.sh.in"))


def test_activate_application_does_nothing_off_macos(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "linux")
    assert macos.activate_application() is False


def test_bundle_resources_do_not_reference_temporary_paths():
    resources = os.path.dirname(PLIST_TEMPLATE)
    for name in ("launcher.cpp.in", "launcher.sh.in", "embed_runtime.py",
                 "embed_runtime.cmake.in", "Info.plist.in"):
        text = open(os.path.join(resources, name), encoding="utf-8").read()
        assert "/tmp" not in text, name
        assert "CMAKE_INSTALL_PREFIX@" not in text, name


def test_launchers_prefer_the_embedded_runtime():
    resources = os.path.dirname(PLIST_TEMPLATE)
    for name in ("launcher.cpp.in", "launcher.sh.in"):
        text = open(os.path.join(resources, name), encoding="utf-8").read()
        assert "grc-runtime" in text, name
        assert "GRC_BLOCKS_DIR" in text, name
    assert "write_bytecode = 0" in open(os.path.join(resources, "launcher.cpp.in"), encoding="utf-8").read()
    assert "PYTHONDONTWRITEBYTECODE=1" in open(os.path.join(resources, "launcher.sh.in"), encoding="utf-8").read()


def test_embed_runtime_finds_pyqt_site_and_skips_junk(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "embed_runtime", os.path.join(os.path.dirname(PLIST_TEMPLATE), "embed_runtime.py"))
    embed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(embed)

    (tmp_path / "PyQt6").mkdir()
    assert embed.find_pyqt_site("python3", str(tmp_path)) == str(tmp_path)
    assert embed.find_pyqt_site("python3", str(tmp_path / "missing")) is None
    assert embed.ignore_junk("x", ["a.py", "a.pyc", "__pycache__", ".DS_Store"]) == [
        "a.pyc", "__pycache__", ".DS_Store"]
    assert {"QtCore", "QtGui", "QtWidgets"} <= embed.PYQT_MODULES


def test_window_chrome_helpers_do_nothing_off_macos(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "linux")
    assert macos.configure_unified_window(1) is False
    assert macos.toggle_full_screen(1) is False
    assert macos.zoom_window(1) is False
    assert macos.bring_all_to_front() is False
    assert macos.show_about_panel("GNU Radio Companion", "1.0") is False
    assert macos.add_sidebar_vibrancy(1, 100, 100) is None


def test_window_chrome_helpers_tolerate_a_missing_window():
    assert macos.configure_unified_window(0) is False
    assert macos.add_sidebar_vibrancy(0, 100, 100) is None


def test_native_ui_has_sheet_helpers():
    text = open(os.path.join(ROOT, "grc", "gui_qt", "components", "native_ui.py"), encoding="utf-8").read()
    assert "WindowModal" in text
    for name in ("def open_file", "def save_file", "def ask", "def info"):
        assert name in text
