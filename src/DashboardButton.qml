import QtQuick 2.12
import QtQuick.Controls 2.12

Button {
    id: control
    implicitHeight: 48
    implicitWidth: Math.max(64, label.implicitWidth + 26)
    contentItem: Text {
        id: label
        text: control.text
        color: control.enabled ? "#f4d18b" : "#737c8b"
        font.pixelSize: 16
        font.bold: true
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
    }
    background: Rectangle {
        color: control.down ? "#66502f" : "#162844"
        border.color: "#d7ac55"
        border.width: 1
        radius: 8
    }
}
