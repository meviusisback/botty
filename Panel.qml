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
    state: "idle",
    headline: "Ready",
    last_query: "",
    last_answer: "",
    active_engine: "hermes",
    active_model: "ox-alpha-free",
    active_provider: "opencode-go",
    session_turns: 0,
    memory_count: 0,
    skills_count: 0,
    has_active_work: false
  })

  property var historyData: ({ session_id: "botty-widget", messages: [] })
  property int lastMessageCount: 0

  property var modelsCatalog: ({ providers: [] })
  property var enginesData: ({ engines: [] })
  property string selectedProviderId: "opencode-go"
  property string modelSearchFilter: ""

  property var memoriesData: []
  property var skillsData: []
  property var vaultSecurity: ({ encryption_enabled: true, cipher: "AES-256-CBC (PBKDF2)" })

  property string currentView: "chat" // "chat" | "memories" | "settings"
  property string promptText: ""
  
  // Generic File Attachment State
  property string attachedFilePath: ""
  property string attachedFileName: ""
  property string attachedFileSize: ""
  property string attachedFileIcon: "󰈔"
  property bool attachedFileIsImage: false

  property bool screenContextEnabled: false
  property var lastCaptureInfo: null

  // Voice dictation state
  property bool isRecordingVoice: false
  property bool isTranscribingVoice: false

  property bool isProcessing: (rawStatus && rawStatus.state === "working") || askProc.running

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

  function fetchModels(engine) {
    var eng = engine || root.rawStatus.active_engine || "hermes"
    modelsProc.command = ["python3", root.scriptPath(), "models", "--engine", eng]
    if (!modelsProc.running) {
      modelsProc.running = true
    }
    if (!enginesProc.running) {
      enginesProc.running = true
    }
  }

  function fetchMemories() {
    if (!memoriesProc.running) {
      memoriesProc.running = true
    }
    if (!vaultProc.running) {
      vaultProc.running = true
    }
  }

  function fetchSkills() {
    if (!skillsProc.running) {
      skillsProc.running = true
    }
  }

  function attachFileNow(filepath) {
    if (!filepath) return
    inspectProc.command = ["python3", root.scriptPath(), "inspect-file", filepath]
    inspectProc.running = true
  }

  function sendQuery(text, filePath, useScreen) {
    if (askProc.running || root.rawStatus.state === "working") return

    var q = text || promptInput.text || ""
    var fpath = filePath || root.attachedFilePath || ""
    var scr = (useScreen !== undefined) ? useScreen : root.screenContextEnabled

    if (!q.trim() && !fpath && !scr) return

    var cmd = ["python3", root.scriptPath(), "ask", q]
    if (fpath) {
      if (root.attachedFileIsImage) {
        cmd.push("--image")
      } else {
        cmd.push("--file")
      }
      cmd.push(fpath)
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
    root.attachedFilePath = ""
    root.attachedFileName = ""
    root.attachedFileSize = ""
    root.attachedFileIsImage = false
    root.screenContextEnabled = false
    root.lastCaptureInfo = null
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
    root.lastMessageCount = 0
  }

  function selectEngine(engineId) {
    root.rawStatus.active_engine = engineId
    setEngineProc.command = ["python3", root.scriptPath(), "set-engine", engineId]
    setEngineProc.running = true
  }

  function selectModel(modelId, providerId) {
    var eng = root.rawStatus.active_engine || "hermes"
    var cmd = ["python3", root.scriptPath(), "set-model", modelId, "--engine", eng]
    if (providerId) {
      cmd.push("--provider")
      cmd.push(providerId)
    }
    setModelProc.command = cmd
    setModelProc.running = true
  }

  function toggleVoiceDictation() {
    if (root.isRecordingVoice) {
      root.isTranscribingVoice = true
      dictateStopProc.command = ["python3", root.scriptPath(), "dictate-stop"]
      dictateStopProc.running = true
    } else {
      dictateStartProc.command = ["python3", root.scriptPath(), "dictate-start"]
      dictateStartProc.running = true
    }
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

  function deleteMemoryNow(memId, isUser) {
    var cmd = ["python3", root.scriptPath(), "delete-memory", String(memId)]
    if (isUser) cmd.push("--user")
    delMemProc.command = cmd
    delMemProc.running = true
  }

  Process {
    id: pickFileProc
    command: ["python3", root.scriptPath(), "pick-file"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok && data.path) {
            root.attachFileNow(data.path)
          }
        } catch (e) {}
      }
    }
  }
  Timer {
    id: focusTimer
    interval: 50
    running: false
    repeat: false
    onTriggered: {
      if (root.opened && promptInput) {
        promptInput.forceActiveFocus()
      }
    }
  }

  onOpenedChanged: {
    if (opened) {
      focusTimer.start()
      Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
    }
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

  // Voice recording pulsing animation
  property real voicePulseOpacity: 1.0
  SequentialAnimation on voicePulseOpacity {
    running: root.isRecordingVoice
    loops: Animation.Infinite
    NumberAnimation { to: 0.25; duration: 500; easing.type: Easing.InOutQuad }
    NumberAnimation { to: 1.0; duration: 500; easing.type: Easing.InOutQuad }
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
            var msgs = data.history.messages || []
            var newCount = msgs.length
            var shouldScroll = (newCount > root.lastMessageCount)
            root.lastMessageCount = newCount
            root.historyData = data.history
            if (shouldScroll) {
              Qt.callLater(function() {
                if (chatListView.count > 0) {
                  chatListView.positionViewAtEnd()
                }
              })
            }
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: inspectProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.attachedFilePath = data.path
            root.attachedFileName = data.filename
            root.attachedFileSize = data.size_str
            root.attachedFileIcon = data.icon || "󰈔"
            root.attachedFileIsImage = Boolean(data.is_image)
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
            root.attachedFilePath = data.image_path
            root.attachedFileName = data.filename
            root.attachedFileSize = "Capture"
            root.attachedFileIcon = "󰹑"
            root.attachedFileIsImage = true
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
            root.attachedFilePath = data.image_path
            root.attachedFileName = data.filename
            root.attachedFileSize = "Clipboard"
            root.attachedFileIcon = "󰋩"
            root.attachedFileIsImage = true
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
    id: enginesProc
    command: ["python3", root.scriptPath(), "engines"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.enginesData = data
            root.rawStatus.active_engine = data.active_engine
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: setEngineProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchModels(root.rawStatus.active_engine)
      }
    }
  }

  Process {
    id: modelsProc
    command: ["python3", root.scriptPath(), "models", "--engine", (root.rawStatus.active_engine || "hermes")]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.modelsCatalog = data
            root.rawStatus.active_model = data.active_model
            root.rawStatus.active_provider = data.active_provider
            if (data.active_provider) {
              root.selectedProviderId = data.active_provider
            } else if (data.providers && data.providers.length > 0) {
              root.selectedProviderId = data.providers[0].id
            }
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
    id: dictateStartProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          root.isRecordingVoice = Boolean(data && data.ok && data.recording)
        } catch (e) {}
      }
    }
  }

  Process {
    id: dictateStopProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.isRecordingVoice = false
        root.isTranscribingVoice = false
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok && data.text) {
            var current = promptInput.text.trim()
            promptInput.text = current.length > 0 ? (current + " " + data.text) : data.text
            promptInput.forceActiveFocus()
          }
        } catch (e) {}
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
    id: vaultProc
    command: ["python3", root.scriptPath(), "vault-status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.vaultSecurity = data
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: delMemProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchStatus()
        root.fetchMemories()
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
    function open(): void { root.open(); focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) }
    function close(): void { root.close() }
    function toggle(): void { root.toggle(); if (root.opened) { focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) } }
    function refresh(): void { root.fetchStatus(); root.fetchHistory() }
    function ask(query: string): void { root.open(); focusTimer.start(); root.sendQuery(query, "", false) }
    function captureAndAsk(query: string): void { root.open(); focusTimer.start(); root.sendQuery(query, "", true) }
    function captureWindowContext(): void { root.captureScreenNow(); root.open(); focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) }
    function attach(path: string): void { root.attachFileNow(path) }
    function clear(): void { root.clearChat() }
  }

  IpcHandler {
    enabled: true
    target: "botty"
    function open(): void { root.open(); focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) }
    function close(): void { root.close() }
    function toggle(): void { root.toggle(); if (root.opened) { focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) } }
    function refresh(): void { root.fetchStatus(); root.fetchHistory() }
    function ask(query: string): void { root.open(); focusTimer.start(); root.sendQuery(query, "", false) }
    function captureAndAsk(query: string): void { root.open(); focusTimer.start(); root.sendQuery(query, "", true) }
    function captureWindowContext(): void { root.captureScreenNow(); root.open(); focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) }
    function attach(path: string): void { root.attachFileNow(path) }
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
    contentWidth: Style.space(560)
    contentHeight: Style.space(680)

    onOpenChanged: {
      if (open) {
        root.fetchStatus()
        root.fetchHistory()
        root.fetchModels()
        focusTimer.start()
        Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
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
            font.pixelSize: Style.space(20)
            opacity: root.rawStatus.state === "working" ? root.pulseOpacity : 1.0
          }
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(2)

          RowLayout {
            spacing: Style.space(6)

            Text {
              text: "Botty 🐼"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }

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

            Rectangle {
              radius: Style.space(4)
              color: root.alpha(root.foreground, 0.08)
              implicitWidth: engineText.implicitWidth + Style.space(8)
              implicitHeight: engineText.implicitHeight + Style.space(3)

              Text {
                id: engineText
                anchors.centerIn: parent
                text: String(root.rawStatus.active_engine || "hermes").toUpperCase()
                textFormat: Text.PlainText
                font.family: root.fontFamily
                font.pixelSize: Style.space(9)
                font.bold: true
                color: root.accent
              }
            }
          }

          Text {
            text: (root.rawStatus.active_model || "ox-alpha-free") + " (" + (root.rawStatus.active_provider || "opencode-go") + ")"
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

          Button {
            iconText: "󰭹"
            tooltipText: "Chat"
            selected: root.currentView === "chat"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: root.currentView = "chat"
          }

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

          Button {
            iconText: "󰒓"
            tooltipText: "Agents, Providers & Models"
            selected: root.currentView === "settings"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: {
              root.currentView = "settings"
              root.fetchModels()
            }
          }

          Button {
            iconText: "󰆴"
            tooltipText: "Clear Chat"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: root.clearChat()
          }

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
              spacing: Style.space(10)
              boundsBehavior: Flickable.StopAtBounds

              delegate: Item {
                id: msgDelegate
                width: chatListView.width
                implicitHeight: msgCard.implicitHeight + Style.space(4)

                readonly property bool isUser: modelData.role === "user"
                readonly property var blocks: Model.parseBlocks(modelData.content || "")
                readonly property var attachments: modelData.attachments || []
                readonly property var actions: modelData.actions || []

                BorderSurface {
                  id: msgCard
                  anchors.left: isUser ? undefined : parent.left
                  anchors.right: isUser ? parent.right : undefined
                  anchors.leftMargin: isUser ? 0 : Style.space(4)
                  anchors.rightMargin: isUser ? Style.space(4) : 0
                  width: isUser ? Math.min(parent.width * 0.88, Math.max(contentCol.implicitWidth + Style.space(24), Style.space(140))) : (parent.width - Style.space(8))
                  implicitHeight: contentCol.implicitHeight + Style.space(20)
                  radius: Style.space(10)
                  color: isUser ? root.alpha(root.accent, 0.12) : root.alpha(root.foreground, 0.04)
                  borderSpec: Border.controlSpec("normal", isUser ? root.alpha(root.accent, 0.3) : root.alpha(root.foreground, 0.12), root.accent)

                  ColumnLayout {
                    id: contentCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Style.space(10)
                    spacing: Style.space(6)

                    // Sender Header & Timestamp
                    RowLayout {
                      Layout.fillWidth: true
                      spacing: Style.space(6)

                      Text {
                        text: isUser ? "You" : "Botty 🐼"
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

                    // Context / Attachment Badge
                    Repeater {
                      model: attachments
                      delegate: BorderSurface {
                        Layout.fillWidth: true
                        implicitHeight: attRow.implicitHeight + Style.space(8)
                        radius: Style.space(4)
                        color: root.alpha(root.accent, 0.1)
                        borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.25), root.accent)

                        RowLayout {
                          id: attRow
                          anchors.left: parent.left
                          anchors.right: parent.right
                          anchors.top: parent.top
                          anchors.margins: Style.space(4)
                          spacing: Style.space(6)

                          Text {
                            text: modelData.icon || (modelData.is_screen_capture ? "󰹑" : "󰈔")
                            color: root.accent
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                          }

                          Text {
                            text: {
                              if (modelData.is_screen_capture) {
                                var app = modelData.app_name || "Active Window"
                                var title = modelData.window_title ? (" — " + modelData.window_title) : ""
                                return "Screen Context: " + app + title
                              }
                              var size = modelData.size_str ? (" (" + modelData.size_str + ")") : ""
                              return "Attached: " + (modelData.filename || "file") + size
                            }
                            textFormat: Text.PlainText
                            font.family: root.fontFamily
                            font.pixelSize: Style.space(10)
                            font.bold: true
                            color: root.accent
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                          }
                        }
                      }
                    }

                    // Rendered Content Blocks
                    Repeater {
                      model: blocks
                      delegate: Item {
                        Layout.fillWidth: true
                        implicitHeight: modelData.type === "code" ? codeCard.implicitHeight : (textBlock.implicitHeight + Style.space(2))

                        Text {
                          id: textBlock
                          visible: modelData.type !== "code"
                          width: parent.width
                          text: modelData.text || ""
                          textFormat: Text.MarkdownText
                          wrapMode: Text.Wrap
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.body
                          color: root.foreground
                          linkColor: root.accent
                          onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                        }

                        BorderSurface {
                          id: codeCard
                          visible: modelData.type === "code"
                          width: parent.width
                          implicitHeight: codeCol.implicitHeight + Style.space(16)
                          radius: Style.space(6)
                          color: root.alpha(root.foreground, 0.08)
                          borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.25), root.accent)

                          ColumnLayout {
                            id: codeCol
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Style.space(8)
                            spacing: Style.space(4)

                            RowLayout {
                              Layout.fillWidth: true
                              Text {
                                text: (modelData.language || "CODE").toUpperCase()
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

                    // Action Receipts
                    Repeater {
                      model: actions
                      delegate: BorderSurface {
                        Layout.fillWidth: true
                        implicitHeight: actRow.implicitHeight + Style.space(8)
                        radius: Style.space(4)
                        color: root.alpha(root.accent, 0.08)
                        borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.2), root.accent)

                        RowLayout {
                          id: actRow
                          anchors.left: parent.left
                          anchors.right: parent.right
                          anchors.top: parent.top
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
                      Layout.topMargin: Style.space(2)

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
            id: thinkingCard
            visible: root.rawStatus.state === "working"
            Layout.fillWidth: true
            implicitHeight: thinkRow.implicitHeight + Style.space(16)
            radius: Style.space(8)
            color: root.alpha(root.accent, 0.1)
            borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.3), root.accent)

            RowLayout {
              id: thinkRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
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
                text: "Botty is processing your request…"
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                color: root.foreground
                Layout.fillWidth: true
              }
            }
          }

          // Active Context Strip (Files, Documents, Media, Screen)
          BorderSurface {
            id: contextStrip
            visible: root.attachedFilePath.length > 0 || root.screenContextEnabled
            Layout.fillWidth: true
            implicitHeight: ctxRow.implicitHeight + Style.space(12)
            radius: Style.space(6)
            color: root.alpha(root.accent, 0.08)
            borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.3), root.accent)

            RowLayout {
              id: ctxRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.space(6)
              spacing: Style.space(8)

              Text {
                text: root.attachedFileIcon || (root.screenContextEnabled ? "󰹑" : "󰈔")
                font.family: root.fontFamily
                font.pixelSize: Style.space(14)
                color: root.accent
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(1)

                Text {
                  text: {
                    if (root.screenContextEnabled) {
                      var win = root.lastCaptureInfo && root.lastCaptureInfo.active_window ? root.lastCaptureInfo.active_window : null
                      var app = win ? (win.class || win.title || "Active Window") : "Active Window"
                      return "Screen Context (" + app + ")"
                    }
                    var sz = root.attachedFileSize ? (" • " + root.attachedFileSize) : ""
                    return "Attached: " + (root.attachedFileName || "File") + sz
                  }
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  color: root.accent
                }

                Text {
                  text: {
                    if (root.screenContextEnabled && root.lastCaptureInfo && root.lastCaptureInfo.active_window) {
                      return root.lastCaptureInfo.active_window.title || "Active window context attached"
                    }
                    return root.attachedFilePath || "Attached file ready to send"
                  }
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
                  root.attachedFilePath = ""
                  root.attachedFileName = ""
                  root.attachedFileSize = ""
                  root.attachedFileIsImage = false
                  root.screenContextEnabled = false
                  root.lastCaptureInfo = null
                }
              }
            }
          }

          // Prompt & Context Controls
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)

            // Unified Input Row: [+] [Text Field] [Screen 󰹑] [Mic 󰍬] [Send 󰒭]
            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(6)

              // Consolidated [+] Attachment Button (File, Image, Document, Code)
              Button {
                id: attachBtn
                enabled: !root.isProcessing
                iconText: "󰐕"
                tooltipText: "Attach file, document, code, or image (Right-click: paste clipboard image)"
                implicitHeight: promptInput.implicitHeight
                implicitWidth: promptInput.implicitHeight
                fontSize: Style.space(14)
                onClicked: if (!pickFileProc.running) pickFileProc.running = true
                onRightClicked: root.attachClipboardImage()
              }

              // Text Input
              TextField {
                id: promptInput
                enabled: !root.isProcessing
                Layout.fillWidth: true
                placeholderText: root.isRecordingVoice ? "🎙️ Listening… speak now (click mic to transcribe)" : "Ask Botty or command a desktop action… (Enter to send)"
                font.pixelSize: Style.font.body
                onAccepted: {
                  if (!root.isProcessing && (text.trim().length > 0 || root.attachedFilePath.length > 0 || root.screenContextEnabled)) {
                    root.sendQuery(text, root.attachedFilePath, root.screenContextEnabled)
                  }
                }
              }

              // Screen Context Toggle Button
              Button {
                id: screenBtn
                enabled: !root.isProcessing
                iconText: "󰹑"
                tooltipText: root.screenContextEnabled ? "Screen Context Active (Click to remove)" : "Attach Active Screen Context"
                selected: root.screenContextEnabled
                implicitHeight: promptInput.implicitHeight
                implicitWidth: promptInput.implicitHeight
                onClicked: {
                  if (!root.screenContextEnabled) {
                    root.captureScreenNow()
                  } else {
                    root.screenContextEnabled = false
                    root.attachedFilePath = ""
                    root.lastCaptureInfo = null
                  }
                }
              }

              // Voice Dictation Button
              Button {
                id: micBtn
                enabled: !root.isProcessing
                iconText: root.isRecordingVoice ? "󰍬" : "󰍭"
                tooltipText: root.isRecordingVoice ? "Stop recording & transcribe" : "Voice Dictation (Click to speak)"
                selected: root.isRecordingVoice
                opacity: root.isRecordingVoice ? root.voicePulseOpacity : 1.0
                implicitHeight: promptInput.implicitHeight
                implicitWidth: promptInput.implicitHeight
                onClicked: root.toggleVoiceDictation()
              }

              // Send Button
              Button {
                iconText: "󰒭"
                text: "Send"
                enabled: !root.isProcessing
                selected: true
                implicitHeight: promptInput.implicitHeight
                onClicked: {
                  if (!root.isProcessing && (promptInput.text.trim().length > 0 || root.attachedFilePath.length > 0 || root.screenContextEnabled)) {
                    root.sendQuery(promptInput.text, root.attachedFilePath, root.screenContextEnabled)
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

          // Local Security & Encryption Status Card
          BorderSurface {
            Layout.fillWidth: true
            implicitHeight: secRow.implicitHeight + Style.space(12)
            radius: Style.space(6)
            color: root.alpha(root.accent, 0.08)
            borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.25), root.accent)

            RowLayout {
              id: secRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.space(7)
              spacing: Style.space(8)

              Text {
                text: "🔒"
                font.pixelSize: Style.space(14)
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(1)

                Text {
                  text: "Local AES-256 Memory Encryption Active"
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  color: root.accent
                }

                Text {
                  text: "Hardware-bound PBKDF2 vault • POSIX 0600 permissions (Owner-only access on this machine)"
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(10)
                  color: root.dim
                  wrapMode: Text.Wrap
                  Layout.fillWidth: true
                }
              }
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

              delegate: Item {
                width: memoriesList.width
                implicitHeight: memCard.implicitHeight + Style.space(4)

                BorderSurface {
                  id: memCard
                  anchors.left: parent.left
                  anchors.right: parent.right
                  implicitHeight: memRow.implicitHeight + Style.space(16)
                  radius: Style.space(6)
                  color: root.alpha(root.foreground, 0.04)
                  borderSpec: Border.controlSpec("normal", root.alpha(root.foreground, 0.1), root.accent)

                  RowLayout {
                    id: memRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Style.space(8)
                    spacing: Style.space(8)

                    Text {
                      text: modelData.type === "user" ? "󰋚 User" : "🐼 Botty"
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

                    Button {
                      iconText: "󰆴"
                      tooltipText: "Delete / cancel this memory"
                      implicitWidth: Style.space(24)
                      implicitHeight: Style.space(24)
                      onClicked: root.deleteMemoryNow(modelData.id, modelData.type === "user")
                    }
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
            implicitHeight: Style.space(130)
            clip: true

            ListView {
              id: skillsList
              model: root.skillsData
              spacing: Style.space(4)

              delegate: Item {
                width: skillsList.width
                implicitHeight: skillCard.implicitHeight + Style.space(2)

                BorderSurface {
                  id: skillCard
                  anchors.left: parent.left
                  anchors.right: parent.right
                  implicitHeight: skillRow.implicitHeight + Style.space(12)
                  radius: Style.space(6)
                  color: root.alpha(root.foreground, 0.04)
                  borderSpec: Border.controlSpec("normal", root.alpha(root.foreground, 0.1), root.accent)

                  RowLayout {
                    id: skillRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
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
      }

      // ========================================================= VIEW: AGENTS, PROVIDERS & MODELS
      Item {
        id: settingsViewItem
        visible: root.currentView === "settings"
        Layout.fillWidth: true
        Layout.fillHeight: true

        readonly property var allEngines: (root.enginesData && root.enginesData.engines) ? root.enginesData.engines : []
        readonly property var allProviders: (root.modelsCatalog && root.modelsCatalog.providers) ? root.modelsCatalog.providers : []
        readonly property var currentProviderObj: {
          for (var i = 0; i < allProviders.length; i++) {
            if (allProviders[i].id === root.selectedProviderId) return allProviders[i]
          }
          return (allProviders.length > 0) ? allProviders[0] : ({ id: "opencode-go", name: "OpenCode Go", models: [] })
        }

        readonly property var filteredModels: {
          var list = (currentProviderObj && currentProviderObj.models) ? currentProviderObj.models : []
          var filter = root.modelSearchFilter.trim().toLowerCase()
          if (!filter) return list
          return list.filter(function(m) {
            var id = String(m.id || "").toLowerCase()
            var name = String(m.name || "").toLowerCase()
            return id.indexOf(filter) !== -1 || name.indexOf(filter) !== -1
          })
        }

        ColumnLayout {
          anchors.fill: parent
          spacing: Style.space(10)

          // ----------------------------------------------------- Section 1: Agent Engine
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(4)

            Text {
              text: "1. Agent Engine (Backend Runner):"
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              color: root.accent
            }

            Flow {
              Layout.fillWidth: true
              spacing: Style.space(6)

              Repeater {
                model: settingsViewItem.allEngines
                delegate: Button {
                  text: (modelData.icon ? (modelData.icon + " ") : "") + (modelData.id === "hermes" ? "Hermes (Botty)" : (modelData.id === "omp" ? "OMP" : (modelData.id === "claude" ? "Claude" : "Codex")))
                  tooltipText: modelData.name + " (" + modelData.desc + ")"
                  fontSize: Style.space(11)
                  selected: modelData.id === (root.rawStatus.active_engine || "hermes")
                  implicitHeight: Style.space(28)
                  onClicked: root.selectEngine(modelData.id)
                }
              }
            }
          }

          PanelSeparator { Layout.fillWidth: true }

          // ----------------------------------------------------- Section 2: Active Hermes Providers
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(4)

            Text {
              text: "2. Active Inference Providers in Hermes (" + settingsViewItem.allProviders.length + " configured with keys):"
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              color: root.accent
            }

            Flow {
              Layout.fillWidth: true
              spacing: Style.space(6)

              Repeater {
                model: settingsViewItem.allProviders
                delegate: Button {
                  text: modelData.name || modelData.id
                  fontSize: Style.space(11)
                  selected: modelData.id === root.selectedProviderId
                  implicitHeight: Style.space(28)
                  onClicked: {
                    root.selectedProviderId = modelData.id
                    root.modelSearchFilter = ""
                  }
                }
              }
            }
          }

          // ----------------------------------------------------- Section 3: Model Search & Selector
          ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Style.space(6)

            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(6)

              Text {
                text: "3. Select Model (" + settingsViewItem.filteredModels.length + " in " + settingsViewItem.currentProviderObj.name + "):"
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                color: root.accent
              }

              Item { Layout.fillWidth: true }
            }

            TextField {
              id: modelSearchInput
              Layout.fillWidth: true
              placeholderText: "🔍 Search models under " + settingsViewItem.currentProviderObj.name + "…"
              font.pixelSize: Style.font.body
              text: root.modelSearchFilter
              onTextChanged: root.modelSearchFilter = text
            }

            ScrollView {
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true

              ListView {
                id: modelsList
                model: settingsViewItem.filteredModels
                spacing: Style.space(6)

                delegate: Item {
                  width: modelsList.width
                  implicitHeight: modelCard.implicitHeight + Style.space(4)
                  readonly property bool isCurrent: modelData.id === root.rawStatus.active_model

                  BorderSurface {
                    id: modelCard
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: Style.space(2)
                    anchors.rightMargin: Style.space(2)
                    implicitHeight: modelRow.implicitHeight + Style.space(14)
                    radius: Style.space(8)
                    color: isCurrent ? root.alpha(root.accent, 0.15) : root.alpha(root.foreground, 0.04)
                    borderSpec: Border.controlSpec("normal", isCurrent ? root.accent : root.alpha(root.foreground, 0.1), root.accent)

                    RowLayout {
                      id: modelRow
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.top: parent.top
                      anchors.margins: Style.space(7)
                      spacing: Style.space(8)

                      Text {
                        text: isCurrent ? "󰄬" : "🐼"
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
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                          }
                        }

                        Text {
                          text: modelData.id
                          textFormat: Text.PlainText
                          font.family: "JetBrainsMono Nerd Font"
                          font.pixelSize: Style.space(10)
                          color: root.dim
                          elide: Text.ElideRight
                          Layout.fillWidth: true
                        }
                      }

                      Button {
                        text: isCurrent ? "Active" : "Select"
                        selected: isCurrent
                        implicitHeight: Style.space(26)
                        fontSize: Style.space(10)
                        onClicked: root.selectModel(modelData.id, root.selectedProviderId)
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
  }
}
