import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "meviusisback.botty"
  ipcTarget: "meviusisback.botty"
  manageIpc: false

  // Bar slot sizing
  implicitWidth: root.barShowsText ? Math.max(dataButton.implicitWidth, Style.space(120)) : button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.6)
  readonly property color muted: alpha(foreground, 0.45)
  readonly property color accent: Color.accent
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color track: Style.selectedFillFor(foreground, Color.accent)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // Plugin settings
  readonly property int refreshIntervalSec: Math.max(1, Number(root.setting("refreshIntervalSec", 2)) || 2)
  readonly property string barDisplay: String(root.setting("barDisplay", "Icon"))
  readonly property bool showStatusInBar: Boolean(root.setting("showStatusInBar", true))
  readonly property bool barShowsText: barDisplay.toLowerCase() === "status" || barDisplay.toLowerCase() === "compact"

  // Live state
  property var rawStatus: ({
    ok: true,
    state: "idle", // idle, working, waiting, error
    headline: "Ready",
    last_query: "",
    last_answer: "",
    active_model: "ox-alpha-free",
    active_provider: "opencode-go",
    session_turns: 0,
    memory_count: 0,
    skills_count: 0,
    has_active_work: false
  })

  property var historyData: ({ session_id: "botty-widget", messages: [] })
  property var modelsData: []
  property var memoriesData: []
  property var skillsData: []

  property string currentView: "chat" // "chat" | "memories" | "settings"
  property string promptText: ""
  property string attachedImagePath: ""
  property string attachedImageFilename: ""
  property bool screenContextEnabled: false
  property var lastCaptureInfo: null

  property bool isProcessing: rawStatus && rawStatus.state === "working"

  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  function scriptPath() {
    return Qt.resolvedUrl("botty_backend.py").toString().replace(/^file:\/\//, "")
  }

  function fetchStatus() {
    if (!statusProc.running) {
      statusProc.running = true
    }
  }

  function fetchHistory() {
    if (!historyProc.running) {
      historyProc.running = true
    }
  }

  function fetchModels() {
    if (!modelsProc.running) {
      modelsProc.running = true
    }
  }

  function fetchMemories() {
    if (!memoriesProc.running) {
      memoriesProc.running = true
    }
  }

  function fetchSkills() {
    if (!skillsProc.running) {
      skillsProc.running = true
    }
  }

  function sendQuery(text, imagePath, useScreen) {
    var q = text || promptInput.text || ""
    var img = imagePath || root.attachedImagePath || ""
    var scr = (useScreen !== undefined) ? useScreen : root.screenContextEnabled

    if (!q.trim() && !img && !scr) return

    var cmd = ["python3", root.scriptPath(), "ask", q]
    if (img) {
      cmd.push("--image")
      cmd.push(img)
    }
    if (scr) {
      cmd.push("--screen")
    }

    askProc.command = cmd
    askProc.running = true

    // Optimistically update status and clear input
    root.rawStatus.state = "working"
    root.rawStatus.headline = "Botty thinking…"
    promptInput.text = ""
    root.promptText = ""
    root.attachedImagePath = ""
    root.attachedImageFilename = ""
    root.screenContextEnabled = false
    root.fetchHistory()
  }

  function captureScreenNow() {
    captureProc.command = ["python3", root.scriptPath(), "capture", "--mode", "activewindow"]
    captureProc.running = true
  }

  function attachClipboardImage() {
    clipProc.command = ["python3", root.scriptPath(), "clip-image"]
    clipProc.running = true
  }

  function clearChat() {
    clearProc.command = ["python3", root.scriptPath(), "clear"]
    clearProc.running = true
  }

  function selectModel(modelId, providerId) {
    var cmd = ["python3", root.scriptPath(), "set-model", modelId]
    if (providerId) {
      cmd.push("--provider")
      cmd.push(providerId)
    }
    setModelProc.command = cmd
    setModelProc.running = true
  }

  function copyText(text) {
    if (!text) return
    copyProc.command = ["python3", root.scriptPath(), "copy", text]
    copyProc.running = true
  }

  function compactMemoryNow() {
    compactProc.command = ["python3", root.scriptPath(), "compact"]
    compactProc.running = true
  }

  function addMemoryNow(text, isUser) {
    if (!text || !text.trim()) return
    var cmd = ["python3", root.scriptPath(), "add-memory", text]
    if (isUser) cmd.push("--user")
    addMemProc.command = cmd
    addMemProc.running = true
  }

  // Periodic status poll
  Timer {
    interval: {
      if (root.opened) return 1000
      if (root.rawStatus.state === "working") return 1200
      return root.refreshIntervalSec * 1000
    }
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      root.fetchStatus()
      if (root.opened && root.currentView === "chat") {
        root.fetchHistory()
      }
    }
  }

  // Live pulsing animation for active thinking state
  property real pulseOpacity: 1.0
  SequentialAnimation on pulseOpacity {
    running: root.rawStatus.state === "working"
    loops: Animation.Infinite
    NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutQuad }
    NumberAnimation { to: 1.0; duration: 700; easing.type: Easing.InOutQuad }
  }

  // ------------------------------------------------------------- Process Handlers
  Process {
    id: statusProc
    command: ["python3", root.scriptPath(), "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.rawStatus = data
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: historyProc
    command: ["python3", root.scriptPath(), "history"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok && data.history) {
            root.historyData = data.history
            if (chatListView.count > 0) {
              chatListView.positionViewAtEnd()
            }
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: askProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchHistory()
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchHistory()
      }
    }
  }

  Process {
    id: captureProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.attachedImagePath = data.image_path
            root.attachedImageFilename = data.filename
            root.screenContextEnabled = true
            root.lastCaptureInfo = data
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: clipProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.attachedImagePath = data.image_path
            root.attachedImageFilename = data.filename
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: clearProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchHistory()
      }
    }
  }

  Process {
    id: modelsProc
    command: ["python3", root.scriptPath(), "models"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.modelsData = data.models || []
            root.rawStatus.active_model = data.active_model
            root.rawStatus.active_provider = data.active_provider
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: setModelProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchModels()
      }
    }
  }

  Process {
    id: memoriesProc
    command: ["python3", root.scriptPath(), "memories"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.memoriesData = data.memories || []
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: skillsProc
    command: ["python3", root.scriptPath(), "skills"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.skillsData = data.skills || []
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: compactProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchMemories()
      }
    }
  }

  Process {
    id: addMemProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchMemories()
      }
    }
  }

  Process {
    id: copyProc
  }

  // ------------------------------------------------------------- IPC Handlers
  IpcHandler {
    enabled: true
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.fetchStatus(); root.fetchHistory() }
    function ask(query: string): void { root.open(); root.sendQuery(query, "", false) }
    function captureAndAsk(query: string): void { root.open(); root.sendQuery(query, "", true) }
    function clear(): void { root.clearChat() }
  }

  IpcHandler {
    enabled: true
    target: "botty"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.fetchStatus(); root.fetchHistory() }
    function ask(query: string): void { root.open(); root.sendQuery(query, "", false) }
    function captureAndAsk(query: string): void { root.open(); root.sendQuery(query, "", true) }
    function clear(): void { root.clearChat() }
  }

  // ------------------------------------------------------------- Bar Button (Icon Mode)
  BarIconButton {
    id: button
    anchors.fill: parent
    visible: !root.barShowsText
    bar: root.bar
    text: Model.statusIcon(root.rawStatus.state)
    tooltipText: Model.getTooltipText(root.rawStatus)
    active: root.rawStatus.state === "working" || root.rawStatus.state === "waiting"
    activeColor: Model.statusColor(root.rawStatus.state, root.foreground, root.accent, root.urgent)
    opacity: root.rawStatus.state === "working" ? root.pulseOpacity : 1.0

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) {
        root.captureScreenNow()
        root.open()
      } else {
        root.toggle()
      }
    }

    // Status Indicator Dot / Badge
    Rectangle {
      id: iconDot
      visible: root.rawStatus.state !== "idle" || root.rawStatus.memory_count > 0
      anchors.top: parent.top
      anchors.right: parent.right
      anchors.topMargin: Style.space(3)
      anchors.rightMargin: Style.space(3)
      width: Style.space(6)
      height: Style.space(6)
      radius: width / 2
      color: Model.statusColor(root.rawStatus.state, root.accent, root.accent, root.urgent)
    }
  }

  // ------------------------------------------------------------- Bar Button (Data / Status Mode)
  Item {
    id: dataButton
    anchors.fill: parent
    visible: root.barShowsText

    function triggerPress(buttonCode) {
      if (root.bar) root.bar.hideTooltip(dataButton)
      if (buttonCode === Qt.RightButton) {
        root.captureScreenNow()
        root.open()
      } else {
        root.toggle()
      }
    }

    implicitWidth: chipRow.implicitWidth + Style.space(16)
    implicitHeight: chipRow.implicitHeight

    RowLayout {
      id: chipRow
      anchors.centerIn: parent
      spacing: Style.space(6)

      Text {
        text: Model.statusIcon(root.rawStatus.state)
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        color: Model.statusColor(root.rawStatus.state, root.foreground, root.accent, root.urgent)
        opacity: root.rawStatus.state === "working" ? root.pulseOpacity : 1.0
      }

      Text {
        text: Model.getBarHeadline(root.rawStatus, 24)
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        color: root.foreground
        elide: Text.ElideRight
      }

      Rectangle {
        width: Style.space(6)
        height: Style.space(6)
        radius: width / 2
        color: Model.statusColor(root.rawStatus.state, root.accent, root.accent, root.urgent)
      }
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      hoverEnabled: true
      onClicked: function(mouse) { dataButton.triggerPress(mouse.button) }
      onContainsMouseChanged: {
        if (!root.bar) return
        if (containsMouse) root.bar.showTooltip(dataButton, Model.getTooltipText(root.rawStatus))
        else root.bar.hideTooltip(dataButton)
      }
      Component.onCompleted: if (root.bar && root.bar.registerClickTarget) root.bar.registerClickTarget(dataButton)
      Component.onDestruction: if (root.bar && root.bar.unregisterClickTarget) root.bar.unregisterClickTarget(dataButton)
    }
  }

  // ------------------------------------------------------------- Main Popup Card
  PopupCard {
    id: popup
    anchorItem: root.barShowsText ? dataButton : button
    bar: root.bar
    owner: root
    open: root.opened
    contentWidth: Style.space(540)
    contentHeight: Style.space(700)

    onOpenChanged: {
      if (open) {
        root.fetchStatus()
        root.fetchHistory()
        root.fetchModels()
        promptInput.forceActiveFocus()
      }
    }

    ColumnLayout {
      anchors.fill: parent
      anchors.margins: Style.space(6)
      spacing: Style.space(10)

      // ========================================================= Header Row
      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(12)

        // Botty Avatar / Status Icon
        BorderSurface {
          implicitWidth: Style.space(38)
          implicitHeight: Style.space(38)
          radius: Style.space(19)
          color: root.alpha(Model.statusColor(root.rawStatus.state, root.accent, root.accent, root.urgent), 0.15)
          borderSpec: Border.controlSpec("normal", root.alpha(Model.statusColor(root.rawStatus.state, root.accent, root.accent, root.urgent), 0.4), root.accent)

          Text {
            anchors.centerIn: parent
            text: Model.statusIcon(root.rawStatus.state)
            color: Model.statusColor(root.rawStatus.state, root.foreground, root.accent, root.urgent)
            font.family: root.fontFamily
            font.pixelSize: Style.space(22)
            opacity: root.rawStatus.state === "working" ? root.pulseOpacity : 1.0
          }
        }

        // Title and Status details
        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(2)

          RowLayout {
            spacing: Style.space(6)

            Text {
              text: "Botty"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }

            // Status Badge
            Rectangle {
              radius: Style.space(4)
              color: root.alpha(Model.statusColor(root.rawStatus.state, root.accent, root.accent, root.urgent), 0.2)
              border.color: root.alpha(Model.statusColor(root.rawStatus.state, root.accent, root.accent, root.urgent), 0.5)
              border.width: 1
              implicitWidth: statusText.implicitWidth + Style.space(8)
              implicitHeight: statusText.implicitHeight + Style.space(3)

              Text {
                id: statusText
                anchors.centerIn: parent
                text: Model.statusBadgeText(root.rawStatus.state)
                textFormat: Text.PlainText
                font.family: root.fontFamily
                font.pixelSize: Style.space(9)
                font.bold: true
                color: Model.statusColor(root.rawStatus.state, root.foreground, root.accent, root.urgent)
              }
            }
          }

          Text {
            text: "Model: " + (root.rawStatus.active_model || "ox-alpha-free") + " (" + (root.rawStatus.active_provider || "opencode-go") + ")"
            textFormat: Text.PlainText
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
            Layout.fillWidth: true
          }
        }

        // Header View Switchers & Action Buttons
        RowLayout {
          spacing: Style.space(4)

          // Chat View Button
          Button {
            iconText: "󰭹"
            tooltipText: "Chat"
            selected: root.currentView === "chat"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: root.currentView = "chat"
          }

          // Memories & Skills Button
          Button {
            iconText: "󰋚"
            tooltipText: "Memories & Skills"
            selected: root.currentView === "memories"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: {
              root.currentView = "memories"
              root.fetchMemories()
              root.fetchSkills()
            }
          }

          // Model & Settings Button
          Button {
            iconText: "󰒓"
            tooltipText: "Models & Settings"
            selected: root.currentView === "settings"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: {
              root.currentView = "settings"
              root.fetchModels()
            }
          }

          // Clear Chat Button
          Button {
            iconText: "󰆴"
            tooltipText: "Clear Chat"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: root.clearChat()
          }

          // Close Button
          Button {
            iconText: "󰅖"
            tooltipText: "Close Panel"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: root.close()
          }
        }
      }

      PanelSeparator { Layout.fillWidth: true }

      // ========================================================= VIEW: CHAT
      Item {
        id: chatViewItem
        visible: root.currentView === "chat"
        Layout.fillWidth: true
        Layout.fillHeight: true

        ColumnLayout {
          anchors.fill: parent
          spacing: Style.space(8)

          // Scrollable Chat Message List
          ScrollView {
            id: chatScrollView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ListView {
              id: chatListView
              model: (root.historyData && root.historyData.messages) ? root.historyData.messages : []
              spacing: Style.space(12)
              boundsBehavior: Flickable.StopAtBounds

              delegate: Item {
                id: msgDelegate
                width: chatListView.width
                implicitHeight: msgCard.implicitHeight

                readonly property bool isUser: modelData.role === "user"
                readonly property var blocks: Model.parseBlocks(modelData.content || "")
                readonly property var attachments: modelData.attachments || []
                readonly property var actions: modelData.actions || []

                BorderSurface {
                  id: msgCard
                  anchors.left: isUser ? undefined : parent.left
                  anchors.right: isUser ? parent.right : undefined
                  width: Math.min(parent.width * 0.94, Math.max(contentCol.implicitWidth + Style.space(24), Style.space(200)))
                  radius: Style.space(10)
                  color: isUser ? root.alpha(root.accent, 0.14) : Color.layer(Color.surface, 1)
                  borderSpec: Border.controlSpec("normal", isUser ? root.alpha(root.accent, 0.35) : root.alpha(root.foreground, 0.1), root.accent)

                  ColumnLayout {
                    id: contentCol
                    anchors.fill: parent
                    anchors.margins: Style.space(10)
                    spacing: Style.space(6)

                    // Sender Header & Timestamp
                    RowLayout {
                      Layout.fillWidth: true
                      spacing: Style.space(6)

                      Text {
                        text: isUser ? "You" : "Botty"
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.bold: true
                        color: isUser ? root.accent : root.foreground
                      }

                      Item { Layout.fillWidth: true }

                      Text {
                        text: Model.formatTime(modelData.timestamp)
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(9)
                        color: root.muted
                      }
                    }

                    // Attachments Preview (Screenshots / Media)
                    Repeater {
                      model: attachments
                      delegate: BorderSurface {
                        Layout.fillWidth: true
                        radius: Style.space(6)
                        color: root.alpha(root.foreground, 0.04)
                        borderSpec: Border.controlSpec("normal", root.alpha(root.foreground, 0.15), root.accent)

                        RowLayout {
                          anchors.fill: parent
                          anchors.margins: Style.space(6)
                          spacing: Style.space(8)

                          Image {
                            source: modelData.path ? ("file://" + modelData.path) : ""
                            Layout.preferredWidth: Style.space(64)
                            Layout.preferredHeight: Style.space(48)
                            fillMode: Image.PreserveAspectCrop
                            mipmap: true
                          }

                          ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Style.space(2)

                            Text {
                              text: modelData.is_screen_capture ? "󰹑 Screen Capture" : "󰋩 Image Attachment"
                              font.family: root.fontFamily
                              font.pixelSize: Style.font.caption
                              font.bold: true
                              color: root.accent
                            }

                            Text {
                              text: modelData.filename || "Attached media"
                              textFormat: Text.PlainText
                              font.family: root.fontFamily
                              font.pixelSize: Style.space(10)
                              color: root.dim
                              elide: Text.ElideMiddle
                              Layout.fillWidth: true
                            }
                          }
                        }
                      }
                    }

                    // Rendered Content Blocks
                    Repeater {
                      model: blocks
                      delegate: Item {
                        Layout.fillWidth: true
                        implicitHeight: blockLoader.implicitHeight

                        Loader {
                          id: blockLoader
                          anchors.fill: parent
                          sourceComponent: modelData.type === "code" ? codeComponent : textComponent
                        }

                        // Component for Text / Markdown
                        Component {
                          id: textComponent
                          Text {
                            text: modelData.text || ""
                            textFormat: Text.MarkdownText
                            wrapMode: Text.Wrap
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.body
                            color: root.foreground
                            linkColor: root.accent
                            onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                          }
                        }

                        // Component for Code Blocks with Syntax Styling & Copy
                        Component {
                          id: codeComponent
                          BorderSurface {
                            radius: Style.space(6)
                            color: Color.layer(Color.surface, 2)
                            borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.25), root.accent)

                            ColumnLayout {
                              anchors.fill: parent
                              anchors.margins: Style.space(6)
                              spacing: Style.space(4)

                              // Code header
                              RowLayout {
                                Layout.fillWidth: true
                                Text {
                                  text: (modelData.language || "code").toUpperCase()
                                  font.family: "JetBrainsMono Nerd Font"
                                  font.pixelSize: Style.space(9)
                                  font.bold: true
                                  color: root.accent
                                }
                                Item { Layout.fillWidth: true }
                                Button {
                                  iconText: "󰆏"
                                  text: "Copy"
                                  fontSize: Style.space(10)
                                  implicitHeight: Style.space(22)
                                  onClicked: root.copyText(modelData.code)
                                }
                              }

                              // Code content
                              Text {
                                text: modelData.code || ""
                                textFormat: Text.PlainText
                                font.family: "JetBrainsMono Nerd Font"
                                font.pixelSize: Style.space(11)
                                color: root.foreground
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                              }
                            }
                          }
                        }
                      }
                    }

                    // Action Receipts (Computer actions / memories / skills created)
                    Repeater {
                      model: actions
                      delegate: BorderSurface {
                        Layout.fillWidth: true
                        radius: Style.space(4)
                        color: root.alpha(root.accent, 0.08)
                        borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.2), root.accent)

                        RowLayout {
                          anchors.fill: parent
                          anchors.margins: Style.space(4)
                          spacing: Style.space(6)

                          Text {
                            text: "󰄬"
                            color: "#10B981"
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                          }

                          Text {
                            text: modelData.text || "Action executed"
                            textFormat: Text.PlainText
                            font.family: root.fontFamily
                            font.pixelSize: Style.space(10)
                            color: root.dim
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                          }
                        }
                      }
                    }

                    // Assistant Action Buttons
                    RowLayout {
                      visible: !isUser
                      Layout.fillWidth: true
                      spacing: Style.space(4)
                      Layout.topMargin: Style.space(4)

                      Item { Layout.fillWidth: true }

                      Button {
                        iconText: "󰆏"
                        text: "Copy"
                        fontSize: Style.space(10)
                        implicitHeight: Style.space(22)
                        onClicked: root.copyText(modelData.content)
                      }

                      Button {
                        iconText: "󰋚"
                        text: "Save Fact"
                        fontSize: Style.space(10)
                        implicitHeight: Style.space(22)
                        onClicked: root.addMemoryNow(modelData.content, false)
                      }
                    }
                  }
                }
              }
            }
          }

          // Active Thinking Indicator Card
          BorderSurface {
            visible: root.rawStatus.state === "working"
            Layout.fillWidth: true
            radius: Style.space(8)
            color: root.alpha(root.accent, 0.1)
            borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.3), root.accent)

            RowLayout {
              anchors.fill: parent
              anchors.margins: Style.space(8)
              spacing: Style.space(10)

              Text {
                text: "󰑐"
                font.family: root.fontFamily
                font.pixelSize: Style.space(16)
                color: root.accent
                opacity: root.pulseOpacity
              }

              Text {
                text: "Botty is reading context and executing response…"
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                color: root.foreground
                Layout.fillWidth: true
              }
            }
          }

          // Attachment Strip (Screenshots or images pending send)
          BorderSurface {
            visible: root.attachedImagePath.length > 0 || root.screenContextEnabled
            Layout.fillWidth: true
            radius: Style.space(6)
            color: root.alpha(root.accent, 0.08)
            borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.3), root.accent)

            RowLayout {
              anchors.fill: parent
              anchors.margins: Style.space(6)
              spacing: Style.space(8)

              Image {
                visible: root.attachedImagePath.length > 0
                source: root.attachedImagePath ? ("file://" + root.attachedImagePath) : ""
                Layout.preferredWidth: Style.space(48)
                Layout.preferredHeight: Style.space(36)
                fillMode: Image.PreserveAspectCrop
                mipmap: true
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(2)

                Text {
                  text: root.screenContextEnabled ? "󰹑 Active Screen Context Attached" : "󰋩 Image Attached"
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  color: root.accent
                }

                Text {
                  text: root.attachedImageFilename || "Ready to send"
                  textFormat: Text.PlainText
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(10)
                  color: root.dim
                  elide: Text.ElideMiddle
                  Layout.fillWidth: true
                }
              }

              Button {
                iconText: "󰅖"
                tooltipText: "Remove attachment"
                implicitWidth: Style.space(24)
                implicitHeight: Style.space(24)
                onClicked: {
                  root.attachedImagePath = ""
                  root.attachedImageFilename = ""
                  root.screenContextEnabled = false
                }
              }
            }
          }

          // Prompt & Context Controls
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)

            // Context Quick Action Bar
            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(6)

              // Screen Capture Toggle Button
              Button {
                iconText: "󰹑"
                text: "Screen Context"
                fontSize: Style.font.caption
                selected: root.screenContextEnabled
                implicitHeight: Style.space(28)
                onClicked: {
                  if (!root.screenContextEnabled) {
                    root.captureScreenNow()
                  } else {
                    root.screenContextEnabled = false
                    root.attachedImagePath = ""
                  }
                }
              }

              // Attach Clipboard Image Button
              Button {
                iconText: "󰋩"
                text: "Paste Image"
                fontSize: Style.font.caption
                implicitHeight: Style.space(28)
                onClicked: root.attachClipboardImage()
              }

              Item { Layout.fillWidth: true }

              Text {
                text: (root.rawStatus.active_model || "ox-alpha-free")
                font.family: root.fontFamily
                font.pixelSize: Style.space(10)
                color: root.muted
              }
            }

            // Input Row
            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(6)

              TextField {
                id: promptInput
                Layout.fillWidth: true
                placeholderText: "Ask Botty or command a desktop action… (Enter to send)"
                font.pixelSize: Style.font.body
                onAccepted: {
                  if (text.trim().length > 0 || root.attachedImagePath.length > 0 || root.screenContextEnabled) {
                    root.sendQuery(text, root.attachedImagePath, root.screenContextEnabled)
                  }
                }
              }

              Button {
                iconText: "󰒭"
                text: "Send"
                selected: true
                implicitHeight: promptInput.implicitHeight
                onClicked: {
                  if (promptInput.text.trim().length > 0 || root.attachedImagePath.length > 0 || root.screenContextEnabled) {
                    root.sendQuery(promptInput.text, root.attachedImagePath, root.screenContextEnabled)
                  }
                }
              }
            }
          }
        }
      }

      // ========================================================= VIEW: MEMORIES & SKILLS
      Item {
        id: memoriesViewItem
        visible: root.currentView === "memories"
        Layout.fillWidth: true
        Layout.fillHeight: true

        ColumnLayout {
          anchors.fill: parent
          spacing: Style.space(10)

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            Text {
              text: "Continuous Learning & Skills"
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              color: root.foreground
            }

            Item { Layout.fillWidth: true }

            Button {
              iconText: "󰑐"
              text: "Compact Memory"
              onClicked: root.compactMemoryNow()
            }
          }

          // New Memory Input
          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)

            TextField {
              id: newMemInput
              Layout.fillWidth: true
              placeholderText: "Teach Botty a fact to remember permanently…"
              onAccepted: {
                if (text.trim()) {
                  root.addMemoryNow(text, false)
                  text = ""
                }
              }
            }

            Button {
              iconText: "󰄬"
              text: "Remember"
              selected: true
              onClicked: {
                if (newMemInput.text.trim()) {
                  root.addMemoryNow(newMemInput.text, false)
                  newMemInput.text = ""
                }
              }
            }
          }

          Text {
            text: "Stored Long-Term Memories (" + root.memoriesData.length + ")"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            color: root.accent
          }

          ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ListView {
              id: memoriesList
              model: root.memoriesData
              spacing: Style.space(6)

              delegate: BorderSurface {
                width: memoriesList.width
                radius: Style.space(6)
                color: Color.layer(Color.surface, 1)
                borderSpec: Border.controlSpec("normal", root.alpha(root.foreground, 0.1), root.accent)

                RowLayout {
                  anchors.fill: parent
                  anchors.margins: Style.space(8)
                  spacing: Style.space(8)

                  Text {
                    text: modelData.type === "user" ? "󰋚 User" : "󰚩 Botty"
                    font.family: root.fontFamily
                    font.pixelSize: Style.space(10)
                    font.bold: true
                    color: root.accent
                  }

                  Text {
                    text: modelData.text || ""
                    textFormat: Text.PlainText
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    color: root.foreground
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                  }

                  Button {
                    iconText: "󰆏"
                    tooltipText: "Copy memory"
                    implicitWidth: Style.space(24)
                    implicitHeight: Style.space(24)
                    onClicked: root.copyText(modelData.text)
                  }
                }
              }
            }
          }

          Text {
            text: "Learned Custom Skills (" + root.skillsData.length + ")"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            color: root.accent
          }

          ScrollView {
            Layout.fillWidth: true
            implicitHeight: Style.space(120)
            clip: true

            ListView {
              id: skillsList
              model: root.skillsData
              spacing: Style.space(4)

              delegate: BorderSurface {
                width: skillsList.width
                radius: Style.space(6)
                color: Color.layer(Color.surface, 1)
                borderSpec: Border.controlSpec("normal", root.alpha(root.foreground, 0.1), root.accent)

                RowLayout {
                  anchors.fill: parent
                  anchors.margins: Style.space(6)
                  spacing: Style.space(8)

                  Text {
                    text: "󰘦"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.icon
                    color: root.accent
                  }

                  ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Style.space(2)

                    Text {
                      text: modelData.name || ""
                      textFormat: Text.PlainText
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                      color: root.foreground
                    }

                    Text {
                      text: modelData.description || ""
                      textFormat: Text.PlainText
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      color: root.dim
                      elide: Text.ElideRight
                      Layout.fillWidth: true
                    }
                  }
                }
              }
            }
          }
        }
      }

      // ========================================================= VIEW: SETTINGS & MODELS
      Item {
        id: settingsViewItem
        visible: root.currentView === "settings"
        Layout.fillWidth: true
        Layout.fillHeight: true

        ColumnLayout {
          anchors.fill: parent
          spacing: Style.space(10)

          Text {
            text: "Model Selection"
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            color: root.foreground
          }

          Text {
            text: "Select the LLM model used underneath by Botty profile:"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.dim
          }

          ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ListView {
              id: modelsList
              model: root.modelsData
              spacing: Style.space(6)

              delegate: BorderSurface {
                width: modelsList.width
                readonly property bool isCurrent: modelData.id === root.rawStatus.active_model
                radius: Style.space(8)
                color: isCurrent ? root.alpha(root.accent, 0.15) : Color.layer(Color.surface, 1)
                borderSpec: Border.controlSpec("normal", isCurrent ? root.accent : root.alpha(root.foreground, 0.1), root.accent)

                RowLayout {
                  anchors.fill: parent
                  anchors.margins: Style.space(8)
                  spacing: Style.space(10)

                  Text {
                    text: isCurrent ? "󰄬" : "󰚩"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.icon
                    color: isCurrent ? "#10B981" : root.muted
                  }

                  ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Style.space(2)

                    RowLayout {
                      spacing: Style.space(6)

                      Text {
                        text: modelData.name || modelData.id
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.bold: isCurrent
                        color: isCurrent ? root.accent : root.foreground
                      }

                      Rectangle {
                        radius: Style.space(3)
                        color: root.alpha(root.foreground, 0.08)
                        implicitWidth: catText.implicitWidth + Style.space(6)
                        implicitHeight: catText.implicitHeight + Style.space(2)

                        Text {
                          id: catText
                          anchors.centerIn: parent
                          text: modelData.category || modelData.provider
                          font.family: root.fontFamily
                          font.pixelSize: Style.space(8)
                          color: root.muted
                        }
                      }
                    }

                    Text {
                      text: modelData.id
                      textFormat: Text.PlainText
                      font.family: "JetBrainsMono Nerd Font"
                      font.pixelSize: Style.space(10)
                      color: root.dim
                    }
                  }

                  Button {
                    text: isCurrent ? "Active" : "Select"
                    selected: isCurrent
                    onClicked: root.selectModel(modelData.id, modelData.provider)
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
