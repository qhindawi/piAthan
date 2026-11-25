import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12
import QtQuick.Window 2.12

ApplicationWindow {
    id: window
    visible: false
    width: 1024; height: 600
    color: "#081830"
    title: "piAthan"
    property string page: "home"
    property var dashboardData: dashboard.data
    property var audio: window.dashboardData.audio || ({})
    property var quran: audio.quran || ({})
    property var library: audio.library || ({ albums: [] })
    property int albumIndex: 0
    property int trackIndex: 0
    property bool timerInitialized: false
    property string volumeProfile: "athan"

    function request(action, args) { dashboard.command(action, args || {}) }
    function openVolume(profile) { volumeProfile = profile; request("volume_profiles_status"); volumePopup.open() }
    onPageChanged: if (page === "quran") timerInitialized = false

    Connections {
        target: dashboard
        function onChanged() {
            if (page === "quran" && !timerInitialized && quran.timer_duration_seconds !== undefined) {
                hours.currentIndex = Math.floor(quran.timer_duration_seconds / 3600)
                minutes.currentIndex = Math.floor(quran.timer_duration_seconds / 60) % 60
                timerInitialized = true
            }
        }
    }

    Timer {
        interval: 1000; repeat: true; running: true; triggeredOnStart: true
        onTriggered: {
            request("quran_status")
        }
    }
    Timer {
        interval: 3000; repeat: true; running: page === "quran"; triggeredOnStart: true
        onTriggered: request("quran_library")
    }

    Rectangle {
        anchors.fill: parent; color: "#081830"
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 16; spacing: 8
            RowLayout {
                visible: page !== "home"; Layout.fillWidth: true
                DashboardButton { text: "‹ Home"; onClicked: page = "home" }
                Label { text: page === "weather" ? "Weather" : "Qur’an"; color: "#f1c66e"; font.pixelSize: 26; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
            }

            HomeScreen { visible: page === "home"; Layout.fillWidth: true; Layout.fillHeight: true; host: window }

            ColumnLayout {
                visible: page === "quran"; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
                Label { text: "Folder"; color: "white"; font.pixelSize: 18 }
                ComboBox { id: albums; Layout.fillWidth: true; model: library.albums || []; textRole: "folder"; onActivated: { albumIndex = currentIndex; trackIndex = 0 } }
                Label { text: "Track"; color: "white"; font.pixelSize: 18 }
                ComboBox { id: tracks; Layout.fillWidth: true; model: (library.albums && library.albums[albumIndex]) ? library.albums[albumIndex].tracks : []; textRole: "title"; onActivated: { trackIndex = currentIndex; request("quran_select", { album: albums.currentText, track: tracks.currentIndex }) } }
                Label { text: "Timer duration"; color: "#f1c66e"; font.pixelSize: 20 }
                RowLayout {
                    Label { text: "Hours"; color: "white" }
                    Tumbler { id: hours; model: 24; visibleItemCount: 3; Layout.preferredWidth: 90; Layout.preferredHeight: 100 }
                    Label { text: "Minutes"; color: "white" }
                    Tumbler { id: minutes; model: 60; visibleItemCount: 3; Layout.preferredWidth: 90; Layout.preferredHeight: 100 }
                    DashboardButton { text: "Set duration"; onClicked: request("quran_timer_set", { hours: hours.currentIndex, minutes: minutes.currentIndex }) }
                }
                RowLayout {
                    DashboardButton { text: "Qur’an volume"; onClicked: openVolume("quran") }
                    DashboardButton { text: "Refresh"; onClicked: request("quran_library", { refresh: true }) }
                }
                Item { Layout.fillHeight: true }
            }

            ColumnLayout {
                visible: page === "weather"; Layout.fillWidth: true; Layout.fillHeight: true
                Label { text: window.dashboardData.weather && window.dashboardData.weather.current ? (((window.dashboardData.weather.location || {}).name || "") + "  " + window.dashboardData.weather.current.icon + "  " + window.dashboardData.weather.current.temperature_c + "°C  " + window.dashboardData.weather.current.condition) : "Weather unavailable"; color: "white"; font.pixelSize: 30 }
                Label { text: window.dashboardData.weather && window.dashboardData.weather.fetched_at ? "Updated " + window.dashboardData.weather.fetched_at + (window.dashboardData.weather.age_minutes !== undefined ? "  (" + window.dashboardData.weather.age_minutes + " min ago)" : "") : "No cached forecast"; color: "#d3c3a4" }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; orientation: ListView.Horizontal; spacing: 8; clip: true
                    model: (window.dashboardData.weather || {}).hourly || []
                    delegate: Rectangle {
                        width: 110; height: 120; color: "#162844"; radius: 8
                        Column { anchors.centerIn: parent; spacing: 8
                            Label { text: modelData.time.slice(11); color: "white" }
                            Label { text: modelData.icon || "?"; color: "#f1c66e"; font.pixelSize: 24 }
                            Label { text: modelData.temperature_c + "°C"; color: "#f1c66e"; font.pixelSize: 22 }
                        }
                    }
                }
            }
            Label { visible: audio.ok === false; text: audio.error || ""; color: "#ffb2a7"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        }
    }
    Dialog {
        id: volumePopup
        width: 300
        anchors.centerIn: parent; modal: true; title: volumeProfile === "athan" ? "Prayer volume" : "Qur’an volume"
        standardButtons: Dialog.Close
        contentItem: RowLayout {
            DashboardButton { text: "−"; onClicked: request("volume_profile_step", { profile: volumeProfile, delta: -0.05 }) }
            Label { text: audio.profiles && audio.profiles[volumeProfile] !== undefined ? Math.round(audio.profiles[volumeProfile] * 100) + "%" : "--"; font.pixelSize: 30 }
            DashboardButton { text: "+"; onClicked: request("volume_profile_step", { profile: volumeProfile, delta: 0.05 }) }
        }
    }
}
