"""
Android Phone Controller for MARK XLVIII (JARVIS)
Integrates with Android devices via ADB (USB / Wireless) and Microsoft Phone Link.
"""

import os
import re
import sys
import json
import time
import subprocess
from pathlib import Path

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _get_base_dir()
TOOLS_DIR = BASE_DIR / "tools" / "platform-tools"
ADB_BIN = TOOLS_DIR / "adb.exe"

def _ensure_adb() -> bool:
    """Ensure adb executable is available, downloading standalone binary if needed."""
    global ADB_BIN
    if ADB_BIN.exists():
        return True
    import shutil
    sys_adb = shutil.which("adb")
    if sys_adb:
        ADB_BIN = Path(sys_adb)
        return True

    # Try downloading official platform-tools for Windows if missing
    if sys.platform == "win32":
        try:
            import urllib.request
            import zipfile
            print("[PhoneController] ⏳ Downloading Android platform-tools...")
            TOOLS_DIR.mkdir(parents=True, exist_ok=True)
            zip_dest = BASE_DIR / "tools" / "platform-tools.zip"
            url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
            urllib.request.urlretrieve(url, zip_dest)
            with zipfile.ZipFile(zip_dest, "r") as z:
                z.extractall(BASE_DIR / "tools")
            if zip_dest.exists():
                zip_dest.unlink()
            if ADB_BIN.exists():
                print("[PhoneController] ✅ Android platform-tools installed successfully.")
                return True
        except Exception as e:
            print(f"[PhoneController] Warning: Could not auto-download platform-tools: {e}")

    ADB_BIN = Path("adb")
    return False

# Ensure on startup or first call
_ensure_adb()

# Popular Android App Packages
APP_PACKAGES = {
    "whatsapp":   "com.whatsapp",
    "youtube":    "com.google.android.youtube",
    "instagram":  "com.instagram.android",
    "spotify":    "com.spotify.music",
    "chrome":     "com.android.chrome",
    "telegram":   "org.telegram.messenger",
    "settings":   "com.android.settings",
    "maps":       "com.google.android.apps.maps",
    "camera":     "camera",
    "dialer":     "com.google.android.dialer",
    "phone":      "com.google.android.dialer",
    "calculator": "com.google.android.calculator",
    "gallery":    "com.google.android.apps.photos",
    "photos":     "com.google.android.apps.photos",
    "gmail":      "com.google.android.gm",
    "clock":      "com.google.android.deskclock",
}

def _run_adb(*args, timeout: int = 10) -> tuple[int, str]:
    """Execute an ADB command with creationflags hidden."""
    cmd = [str(ADB_BIN)] + list(args)
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        res = subprocess.run(cmd, **kwargs)
        return res.returncode, res.stdout.strip()
    except Exception as e:
        return 1, str(e)

def get_connected_devices() -> list[str]:
    """Returns list of connected Android device IDs."""
    code, out = _run_adb("devices")
    devices = []
    if code == 0:
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
    return devices

def _open_phone_link() -> str:
    """Launch Microsoft Phone Link (Windows default)."""
    try:
        os.system("start ms-phone-link:")
        return "Opened Microsoft Phone Link on your desktop, sir."
    except Exception as e:
        return f"Could not launch Phone Link: {e}"

def _is_screen_on() -> bool:
    """Check if device screen is currently on."""
    code, out = _run_adb("shell", "dumpsys", "power")
    if "mHoldingDisplaySuspendBlocker=true" in out or "Display Power: state=ON" in out:
        return True
    return False

def unlock_phone(pin: str = "") -> str:
    """Wake screen, swipe up, and optionally enter unlock PIN."""
    devices = get_connected_devices()
    if not devices:
        # Fall back to launching Microsoft Phone Link on PC
        return _open_phone_link()

    # Wake screen if off
    if not _is_screen_on():
        _run_adb("shell", "input", "keyevent", "26")
        time.sleep(0.3)

    # Swipe up to reveal lock screen PIN / keypad
    _run_adb("shell", "input", "swipe", "500", "1600", "500", "400", "250")
    time.sleep(0.4)

    # If pin is provided or stored in config
    if not pin:
        try:
            cfg_path = BASE_DIR / "config" / "api_keys.json"
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                pin = cfg.get("phone_pin", "")
        except Exception:
            pin = ""

    if pin:
        _run_adb("shell", "input", "text", str(pin))
        time.sleep(0.2)
        _run_adb("shell", "input", "keyevent", "66")  # Enter key
        return "Phone unlocked successfully, sir."

    return "Phone screen turned on and swiped up, sir."

def lock_phone() -> str:
    """Turn off / lock the phone screen."""
    devices = get_connected_devices()
    if not devices:
        return "No Android device connected via ADB."
    if _is_screen_on():
        _run_adb("shell", "input", "keyevent", "26")
    return "Phone screen locked, sir."

def open_phone_app(app_name: str) -> str:
    """Open an app on the Android phone."""
    devices = get_connected_devices()
    if not devices:
        return _open_phone_link()

    # Make sure phone is awake
    if not _is_screen_on():
        unlock_phone()

    app_key = app_name.lower().strip()
    if app_key == "camera":
        _run_adb("shell", "am", "start", "-a", "android.media.action.STILL_IMAGE_CAMERA")
        return "Opened camera on your phone, sir."

    package = APP_PACKAGES.get(app_key)
    if package:
        code, out = _run_adb("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
        if code == 0:
            return f"Opened {app_name} on your phone, sir."
    
    # Generic attempt by app name
    code, out = _run_adb("shell", "am", "start", "-n", f"{app_name}/.MainActivity")
    return f"Sent command to open {app_name} on your phone, sir."

def get_phone_battery() -> str:
    """Query current battery percentage of the phone."""
    devices = get_connected_devices()
    if not devices:
        return "No phone connected to check battery."
    code, out = _run_adb("shell", "dumpsys", "battery")
    m = re.search(r"level:\s*(\d+)", out)
    if m:
        pct = m.group(1)
        return f"Phone battery is currently at {pct}%, sir."
    return "Could not retrieve phone battery telemetry."

def phone_home() -> str:
    """Press home button on phone."""
    _run_adb("shell", "input", "keyevent", "3")
    return "Navigated to home screen on phone, sir."

def phone_controller(parameters: dict = None, player=None, **kwargs) -> str:
    """Main tool handler for JARVIS."""
    params = parameters or {}
    action = params.get("action", "unlock").lower().strip()
    app_name = params.get("app_name", "").strip()
    pin = params.get("pin", "").strip()

    print(f"[PhoneController] 📱 Action: {action}, App: {app_name}")
    if player:
        player.write_log(f"[phone] {action} {app_name}".strip())

    if action in ("unlock", "wake", "open", "open_phone"):
        if app_name:
            unlock_phone(pin)
            time.sleep(0.5)
            res = open_phone_app(app_name)
        else:
            res = unlock_phone(pin)
    elif action in ("lock", "sleep", "screen_off"):
        res = lock_phone()
    elif action in ("open_app", "launch"):
        res = open_phone_app(app_name or "WhatsApp")
    elif action in ("battery", "battery_status", "status"):
        res = get_phone_battery()
    elif action in ("home", "home_screen"):
        res = phone_home()
    else:
        res = unlock_phone(pin)

    if player:
        player.write_log(f"[phone] {res}")
    return res

if __name__ == "__main__":
    devs = get_connected_devices()
    print("Connected devices:", devs)
    if devs:
        print(get_phone_battery())
    else:
        print("No devices. Testing Phone Link trigger...")
        print(unlock_phone())
