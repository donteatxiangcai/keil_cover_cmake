// Keil 工程 (.uvprojx) -> CMake 工程 转换工具
// 拖入文件即可生成 CMakeLists.txt / 工具链文件 / 分散加载文件

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    width: 1020
    height: 780
    minimumWidth: 820
    minimumHeight: 640
    visible: true
    title: qsTr("Keil → CMake 转换工具")
    color: "#f4f6f9"

    property color accent: "#2b7cff"
    property color ink: "#1f2329"
    property color muted: "#8a8f98"
    property color line: "#e3e8ef"
    property string monoFont: "Consolas"

    ListModel { id: logModel }

    Connections {
        target: backend
        function onLogLine(line) {
            logModel.append({ "line": line })
            if (logModel.count > 2000)
                logModel.remove(0, logModel.count - 2000)
        }
        function onFinished(ok, msg) {
            backend.onFinished(ok, msg)
            if (ok)
                logModel.append({ "line": "✔ 生成成功" })
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        // ================= 标题 =================
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            ColumnLayout {
                spacing: 2
                Text {
                    text: qsTr("Keil 工程 → CMake 工程")
                    font.pixelSize: 20
                    font.bold: true
                    color: win.ink
                }
                Text {
                    text: qsTr("把 .uvprojx 拖进下面的方框，自动生成 CMakeLists.txt、工具链文件和分散加载文件")
                    font.pixelSize: 12
                    color: win.muted
                }
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                radius: 4
                color: "#eaf1ff"
                implicitWidth: badge.width + 16
                implicitHeight: 22
                Text {
                    id: badge
                    anchors.centerIn: parent
                    text: qsTr("Arm Compiler 6")
                    font.pixelSize: 11
                    color: win.accent
                }
            }
        }

        // ================= 拖放区 =================
        Rectangle {
            id: dropRect
            Layout.fillWidth: true
            Layout.preferredHeight: 104
            radius: 10
            color: windowDrop.containsDrag ? "#e6f0ff" : (backend.projectPath ? "#ffffff" : "#fbfcfe")
            border.width: 2
            border.color: windowDrop.containsDrag ? win.accent
                                                  : (backend.projectPath ? "#b7d3ff" : "#cfd6e0")
            Behavior on color { ColorAnimation { duration: 120 } }

            ColumnLayout {
                anchors.centerIn: parent
                width: parent.width - 48
                spacing: 4

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: dropRect.width - 56
                    text: backend.projectPath ? backend.projectPath : qsTr("把 .uvprojx 文件拖到这里")
                    font.pixelSize: backend.projectPath ? 13 : 16
                    font.bold: !backend.projectPath
                    color: backend.projectPath ? win.ink : "#9aa3ad"
                    elide: Text.ElideMiddle
                    horizontalAlignment: Text.AlignHCenter
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    visible: !backend.projectPath
                    text: qsTr("或者点击这里选择文件")
                    font.pixelSize: 12
                    color: win.accent
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    visible: backend.projectPath !== ""
                    text: qsTr("点击可重新选择")
                    font.pixelSize: 11
                    color: win.muted
                }
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: backend.browseFile()
            }
        }

        // ================= 工程信息 =================
        Rectangle {
            id: infoCard
            Layout.fillWidth: true
            Layout.preferredHeight: backend.hasInfo ? 96 : 0
            visible: backend.hasInfo
            radius: 10
            color: "#ffffff"
            border.color: win.line

            RowLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 40

                ColumnLayout {
                    spacing: 6
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("Target"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text { text: backend.info.target; color: win.ink; font.pixelSize: 12; font.bold: true }
                    }
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("器件"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text { text: backend.info.device; color: win.ink; font.pixelSize: 12 }
                    }
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("内核"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text { text: backend.info.core + " · " + backend.info.fpu; color: win.ink; font.pixelSize: 12 }
                    }
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("源文件"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text { text: backend.info.sources + " · " + backend.info.groups + qsTr(" 组"); color: win.ink; font.pixelSize: 12 }
                    }
                }

                ColumnLayout {
                    spacing: 6
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("宏定义"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text {
                            text: backend.info.defines
                            color: win.ink
                            font.pixelSize: 12
                            elide: Text.ElideRight
                            Layout.maximumWidth: 300
                        }
                    }
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("头文件"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text { text: backend.info.includes; color: win.ink; font.pixelSize: 12 }
                    }
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("优化"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text { text: backend.info.optim + " · " + backend.info.std; color: win.ink; font.pixelSize: 12 }
                    }
                    RowLayout {
                        spacing: 8
                        Text { text: qsTr("产物"); color: win.muted; font.pixelSize: 11; Layout.preferredWidth: 56 }
                        Text { text: backend.info.output_name + ".axf / .hex"; color: win.ink; font.pixelSize: 12 }
                    }
                }

                Item { Layout.fillWidth: true }
            }
        }

        // ================= 选项 =================
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            enabled: backend.hasInfo && !backend.busy

            Text { text: qsTr("Target"); color: win.muted; font.pixelSize: 12 }
            ComboBox {
                id: targetBox
                Layout.preferredWidth: 140
                model: backend.targets
                currentIndex: Math.max(0, backend.targets.indexOf(backend.target))
                onActivated: backend.setTarget(currentText)
            }

            Text { text: qsTr("输出目录"); color: win.muted; font.pixelSize: 12 }

            TextField {
                id: outField
                Layout.fillWidth: true
                text: backend.outDir
                placeholderText: qsTr("CMakeLists.txt 生成到哪里")
                font.pixelSize: 12
                selectByMouse: true
                onEditingFinished: backend.setOutDir(text)

                Connections {
                    target: backend
                    function onOutDirChanged() { outField.text = backend.outDir }
                }
            }

            Button {
                text: qsTr("浏览…")
                onClicked: backend.browseOutDir()
            }
        }

        // ================= 操作按钮 =================
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                id: genBtn
                text: qsTr("生成 CMake 工程")
                enabled: backend.hasInfo && !backend.busy
                highlighted: true
                implicitHeight: 34
                implicitWidth: 160
                onClicked: {
                    logModel.append({ "line": "———— 开始生成 ————" })
                    backend.generate(false, true)
                }
            }

            Button {
                text: qsTr("生成并编译验证")
                enabled: backend.hasInfo && !backend.busy
                implicitHeight: 34
                implicitWidth: 150
                onClicked: {
                    logModel.append({ "line": "———— 开始生成 + 编译验证 ————" })
                    backend.generate(true, true)
                }
            }

            BusyIndicator {
                running: backend.busy
                visible: backend.busy
                implicitWidth: 28
                implicitHeight: 28
            }

            Item { Layout.fillWidth: true }

            Button {
                text: qsTr("打开输出目录")
                enabled: backend.hasInfo
                implicitHeight: 34
                onClicked: backend.openOutDir()
            }

            Button {
                text: qsTr("清空日志")
                implicitHeight: 34
                onClicked: logModel.clear()
            }
        }

        // ================= 日志 =================
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 10
            color: "#1e2229"
            border.color: "#2b3038"

            ListView {
                id: logView
                anchors.fill: parent
                anchors.margins: 10
                clip: true
                model: logModel
                spacing: 1
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Text {
                    width: logView.width - 14
                    text: model.line
                    color: model.line.indexOf("✔") >= 0 ? "#7ee787"
                         : model.line.indexOf("警告") >= 0 ? "#ffa657"
                         : model.line.indexOf("失败") >= 0 ? "#ff7b72"
                         : "#c9d1d9"
                    font.family: win.monoFont
                    font.pixelSize: 12
                    wrapMode: Text.WrapAnywhere
                }

                onCountChanged: {
                    if (count > 0)
                        Qt.callLater(positionViewAtEnd)
                }
            }

            Text {
                anchors.centerIn: parent
                visible: logModel.count === 0
                text: qsTr("转换日志会显示在这里")
                color: "#5b626b"
                font.pixelSize: 12
            }
        }

        // ================= 状态栏 =================
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Rectangle {
                width: 7
                height: 7
                radius: 4
                color: backend.busy ? "#f0a020"
                     : (backend.hasInfo ? "#3fb950" : "#b0b6be")
            }

            Text {
                text: backend.status
                color: win.muted
                font.pixelSize: 12
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Text {
                text: qsTr("复用 tools/keil2cmake.py")
                color: "#b0b6be"
                font.pixelSize: 11
            }
        }
    }

    // 整个窗口都接受拖放
    DropArea {
        id: windowDrop
        anchors.fill: parent
        onDropped: function (drop) {
            if (drop.urls.length > 0)
                backend.analyze(drop.urls[0].toString())
        }
    }
}
