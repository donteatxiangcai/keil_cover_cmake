# -*- coding: utf-8 -*-
"""
Keil → CMake 转换工具的 GUI 后端 (Qt/QML 与 Python 的桥)

界面只负责显示, 真正的转换逻辑复用 ../keil2cmake.py
"""

import os
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl
from PySide6.QtWidgets import QFileDialog

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import keil2cmake as k2c  # noqa: E402


def _to_local_path(s):
    """QML 传来的可能是 file:///E:/... 也可能是普通路径"""
    if not s:
        return ""
    if s.startswith("file:"):
        return QUrl(s).toLocalFile()
    return s


EMPTY_INFO = {
    "target": "", "device": "", "core": "", "fpu": "",
    "sources": "", "groups": "", "defines": "", "includes": "",
    "optim": "", "std": "", "output_name": "",
}


def _default_outdir(uvprojx):
    """Keil 常见布局: <项目根>/MDK-ARM/xxx.uvprojx -> 默认输出到 <项目根>"""
    d = os.path.dirname(os.path.abspath(uvprojx))
    if os.path.basename(d).lower() in ("mdk-arm", "mdk", "keil", "uvision"):
        parent = os.path.dirname(d)
        if parent and os.path.isdir(parent):
            return parent
    return d


class Backend(QObject):
    logLine = Signal(str)
    infoChanged = Signal()
    busyChanged = Signal()
    statusChanged = Signal()
    projectChanged = Signal()
    outDirChanged = Signal()
    targetsChanged = Signal()
    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project = ""
        self._outdir = ""
        self._busy = False
        self._status = "就绪：把 .uvprojx 文件拖进窗口"
        self._info = dict(EMPTY_INFO)
        self._targets = []
        self._target = ""
        k2c.set_logger(self._on_log)

    # ------------------------------------------------------------------ 属性
    def _get_project(self):
        return self._project

    projectPath = Property(str, _get_project, notify=projectChanged)

    def _get_outdir(self):
        return self._outdir

    outDir = Property(str, _get_outdir, notify=outDirChanged)

    def _get_busy(self):
        return self._busy

    busy = Property(bool, _get_busy, notify=busyChanged)

    def _get_status(self):
        return self._status

    status = Property(str, _get_status, notify=statusChanged)

    def _get_info(self):
        return self._info

    info = Property("QVariantMap", _get_info, notify=infoChanged)

    def _get_has_info(self):
        return bool(self._info.get("target"))

    hasInfo = Property(bool, _get_has_info, notify=infoChanged)

    def _get_targets(self):
        return self._targets

    targets = Property("QVariantList", _get_targets, notify=targetsChanged)

    def _get_target(self):
        return self._target

    target = Property(str, _get_target, notify=targetsChanged)

    # ------------------------------------------------------------------ 内部
    def _on_log(self, msg):
        self.logLine.emit(str(msg))

    def _set_status(self, s):
        self._status = s
        self.statusChanged.emit()

    def _set_busy(self, b):
        self._busy = b
        self.busyChanged.emit()

    # ------------------------------------------------------------------ 槽
    @Slot()
    def browseFile(self):
        start = os.path.dirname(self._project) if self._project else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            None, "选择 Keil 工程文件", start,
            "Keil 工程 (*.uvprojx *.uvproj);;所有文件 (*.*)")
        if path:
            self.analyze(path)

    @Slot(str)
    def browseOutDir(self):
        start = self._outdir or (os.path.dirname(self._project) if self._project else str(Path.home()))
        path = QFileDialog.getExistingDirectory(None, "选择输出目录", start)
        if path:
            self._outdir = os.path.normpath(path)
            self.outDirChanged.emit()

    @Slot(str)
    def setOutDir(self, path):
        self._outdir = os.path.normpath(_to_local_path(path))
        self.outDirChanged.emit()

    @Slot(str)
    def setTarget(self, name):
        if not name or name == self._target:
            return
        self._target = name
        self.targetsChanged.emit()
        if self._project:
            self.analyze(self._project)

    @Slot(str)
    def analyze(self, path):
        """拖入/选中文件后: 解析并显示工程概要"""
        path = _to_local_path(path)
        if not path or not os.path.isfile(path):
            self._set_status("文件不存在: %s" % path)
            return
        if not path.lower().endswith((".uvprojx", ".uvproj")):
            self._set_status("这不是 Keil 工程文件 (.uvprojx)")
            return
        try:
            p = k2c.parse_project(path, self._target or None)
        except SystemExit as e:
            self._set_status(str(e))
            return
        except Exception:
            self._set_status("解析失败")
            self._on_log(traceback.format_exc())
            return

        new_path = os.path.abspath(path)
        if new_path != self._project or not self._outdir:
            self._project = new_path
            self._outdir = _default_outdir(new_path)
        else:
            self._project = new_path
        self._target = p["target"]
        self._targets = k2c.target_names(self._project)
        self._info = k2c.summarize(p)

        self.projectChanged.emit()
        self.targetsChanged.emit()
        self.outDirChanged.emit()
        self.infoChanged.emit()
        self._set_status("已解析 %s（%s）" % (p["target"], p["device"] or "未知器件"))
        self._on_log("载入工程: %s" % self._project)
        self._on_log("  Target=%s  CPU=%s  %s" % (
            p["target"], p["core"] or "?", self._info["sources"]))

    @Slot(bool, bool)
    def generate(self, do_build, force):
        """生成 CMake 工程; do_build=True 时顺带编译验证"""
        if self._busy:
            return
        if not self._project:
            self._set_status("请先拖入一个 .uvprojx 文件")
            return
        outdir = self._outdir or _default_outdir(self._project)
        self._set_busy(True)
        self._set_status("正在生成…" if not do_build else "正在生成并编译验证…")
        t = threading.Thread(target=self._run, args=(self._project, outdir, do_build, force), daemon=True)
        t.start()

    def _run(self, project, outdir, do_build, force):
        ok, msg = True, ""
        try:
            k2c.build_project(project, outdir, target=self._target or None,
                              do_build=do_build, force=force)
        except SystemExit as e:
            ok, msg = False, str(e)
        except Exception:
            ok, msg = False, traceback.format_exc()
        finally:
            self._set_busy(False)
            self.finished.emit(ok, msg)

    @Slot(bool, str)
    def onFinished(self, ok, msg):
        """供 QML 调用, 刷新状态栏"""
        if ok:
            self._set_status("生成完成 → %s" % self._outdir)
        else:
            self._set_status("失败: %s" % (msg.strip().splitlines()[-1] if msg else "未知错误"))
            self._on_log(msg or "")

    @Slot()
    def openOutDir(self):
        d = self._outdir
        if d and os.path.isdir(d):
            try:
                os.startfile(d)  # noqa: S606  (Windows)
            except Exception as e:
                self._set_status("打开目录失败: %s" % e)
        else:
            self._set_status("输出目录还不存在")

    @Slot()
    def openCMakeLists(self):
        f = os.path.join(self._outdir or "", "CMakeLists.txt")
        if os.path.isfile(f):
            try:
                os.startfile(f)
            except Exception as e:
                self._set_status("打开失败: %s" % e)
        else:
            self._set_status("还没有生成 CMakeLists.txt")
