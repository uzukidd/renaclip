# RenaClip

![renaclip_logo](README.assets/renaclip_logo.png)

**R**eal-time **E**nhanced **N**eural **A**ssistant for **Clip**board — use Gemini-powered “gems” to process clipboard text with global hotkeys.

![Notepad_6MNS1et3W5](README.assets/Notepad_6MNS1et3W5.gif)

---

## To-does

- [x] More friendly user interface.
- [x] Sync Gems with Gemini.
- [ ] Portable build / single executable (e.g. PyInstaller, flet pack).
- [ ] Display generated content directly in a pop-up window.
- [ ] Stack-based clipboard.
- [ ] Supports multimodal input and response.
- [ ] Supports variant models.

---

## Overview

RenaClip runs a clipboard service in the system tray. You define **gems** (each with a name, description, and system prompt). When you press a hotkey (e.g. **Ctrl+1**, **Ctrl+2**), the current clipboard content is sent to the corresponding gem; the model’s reply is written back to the clipboard and a notification is shown.

- **UI**: Manage gems and settings via a desktop app (Flet).
- **Tray**: Service runs in the background; left-click the tray icon to open the UI, or use the menu to exit.

---

## Requirements

- Python 3.12+
- Dependencies in `requirements.txt` (Gemini API, clipboard, hotkeys, tray, etc.)

```bash
pip install -r requirements.txt
```

You also need valid Gemini cookies (e.g. `__Secure-1PSID`, `__Secure-1PSIDTS`) configured in **Settings** (see below).  
If you use **Log in via browser-cookie3**, ensure you are already logged into [Gemini](https://gemini.google.com) in your browser.

---

## Running

1. **Start the clipboard service (with tray)**  
   From the project directory:
   ```bash
   python renaclip_app.py
   ```
   Optional: `python renaclip_app.py --gem "Gem name" --gem "Another"` to use specific gems.

   ![image-20260219015355436](README.assets/image-20260219121402070.png)

2. **Open the menu**  
   Left-click the RenaClip tray icon, or choose **Open menu** from the tray menu.  
   If the menu is already open, another window is not started.

   ![image-20260219020449869](README.assets/image-20260219020449869.png)

3. **Use hotkeys**  
   Copy text, then press the modifier + number for a gem (e.g. **Ctrl+1** for the first gem). The result replaces the clipboard content.

4. **Exit**  
   Right-click the tray icon → **Exit**.

---

## Menu

### 1. Main interface

Gems list, **Settings**, and **Add Gem**. Each gem can be edited or deleted.

![Main interface](README.assets/main.png)

### 2. Edit / Add Gem

Set the gem **name**, **description**, and **prompt** (system instruction). Used when creating a new gem or editing an existing one.

![Edit Gem](README.assets/edit.png)

### 3. Settings

Configure Gemini cookies (`GEMINI_1PSID`, `GEMINI_1PSIDTS`) or enable `Log in via browser-cookie3`, optional **SOCKS5 proxy**, **hotkey modifier** (e.g. ctrl, ctrl+shift), and **model**.  
After changing settings, restart to take effect.

![Settings](README.assets/settings.png)

---

## Configuration

- **Gems and settings** are stored in `gem_config.json` (created on first save).
- **Modifying gems or settings requires restarting the program** (restart the tray service / UI as needed).

---

## License

See repository for license information.