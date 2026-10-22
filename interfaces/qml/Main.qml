import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "mainWindow"
    visible: false
    width: 1000; height: 700
    minimumWidth: 920; minimumHeight: 600
    title: "RenaClip"
    color: paletteTokens.background
    font.family: "Segoe UI"; font.pixelSize: 14
    Material.theme: Material.Dark
    Material.accent: paletteTokens.accentLight
    Material.foreground: paletteTokens.text
    Material.background: paletteTokens.background
    onClosing: function(close) { close.accepted = false; bridge.requestClose() }
    property string statusMessage: ""
    property int editIndex: -1
    property int providerIndex: -1
    property int dragFrom: -1
    property int dragTarget: -1
    property real dragY: 0
    property string draftBackend: "openai"
    property string localError: ""
    property bool refreshRequested: false

    function text(value) { return value === undefined || value === null ? "" : String(value) }
    function openSkill(index) {
        bridge.clearError(); localError = ""; editIndex = index
        var entry = index >= 0 ? bridge.skills[index] : {}
        skillName.text = text(entry.name); skillDescription.text = text(entry.description)
        skillPrompt.text = text(entry.prompt); skillOnline.checked = !!entry.web_search
        skillPopup.checked = !!entry.popup_mode
        skillDialog.open()
    }
    function openSettings() {
        bridge.clearError(); localError = ""
        var s = bridge.beginSettings()
        hotkeyField.currentIndex = Math.max(0, hotkeyField.model.indexOf(text(s.HOTKEY_MODIFIER) || "ctrl"))
        startupToggle.checked = !!s.START_WITH_WINDOWS
        screenshotToggle.checked = !!s.SCREENSHOT_QA_ENABLED; searchToggle.checked = !!s.SCREENSHOT_QA_WEB_SEARCH
        draftBackend = text(s.BACKEND) || "openai"
        psid.text = text(s.GEMINI_PSID); psidts.text = text(s.GEMINI_PSIDTS)
        proxyField.text = text(s.GEMINI_PROXY); browserCookie.checked = !!s.GEMINI_USE_BROWSER_COOKIE
        browserField.currentIndex = text(s.GEMINI_COOKIE_BROWSER) === "chrome" ? 1 : 0
        geminiModel.currentIndex = Math.max(0, geminiModel.model.indexOf(text(s.GEMINI_MODEL)))
        settingsDialog.open()
    }
    function providerModels() {
        var result = []
        var lines = modelIds.text.replace(/,/g, "\n").split("\n")
        for (var i = 0; i < lines.length; i++) {
            var item = lines[i].trim()
            if (item.length && result.indexOf(item) < 0) result.push(item)
        }
        return result
    }
    function updateProviderModels(preferred) {
        var values = providerModels()
        if (preferred && values.indexOf(preferred) < 0) values.unshift(preferred)
        if (!values.length) values.push("gpt-4o")
        providerModel.model = values
        providerModel.currentIndex = Math.max(0, values.indexOf(preferred))
    }
    function openProvider(index) {
        bridge.clearError(); localError = ""; providerIndex = index; refreshRequested = false
        var p = index >= 0 ? bridge.providersDraft[index] : {name:"New provider", model:"gpt-4o",models:["gpt-4o"]}
        providerName.text = text(p.name); apiKey.text = text(p.api_key); apiKey.revealed = false; apiKey.echoMode = TextInput.Password
        baseUrl.text = text(p.base_url); apiInterface.currentIndex = p.api_interface === "responses" ? 1 : 0
        modelIds.text = (p.models || []).join("\n"); updateProviderModels(text(p.model))
        streaming.checked = !!p.streaming; providerDialog.open()
    }
    function finishDrag() {
        var from = dragFrom; var target = dragTarget
        dragFrom = -1; dragTarget = -1
        if (from >= 0 && target >= 0 && from !== target) bridge.moveSkill(from, target)
    }
    Connections {
        target: bridge
        function onRefreshChanged() {
            if (!root.refreshRequested || bridge.refreshing) return
            root.refreshRequested = false
            if (bridge.refreshedModels.length) {
                var previous = providerModel.currentText
                modelIds.text = bridge.refreshedModels.join("\n")
                root.updateProviderModels(previous)
            }
        }
    }
    component MaterialIconButton: ToolButton {
        property string symbol
        property string tip: ""
        implicitWidth: 40; implicitHeight: 40
        icon.source: "icons/" + symbol + ".svg"
        icon.width: 24; icon.height: 24; icon.color: paletteTokens.muted
        display: AbstractButton.IconOnly
        ToolTip.visible: hovered && tip.length > 0; ToolTip.text: tip
    }
    component OutlineButton: Button {
        id: control
        implicitHeight: 32; implicitWidth: Math.max(72, contentItem.implicitWidth + 32)
        topInset: 0; bottomInset: 0
        contentItem: Label { text: control.text; color: control.enabled ? paletteTokens.accentLight : paletteTokens.muted; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 8; color: control.down ? paletteTokens.accentSurface : "transparent"; border.width: 1; border.color: control.hovered ? paletteTokens.accentLight : "#786773" }
    }
    component PinkButton: OutlineButton {
        id: control
        contentItem: Label { text: control.text; color: paletteTokens.text; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 8; color: control.down ? "#9d1249" : paletteTokens.accent }
    }
    component Field: Item {
        id: field
        property string label: ""
        property alias text: entry.text
        property alias echoMode: entry.echoMode
        property string hint: ""
        property bool revealable: false
        property bool revealed: false
        implicitHeight: 48; implicitWidth: 300
        Rectangle { anchors.fill: parent; color: "transparent"; radius: 4; border.color: entry.activeFocus ? paletteTokens.accentLight : "#09080b" }
        TextField {
            id: entry; objectName: field.objectName.length ? field.objectName + "Input" : ""
            anchors.fill: parent; leftPadding: 16; rightPadding: field.revealable ? 48 : 16
            topPadding: 12; bottomPadding: 12; topInset: 0; bottomInset: 0
            placeholderText: field.hint; background: Item {}
        }
        MaterialIconButton {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            visible: field.revealable; symbol: field.revealed ? "visibility_off" : "visibility"
            tip: field.revealed ? "Hide value" : "Show value"
            onClicked: { field.revealed = !field.revealed; entry.echoMode = field.revealed ? TextInput.Normal : TextInput.Password }
        }
        Rectangle { x: 12; y: -7; width: caption.implicitWidth + 8; height: 15; color: field.Material.background; visible: field.label.length > 0
            Label { id: caption; anchors.centerIn: parent; text: field.label; font.pixelSize: 10; color: paletteTokens.muted }
        }
    }
    component SelectField: Item {
        id: box
        property string label: ""
        property alias model: select.model
        property alias currentIndex: select.currentIndex
        property alias currentText: select.currentText
        property alias combo: select
        implicitHeight: 48; implicitWidth: 180
        Rectangle { anchors.fill: parent; radius: 4; color: "transparent"; border.color: select.activeFocus ? paletteTokens.accentLight : "#09080b" }
        ComboBox { id: select; objectName: box.objectName.length ? box.objectName + "Input" : ""; anchors.fill: parent; leftPadding: 16; topInset: 0; bottomInset: 0; background: Item {} }
        Rectangle { x: 12; y: -7; width: caption.implicitWidth + 8; height: 15; color: box.Material.background
            Label { id: caption; anchors.centerIn: parent; text: box.label; font.pixelSize: 10; color: paletteTokens.muted }
        }
    }
    component MultilineField: Item {
        id: area
        property string label: ""
        property alias text: editor.text
        implicitWidth: 300; implicitHeight: 110
        Rectangle { anchors.fill: parent; color: "transparent"; radius: 4; border.color: editor.activeFocus ? paletteTokens.accentLight : "#09080b" }
        ScrollView { id: textScroll; anchors.fill: parent; anchors.margins: 14; clip: true; contentWidth: availableWidth
            TextEdit { id: editor; objectName: area.objectName.length ? area.objectName + "Input" : ""; width: textScroll.availableWidth; color: paletteTokens.text; font: root.font; wrapMode: TextEdit.Wrap; selectByMouse: true }
        }
        Rectangle { x: 12; y: -7; width: caption.implicitWidth + 8; height: 15; color: area.Material.background
            Label { id: caption; anchors.centerIn: parent; text: area.label; font.pixelSize: 10; color: paletteTokens.muted }
        }
    }
    component Rule: Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: paletteTokens.divider }
    component ErrorLabel: Label {
        Layout.fillWidth: true
        text: root.localError || (bridge ? bridge.errorMessage : "")
        visible: text.length > 0
        color: "#ffb4ab"; wrapMode: Text.Wrap; font.pixelSize: 12
    }
    component ProductionDialog: Dialog {
        id: baseDialog
        property var commit: function() { return true }
        property var cancelAction: function() {}
        property bool committed: false
        anchors.centerIn: parent
        modal: true; padding: 24
        Material.background: paletteTokens.surface
        Overlay.modal: Rectangle { color: "#a6000000" }
        background: Rectangle { color: paletteTokens.surface; radius: 8 }
        header: Label { text: baseDialog.title; font.pixelSize: 20; font.weight: Font.DemiBold; padding: 24; bottomPadding: 20 }
        onOpened: committed = false
        onClosed: { if (!committed) cancelAction() }
        footer: DialogButtonBox {
            spacing: 12; leftPadding: 24; rightPadding: 24; topPadding: 20; bottomPadding: 24
            implicitHeight: 76; background: Item {}
            OutlineButton { text: "Cancel"; objectName: baseDialog.objectName + "Cancel"; DialogButtonBox.buttonRole: DialogButtonBox.ActionRole; onClicked: baseDialog.reject() }
            PinkButton { text: "Save"; objectName: baseDialog.objectName + "Save"; DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: { if (baseDialog.commit()) { baseDialog.committed = true; baseDialog.accept() } }
            }
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 26
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Label { text: "RenaClip"; font.pixelSize: 24; font.weight: Font.Bold }
            Item { Layout.fillWidth: true }
            SelectField {
                id: reasoningField; objectName: "reasoningCombo"; label: "Reasoning"; Layout.preferredWidth: 180
                model: ["Default", "Low", "Medium", "High"]
                Binding { target: reasoningField.combo; property: "currentIndex"; value: Math.max(0, ["default", "low", "medium", "high"].indexOf(bridge ? bridge.reasoning : "default")) }
                combo.onActivated: bridge.setReasoning(["default", "low", "medium", "high"][currentIndex])
            }
            SelectField {
                id: modelField; objectName: "modelCombo"; label: "Model"; Layout.preferredWidth: 280
                model: bridge ? bridge.modelRoutes : []
                Binding { target: modelField.combo; property: "currentIndex"; value: bridge ? bridge.activeRoute : -1 }
                combo.textRole: "title"
                combo.displayText: bridge && bridge.activeRoute >= 0 && bridge.activeRoute < bridge.modelRoutes.length ? bridge.modelRoutes[bridge.activeRoute].model : "Select model"
                combo.delegate: ItemDelegate {
                    required property int index
                    required property var modelData
                    width: modelField.width; enabled: !modelData.heading
                    text: modelData.title; leftPadding: enabled ? 24 : 16
                    contentItem: Label { text: modelData.title; color: enabled ? paletteTokens.text : paletteTokens.accentLight; verticalAlignment: Text.AlignVCenter }
                }
                combo.onActivated: bridge.setRoute(currentIndex)
            }
            OutlineButton { objectName: "settingsButton"; text: "Settings"; Layout.preferredWidth: 100; onClicked: root.openSettings() }
            PinkButton { objectName: "addSkillButton"; text: "Add Skill"; Layout.preferredWidth: 105; onClicked: root.openSkill(-1) }
        }
        Rule {}
        ErrorLabel { visible: !settingsDialog.visible && !skillDialog.visible && !providerDialog.visible && text.length > 0 }
        ListView {
            id: skillList; objectName: "skillList"; Layout.fillWidth: true; Layout.fillHeight: true
            model: bridge ? bridge.skills : []; spacing: 8; clip: true
            interactive: root.dragFrom < 0
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            Timer {
                interval: 50; repeat: true; running: root.dragFrom >= 0
                onTriggered: {
                    if (root.dragY < 30) skillList.contentY = Math.max(0, skillList.contentY - 12)
                    else if (root.dragY > skillList.height - 30) skillList.contentY = Math.min(Math.max(0, skillList.contentHeight - skillList.height), skillList.contentY + 12)
                    root.dragTarget = Math.max(0, Math.min(bridge.skills.length - 1, Math.floor((root.dragY + skillList.contentY) / 92)))
                }
            }
            delegate: Rectangle {
                id: card
                required property int index
                required property var modelData
                width: skillList.width; height: 84; radius: 8
                color: hover.hovered ? paletteTokens.raised : paletteTokens.surface
                opacity: root.dragFrom === card.index ? 0.65 : 1
                border.width: root.dragFrom >= 0 && root.dragTarget === card.index ? 1 : 0
                border.color: paletteTokens.accentLight
                HoverHandler { id: hover }
                RowLayout { anchors.fill: parent; anchors.margins: 16; spacing: 10
                    ColumnLayout { Layout.fillWidth: true; spacing: 12
                        RowLayout { spacing: 8; Layout.fillWidth: true
                            Label { text: root.text(card.modelData.name); font.family: "Microsoft YaHei UI"; font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.maximumWidth: Math.max(100, card.width - 420) }
                            Rectangle { implicitWidth: shortcutLabel.implicitWidth + 16; implicitHeight: 24; radius: 4; color: paletteTokens.accent
                                Label { id: shortcutLabel; anchors.centerIn: parent; text: (bridge ? bridge.hotkey : "") + "+" + (card.index + 1); font.pixelSize: 10 }
                            }
                            MaterialIconButton { symbol: "public"; tip: "Web search enabled"; visible: !!card.modelData.web_search; padding: 0; implicitWidth: 20; implicitHeight: 24; icon.width: 16; icon.height: 16; icon.color: paletteTokens.accentLight }
                        }
                        Label { text: root.text(card.modelData.description); font.family: "Microsoft YaHei UI"; font.pixelSize: 12; color: paletteTokens.muted; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    OutlineButton { text: "Edit"; objectName: "editSkill" + card.index; onClicked: root.openSkill(card.index) }
                    OutlineButton { text: "Delete"; objectName: "deleteSkill" + card.index; Layout.preferredWidth: 88; onClicked: bridge.deleteSkill(card.index) }
                    MaterialIconButton {
                        symbol: "drag_handle"; tip: "Drag to reorder"; Layout.leftMargin: 12; icon.color: paletteTokens.text
                        objectName: "dragHandle" + card.index
                        MouseArea {
                            anchors.fill: parent; cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor; preventStealing: true
                            onPressed: function(mouse) { root.dragFrom = card.index; root.dragTarget = card.index; root.dragY = mapToItem(skillList, mouse.x, mouse.y).y }
                            onPositionChanged: function(mouse) {
                                if (!pressed) return
                                root.dragY = mapToItem(skillList, mouse.x, mouse.y).y
                                root.dragTarget = Math.max(0, Math.min(bridge.skills.length - 1, Math.floor((root.dragY + skillList.contentY) / 92)))
                            }
                            onReleased: root.finishDrag()
                            onCanceled: { root.dragFrom = -1; root.dragTarget = -1 }
                        }
                    }
                }
            }
        }
    }
    Label {
        anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 12
        text: root.statusMessage; visible: text.length > 0; color: paletteTokens.muted; font.pixelSize: 12
    }
    ProductionDialog {
        id: settingsDialog; objectName: "settingsDialog"
        width: 550; height: Math.min(root.height - 40, 650); title: "Settings"
        onRejected: { bridge.cancelSettings(); root.localError = "" }
        cancelAction: function() { bridge.cancelSettings(); root.localError = "" }
        commit: function() {
            root.localError = ""
            bridge.updateSettingsDraft({START_WITH_WINDOWS:startupToggle.checked, HOTKEY_MODIFIER:hotkeyField.currentText, BACKEND:root.draftBackend,
                SCREENSHOT_QA_ENABLED:screenshotToggle.checked, SCREENSHOT_QA_WEB_SEARCH:searchToggle.checked,
                GEMINI_PSID:psid.text, GEMINI_PSIDTS:psidts.text, GEMINI_PROXY:proxyField.text,
                GEMINI_USE_BROWSER_COOKIE:browserCookie.checked, GEMINI_COOKIE_BROWSER:browserField.currentText,
                GEMINI_MODEL:geminiModel.currentText})
            return bridge.saveSettings()
        }
        contentItem: ScrollView { clip: true; contentWidth: availableWidth
            ColumnLayout { width: settingsDialog.availableWidth; spacing: 12
                ErrorLabel {}
                Label { text: "Startup"; font.weight: Font.DemiBold }
                CheckBox { id: startupToggle; objectName: "startWithWindows"; implicitHeight: 34; text: "Start with Windows" }
                Label { text: "Launch in the system tray when you sign in."; font.pixelSize: 11; color: paletteTokens.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Rule {}
                Label { text: "Hotkey"; font.weight: Font.DemiBold }
                SelectField { id: hotkeyField; objectName: "hotkeyModifier"; Layout.fillWidth: true; label: "Hotkey modifier"; model: ["ctrl", "ctrl+shift", "ctrl+alt", "ctrl+shift+alt"] }
                Rule {}
                Label { text: "Screenshot QA"; font.weight: Font.DemiBold }
                CheckBox { id: screenshotToggle; objectName: "screenshotEnabled"; implicitHeight: 34; text: "Screenshot Q&A (Hotkey + 0)" }
                CheckBox { id: searchToggle; objectName: "screenshotWebSearch"; implicitHeight: 34; text: "Web search"; enabled: screenshotToggle.checked }
                Rule {}
                Label { text: "Backend: " + (root.draftBackend === "openai" ? "OpenAI" : "Gemini"); font.weight: Font.DemiBold }
                RowLayout {
                    OutlineButton { text: "OpenAI"; onClicked: root.draftBackend = "openai"; background: Rectangle { color: root.draftBackend === "openai" ? paletteTokens.accentSurface : paletteTokens.surface; radius: 8 } }
                    OutlineButton { text: "Gemini Web (unstable)"; onClicked: root.draftBackend = "gemini"; background: Rectangle { color: root.draftBackend === "gemini" ? paletteTokens.accentSurface : paletteTokens.surface; radius: 8 } }
                }
                Rule {}
                Label { text: "OpenAI"; font.weight: Font.DemiBold }
                Repeater {
                    model: bridge ? bridge.providersDraft : []
                    delegate: RowLayout {
                        required property int index
                        required property var modelData
                        Layout.fillWidth: true
                        ColumnLayout { Layout.fillWidth: true; spacing: 5
                            Label { text: root.text(modelData.name); font.weight: Font.DemiBold }
                            Label { text: (modelData.api_interface === "responses" ? "Responses API" : "Chat Completions") + "  |  " + root.text(modelData.model) + "  |  " + (root.text(modelData.base_url) || "default endpoint"); font.pixelSize: 11; color: paletteTokens.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                        MaterialIconButton { symbol: "edit"; tip: "Edit provider"; onClicked: root.openProvider(index) }
                        MaterialIconButton { symbol: "delete_outline"; tip: "Delete provider"; enabled: bridge && bridge.providersDraft.length > 1; onClicked: bridge.deleteProvider(index) }
                    }
                }
                OutlineButton { text: "Add provider"; onClicked: root.openProvider(-1) }
                Rule {}
                Label { text: "Gemini"; font.weight: Font.DemiBold }
                ColumnLayout { Layout.fillWidth: true; spacing: 14; enabled: root.draftBackend === "gemini"
                    Field { id: psid; objectName: "geminiPsid"; Layout.fillWidth: true; label: "GEMINI_PSID"; echoMode: TextInput.Password; revealable: true; enabled: !browserCookie.checked }
                    Field { id: psidts; objectName: "geminiPsidts"; Layout.fillWidth: true; label: "GEMINI_PSIDTS"; echoMode: TextInput.Password; revealable: true; enabled: !browserCookie.checked }
                    CheckBox { id: browserCookie; objectName: "geminiBrowserCookie"; text: "Log in via browser-cookie3" }
                    SelectField { id: browserField; objectName: "geminiBrowser"; Layout.fillWidth: true; label: "Cookie browser"; model: ["edge", "chrome"] }
                    Field { id: proxyField; objectName: "geminiProxy"; Layout.fillWidth: true; label: "Proxy"; hint: "socks5://127.0.0.1:8889" }
                    SelectField { id: geminiModel; objectName: "geminiModel"; Layout.fillWidth: true; label: "Gemini model"; model: ["unspecified", "gemini-3.0-pro", "gemini-3.0-flash", "gemini-3.0-flash-thinking"] }
                }
            }
        }
    }
    ProductionDialog {
        id: skillDialog; objectName: "skillDialog"; width: 390; title: root.editIndex < 0 ? "Add Skill" : "Edit Skill"
        commit: function() {
            if (!skillName.text.trim()) { root.localError = "Please enter a skill name."; return false }
            root.localError = ""
            return bridge.saveSkill(root.editIndex, {name:skillName.text.trim(), description:skillDescription.text, prompt:skillPrompt.text, web_search:skillOnline.checked, popup_mode:skillPopup.checked})
        }
        ColumnLayout { width: skillDialog.availableWidth; spacing: 14
            ErrorLabel {}
            Field { id: skillName; objectName: "skillName"; Layout.fillWidth: true; label: "Title" }
            MultilineField { id: skillDescription; objectName: "skillDescription"; Layout.fillWidth: true; Layout.preferredHeight: 65; label: "Description" }
            MultilineField { id: skillPrompt; objectName: "skillPrompt"; Layout.fillWidth: true; Layout.preferredHeight: 110; label: "Prompt" }
            CheckBox { id: skillOnline; objectName: "skillWebSearch"; text: "Enable web search" }
            CheckBox { id: skillPopup; objectName: "skillPopupMode"; text: "Popup mode" }
            Label { text: "Stream near the cursor without changing the clipboard."; font.pixelSize: 11; color: paletteTokens.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
        }
    }
    ProductionDialog {
        id: providerDialog; objectName: "providerDialog"; width: 550; title: "Edit Provider: " + providerName.text
        onOpened: { committed = false; bridge.cancelModelRefresh() }
        onClosed: { bridge.cancelModelRefresh(); root.refreshRequested = false; if (!committed) cancelAction() }
        commit: function() {
            if (!providerName.text.trim()) { root.localError = "Please enter a provider name."; return false }
            root.localError = ""
            return bridge.saveProvider(root.providerIndex, {name:providerName.text.trim(), api_key:apiKey.text, base_url:baseUrl.text,
                api_interface:apiInterface.currentIndex === 1 ? "responses" : "chat_completions",
                model:providerModel.currentText, models:root.providerModels(), streaming:streaming.checked})
        }
        cancelAction: function() { root.refreshRequested = false; root.localError = "" }
        ColumnLayout { width: providerDialog.availableWidth; spacing: 14
            ErrorLabel {}
            Field { id: providerName; objectName: "providerName"; Layout.fillWidth: true; label: "Provider name" }
            Field { id: apiKey; objectName: "providerApiKey"; Layout.fillWidth: true; hint: "API key"; echoMode: TextInput.Password; revealable: true }
            Field { id: baseUrl; objectName: "providerBaseUrl"; Layout.fillWidth: true; hint: "Base URL" }
            SelectField { id: apiInterface; objectName: "providerInterface"; Layout.fillWidth: true; label: "API interface"; model: ["Chat Completions", "Responses API"] }
            RowLayout { Layout.fillWidth: true
                SelectField { id: providerModel; objectName: "providerModel"; Layout.fillWidth: true; label: "Default model"; model: []
                    combo.onPressedChanged: { if (combo.pressed) root.updateProviderModels(currentText) }
                }
                MaterialIconButton { symbol: "refresh"; tip: "Refresh models"; enabled: bridge && !bridge.refreshing
                    onClicked: { root.localError = ""; bridge.clearError(); root.refreshRequested = true; bridge.refreshModels(apiKey.text, baseUrl.text) }
                }
                BusyIndicator { running: bridge && bridge.refreshing; visible: running; Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
            }
            MultilineField { id: modelIds; objectName: "providerModels"; Layout.fillWidth: true; Layout.preferredHeight: 105; label: "Model IDs (one per line)" }
            CheckBox { id: streaming; objectName: "providerStreaming"; text: "Streaming mode" }
        }
    }
}
