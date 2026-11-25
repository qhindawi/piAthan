import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ColumnLayout {
    id: home
    property var host
    property var info: host.dashboardData || ({})
    property var quran: host.quran || ({})
    property color gold: "#ffc34d"
    property color cream: "#fff4e3"
    spacing: 8

    RowLayout {
        Layout.fillWidth: true; Layout.minimumHeight: 85; Layout.preferredHeight: 85; Layout.maximumHeight: 85; spacing: 18
        Label { text: home.info.clock || "--:--"; color: home.cream; font.pixelSize: 66; font.bold: true; Layout.preferredWidth: 220; horizontalAlignment: Text.AlignHCenter }
        Rectangle { color: home.gold; Layout.preferredWidth: 1; Layout.preferredHeight: 55 }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 5
            RowLayout {
                Layout.fillWidth: true; spacing: 9
                DateIcon { kind: "hijri"; Layout.preferredWidth: 27; Layout.preferredHeight: 27 }
                Label { text: home.info.schedule && home.info.schedule.hijri ? home.info.schedule.hijri.day + " " + home.info.schedule.hijri.month + " " + home.info.schedule.hijri.year + " AH" : "Hijri date unavailable"; color: home.cream; font.pixelSize: 20; elide: Text.ElideRight; Layout.fillWidth: true }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 9
                DateIcon { kind: "calendar"; Layout.preferredWidth: 27; Layout.preferredHeight: 27 }
                Label { text: home.info.date || ""; color: home.cream; font.pixelSize: 19; elide: Text.ElideRight; Layout.fillWidth: true }
            }
        }
        Rectangle { color: home.gold; Layout.preferredWidth: 1; Layout.preferredHeight: 55 }
        Item {
            Layout.preferredWidth: 285; Layout.fillHeight: true
            RowLayout {
                anchors.fill: parent
                Label { text: home.info.weather && home.info.weather.current ? home.info.weather.current.icon : "☁"; color: home.cream; font.pixelSize: 48 }
                ColumnLayout {
                    Layout.fillWidth: true
                    Label { text: home.info.weather && home.info.weather.current ? home.info.weather.current.temperature_c + "°C" : "--°C"; color: home.cream; font.pixelSize: 30; font.bold: true }
                    Label { text: home.info.weather && home.info.weather.current ? home.info.weather.current.condition + " · " + ((home.info.weather.location || {}).name || "") : "Weather unavailable"; color: home.cream; font.pixelSize: 15; elide: Text.ElideRight; Layout.fillWidth: true }
                }
            }
            MouseArea { anchors.fill: parent; onClicked: host.page = "weather" }
        }
    }
    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#5e687c" }
    Label {
        property var state: (home.info.status || {}).schedule || ({})
        visible: !!state.state && (state.state !== "ready" || !!state.using_cache)
        text: "Schedule: " + state.state + (state.using_cache ? " (using cached times)" : "") + (state.attempt ? "  " + state.attempt + "/" + state.max_attempts : "") + (state.next_retry ? "  next retry: " + state.next_retry.slice(11, 16) : "")
        color: home.gold; font.pixelSize: 12; Layout.fillWidth: true
    }
    RowLayout {
        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 230; Layout.preferredHeight: 330; spacing: 10
        Item {
            id: nextPanel
            Layout.preferredWidth: parent.width * 0.43; Layout.fillHeight: true; Layout.minimumHeight: 230
            Canvas {
                id: archCanvas
                anchors.fill: parent
                property url dawnImage: Qt.resolvedUrl("../assets/prayer-dawn.png")
                Component.onCompleted: loadImage(dawnImage)
                onImageLoaded: requestPaint()
                onPaint: {
                    var c = getContext("2d"), w = width, h = height
                    c.clearRect(0,0,w,h); c.beginPath(); c.moveTo(3,h-16); c.lineTo(3,h*0.42)
                    c.quadraticCurveTo(3,h*0.27,w*0.12,h*0.24)
                    c.bezierCurveTo(w*0.17,h*0.10,w*0.40,h*0.10,w*0.50,4)
                    c.bezierCurveTo(w*0.60,h*0.10,w*0.83,h*0.10,w*0.88,h*0.24)
                    c.quadraticCurveTo(w-3,h*0.27,w-3,h*0.42); c.lineTo(w-3,h-16)
                    c.quadraticCurveTo(w-3,h-3,w-18,h-3); c.lineTo(18,h-3); c.quadraticCurveTo(3,h-3,3,h-16)
                    c.closePath(); c.fillStyle="#0a1931"; c.fill()
                    c.save(); c.clip()
                    if (isImageLoaded(dawnImage)) {
                        // Scale both dimensions together and crop at the sides.
                        var sceneWidth = w * 2.35
                        var sceneHeight = sceneWidth * 701 / 2243
                        c.drawImage(dawnImage, (w - sceneWidth) / 2, h - sceneHeight - 3, sceneWidth, sceneHeight)
                    }
                    c.restore(); c.lineWidth=4; c.strokeStyle="#e5ad4c"; c.stroke()
                }
            }
            Image {
                anchors.horizontalCenter: parent.horizontalCenter; anchors.top: parent.top; anchors.topMargin: 12
                width: 52; height: 62; fillMode: Image.PreserveAspectFit
                source: "../assets/fanoos.png"
            }
            Column {
                anchors.horizontalCenter: parent.horizontalCenter; anchors.top: parent.top; anchors.topMargin: Math.max(74, parent.height * 0.21); spacing: 2; width: parent.width - 24
                Label { width: parent.width; text: "Next prayer"; horizontalAlignment: Text.AlignHCenter; color: home.cream; font.pixelSize: 26 }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter; spacing: 8
                    Label { id: prayerName; text: home.info.nextName || "Unavailable"; color: home.gold; font.pixelSize: 40; font.bold: true }
                    Label { text: home.info.nextName ? "in" : ""; color: home.cream; font.pixelSize: 26; anchors.baseline: prayerName.baseline }
                }
                Text { width: parent.width; height: 76; text: home.info.countdown || "--:--:--"; color: home.cream; font.pixelSize: 66; minimumPixelSize: 28; fontSizeMode: Text.HorizontalFit; font.bold: true; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            }
            Button {
                anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.leftMargin: 14; anchors.bottomMargin: 14
                width: 48; height: 48; onClicked: host.openVolume("athan")
                background: Rectangle { color: "#101d33"; radius: width/2; border.color: home.gold; border.width: 2 }
                contentItem: Item {
                    Canvas {
                        anchors.centerIn: parent; width: 27; height: 27
                        onPaint: {
                            var c = getContext("2d")
                            c.clearRect(0, 0, width, height); c.fillStyle = home.cream; c.strokeStyle = home.cream; c.lineWidth = 2; c.lineCap = "round"
                            c.beginPath(); c.moveTo(3, 10); c.lineTo(8, 10); c.lineTo(14, 5); c.lineTo(14, 22); c.lineTo(8, 17); c.lineTo(3, 17); c.closePath(); c.fill()
                            c.beginPath(); c.arc(15, 13.5, 5, -0.75, 0.75); c.stroke()
                            c.beginPath(); c.arc(15, 13.5, 9, -0.75, 0.75); c.stroke()
                        }
                    }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 230; color: "#111e34"; radius: 13; border.color: "#6e7888"; clip: true
            ColumnLayout {
                anchors.fill: parent; spacing: 0
                Label { text: "Prayer"; color: home.gold; font.pixelSize: 23; font.bold: true; Layout.fillWidth: true; Layout.preferredHeight: 46; leftPadding: 24; verticalAlignment: Text.AlignVCenter }
                Repeater {
                    model: home.info.rows || []
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        color: modelData.name === home.info.nextName ? "#55452e" : "transparent"
                        Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: "#626376" }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 17; anchors.rightMargin: 12; spacing: 8
                            PrayerIcon { prayer: modelData.name; Layout.preferredWidth: 38; Layout.preferredHeight: 38 }
                            Label { text: modelData.name; color: home.cream; font.pixelSize: 23; font.bold: true; Layout.preferredWidth: 100 }
                            Label { text: modelData.relative_day ? modelData.relative_day + " at" : "unavailable"; color: home.cream; font.pixelSize: 14; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight }
                            Label { text: modelData.time || "--:--"; color: home.cream; font.pixelSize: 25; font.bold: true; Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                            Label { text: modelData.first_athan ? "First Athan\n" + modelData.first_athan.time : ""; color: home.cream; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; Layout.preferredWidth: 76 }
                        }
                    }
                }
            }
        }
    }
    Rectangle {
        Layout.fillWidth: true; Layout.minimumHeight: 112; Layout.preferredHeight: 112; Layout.maximumHeight: 112; color: "#111d32"; radius: 14; border.color: "#78808f"
        Item {
            anchors.fill: parent; anchors.margins: 10
            Rectangle {
                id: cover
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                width: 85; height: 85; radius: 6; color: "#27354b"; border.color: home.gold
                Image { anchors.fill: parent; anchors.margins: 3; fillMode: Image.PreserveAspectFit; source: home.quran.artwork ? "file://" + home.quran.artwork : "" }
                Label { anchors.centerIn: parent; visible: !home.quran.artwork; text: "Qur’an"; color: home.gold }
                MouseArea { anchors.fill: parent; onClicked: { host.page = "quran"; host.request("quran_library") } }
            }
            Rectangle { id: coverDivider; anchors.left: cover.right; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; width: 1; height: 75; color: home.gold }
            Item {
                anchors.left: coverDivider.right; anchors.leftMargin: 12; anchors.right: playback.left; anchors.rightMargin: 12; anchors.top: parent.top; anchors.bottom: parent.bottom
                Column {
                    anchors.verticalCenter: parent.verticalCenter; width: parent.width; spacing: 4
                    Label { width: parent.width; text: home.quran.album || "No folder selected"; color: home.cream; font.pixelSize: 18; elide: Text.ElideRight }
                    Label { width: parent.width; text: home.quran.title || "No track selected"; color: home.cream; font.pixelSize: 26; font.bold: true; elide: Text.ElideRight }
                }
                MouseArea { anchors.fill: parent; onClicked: { host.page = "quran"; host.request("quran_library") } }
            }
            ColumnLayout {
                id: playback
                anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                RowLayout {
                    spacing: 5; Layout.alignment: Qt.AlignHCenter
                    Repeater {
                        model: [{text:"◀◀", action:"quran_previous"}, {text:home.quran.playing ? "❚❚" : "▶", action:home.quran.playing ? "quran_pause" : "quran_play"}, {text:"▶▶", action:"quran_next"}]
                        Button {
                            id: playbackButton
                            Layout.preferredWidth: 52; Layout.preferredHeight: 52; text: modelData.text; onClicked: host.request(modelData.action)
                            background: Rectangle { radius: width/2; color: index === 1 ? "#ffc354" : "#101d33"; border.color: home.gold; border.width: 2 }
                            contentItem: Item { Label { anchors.centerIn: parent; text: playbackButton.text; color: index === 1 ? "#081830" : home.cream; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.pixelSize: 20 } }
                        }
                    }
                }
                Label { text: home.quran.time_display || "--:-- / --:--"; color: home.cream; font.pixelSize: 14; Layout.alignment: Qt.AlignHCenter }
            }
            Rectangle { anchors.left: playback.right; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; width: 1; height: 70; color: home.gold }
            DashboardButton { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: home.quran.timer_active ? "Stop timer\n" + Math.floor((home.quran.timer_remaining_seconds || 0)/60) + " min left" : "◷  Start timer"; onClicked: host.request(home.quran.timer_active ? "quran_timer_cancel" : "quran_timer_start") }
        }
    }
}
