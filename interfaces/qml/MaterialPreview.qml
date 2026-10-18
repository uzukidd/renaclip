import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "reviewWindow"
    visible: true
    width: 1000; height: 700
    minimumWidth: 920; minimumHeight: 600
    title: "RenaClip — README layout preview"
    color: paletteTokens.background
    font.family: "Segoe UI"; font.pixelSize: 14
    Material.theme: Material.Dark
    Material.accent: paletteTokens.accentLight
    Material.foreground: paletteTokens.text
    Material.background: paletteTokens.background
    property string activeModel: "gpt-5.6-luna"
    property string reasoning: "Low"
    property string hotkey: "Ctrl+Shift"
    property bool screenshotEnabled: true
    property bool screenshotSearch: true
    property int editIndex: -1
    property string providerName: "Default"
    property int dragFrom: -1
    property int dragTarget: -1
    property real dragY: 0
    property string skillOrder: {
        var values = []
        for (var i = 0; i < skills.count; i++) values.push(skills.get(i).name)
        return values.join(" | ")
    }

    ListModel {
        id: skills
        ListElement { name: "Chinese to English Translator"; detail: "Translates Chinese text to English."; prompt: "You are a professional translator. Reply with only the English translation."; online: false }
        ListElement { name: "极简词解"; detail: "拒绝列表与废话，专注用一段精炼、连贯、忠实的文字为你拆解任何名词。"; prompt: "用一段精炼的文字解释用户输入的名词。"; online: true }
    }
    component MaterialIconButton: ToolButton {
        property string symbol
        property string tip: ""
        implicitWidth: 40; implicitHeight: 40
        icon.source: "icons/" + symbol + ".svg"
        icon.width: 24; icon.height: 24
        icon.color: paletteTokens.muted
        display: AbstractButton.IconOnly
        ToolTip.visible: hovered && tip.length > 0
        ToolTip.text: tip
    }
    component OutlineButton: Button {
        id: control
        implicitHeight: 32; implicitWidth: Math.max(72, contentItem.implicitWidth + 32)
        topInset: 0; bottomInset: 0
        contentItem: Label { text: control.text; color: paletteTokens.accentLight; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 8; color: control.down ? paletteTokens.accentSurface : "transparent"; border.width: 1; border.color: control.hovered ? paletteTokens.accentLight : "#786773" }
    }
    component PinkButton: OutlineButton {
        id: control
        contentItem: Label { text: control.text; color: paletteTokens.text; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 8; color: control.down ? "#9d1249" : paletteTokens.accent }
    }
    component Field: Item {
        id: field
        property string label
        property alias text: entry.text
        property alias echoMode: entry.echoMode
        property string hint: ""
        property bool revealable: false
        property bool revealed: false
        implicitHeight: 48; implicitWidth: 300
        Rectangle { anchors.fill: parent; color: "transparent"; radius: 4; border.color: entry.activeFocus ? paletteTokens.accentLight : "#09080b" }
        TextField { id: entry; anchors.fill: parent; leftPadding: 16; rightPadding: field.revealable ? 48 : 16; topPadding: 12; bottomPadding: 12; topInset: 0; bottomInset: 0; placeholderText: field.hint; background: Item {} }
        MaterialIconButton {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            visible: field.revealable
            symbol: field.revealed ? "visibility_off" : "visibility"
            tip: field.revealed ? "Hide API key" : "Show API key"
            onClicked: { field.revealed = !field.revealed; entry.echoMode = field.revealed ? TextInput.Normal : TextInput.Password }
        }
        Rectangle { x: 12; y: -7; width: fieldCaption.implicitWidth + 8; height: 15; color: field.parent ? field.Material.background : paletteTokens.surface; visible: field.label.length > 0
            Label { id: fieldCaption; anchors.centerIn: parent; text: field.label; font.pixelSize: 10; color: paletteTokens.muted }
        }
    }
    component SelectField: Item {
        id: box
        property string label
        property alias model: select.model
        property alias currentIndex: select.currentIndex
        property alias currentText: select.currentText
        property alias combo: select
        implicitHeight: 48; implicitWidth: 180
        Rectangle { anchors.fill: parent; radius: 4; color: "transparent"; border.color: select.activeFocus ? paletteTokens.accentLight : "#09080b" }
        ComboBox { id: select; anchors.fill: parent; leftPadding: 16; topInset: 0; bottomInset: 0; background: Item {} }
        Rectangle { x: 12; y: -7; width: comboCaption.implicitWidth + 8; height: 15; color: box.Material.background
            Label { id: comboCaption; anchors.centerIn: parent; text: box.label; font.pixelSize: 10; color: paletteTokens.muted }
        }
    }
    component MultilineField: Item {
        id: area
        property string label
        property alias text: editor.text
        implicitWidth: 300; implicitHeight: 110
        Rectangle { anchors.fill: parent; color: "transparent"; radius: 4; border.color: editor.activeFocus ? paletteTokens.accentLight : "#09080b" }
        ScrollView { id: textScroll; anchors.fill: parent; anchors.margins: 14; clip: true; contentWidth: availableWidth
            TextEdit { id: editor; width: textScroll.availableWidth; color: paletteTokens.text; font: root.font; wrapMode: TextEdit.Wrap; selectByMouse: true }
        }
        Rectangle { x: 12; y: -7; width: areaCaption.implicitWidth + 8; height: 15; color: area.Material.background
            Label { id: areaCaption; anchors.centerIn: parent; text: area.label; font.pixelSize: 10; color: paletteTokens.muted }
        }
    }
    component Rule: Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: paletteTokens.divider }
    component ReviewDialog: Dialog {
        anchors.centerIn: parent
        modal: true; padding: 24
        Material.background: paletteTokens.surface
        Overlay.modal: Rectangle { color: "#a6000000" }
        background: Rectangle { color: paletteTokens.surface; radius: 8 }
        header: Label { text: parent.title; font.pixelSize: 20; font.weight: Font.DemiBold; padding: 24; bottomPadding: 20 }
        footer: DialogButtonBox {
            objectName: "reviewDialogButtons"
            spacing: 12
            leftPadding: 24; rightPadding: 24
            topPadding: 20; bottomPadding: 24
            implicitHeight: 76
            background: Item {}
            OutlineButton { text: "Cancel"; objectName: "cancelButton"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            PinkButton { text: "Save"; objectName: "saveButton"; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 26
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Label { text: "RenaClip"; font.pixelSize: 24; font.weight: Font.Bold }
            Item { Layout.fillWidth: true }
            SelectField { id: reasoningField; objectName: "reasoningCombo"; label: "Reasoning"; Layout.preferredWidth: 180; model: ["Default", "Low", "Medium", "High"]; currentIndex: 1; combo.onActivated: root.reasoning = currentText }
            SelectField {
                id: modelField; objectName: "modelCombo"; label: "Model"; Layout.preferredWidth: 280
                model: ["OpenAI", "gpt-5.6-luna", "gpt-4.1", "DeepSeek", "deepseek-chat", "deepseek-reasoner"]
                currentIndex: 1
                combo.displayText: root.activeModel
                combo.delegate: ItemDelegate {
                    required property int index
                    required property string modelData
                    width: modelField.width; enabled: index !== 0 && index !== 3
                    text: modelData; leftPadding: enabled ? 24 : 16
                    contentItem: Label { text: modelData; color: enabled ? paletteTokens.text : paletteTokens.accentLight; verticalAlignment: Text.AlignVCenter }
                }
                combo.onActivated: { if (currentIndex !== 0 && currentIndex !== 3) root.activeModel = currentText }
            }
            OutlineButton { text: "Settings"; Layout.preferredWidth: 100; onClicked: settingsDialog.open() }
            PinkButton { text: "Add Skill"; Layout.preferredWidth: 105; onClicked: { root.editIndex = -1; skillName.text = ""; skillDescription.text = ""; skillPrompt.text = ""; skillOnline.checked = false; skillDialog.open() } }
        }
        Rule {}
        ListView {
            id: skillList; Layout.fillWidth: true; Layout.fillHeight: true
            model: skills; spacing: 8; clip: true
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Rectangle {
                id: card
                required property int index
                required property string name
                required property string detail
                required property string prompt
                required property bool online
                width: skillList.width; height: 84; radius: 8
                color: hover.hovered ? paletteTokens.raised : paletteTokens.surface
                opacity: root.dragFrom === card.index ? 0.65 : 1
                border.width: root.dragFrom >= 0 && root.dragTarget === card.index ? 1 : 0
                border.color: paletteTokens.accentLight
                HoverHandler { id: hover }
                RowLayout { anchors.fill: parent; anchors.margins: 16; spacing: 10
                    ColumnLayout { Layout.fillWidth: true; spacing: 12
                        RowLayout { spacing: 8
                            Label { text: card.name; font.family: "Microsoft YaHei UI"; font.pixelSize: 16; font.weight: Font.DemiBold }
                            Rectangle { implicitWidth: shortcutLabel.implicitWidth + 16; implicitHeight: 24; radius: 4; color: paletteTokens.accent
                                Label { id: shortcutLabel; anchors.centerIn: parent; text: root.hotkey + "+" + (card.index + 1); font.pixelSize: 10 }
                            }
                            MaterialIconButton { symbol: "public"; tip: "Web search enabled"; visible: card.online; padding: 0; implicitWidth: 20; implicitHeight: 24; icon.width: 16; icon.height: 16; icon.color: paletteTokens.accentLight }
                        }
                        Label { text: card.detail; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; color: paletteTokens.muted; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    OutlineButton { text: "Edit"; onClicked: { root.editIndex = card.index; skillName.text = card.name; skillDescription.text = card.detail; skillPrompt.text = card.prompt; skillOnline.checked = card.online; skillDialog.open() } }
                    OutlineButton { text: "Delete"; Layout.preferredWidth: 88; onClicked: skills.remove(card.index) }
                    MaterialIconButton {
                        symbol: "drag_handle"; tip: "Drag to reorder"; Layout.leftMargin: 12; icon.color: paletteTokens.text
                        objectName: "dragHandle" + card.index
                        MouseArea {
                            anchors.fill: parent; cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                            preventStealing: true
                            onPressed: function(mouse) {
                                root.dragFrom = card.index; root.dragTarget = card.index
                                root.dragY = mapToItem(skillList, mouse.x, mouse.y).y
                            }
                            onPositionChanged: function(mouse) {
                                if (!pressed) return
                                root.dragY = mapToItem(skillList, mouse.x, mouse.y).y
                                root.dragTarget = Math.max(0, Math.min(skills.count - 1, Math.floor((root.dragY + skillList.contentY) / 92)))
                            }
                            onReleased: {
                                if (root.dragFrom !== root.dragTarget) skills.move(root.dragFrom, root.dragTarget, 1)
                                root.dragFrom = -1; root.dragTarget = -1
                                root.refreshSkillOrder()
                            }
                            onCanceled: { root.dragFrom = -1; root.dragTarget = -1 }
                        }
                    }
                }
            }
        }
    }

    ReviewDialog {
        id: settingsDialog; objectName: "settingsDialog"
        width: 550; height: Math.min(root.height - 40, 650); title: "Settings"
        onOpened: { screenshotToggle.checked = root.screenshotEnabled; searchToggle.checked = root.screenshotSearch; hotkeyField.currentIndex = hotkeyField.model.indexOf(root.hotkey) }
        onAccepted: { root.screenshotEnabled = screenshotToggle.checked; root.screenshotSearch = searchToggle.checked; root.hotkey = hotkeyField.currentText }
        contentItem: ScrollView { clip: true
            ColumnLayout { width: settingsDialog.availableWidth; spacing: 12
                Label { text: "Hotkey"; font.weight: Font.DemiBold }
                SelectField { id: hotkeyField; Layout.fillWidth: true; label: "Hotkey modifier"; model: ["Ctrl", "Ctrl+Shift", "Ctrl+Alt", "Ctrl+Shift+Alt"]; currentIndex: 1 }
                Rule {}
                Label { text: "Screenshot QA"; font.weight: Font.DemiBold }
                CheckBox { id: screenshotToggle; implicitHeight: 34; text: "Screenshot Q&A (Hotkey + 0)" }
                CheckBox { id: searchToggle; implicitHeight: 34; text: "Web search"; enabled: screenshotToggle.checked }
                Rule {}
                Label { text: "Backend: Openai"; font.weight: Font.DemiBold }
                RowLayout {
                    OutlineButton { text: "OpenAI"; background: Rectangle { color: paletteTokens.accentSurface; radius: 8 } }
                    OutlineButton { text: "Gemini Web (unstable)"; background: Rectangle { color: paletteTokens.surface; radius: 8 } }
                }
                Rule {}
                Label { text: "OpenAI"; font.weight: Font.DemiBold }
                RowLayout { Layout.fillWidth: true
                    ColumnLayout { Layout.fillWidth: true; spacing: 5
                        Label { text: root.providerName; font.weight: Font.DemiBold }
                        Label { text: "Chat Completions  |  deepseek-v4-flash  |  https://api.deepseek.com"; font.pixelSize: 11; color: paletteTokens.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                    MaterialIconButton { symbol: "edit"; tip: "Edit provider"; onClicked: openProviderPreview() }
                    MaterialIconButton { symbol: "delete_outline"; tip: "Delete provider" }
                }
                OutlineButton { text: "Add provider"; onClicked: { providerNameInput.text = "New provider"; providerDialog.open() } }
            }
        }
    }
    ReviewDialog {
        id: skillDialog; objectName: "skillDialog"; width: 350; title: root.editIndex < 0 ? "Add Skill" : "Edit Skill"
        onAccepted: {
            if (!skillName.text.trim()) return
            var value = {name: skillName.text.trim(), detail: skillDescription.text, prompt: skillPrompt.text, online: skillOnline.checked}
            if (root.editIndex < 0) skills.append(value); else skills.set(root.editIndex, value)
        }
        ColumnLayout { width: skillDialog.availableWidth; spacing: 14
            Field { id: skillName; Layout.fillWidth: true; label: "Title" }
            MultilineField { id: skillDescription; Layout.fillWidth: true; Layout.preferredHeight: 65; label: "Description" }
            MultilineField { id: skillPrompt; Layout.fillWidth: true; Layout.preferredHeight: 110; label: "Prompt" }
            CheckBox { id: skillOnline; text: "Enable web search" }
        }
    }
    ReviewDialog {
        id: providerDialog; objectName: "providerDialog"; width: 550; title: "Edit Provider: " + providerNameInput.text
        onAccepted: root.providerName = providerNameInput.text.trim() || "Default"
        ColumnLayout { width: providerDialog.availableWidth; spacing: 14
            Field { id: providerNameInput; Layout.fillWidth: true; label: "Provider name"; text: "New provider" }
            Field { Layout.fillWidth: true; hint: "API key"; echoMode: TextInput.Password; revealable: true }
            Field { Layout.fillWidth: true; hint: "Base URL" }
            SelectField { Layout.fillWidth: true; label: "API interface"; model: ["Chat Completions", "Responses API"] }
            RowLayout { Layout.fillWidth: true
                SelectField { Layout.fillWidth: true; label: "Default model"; model: ["gpt-4o", "gpt-4.1"] }
                MaterialIconButton { symbol: "refresh"; tip: "Refresh models" }
            }
            MultilineField { Layout.fillWidth: true; Layout.preferredHeight: 105; label: "Model IDs (one per line)"; text: "gpt-4o" }
            CheckBox { text: "Streaming mode" }
        }
    }
    function refreshSkillOrder() {
        var values = []
        for (var i = 0; i < skills.count; i++) values.push(skills.get(i).name)
        root.skillOrder = values.join(" | ")
    }
    function openSettingsPreview() { settingsDialog.open() }
    function openSkillPreview() { root.editIndex = 0; skillName.text = skills.get(0).name; skillDescription.text = skills.get(0).detail; skillPrompt.text = skills.get(0).prompt; skillOnline.checked = skills.get(0).online; skillDialog.open() }
    function openProviderPreview() { providerNameInput.text = "New provider"; providerDialog.open() }
}
