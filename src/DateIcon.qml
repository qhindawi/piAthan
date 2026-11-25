import QtQuick 2.12

Canvas {
    id: icon
    property string kind: "calendar"
    implicitWidth: 27
    implicitHeight: 27
    onKindChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d")
        c.clearRect(0, 0, width, height)
        c.save(); c.scale(width / 32, height / 32)
        c.fillStyle = "#ffc34d"; c.strokeStyle = "#ffc34d"; c.lineWidth = 2.4; c.lineCap = "round"; c.lineJoin = "round"
        if (kind === "hijri") {
            c.beginPath(); c.moveTo(16, 3); c.bezierCurveTo(1, 6, 2, 28, 17, 29)
            c.bezierCurveTo(23, 29, 27, 26, 29, 22); c.bezierCurveTo(18, 25, 9, 15, 16, 3); c.closePath(); c.fill()
            c.beginPath()
            for (var i = 0; i < 10; ++i) {
                var angle = -Math.PI / 2 + i * Math.PI / 5
                var radius = i % 2 === 0 ? 4 : 1.6
                if (i === 0) c.moveTo(25 + radius * Math.cos(angle), 8 + radius * Math.sin(angle))
                else c.lineTo(25 + radius * Math.cos(angle), 8 + radius * Math.sin(angle))
            }
            c.closePath(); c.fill()
        } else {
            c.beginPath(); c.moveTo(7, 8); c.lineTo(25, 8); c.quadraticCurveTo(28, 8, 28, 11)
            c.lineTo(28, 26); c.quadraticCurveTo(28, 29, 25, 29); c.lineTo(7, 29)
            c.quadraticCurveTo(4, 29, 4, 26); c.lineTo(4, 11); c.quadraticCurveTo(4, 8, 7, 8); c.closePath(); c.stroke()
            c.fillRect(5, 10, 22, 4)
            c.beginPath(); c.moveTo(10, 4); c.lineTo(10, 10); c.moveTo(22, 4); c.lineTo(22, 10); c.stroke()
        }
        c.restore()
    }
}
