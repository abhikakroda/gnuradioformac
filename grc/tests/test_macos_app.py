import os
import plistlib
import re
import struct

import pytest

from grc.core.utils import macos

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ICNS = os.path.join(ROOT, "release", "resources", "icons", "gnuradio-companion.icns")
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


def test_icns_is_valid_and_has_retina_sizes():
    data = open(ICNS, "rb").read()
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
    for size in (16, 32, 128, 256, 512, 1024):
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
    for name, value in values.items():
        text = text.replace("@%s@" % name, value)
    info = plistlib.loads(text.encode("utf-8"))
    doc_types = info["CFBundleDocumentTypes"]
    assert any("grc" in doc["CFBundleTypeExtensions"] for doc in doc_types)
    assert all(doc["CFBundleTypeRole"] == "Editor" for doc in doc_types)


def test_launcher_templates_exist_and_keep_bundle_identity():
    resources = os.path.dirname(PLIST_TEMPLATE)
    native = open(os.path.join(resources, "launcher.cpp.in"), encoding="utf-8").read()
    assert "Py_InitializeFromConfig" in native
    assert "parse_argv = 0" in native
    assert os.path.isfile(os.path.join(resources, "launcher.sh.in"))


def test_activate_application_does_nothing_off_macos(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "linux")
    assert macos.activate_application() is False
