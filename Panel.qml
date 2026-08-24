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
  property int lastReadMessageCount: 0
  property int unreadCount: 0

  property var modelsCatalog: ({ providers: [] })
  property var enginesData: ({ engines: [] })
  property string selectedProviderId: "opencode-go"
  property string modelSearchFilter: ""

  property var memoriesData: []
  property var skillsData: []
  property var vaultSecurity: ({ encryption_enabled: true, cipher: "AES-256-CBC (PBKDF2)" })

  property string currentView: "chat" // "chat" | "memories" | "settings" | "logs"
  property string promptText: ""
  
  // Generic File Attachment State
  property string attachedFilePath: ""
  property string attachedFileName: ""
  property string attachedFileSize: ""
  property string attachedFileIcon: "󰈔"
  property bool attachedFileIsImage: false

  // Situation Context & Visual Screenshot state
  property bool situationContextEnabled: false
  property var situationData: null
  property bool screenContextEnabled: false
  property var lastCaptureInfo: null

  // Logs state
  property var logsData: ({ logs: "", log_count: 0, last_assistant_raw: "", last_assistant_model: "", last_assistant_engine: "" })
  property var selectedMsgLog: null

  // Auto-scroll pinning state
  property bool autoScrollPinned: true

  // Voice dictation state
  property bool isRecordingVoice: false
  property bool isTranscribingVoice: false

  property bool isProcessing: (rawStatus && rawStatus.state === "working") || askProc.running

  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  function scriptPath() {
    return Qt.resolvedUrl("botty_backend.py").toString().replace(/^file:\/\//, "")
  }

  function scrollChat(delta) {
    if (!chatListView) return
    var step = Style.space(70)
    var currentY = chatListView.contentY
    var minY = chatListView.originY
    var maxY = Math.max(minY, minY + chatListView.contentHeight - chatListView.height)
    var targetY = Math.max(minY, Math.min(maxY, currentY + delta * step))
    chatListView.contentY = targetY
    chatListView.returnToBounds()
    root.autoScrollPinned = (targetY >= maxY - Style.space(10))
  }

  function scrollChatToEnd() {
    if (!chatListView) return
    root.autoScrollPinned = true
    chatListView.positionViewAtEnd()
    Qt.callLater(function() {
      if (chatListView && root.autoScrollPinned) {
        chatListView.positionViewAtEnd()
        var minY = chatListView.originY
        var maxY = Math.max(minY, minY + chatListView.contentHeight - chatListView.height)
        chatListView.contentY = maxY
        chatListView.returnToBounds()
      }
    })
    scrollStabilizeTimer.restart()
  }

  Timer {
    id: scrollStabilizeTimer
    interval: 80
    repeat: false
    onTriggered: {
      if (chatListView && root.autoScrollPinned) {
        chatListView.positionViewAtEnd()
        var minY = chatListView.originY
        var maxY = Math.max(minY, minY + chatListView.contentHeight - chatListView.height)
        chatListView.contentY = maxY
      }
    }
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

  function fetchLogs() {
    logsProc.command = ["python3", root.scriptPath(), "logs"]
    logsProc.running = false
    logsProc.running = true
  }

  function clearLogsNow() {
    clearLogsProc.command = ["python3", root.scriptPath(), "clear-logs"]
    clearLogsProc.running = false
    clearLogsProc.running = true
  }

  function showSpecificLog(msgData) {
    root.selectedMsgLog = msgData
    root.currentView = "logs"
    root.fetchLogs()
  }

  function fetchSituationContext() {
    root.situationContextEnabled = true
    root.screenContextEnabled = false
    situationProc.command = ["python3", root.scriptPath(), "situation-context"]
    situationProc.running = false
    situationProc.running = true
  }

  function attachFileNow(filepath) {
    if (!filepath) return
    inspectProc.command = ["python3", root.scriptPath(), "inspect-file", filepath]
    inspectProc.running = true
  }

  function sendQuery(text, filePath, useScreen, useSituation) {
    if (askProc.running || root.rawStatus.state === "working") return

    var q = text || promptInput.text || ""
    var fpath = filePath || root.attachedFilePath || ""
    var scr = (useScreen !== undefined) ? useScreen : root.screenContextEnabled
    var sit = (useSituation !== undefined) ? useSituation : root.situationContextEnabled

    if (!q.trim() && !fpath && !scr && !sit) return

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
    if (sit) {
      cmd.push("--situation")
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
    root.situationContextEnabled = false
    root.lastCaptureInfo = null
    root.situationData = null
    root.fetchHistory()
    root.scrollChatToEnd()
    Qt.callLater(function() { 
      root.scrollChatToEnd()
      if (promptInput) promptInput.forceActiveFocus() 
    })
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
    Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
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
    if (root.currentView === "chat") {
      Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
    }
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
    interval: 30
    running: false
    repeat: false
    onTriggered: {
      if (root.opened && root.currentView === "chat" && promptInput) {
        promptInput.forceActiveFocus()
      }
    }
  }

  onOpenedChanged: {
    if (opened) {
      root.unreadCount = 0
      root.lastReadMessageCount = (root.historyData && root.historyData.messages) ? root.historyData.messages.length : 0
      focusTimer.start()
      Qt.callLater(function() {
        if (root.currentView === "chat" && promptInput) {
          promptInput.forceActiveFocus()
        }
      })
    }
  }

  onIsProcessingChanged: {
    if (!isProcessing && root.opened && root.currentView === "chat" && promptInput) {
      focusTimer.start()
      Qt.callLater(function() { promptInput.forceActiveFocus() })
    }
  }

  onCurrentViewChanged: {
    if (currentView === "chat") {
      focusTimer.start()
      Qt.callLater(function() {
        if (promptInput) promptInput.forceActiveFocus()
      })
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
            if (root.opened) {
              root.unreadCount = 0
              root.lastReadMessageCount = newCount
            } else {
              if (newCount > root.lastReadMessageCount) {
                root.unreadCount = newCount - root.lastReadMessageCount
              }
            }
            if (shouldScroll) {
              root.scrollChatToEnd()
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
            root.attachedFileIcon = "󰄀"
            root.attachedFileIsImage = true
            root.screenContextEnabled = true
            root.situationContextEnabled = false
            root.lastCaptureInfo = data
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: situationProc
    command: ["python3", root.scriptPath(), "situation-context"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.situationData = data
            root.situationContextEnabled = true
            root.screenContextEnabled = false
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: logsProc
    command: ["python3", root.scriptPath(), "logs"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text || "{}")
          if (data && data.ok) {
            root.logsData = data
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: clearLogsProc
    command: ["python3", root.scriptPath(), "clear-logs"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fetchLogs()
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
    function ask(query: string): void { root.open(); focusTimer.start(); root.sendQuery(query, "", false, false) }
    function captureAndAsk(query: string): void { root.open(); focusTimer.start(); root.sendQuery(query, "", false, true) }
    function situationContext(): void {
      root.currentView = "chat"
      root.fetchSituationContext()
      if (!root.opened) {
        root.toggle()
      }
      focusTimer.start()
      Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
    }
    function captureWindowContext(): void {
      root.situationContext()
    }
    function captureScreenshot(): void {
      root.currentView = "chat"
      root.captureScreenNow()
      if (!root.opened) {
        root.toggle()
      }
      focusTimer.start()
      Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
    }
    function attach(path: string): void { root.attachFileNow(path) }
    function clear(): void { root.clearChat() }
  }

  IpcHandler {
    enabled: true
    target: "botty"
    function open(): void { if (!root.opened) root.toggle(); focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) }
    function close(): void { if (root.opened) root.toggle() }
    function toggle(): void { root.toggle(); if (root.opened) { focusTimer.start(); Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() }) } }
    function refresh(): void { root.fetchStatus(); root.fetchHistory() }
    function ask(query: string): void { if (!root.opened) root.toggle(); focusTimer.start(); root.sendQuery(query, "", false, false) }
    function captureAndAsk(query: string): void { if (!root.opened) root.toggle(); focusTimer.start(); root.sendQuery(query, "", false, true) }
    function situationContext(): void {
      root.currentView = "chat"
      root.fetchSituationContext()
      if (!root.opened) {
        root.toggle()
      }
      focusTimer.start()
      Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
    }
    function captureWindowContext(): void {
      root.situationContext()
    }
    function captureScreenshot(): void {
      root.currentView = "chat"
      root.captureScreenNow()
      if (!root.opened) {
        root.toggle()
      }
      focusTimer.start()
      Qt.callLater(function() { if (promptInput) promptInput.forceActiveFocus() })
    }
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
        if (root.opened && root.situationContextEnabled) {
          root.situationContextEnabled = false
          root.situationData = null
        } else {
          root.fetchSituationContext()
          root.open()
        }
      } else {
        root.toggle()
      }
    }

    Rectangle {
      id: iconDot
      visible: (!root.opened && root.unreadCount > 0) || root.rawStatus.state === "working" || root.rawStatus.state === "error"
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
        if (root.opened && root.situationContextEnabled) {
          root.situationContextEnabled = false
          root.situationData = null
        } else {
          root.fetchSituationContext()
          root.open()
        }
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
        visible: (!root.opened && root.unreadCount > 0) || root.rawStatus.state === "working" || root.rawStatus.state === "error"
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
  KeyboardPanel {
    id: popup
    anchorItem: root.barShowsText ? dataButton : button
    bar: root.bar
    owner: root
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: Style.space(560)
    contentHeight: Style.space(680)

    onOpenChanged: {
      if (open) {
        root.fetchStatus()
        root.fetchHistory()
        root.fetchModels()
        focusTimer.start()
        Qt.callLater(function() {
          if (root.currentView === "chat" && promptInput) {
            promptInput.forceActiveFocus()
          }
        })
      }
    }

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: (promptInput && promptInput.activeFocus) || (newMemInput && newMemInput.activeFocus) || (modelSearchInput && modelSearchInput.activeFocus)

      onMoveRequested: function(dx, dy) {
        if (root.currentView === "chat" && dy !== 0) {
          root.scrollChat(dy > 0 ? 1 : -1)
        }
      }

      onCloseRequested: root.close()

      onTextKey: function(t) {
        if (root.currentView === "chat" && promptInput) {
          promptInput.forceActiveFocus()
          promptInput.text = promptInput.text + t
        } else if (root.currentView === "settings" && modelSearchInput) {
          modelSearchInput.forceActiveFocus()
          modelSearchInput.text = modelSearchInput.text + t
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
            iconText: "󰈙"
            tooltipText: "Agent Logs & Full Responses"
            selected: root.currentView === "logs"
            implicitWidth: Style.space(32)
            implicitHeight: Style.space(32)
            onClicked: {
              root.selectedMsgLog = null
              root.currentView = "logs"
              root.fetchLogs()
            }
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

        MouseArea {
          anchors.fill: parent
          z: -1
          onClicked: if (promptInput) promptInput.forceActiveFocus()
        }
        ColumnLayout {
          anchors.fill: parent
          spacing: Style.space(8)

          // Scrollable Chat Message List Container
          Item {
            id: chatListContainer
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
              id: chatListView
              anchors.fill: parent
              model: (root.historyData && root.historyData.messages) ? root.historyData.messages : []
              spacing: Style.space(10)
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              flickableDirection: Flickable.VerticalFlick

              ScrollBar.vertical: ScrollBar {
                id: chatVerticalScrollBar
                policy: ScrollBar.AsNeeded
                active: true
              }

              onCountChanged: {
                root.scrollChatToEnd()
              }

              onContentHeightChanged: {
                if (root.autoScrollPinned) {
                  positionViewAtEnd()
                  var minY = originY
                  var maxY = Math.max(minY, minY + contentHeight - height)
                  contentY = maxY
                }
              }

              onMovementStarted: {
                root.autoScrollPinned = atYEnd
              }

              onMovementEnded: {
                root.autoScrollPinned = atYEnd
              }

              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Up) {
                  root.scrollChat(-1)
                  event.accepted = true
                  if (promptInput) promptInput.forceActiveFocus()
                  return
                }
                if (event.key === Qt.Key_Down) {
                  root.scrollChat(1)
                  event.accepted = true
                  if (promptInput) promptInput.forceActiveFocus()
                  return
                }
                if (event.key === Qt.Key_PageUp) {
                  root.scrollChat(-4)
                  event.accepted = true
                  if (promptInput) promptInput.forceActiveFocus()
                  return
                }
                if (event.key === Qt.Key_PageDown) {
                  root.scrollChat(4)
                  event.accepted = true
                  if (promptInput) promptInput.forceActiveFocus()
                  return
                }
                if (event.key === Qt.Key_Escape) {
                  root.close()
                  event.accepted = true
                  return
                }
                if (event.text && event.text.length > 0 && promptInput && !promptInput.activeFocus) {
                  promptInput.forceActiveFocus()
                  promptInput.text = promptInput.text + event.text
                  event.accepted = true
                  return
                }
              }
              delegate: Item {
                id: msgDelegate
                width: chatListView.width
                implicitHeight: msgCard.implicitHeight + Style.space(4)

                readonly property bool isUser: modelData.role === "user"
                readonly property var blocks: Model.parseBlocks(modelData.content || "")
                readonly property var attachments: modelData.attachments || []
                readonly property var actions: modelData.actions || []
                readonly property string assistantModel: {
                  if (modelData.model && String(modelData.model).trim().length > 0) {
                    return String(modelData.model).trim()
                  }
                  return String(root.rawStatus.active_model || "ox-alpha-free")
                }
                readonly property string assistantEngine: {
                  if (modelData.engine && String(modelData.engine).trim().length > 0) {
                    return String(modelData.engine).trim()
                  }
                  return String(root.rawStatus.active_engine || "hermes")
                }

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

                      // Agent badge for assistant answers
                      BorderSurface {
                        id: agentBadge
                        visible: !isUser && Boolean(assistantEngine.length > 0)
                        readonly property var agInfo: Model.agentLogoInfo(assistantEngine)
                        implicitHeight: Style.space(19)
                        implicitWidth: agRow.implicitWidth + Style.space(10)
                        radius: Style.space(4)
                        color: root.alpha(agInfo.color, 0.08)
                        borderSpec: Border.controlSpec("normal", root.alpha(agInfo.color, 0.22), agInfo.color)

                        Row {
                          id: agRow
                          anchors.centerIn: parent
                          spacing: Style.space(4)

                          Item {
                            width: Style.space(11)
                            height: Style.space(11)
                            anchors.verticalCenter: parent.verticalCenter

                            Image {
                              id: agLogoImg
                              anchors.fill: parent
                              source: (agentBadge.agInfo && agentBadge.agInfo.svg) ? Qt.resolvedUrl(agentBadge.agInfo.svg) : ""
                              sourceSize.width: Style.space(22)
                              sourceSize.height: Style.space(22)
                              fillMode: Image.PreserveAspectFit
                              visible: status === Image.Ready
                            }

                            Text {
                              anchors.centerIn: parent
                              visible: agLogoImg.status !== Image.Ready
                              text: (agentBadge.agInfo && agentBadge.agInfo.fallbackIcon) ? agentBadge.agInfo.fallbackIcon : "󱚣"
                              font.family: root.fontFamily
                              font.pixelSize: Style.space(9)
                              color: agentBadge.agInfo.color
                            }
                          }

                          Text {
                            text: agentBadge.agInfo.name || "Agent"
                            font.family: root.fontFamily
                            font.pixelSize: Style.space(9)
                            font.bold: true
                            color: root.foreground
                            anchors.verticalCenter: parent.verticalCenter
                          }
                        }
                      }

                      // Model badge for assistant answers
                      BorderSurface {
                        id: modelBadge
                        visible: !isUser && Boolean(assistantModel.length > 0)
                        readonly property var logoInfo: Model.modelLogoInfo(assistantModel, assistantEngine)
                        implicitHeight: Style.space(19)
                        implicitWidth: badgeRow.implicitWidth + Style.space(10)
                        radius: Style.space(4)
                        color: root.alpha(logoInfo.color || root.accent, 0.08)
                        borderSpec: Border.controlSpec("normal", root.alpha(logoInfo.color || root.accent, 0.22), logoInfo.color || root.accent)

                        Row {
                          id: badgeRow
                          anchors.centerIn: parent
                          spacing: Style.space(4)

                          // Logo / Icon
                          Item {
                            width: Style.space(11)
                            height: Style.space(11)
                            anchors.verticalCenter: parent.verticalCenter

                            Image {
                              id: badgeLogoImg
                              anchors.fill: parent
                              source: (modelBadge.logoInfo && modelBadge.logoInfo.svg) ? Qt.resolvedUrl(modelBadge.logoInfo.svg) : ""
                              sourceSize.width: Style.space(22)
                              sourceSize.height: Style.space(22)
                              fillMode: Image.PreserveAspectFit
                              visible: status === Image.Ready
                            }

                            Text {
                              anchors.centerIn: parent
                              visible: badgeLogoImg.status !== Image.Ready
                              text: (modelBadge.logoInfo && modelBadge.logoInfo.fallbackIcon) ? modelBadge.logoInfo.fallbackIcon : "󰚩"
                              font.family: root.fontFamily
                              font.pixelSize: Style.space(9)
                              color: (modelBadge.logoInfo && modelBadge.logoInfo.color) ? modelBadge.logoInfo.color : root.accent
                            }
                          }

                          // Model Name Label
                          Text {
                            text: Model.cleanModelName(assistantModel)
                            font.family: "JetBrainsMono Nerd Font"
                            font.pixelSize: Style.space(9)
                            font.bold: true
                            color: root.foreground
                            anchors.verticalCenter: parent.verticalCenter
                          }
                        }
                      }

                      Item { Layout.fillWidth: true }

                      Button {
                        visible: isUser
                        iconText: "󰆏"
                        tooltipText: "Copy question"
                        implicitHeight: Style.space(20)
                        implicitWidth: Style.space(20)
                        fontSize: Style.space(9)
                        onClicked: root.copyText(modelData.content)
                      }

                      Text {
                        text: Model.formatTimestamp(modelData.timestamp)
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
                            text: {
                              if (modelData.type === "situation") return "󰘦"
                              if (modelData.is_screen_capture) return "󰄀"
                              return modelData.icon || "󰈔"
                            }
                            color: root.accent
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                          }

                          Text {
                            text: {
                              if (modelData.type === "situation") {
                                var appName = modelData.app_name || "Desktop"
                                var cwdStr = modelData.cwd ? (" • " + modelData.cwd.replace(/^\/home\/[^/]+/, "~")) : ""
                                var gitStr = modelData.git_branch ? (" (" + modelData.git_branch + ")") : ""
                                return "Situation Context: " + appName + cwdStr + gitStr
                              }
                              if (modelData.is_screen_capture) {
                                var app = modelData.app_name || "Active Window"
                                var title = modelData.window_title ? (" — " + modelData.window_title) : ""
                                return "Screenshot Context: " + app + title
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

                    // Rendered Content Blocks (Selectable with Mouse)
                    Repeater {
                      model: blocks
                      delegate: Item {
                        Layout.fillWidth: true
                        implicitHeight: modelData.type === "code" ? codeCard.implicitHeight : (textBlock.implicitHeight + Style.space(2))

                        TextEdit {
                          id: textBlock
                          visible: modelData.type !== "code"
                          width: parent.width
                          text: modelData.text || ""
                          textFormat: TextEdit.MarkdownText
                          wrapMode: TextEdit.Wrap
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.body
                          color: root.foreground
                          readOnly: true
                          selectByMouse: true
                          mouseSelectionMode: TextEdit.SelectCharacters
                          cursorVisible: false
                          activeFocusOnPress: false
                          selectionColor: root.alpha(root.accent, 0.4)
                          selectedTextColor: root.foreground
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

                            TextEdit {
                              text: modelData.code || ""
                              textFormat: TextEdit.PlainText
                              font.family: "JetBrainsMono Nerd Font"
                              font.pixelSize: Style.space(11)
                              color: root.foreground
                              wrapMode: TextEdit.Wrap
                              Layout.fillWidth: true
                              readOnly: true
                              selectByMouse: true
                              mouseSelectionMode: TextEdit.SelectCharacters
                              cursorVisible: false
                              activeFocusOnPress: false
                              selectionColor: root.alpha(root.accent, 0.4)
                              selectedTextColor: root.foreground
                            }
                          }
                        }
                      }
                    }

                    // Fallback Text if blocks is empty or failed to render (Selectable with Mouse)
                    TextEdit {
                      visible: blocks.length === 0
                      text: (modelData.content && modelData.content.trim().length > 0) ? modelData.content : (isUser ? "(empty message)" : "✓ Done.")
                      textFormat: TextEdit.PlainText
                      wrapMode: TextEdit.Wrap
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      color: (modelData.content && modelData.content.trim().length > 0) ? root.foreground : root.muted
                      font.italic: !modelData.content || modelData.content.trim().length === 0
                      Layout.fillWidth: true
                      readOnly: true
                      selectByMouse: true
                      mouseSelectionMode: TextEdit.SelectCharacters
                      cursorVisible: false
                      activeFocusOnPress: false
                      selectionColor: root.alpha(root.accent, 0.4)
                      selectedTextColor: root.foreground
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
                        iconText: "󰈙"
                        text: "Log"
                        tooltipText: "Inspect raw response, thinking blocks & traces for this turn"
                        fontSize: Style.space(10)
                        implicitHeight: Style.space(22)
                        onClicked: root.showSpecificLog(modelData)
                      }

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

            // Floating scroll to bottom button
            BorderSurface {
              id: scrollToBottomBtn
              visible: chatListView.count > 0 && !chatListView.atYEnd
              anchors.right: parent.right
              anchors.bottom: parent.bottom
              anchors.margins: Style.space(12)
              width: Style.space(32)
              height: Style.space(32)
              radius: width / 2
              color: scrollBottomMouse.containsMouse ? root.accent : root.alpha(root.foreground, 0.22)
              borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.4), root.accent)
              z: 10

              Text {
                anchors.centerIn: parent
                text: "󰁅"
                font.family: root.fontFamily
                font.pixelSize: Style.space(14)
                font.bold: true
                color: scrollBottomMouse.containsMouse ? Color.background : root.foreground
              }

              MouseArea {
                id: scrollBottomMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  chatListView.positionViewAtEnd()
                  if (promptInput) promptInput.forceActiveFocus()
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

          // Active Context Strip (Situation, Files, Documents, Media, Screen)
          BorderSurface {
            id: contextStrip
            visible: root.attachedFilePath.length > 0 || root.screenContextEnabled || root.situationContextEnabled
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
                text: {
                  if (root.situationContextEnabled) return "󰘦"
                  if (root.screenContextEnabled) return "󰄀"
                  return root.attachedFileIcon || "󰈔"
                }
                color: root.accent
                font.family: root.fontFamily
                font.pixelSize: Style.space(16)
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(2)

                Text {
                  text: {
                    if (root.situationContextEnabled) {
                      var sum = Model.formatSituationSummary(root.situationData)
                      return sum.title
                    }
                    if (root.screenContextEnabled) {
                      if (root.lastCaptureInfo && root.lastCaptureInfo.active_window) {
                        var w = root.lastCaptureInfo.active_window
                        var cls = w.class || "Active Window"
                        var tit = w.title ? (" — " + w.title) : ""
                        return cls + tit
                      }
                      return "Active Screen Context"
                    }
                    return root.attachedFileName || "Attached File"
                  }
                  textFormat: Text.PlainText
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  color: root.foreground
                  elide: Text.ElideRight
                  Layout.fillWidth: true
                }

                Text {
                  text: {
                    if (root.situationContextEnabled) {
                      var sum2 = Model.formatSituationSummary(root.situationData)
                      return sum2.subtitle
                    }
                    if (root.screenContextEnabled && root.lastCaptureInfo && root.lastCaptureInfo.active_window) {
                      return "Visual Screenshot: " + (root.lastCaptureInfo.active_window.class || "App") + " (Multimodal image)"
                    }
                    var s = root.attachedFileSize ? ("Size: " + root.attachedFileSize + " • ") : ""
                    var p = root.attachedFilePath || ""
                    if (p.length > 42) p = "…" + p.substring(p.length - 40)
                    return s + p
                  }
                  textFormat: Text.PlainText
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(9)
                  color: root.muted
                  elide: Text.ElideMiddle
                  Layout.fillWidth: true
                }
              }

              Button {
                iconText: "󰅖"
                tooltipText: "Remove context / attachment"
                implicitWidth: Style.space(24)
                implicitHeight: Style.space(24)
                fontSize: Style.space(11)
                onClicked: {
                  root.attachedFilePath = ""
                  root.attachedFileName = ""
                  root.attachedFileSize = ""
                  root.attachedFileIsImage = false
                  root.screenContextEnabled = false
                  root.situationContextEnabled = false
                  root.lastCaptureInfo = null
                  root.situationData = null
                }
              }
            }
          }

          // Prompt & Context Controls
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)

            // Reorganized Input Row: [+] [󰘦 Situation] [󰄀 Screen] [Text Field] [Mic 󰍬] [Send 󰒭]
            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(6)

              // 1. Consolidated [+] Attachment Button (File, Image, Document, Code)
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

              // 2. Desktop Situation Context Toggle Button
              Button {
                id: situationBtn
                enabled: !root.isProcessing
                iconText: "󰘦"
                tooltipText: root.situationContextEnabled ? "Situation Context Active (Click to remove)" : "Desktop Situation Context (Active window, directory, git repo, open tools) [Super + Shift + A]"
                selected: root.situationContextEnabled
                implicitHeight: promptInput.implicitHeight
                implicitWidth: promptInput.implicitHeight
                fontSize: Style.space(14)
                onClicked: {
                  if (!root.situationContextEnabled) {
                    root.fetchSituationContext()
                  } else {
                    root.situationContextEnabled = false
                    root.situationData = null
                  }
                }
              }

              // 3. Visual Screenshot Button (multimodal analysis)
              Button {
                id: screenBtn
                enabled: !root.isProcessing
                iconText: "󰄀"
                tooltipText: root.screenContextEnabled ? "Visual Screenshot Active (Click to remove)" : "Capture Visual Screenshot for Image Analysis (Right-click: paste clipboard image)"
                selected: root.screenContextEnabled
                implicitHeight: promptInput.implicitHeight
                implicitWidth: promptInput.implicitHeight
                fontSize: Style.space(14)
                onClicked: {
                  if (!root.screenContextEnabled) {
                    root.captureScreenNow()
                  } else {
                    root.screenContextEnabled = false
                    root.attachedFilePath = ""
                    root.lastCaptureInfo = null
                  }
                }
                onRightClicked: root.attachClipboardImage()
              }

              // 4. Text Input
              TextField {
                id: promptInput
                focus: true
                Layout.fillWidth: true
                placeholderText: root.isRecordingVoice ? "🎙️ Listening… speak now (click mic to transcribe)" : (root.isProcessing ? "Botty is thinking… (you can type your next prompt)" : "Ask Botty or command a desktop action… (Enter to send)")
                font.pixelSize: Style.font.body
                onAccepted: {
                  if (!root.isProcessing && (text.trim().length > 0 || root.attachedFilePath.length > 0 || root.screenContextEnabled || root.situationContextEnabled)) {
                    root.sendQuery(text, root.attachedFilePath, root.screenContextEnabled, root.situationContextEnabled)
                  }
                  promptInput.forceActiveFocus()
                }

                Keys.onUpPressed: function(event) {
                  root.scrollChat(-1)
                  event.accepted = true
                }
                Keys.onDownPressed: function(event) {
                  root.scrollChat(1)
                  event.accepted = true
                }
                Keys.onPressed: function(event) {
                  if (event.key === Qt.Key_PageUp) {
                    root.scrollChat(-4)
                    event.accepted = true
                  } else if (event.key === Qt.Key_PageDown) {
                    root.scrollChat(4)
                    event.accepted = true
                  }
                }
              }

              // 5. Voice Dictation Button
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

              // 6. Send Button
              Button {
                iconText: "󰒭"
                text: "Send"
                enabled: !root.isProcessing
                selected: true
                implicitHeight: promptInput.implicitHeight
                onClicked: {
                  if (!root.isProcessing && (promptInput.text.trim().length > 0 || root.attachedFilePath.length > 0 || root.screenContextEnabled || root.situationContextEnabled)) {
                    root.sendQuery(promptInput.text, root.attachedFilePath, root.screenContextEnabled, root.situationContextEnabled)
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
                  onClicked: {
                    root.selectEngine(modelData.id)
                    modelsProc.command = ["python3", root.scriptPath(), "models", "--engine", modelData.id]
                    modelsProc.running = true
                  }
                }
              }
            }
          }

          PanelSeparator { Layout.fillWidth: true }

          // ----------------------------------------------------- Section 2: Active Inference Providers
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(4)

            Text {
              text: "2. Active Inference Providers in " + (root.rawStatus.active_engine ? root.rawStatus.active_engine.toUpperCase() : "HERMES") + " (" + settingsViewItem.allProviders.length + " configured):"
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
                  selected: modelData.id === (settingsViewItem.currentProviderObj ? settingsViewItem.currentProviderObj.id : "")
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

      // ========================================================= VIEW: LOGS & FULL RESPONSES
      Item {
        id: logsViewItem
        visible: root.currentView === "logs"
        Layout.fillWidth: true
        Layout.fillHeight: true

        property string logTab: root.selectedMsgLog ? "inspected" : "latest" // "inspected" | "latest" | "stream"

        ColumnLayout {
          anchors.fill: parent
          spacing: Style.space(10)

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            Text {
              text: "Agent Execution Logs & Raw Output"
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              color: root.foreground
            }

            Item { Layout.fillWidth: true }

            Button {
              iconText: "󰌑"
              text: "Back to Chat"
              implicitHeight: Style.space(26)
              fontSize: Style.space(10)
              onClicked: { root.currentView = "chat" }
            }
          }

          // Tab switchers & actions
          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)

            Button {
              visible: Boolean(root.selectedMsgLog)
              iconText: "󰈙"
              text: "Inspected Turn"
              selected: logsViewItem.logTab === "inspected"
              implicitHeight: Style.space(26)
              fontSize: Style.space(10)
              onClicked: { logsViewItem.logTab = "inspected" }
            }

            Button {
              iconText: "󰚩"
              text: "Latest Raw Output"
              selected: logsViewItem.logTab === "latest"
              implicitHeight: Style.space(26)
              fontSize: Style.space(10)
              onClicked: { logsViewItem.logTab = "latest" }
            }

            Button {
              iconText: "󰈙"
              text: "Log Stream (" + (root.logsData.log_count || 0) + ")"
              selected: logsViewItem.logTab === "stream"
              implicitHeight: Style.space(26)
              fontSize: Style.space(10)
              onClicked: { logsViewItem.logTab = "stream" }
            }

            Item { Layout.fillWidth: true }

            Button {
              iconText: "󰑐"
              tooltipText: "Refresh logs"
              implicitWidth: Style.space(26)
              implicitHeight: Style.space(26)
              fontSize: Style.space(11)
              onClicked: root.fetchLogs()
            }

            Button {
              iconText: "󰆏"
              tooltipText: "Copy active log view to clipboard"
              implicitWidth: Style.space(26)
              implicitHeight: Style.space(26)
              fontSize: Style.space(11)
              onClicked: {
                var contentToCopy = ""
                if (logsViewItem.logTab === "inspected" && root.selectedMsgLog) {
                  contentToCopy = root.selectedMsgLog.raw_output || root.selectedMsgLog.content || ""
                } else if (logsViewItem.logTab === "latest") {
                  contentToCopy = root.logsData.last_assistant_raw || ""
                } else {
                  contentToCopy = root.logsData.logs || ""
                }
                root.copyText(contentToCopy)
              }
            }

            Button {
              iconText: "󰆴"
              tooltipText: "Clear botty.log file"
              implicitWidth: Style.space(26)
              implicitHeight: Style.space(26)
              fontSize: Style.space(11)
              onClicked: root.clearLogsNow()
            }
          }

          // Metadata badge if inspecting a message
          BorderSurface {
            visible: logsViewItem.logTab === "inspected" && Boolean(root.selectedMsgLog)
            Layout.fillWidth: true
            implicitHeight: insRow.implicitHeight + Style.space(8)
            radius: Style.space(4)
            color: root.alpha(root.accent, 0.1)
            borderSpec: Border.controlSpec("normal", root.alpha(root.accent, 0.3), root.accent)

            RowLayout {
              id: insRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.space(6)
              spacing: Style.space(6)

              Text {
                text: "Turn: " + (root.selectedMsgLog ? (root.selectedMsgLog.engine || "agent") + " / " + (root.selectedMsgLog.model || "model") : "")
                font.family: "JetBrainsMono Nerd Font"
                font.pixelSize: Style.space(10)
                font.bold: true
                color: root.accent
                Layout.fillWidth: true
                elide: Text.ElideRight
              }

              Button {
                text: "Dismiss"
                implicitHeight: Style.space(20)
                fontSize: Style.space(9)
                onClicked: {
                  root.selectedMsgLog = null
                  logsViewItem.logTab = "latest"
                }
              }
            }
          }

          // Monospace Monitored Log Content Surface
          BorderSurface {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Style.space(8)
            color: root.alpha(root.foreground, 0.04)
            borderSpec: Border.controlSpec("normal", root.alpha(root.foreground, 0.12), root.accent)

            ScrollView {
              anchors.fill: parent
              anchors.margins: Style.space(8)
              clip: true

              TextEdit {
                id: logDisplayArea
                width: parent.width
                text: {
                  if (logsViewItem.logTab === "inspected" && root.selectedMsgLog) {
                    return root.selectedMsgLog.raw_output || root.selectedMsgLog.content || "(No raw output saved for this message)"
                  }
                  if (logsViewItem.logTab === "latest") {
                    return root.logsData.last_assistant_raw || "(No raw assistant output recorded yet. Send a message to see unstripped output & reasoning traces.)"
                  }
                  return root.logsData.logs || "(Log file is empty: " + (root.logsData.log_path || "~/.local/share/botty/botty.log") + ")"
                }
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.Wrap
                font.family: "JetBrainsMono Nerd Font"
                font.pixelSize: Style.space(11)
                color: root.foreground
                readOnly: true
                selectByMouse: true
                mouseSelectionMode: TextEdit.SelectCharacters
                cursorVisible: false
                activeFocusOnPress: false
                selectionColor: root.alpha(root.accent, 0.4)
                selectedTextColor: root.foreground
              }
            }
          }
        }
      }
    }
  }
}
}
