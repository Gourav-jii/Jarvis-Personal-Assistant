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
    "phonepe":    "com.phonepe.app",
    "paytm":      "net.one97.paytm",
    "gpay":       "com.google.android.apps.nbu.paisa.user",
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

def get_device_status() -> dict:
    """Returns detailed status of connected Android devices."""
    code, out = _run_adb("devices")
    status = {"devices": [], "unauthorized": [], "offline": []}
    if code == 0:
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                dev_id, st = parts[0], parts[1].lower()
                if st == "device":
                    status["devices"].append(dev_id)
                elif st == "unauthorized":
                    status["unauthorized"].append(dev_id)
                elif st == "offline":
                    status["offline"].append(dev_id)
    return status

def get_connected_devices() -> list[str]:
    """Returns list of connected and authorized Android device IDs."""
    return get_device_status()["devices"]

def _handle_no_device() -> str:
    """Friendly message when no phone is connected via ADB."""
    return "Sir, koi phone connect nahi mila. Phone ko USB cable se jodein aur USB Debugging ON karein."

def _is_screen_on() -> bool:
    """Check if device screen is currently on."""
    code, out = _run_adb("shell", "dumpsys", "power")
    if any(k in out for k in ("mHoldingDisplaySuspendBlocker=true", "Display Power: state=ON", "mWakefulness=Awake")):
        return True
    return False

def get_screen_size() -> tuple[int, int]:
    """Query physical screen resolution."""
    code, out = _run_adb("shell", "wm", "size")
    m = re.search(r"(\d+)x(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1080, 2400

def unlock_phone(pin: str = "") -> str:
    """Wake screen, dismiss lock screen, and enter PIN if provided."""
    st = get_device_status()

    # 1. Device is connected but waiting for user to tap 'Allow' on phone
    if st["unauthorized"]:
        _run_adb("reconnect")
        return (
            "Sir, aapka Android phone connect hai, lekin screen par 'Allow USB Debugging' permission pending hai. "
            "Kripya phone ki screen unlock karein, 'Always allow from this computer' tick karein, aur 'Allow' par tap karein."
        )

    # 2. No ADB device attached
    if not st["devices"]:
        return _handle_no_device()

    # 3. Wake screen (224 = KEYCODE_WAKEUP, safe even if already on)
    _run_adb("shell", "input", "keyevent", "224")
    time.sleep(0.2)

    # 4. Try Android standard lock dismiss
    _run_adb("shell", "wm", "dismiss-keyguard")
    _run_adb("shell", "input", "keyevent", "82")  # KEYCODE_MENU (unlocks standard screens)
    time.sleep(0.3)

    # 5. Dynamic swipe up based on exact device screen dimensions
    w, h = get_screen_size()
    start_x = w // 2
    start_y = int(h * 0.85)
    end_y   = int(h * 0.20)
    _run_adb("shell", "input", "swipe", str(start_x), str(start_y), str(start_x), str(end_y), "250")
    time.sleep(0.4)

    # 6. Check for PIN
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

    return "Phone screen wake karke unlock kar diya hai, sir."

def lock_phone() -> str:
    """Turn off / lock the phone screen."""
    st = get_device_status()
    if st["unauthorized"]:
        return "Phone connected hai lekin unauthorized hai. Phone par 'Allow' dabayein."
    if not st["devices"]:
        return "No Android device connected via ADB."
    _run_adb("shell", "input", "keyevent", "26")
    return "Phone screen locked, sir."

def open_phone_app(app_name: str) -> str:
    """Open an app on the Android phone."""
    st = get_device_status()
    if st["unauthorized"]:
        _run_adb("reconnect")
        return (
            "Sir, phone par 'Allow USB Debugging' popup par 'Allow' karein pehle."
        )
    if not st["devices"]:
        return _handle_no_device()

    # Make sure phone is awake
    _run_adb("shell", "input", "keyevent", "224")
    _run_adb("shell", "wm", "dismiss-keyguard")

    app_key = app_name.lower().strip()
    if app_key == "camera":
        _run_adb("shell", "am", "start", "-a", "android.media.action.STILL_IMAGE_CAMERA")
        return "Opened camera on your phone, sir."
    elif app_key in ("dialer", "phone"):
        _run_adb("shell", "am", "start", "-a", "android.intent.action.DIAL")
        return "Opened dialer on your phone, sir."

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
    st = get_device_status()
    if st["unauthorized"]:
        return "Phone unauthorized hai. Phone screen par 'Allow' dabayein."
    if not st["devices"]:
        return "No phone connected to check battery."
    code, out = _run_adb("shell", "dumpsys", "battery")
    m = re.search(r"level:\s*(\d+)", out)
    if m:
        pct = m.group(1)
        return f"Phone battery is currently at {pct}%, sir."
    return "Could not retrieve phone battery telemetry."

def phone_home() -> str:
    """Press home button on phone."""
    st = get_device_status()
    if not st["devices"]:
        return "No device connected."
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
    st = get_device_status()
    print("Device status:", st)
    if st["devices"]:
        print("Battery:", get_phone_battery())
    elif st["unauthorized"]:
        print("Device is UNAUTHORIZED! User must allow debugging on screen.")
        print(unlock_phone())
    else:
        print("No devices found.")
        print(unlock_phone())
