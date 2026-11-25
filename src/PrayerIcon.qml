import QtQuick 2.12

Canvas {
    id: icon
    property string prayer: ""
    implicitWidth: 38
    implicitHeight: 38
    onPrayerChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d")
        ctx.clearRect(0, 0, width, height)
        ctx.save()
        ctx.scale(width / 40, height / 40)
        ctx.lineWidth = 2
        ctx.lineCap = "round"
        ctx.strokeStyle = "#ffc34d"
        ctx.fillStyle = "#ffc34d"
        if (prayer === "Fajr") {
            // A crescent above the first glow of dawn; the sun is still below the horizon.
            var glow = ctx.createRadialGradient(20, 29, 0, 20, 29, 15)
            glow.addColorStop(0, "rgba(255, 195, 77, 0.65)")
            glow.addColorStop(1, "rgba(255, 195, 77, 0)")
            ctx.fillStyle = glow
            ctx.fillRect(4, 14, 32, 18)
            ctx.fillStyle = "#ffc34d"
            ctx.beginPath(); ctx.moveTo(5, 32); ctx.quadraticCurveTo(20, 24, 35, 32)
            ctx.quadraticCurveTo(20, 29, 5, 32); ctx.closePath(); ctx.fill()
            ctx.beginPath(); ctx.moveTo(20, 4); ctx.bezierCurveTo(10, 7, 12, 20, 21, 21)
            ctx.bezierCurveTo(25, 21, 28, 19, 29, 16)
            ctx.bezierCurveTo(22, 18, 16, 12, 20, 4); ctx.closePath(); ctx.fill()
        } else if (prayer === "Isha") {
            ctx.beginPath()
            ctx.moveTo(20, 4)
            ctx.bezierCurveTo(2, 8, 4, 34, 21, 35)
            ctx.bezierCurveTo(27, 35, 32, 32, 34, 27)
            ctx.bezierCurveTo(21, 30, 11, 18, 20, 4)
            ctx.closePath()
            ctx.fill()
            function star(x, y, radius) {
                ctx.beginPath()
                for (var p = 0; p < 8; ++p) {
                    var a = -Math.PI / 2 + p * Math.PI / 4
                    var r = p % 2 === 0 ? radius : radius * 0.35
                    if (p === 0) ctx.moveTo(x + r * Math.cos(a), y + r * Math.sin(a))
                    else ctx.lineTo(x + r * Math.cos(a), y + r * Math.sin(a))
                }
                ctx.closePath(); ctx.fill()
            }
            star(29, 9, 3)
            star(24, 16, 2)
            star(33, 18, 1.6)
        } else if (prayer === "Dhuhr") {
            ctx.beginPath(); ctx.arc(20, 20, 7, 0, 2 * Math.PI); ctx.fill()
            for (var i = 0; i < 8; ++i) {
                var a = i * Math.PI / 4
                ctx.beginPath()
                ctx.moveTo(20 + 10 * Math.cos(a), 20 + 10 * Math.sin(a))
                ctx.lineTo(20 + 15 * Math.cos(a), 20 + 15 * Math.sin(a))
                ctx.stroke()
            }
        } else {
            var horizon = prayer === "Maghrib" ? 26 : 25
            ctx.beginPath(); ctx.arc(20, horizon, 9, Math.PI, 2 * Math.PI); ctx.fill()
            ctx.beginPath(); ctx.moveTo(5, horizon + 2); ctx.lineTo(35, horizon + 2); ctx.stroke()
            if (prayer === "Maghrib") {
                ctx.save(); ctx.translate(20, horizon + 7); ctx.scale(8, 1.5)
                ctx.beginPath(); ctx.arc(0, 0, 1, 0, 2 * Math.PI); ctx.fill(); ctx.restore()
            }
            if (prayer !== "Maghrib") {
                for (var j = 0; j < 3; ++j) {
                    var angle = Math.PI + (j + 1) * Math.PI / 4
                    ctx.beginPath()
                    ctx.moveTo(20 + 12 * Math.cos(angle), horizon + 12 * Math.sin(angle))
                    ctx.lineTo(20 + 16 * Math.cos(angle), horizon + 16 * Math.sin(angle))
                    ctx.stroke()
                }
            }
        }
        ctx.restore()
    }
}
