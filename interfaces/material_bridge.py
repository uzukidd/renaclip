"""Persistent configuration bridge for the production Material QML UI."""
from copy import deepcopy
from PySide6.QtCore import QObject, Property, Signal, Slot
from config_loader import (load_config, save_config, normalize_openai_providers,
    normalize_reasoning_effort, VALID_MODIFIERS, VALID_BACKENDS, AVAILABLE_MODELS)
from openai_client import fetch_available_models


class MaterialBridge(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    errorChanged = Signal()
    refreshChanged = Signal()
    configuration_changed = Signal()
    closeRequested = Signal()

    def __init__(self, executor, parent=None):
        super().__init__(parent)
        self.executor = executor
        self._error = ''
        self._refreshing = False
        self._models = []
        self._refresh_token = 0
        self._draft = None
        self.refresh()

    def refresh(self):
        self._skills, self._settings = load_config()
        self._routes = []
        self._active = -1
        for provider in normalize_openai_providers(self._settings):
            self._routes.append(dict(title=provider['name'], provider=provider['name'], model='', heading=True))
            models = list(provider['models'])
            if provider['model'] not in models:
                models.insert(0, provider['model'])
            for model in models:
                if provider['name'] == self._settings['OPENAI_ACTIVE_PROVIDER'] and model == provider['model']:
                    self._active = len(self._routes)
                self._routes.append(dict(title=model, provider=provider['name'], model=model, heading=False))
        self.stateChanged.emit()

    skills = Property('QVariantList', lambda s: deepcopy(s._skills), notify=stateChanged)
    settings = Property('QVariantMap', lambda s: deepcopy(s._settings), notify=stateChanged)
    modelRoutes = Property('QVariantList', lambda s: deepcopy(s._routes), notify=stateChanged)
    activeRoute = Property(int, lambda s: s._active, notify=stateChanged)
    reasoning = Property(str, lambda s: normalize_reasoning_effort(s._settings.get('OPENAI_REASONING_EFFORT')), notify=stateChanged)
    hotkey = Property(str, lambda s: '+'.join(x.capitalize() for x in s._settings.get('HOTKEY_MODIFIER','ctrl').split('+')), notify=stateChanged)
    settingsDraft = Property('QVariantMap', lambda s: deepcopy(s._draft or {}), notify=draftChanged)
    providersDraft = Property('QVariantList', lambda s: deepcopy((s._draft or {}).get('OPENAI_PROVIDERS',[])), notify=draftChanged)
    errorMessage = Property(str, lambda s: s._error, notify=errorChanged)
    refreshing = Property(bool, lambda s: s._refreshing, notify=refreshChanged)
    refreshedModels = Property('QStringList', lambda s: list(s._models), notify=refreshChanged)

    def _fail(self, text):
        self._error = text
        self.errorChanged.emit()
        return False

    @Slot()
    def clearError(self):
        self._error = ''
        self.errorChanged.emit()

    def _persist(self, skills, settings):
        try:
            save_config(skills, settings)
        except OSError:
            return self._fail('Could not save local configuration. Check folder permissions.')
        self.clearError()
        self.refresh()
        self.configuration_changed.emit()
        return True

    @Slot(int, result=bool)
    def setRoute(self, index):
        if not 0 <= index < len(self._routes) or self._routes[index]['heading']:
            return False
        route = self._routes[index]
        settings = deepcopy(self._settings)
        settings['OPENAI_ACTIVE_PROVIDER'] = route['provider']
        for provider in settings['OPENAI_PROVIDERS']:
            if provider['name'] == route['provider']:
                provider['model'] = route['model']
        return self._persist(self._skills, settings)

    @Slot(str, result=bool)
    def setReasoning(self, value):
        settings = deepcopy(self._settings)
        settings['OPENAI_REASONING_EFFORT'] = normalize_reasoning_effort(value)
        return self._persist(self._skills, settings)

    @Slot(int, 'QVariantMap', result=bool)
    def saveSkill(self, index, value):
        if not str(value.get('name','')).strip():
            return self._fail('Please enter a skill title.')
        skills = deepcopy(self._skills)
        if index != -1 and not 0 <= index < len(skills):
            return self._fail('This skill no longer exists. Reopen the editor.')
        entry = dict(skills[index]) if index >= 0 else {}
        entry.update(name=str(value['name']).strip(), description=str(value.get('description','')),
                     prompt=str(value.get('prompt','')), web_search=bool(value.get('web_search',False)))
        if index < 0:
            skills.append(entry)
        else:
            skills[index] = entry
        return self._persist(skills, self._settings)

    @Slot(int, result=bool)
    def deleteSkill(self, index):
        skills = deepcopy(self._skills)
        if not 0 <= index < len(skills):
            return False
        skills.pop(index)
        return self._persist(skills, self._settings)

    @Slot(int, int, result=bool)
    def moveSkill(self, source, target):
        skills = deepcopy(self._skills)
        if not (0 <= source < len(skills) and 0 <= target < len(skills)):
            return False
        skills.insert(target, skills.pop(source))
        return self._persist(skills, self._settings)

    @Slot(result='QVariantMap')
    def beginSettings(self):
        self._draft = deepcopy(self._settings)
        self.clearError()
        self.draftChanged.emit()
        return deepcopy(self._draft)

    @Slot('QVariantMap')
    def updateSettingsDraft(self, value):
        if self._draft is None:
            return
        allowed = ('START_WITH_WINDOWS','BACKEND','HOTKEY_MODIFIER','SCREENSHOT_QA_ENABLED','SCREENSHOT_QA_WEB_SEARCH',
                   'GEMINI_PSID','GEMINI_PSIDTS','GEMINI_PROXY','GEMINI_MODEL',
                   'GEMINI_COOKIE_BROWSER','GEMINI_USE_BROWSER_COOKIE')
        for key in allowed:
            if key in value:
                self._draft[key] = value[key]
        self.draftChanged.emit()

    @Slot(result=bool)
    def saveSettings(self):
        if self._draft is None:
            return False
        if self._draft.get('HOTKEY_MODIFIER') not in VALID_MODIFIERS:
            return self._fail('Unsupported hotkey modifier.')
        if self._draft.get('BACKEND') not in VALID_BACKENDS:
            return self._fail('Unsupported backend.')
        if self._draft.get('GEMINI_MODEL') not in AVAILABLE_MODELS:
            self._draft['GEMINI_MODEL'] = 'unspecified'
        from interfaces.autostart import read_startup_command, write_startup_command, startup_command
        desired = bool(self._draft.get('START_WITH_WINDOWS', False))
        apply_startup = desired or desired != bool(self._settings.get('START_WITH_WINDOWS', False))
        previous_command = None
        if apply_startup:
            try:
                previous_command = read_startup_command()
                write_startup_command(startup_command() if desired else None)
            except OSError:
                return self._fail('Could not update Windows startup. Check your user permissions.')
        result = self._persist(self._skills, self._draft)
        if not result and apply_startup:
            try:
                write_startup_command(previous_command)
            except OSError:
                self._fail('Configuration could not be saved and startup could not be restored. Check Windows Startup apps.')
        if result:
            self.cancelSettings()
        return result

    @Slot()
    def cancelSettings(self):
        self._draft = None
        self._refresh_token += 1
        self._refreshing = False
        self._models = []
        self.draftChanged.emit()
        self.refreshChanged.emit()
        self.clearError()

    @Slot(int, 'QVariantMap', result=bool)
    def saveProvider(self, index, value):
        if self._draft is None:
            return False
        name = str(value.get('name','')).strip()
        if not name:
            return self._fail('Please enter a provider name.')
        providers = deepcopy(self._draft['OPENAI_PROVIDERS'])
        if index != -1 and not 0 <= index < len(providers):
            return self._fail('This provider no longer exists.')
        names = {p['name'] for i,p in enumerate(providers) if i != index}
        base, number = name, 2
        while name in names:
            name = f'{base} ({number})'
            number += 1
        entry = dict(value)
        entry['name'] = name
        entry['models'] = list(dict.fromkeys(str(x).strip() for x in entry.get('models',[]) if str(x).strip()))
        entry['model'] = str(entry.get('model') or (entry['models'][0] if entry['models'] else 'gpt-4o'))
        if entry['model'] not in entry['models']:
            entry['models'].insert(0, entry['model'])
        if entry.get('api_interface') not in ('responses','chat_completions'):
            entry['api_interface'] = 'chat_completions'
        old_name = providers[index]['name'] if index >= 0 else None
        if index < 0:
            providers.append(entry)
        else:
            providers[index] = entry
        self._draft['OPENAI_PROVIDERS'] = providers
        if self._draft['OPENAI_ACTIVE_PROVIDER'] == old_name:
            self._draft['OPENAI_ACTIVE_PROVIDER'] = name
        self.clearError()
        self.draftChanged.emit()
        return True

    @Slot(int, result=bool)
    def deleteProvider(self, index):
        if self._draft is None:
            return False
        providers = self._draft['OPENAI_PROVIDERS']
        if len(providers) <= 1:
            return self._fail('Keep at least one provider.')
        if not 0 <= index < len(providers):
            return False
        name = providers.pop(index)['name']
        if self._draft['OPENAI_ACTIVE_PROVIDER'] == name:
            self._draft['OPENAI_ACTIVE_PROVIDER'] = providers[0]['name']
        self.draftChanged.emit()
        return True

    @Slot(str, str)
    def refreshModels(self, key, url):
        self._refresh_token += 1
        token = self._refresh_token
        self._models = []
        self._refreshing = True
        self.clearError()
        self.refreshChanged.emit()
        def completed(models):
            if token != self._refresh_token:
                return
            self._refreshing = False
            self._models = models or []
            if not models:
                self._fail('No models returned. Check the API key and Base URL.')
            self.refreshChanged.emit()
        def failed(exc):
            if token == self._refresh_token:
                self._refreshing = False
                self._fail('Could not refresh models. Check the endpoint and connection.')
                self.refreshChanged.emit()
        try:
            self.executor.submit(fetch_available_models(key, url or None), on_result=completed, on_error=failed)
        except Exception as exc:
            failed(exc)

    @Slot()
    def cancelModelRefresh(self):
        self._refresh_token += 1
        self._refreshing = False
        self._models = []
        self.refreshChanged.emit()

    @Slot()
    def requestClose(self):
        self.closeRequested.emit()
