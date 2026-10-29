import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtPythonDemo

/*
 * Main.qml - the demo UI.  Every button sends a JSON request to the Python
 * back end through `backend.call(method, params)`; the answer comes back in
 * `onReplied` (or `onFailed`) and is shown next to the button.
 */
ApplicationWindow {
    id: win
    width: 760
    height: 620
    visible: true
    title: qsTr("QtPythonDemo - Qt front end + Python back end")

    Backend {
        id: backend
        Component.onCompleted: start()

        onReplied: (id, method, result) => {
            if (method === "add")        addResult.text = qsTr("= %1").arg(result)
            else if (method === "stats") statsResult.text = qsTr("count %1   mean %2   std %3   min %4   max %5")
                                             .arg(result.count).arg(result.mean.toFixed(3)).arg(result.std.toFixed(3))
                                             .arg(result.min).arg(result.max)
            else if (method === "info")  infoResult.text = qsTr("Python %1, numpy %2\n%3")
                                             .arg(result.python).arg(result.numpy).arg(result.executable)
            else if (method === "slow")  slowResult.text = qsTr("done after %1 s").arg(result.slept)
        }
        onFailed: (id, method, error) => {
            if (method === "add")        addResult.text = error
            else if (method === "stats") statsResult.text = error
            else if (method === "info")  infoResult.text = error
            else if (method === "slow")  slowResult.text = error
        }
        onLogLine: (line) => {
            log.append(line)
            if (log.lineCount > 300) log.remove(0, log.text.indexOf("\n") + 1)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        // ---- status ----
        RowLayout {
            Rectangle { width: 12; height: 12; radius: 6
                        color: backend.state === "ready" ? "#2e9e5b" : backend.state === "starting" ? "#e8a52a" : "#c23b5a" }
            Label { text: qsTr("Back end: %1").arg(backend.state); font.bold: true }
            Label { text: backend.lastError; color: "#c23b5a"; Layout.fillWidth: true; elide: Text.ElideRight }
            Button { text: backend.state === "ready" ? qsTr("Stop") : qsTr("Start")
                     onClicked: backend.state === "ready" ? backend.stop() : backend.start() }
        }
        Label { text: backend.command; color: "#666"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideMiddle }

        // ---- 1. add two numbers (pure Python) ----
        GroupBox {
            title: qsTr("1. add(a, b)  -  pure Python")
            Layout.fillWidth: true
            RowLayout {
                anchors.fill: parent
                TextField { id: a; text: "2"; Layout.preferredWidth: 80; validator: DoubleValidator {} }
                Label { text: "+" }
                TextField { id: b; text: "3"; Layout.preferredWidth: 80; validator: DoubleValidator {} }
                Button { text: qsTr("Add"); enabled: backend.state === "ready"
                         onClicked: backend.call("add", { a: Number(a.text), b: Number(b.text) }) }
                Label { id: addResult; font.bold: true; Layout.fillWidth: true }
            }
        }

        // ---- 2. statistics (numpy: a third-party package shipped in the bundle) ----
        GroupBox {
            title: qsTr("2. stats(values)  -  numpy")
            Layout.fillWidth: true
            ColumnLayout {
                anchors.fill: parent
                RowLayout {
                    TextField { id: values; text: "4, 8, 15, 16, 23, 42"; Layout.fillWidth: true
                                placeholderText: qsTr("numbers separated by commas") }
                    Button { text: qsTr("Compute"); enabled: backend.state === "ready"
                             onClicked: backend.call("stats", { values: values.text.split(",").map(Number) }) }
                }
                Label { id: statsResult; font.bold: true }
            }
        }

        // ---- 3. info + a slow call (the UI never blocks) ----
        GroupBox {
            title: qsTr("3. info()  and  slow(seconds)  -  asynchronous")
            Layout.fillWidth: true
            ColumnLayout {
                anchors.fill: parent
                RowLayout {
                    Button { text: qsTr("Back end info"); enabled: backend.state === "ready"
                             onClicked: backend.call("info") }
                    Label { id: infoResult; Layout.fillWidth: true; wrapMode: Text.Wrap }
                }
                RowLayout {
                    Button { text: qsTr("Slow call (3 s)"); enabled: backend.state === "ready"
                             onClicked: { slowResult.text = qsTr("waiting..."); backend.call("slow", { seconds: 3 }) } }
                    Label { id: slowResult; Layout.fillWidth: true }
                    BusyIndicator { running: slowResult.text === qsTr("waiting..."); implicitHeight: 24 }
                }
            }
        }

        // ---- the JSON traffic, as it happens ----
        Label { text: qsTr("Traffic (-> request, <- response, py: stderr)") }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            TextArea {
                id: log
                readOnly: true
                font.family: "Consolas"
                font.pixelSize: 12
                wrapMode: TextEdit.NoWrap
                background: Rectangle { color: "#f6f6f6"; border.color: "#ccc" }
                onTextChanged: cursorPosition = length
            }
        }
    }
}
