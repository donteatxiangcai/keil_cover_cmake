# -*- coding: utf-8 -*-
"""Keil → CMake 转换工具 (PySide6 / Qt Quick)

用法:
    python main.py                 # 直接运行
    把 .uvprojx 拖进窗口 -> 生成 CMake 工程
"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine

from backend import Backend


def main():
    # Qt Quick Controls 用 Basic 样式(支持自定义外观)
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

    # 高 DPI: 必须在 QApplication 之前设置
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Keil2CMake")
    app.setApplicationDisplayName("Keil → CMake")

    backend = Backend(app)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)

    qml_file = Path(__file__).resolve().parent / "Main.qml"
    engine.load(qml_file)

    if not engine.rootObjects():
        print("QML 加载失败:", qml_file, file=sys.stderr)
        return -1

    rc = app.exec()
    del engine        # 先销毁 QML 对象, 再退出, 避免关闭时刷一堆 null 绑定告警
    return rc


if __name__ == "__main__":
    sys.exit(main())
