import os
import sys
import time
import json
import logging
import webbrowser
import configparser
import subprocess
import traceback
import winreg

logging.getLogger("androguard").setLevel(logging.CRITICAL)
logging.getLogger("asn1crypto").setLevel(logging.CRITICAL)
logging.getLogger("axml").setLevel(logging.CRITICAL)
logging.disable(logging.DEBUG)

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      "--disable-gpu --disable-software-rasterizer --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt6.QtCore import (Qt, QUrl, QSize, QTimer, QThread, pyqtSignal,
                          QRectF, QPropertyAnimation, QEasingCurve, pyqtProperty)
from PyQt6.QtGui import (QFont, QTextCursor, QPixmap, QIcon, QAction,
                         QPainter, QColor, QBrush, QPen)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QLabel, QPushButton,
    QFileDialog, QMessageBox, QFrame, QProgressBar, QTextEdit, QMenu,
    QScrollArea, QComboBox, QAbstractButton
)
from PyQt6.QtWebEngineWidgets import QWebEngineView


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
ADB_DIR = os.path.join(BASE_DIR, "adb")
ADB_EXE = os.path.join(ADB_DIR, "adb.exe")
WSA_BAT = os.path.join(BASE_DIR, "WSA", "Run.bat")
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
APPS_CACHE_FILE = os.path.join(CACHE_DIR, "apps_cache.json")
APK_TMP_DIR = os.path.join(CACHE_DIR, "apk_tmp")

APP_ICON_PATH = os.path.join(BASE_DIR, "icon-1.ico")
APK_ICON_PATH = os.path.join(BASE_DIR, "icon-1.ico")
VERSION_JSON_URL = "https://raw.githubusercontent.com/Gle-Res/Android-Box/main/version.json"
CURRENT_VERSION = "1.1.0"
GITHUB_URL = "https://github.com/Gle-Res"

DEFAULT_ADB_TARGET = "127.0.0.1:58526"
DEBUG = os.environ.get("ANDROIDBOX_DEBUG", "0") == "1"


def log_debug(*args):
    if DEBUG:
        print("[AndroidBox]", *args)
        sys.stdout.flush()


def load_settings():
    default = {"associate_apk": False, "theme": "light"}
    if not os.path.exists(SETTINGS_FILE):
        return default
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        default.update(data)
        if default.get("theme") == "system":
            default["theme"] = "light"
    except Exception:
        pass
    return default


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_debug("保存设置失败:", e)

class IosSwitch(QAbstractButton):
    def __init__(self, parent=None, track_w=52, track_h=30):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._track_w = track_w
        self._track_h = track_h
        self.setFixedSize(track_w, track_h)

        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob_pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._color_on = QColor("#34c759")
        self._color_off = QColor("#e5e5ea")
        self._knob_color = QColor("#ffffff")

        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_knob_pos(self):
        return self._pos

    def set_knob_pos(self, v):
        self._pos = v
        self.update()

    knob_pos = pyqtProperty(float, fget=get_knob_pos, fset=set_knob_pos)

    def setChecked(self, checked):
        super().setChecked(checked)
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self._track_w
        h = self._track_h
        radius = h / 2

        t = self._pos
        r = int(self._color_off.red()   + (self._color_on.red()   - self._color_off.red())   * t)
        g = int(self._color_off.green() + (self._color_on.green() - self._color_off.green()) * t)
        b = int(self._color_off.blue()  + (self._color_on.blue()  - self._color_off.blue())  * t)
        track_color = QColor(r, g, b)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        p.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)

        knob_d = h - 4
        knob_x = 2 + (w - knob_d - 4) * t
        knob_y = 2

        shadow = QColor(0, 0, 0, 40)
        p.setBrush(QBrush(shadow))
        p.drawEllipse(QRectF(knob_x, knob_y + 1.5, knob_d, knob_d))

        p.setBrush(QBrush(self._knob_color))
        p.drawEllipse(QRectF(knob_x, knob_y, knob_d, knob_d))

APK_PROGID = "AndroidBox.apk"


def _get_run_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    else:
        py = sys.executable
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if not os.path.exists(pyw):
            pyw = py
        script = os.path.abspath(sys.argv[0])
        return f'"{pyw}" "{script}" "%1"'


def associate_apk():
    try:
        cmd = _get_run_command()
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.apk")
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, APK_PROGID)
        winreg.CloseKey(key)

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{APK_PROGID}")
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Android APK")
        winreg.CloseKey(key)

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{APK_PROGID}\DefaultIcon")
        if os.path.exists(APK_ICON_PATH):
            icon_path = APK_ICON_PATH
        elif os.path.exists(APP_ICON_PATH):
            icon_path = APP_ICON_PATH
        else:
            icon_path = sys.executable if getattr(sys, "frozen", False) else sys.executable
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon_path)
        winreg.CloseKey(key)

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               rf"Software\Classes\{APK_PROGID}\shell\open\command")
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)

        try:
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        except Exception:
            pass
        return True
    except Exception as e:
        log_debug("关联 APK 失败:", e)
        return False


def unassociate_apk():
    try:
        def delete_key(root, path):
            try:
                winreg.DeleteKey(root, path)
            except Exception:
                pass

        delete_key(winreg.HKEY_CURRENT_USER, r"Software\Classes\.apk")
        delete_key(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{APK_PROGID}\DefaultIcon")
        delete_key(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{APK_PROGID}\shell\open\command")
        delete_key(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{APK_PROGID}\shell\open")
        delete_key(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{APK_PROGID}\shell")
        delete_key(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{APK_PROGID}")

        try:
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        except Exception:
            pass
        return True
    except Exception as e:
        log_debug("删除 APK 关联失败:", e)
        return False

class VersionCheckWorker(QThread):
    result_signal = pyqtSignal(str, str)

    def run(self):
        try:
            import urllib.request
            req = urllib.request.Request(VERSION_JSON_URL, headers={"User-Agent": "AndroidBox"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.result_signal.emit(data.get("version", "未知"),
                                        data.get("release_url", GITHUB_URL))
        except Exception as e:
            log_debug("检查版本失败:", e)
            self.result_signal.emit("检查失败", GITHUB_URL)

VISIBLE_SYSTEM_APPS = {"com.android.vending", "com.google.android.gms"}
NAME_OVERRIDES = {
    "com.android.vending": "Play 商店",
    "com.google.android.gms": "Google",
    "com.google.android.gsf": "Google 服务框架",
    "com.google.android.googlequicksearchbox": "Google",
}


def default_label(pkg):
    if pkg in NAME_OVERRIDES:
        return NAME_OVERRIDES[pkg]
    return pkg.split(".")[-1].capitalize()


def load_adb_target():
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_ADB_TARGET
    try:
        cfg = configparser.ConfigParser()
        cfg.read(CONFIG_FILE, encoding="utf-8")
        return cfg.get("adb", "target", fallback=DEFAULT_ADB_TARGET)
    except Exception:
        return DEFAULT_ADB_TARGET


ADB_TARGET = load_adb_target()


def human_size(num):
    try:
        num = float(num)
    except Exception:
        return "—"
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024:
            return f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} TB"


def load_icon_pixmap(path, size=96):
    if not os.path.exists(path):
        return None
    pix = QPixmap(path)
    if pix.isNull():
        return None
    return pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def make_icon_label(icon_path, fallback_text="📦", size=120, font_size=72):
    label = QLabel()
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pix = load_icon_pixmap(icon_path, size)
    if pix:
        label.setPixmap(pix)
    else:
        label.setText(fallback_text)
        label.setStyleSheet(f"font-size:{font_size}px;")
    return label


def adb_run(args, timeout=30):
    try:
        r = subprocess.run(
            [ADB_EXE] + args, cwd=ADB_DIR,
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "ADB 执行超时"
    except FileNotFoundError:
        return False, f"未找到 adb.exe：{ADB_EXE}"
    except Exception as e:
        return False, str(e)


def adb_connect_and_check(log=None, retries=2):
    adb_run(["start-server"], timeout=15)
    dev_out = ""
    for i in range(retries):
        if log:
            log(f"$ adb connect {ADB_TARGET}")
        _, out = adb_run(["connect", ADB_TARGET], timeout=15)
        if log and out.strip():
            log(out.strip())
        _, dev_out = adb_run(["devices"], timeout=15)
        connected = False
        for line in dev_out.splitlines():
            line = line.strip()
            if line.startswith(ADB_TARGET) and "\t" in line:
                state = line.split("\t")[-1].strip()
                if state == "device":
                    connected = True
                break
        if connected:
            return True, dev_out
        if i < retries - 1:
            if log:
                log("重试连接…")
            time.sleep(1)
    return False, dev_out


def kill_adb_processes():
    if not os.path.exists(ADB_EXE):
        return
    try:
        subprocess.run([ADB_EXE, "kill-server"], cwd=ADB_DIR,
                       capture_output=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass
    try:
        subprocess.run(["taskkill", "/F", "/IM", "adb.exe"],
                       capture_output=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass


def load_apps_cache():
    if not os.path.exists(APPS_CACHE_FILE):
        return {}
    try:
        with open(APPS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_apps_cache(cache):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(APPS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_debug("保存缓存失败:", e)


_APKUTILS_OK = None
_ANDROGUARD_OK = None


def _check_apkutils():
    global _APKUTILS_OK
    if _APKUTILS_OK is not None:
        return _APKUTILS_OK
    try:
        from apkutils import APK
        _APKUTILS_OK = True
    except Exception:
        _APKUTILS_OK = False
    return _APKUTILS_OK


def _check_androguard():
    global _ANDROGUARD_OK
    if _ANDROGUARD_OK is not None:
        return _ANDROGUARD_OK
    try:
        from androguard.core.apk import APK
        _ANDROGUARD_OK = True
    except Exception:
        _ANDROGUARD_OK = False
    return _ANDROGUARD_OK


def _read_label_via_apkutils(apk_path):
    try:
        from apkutils import APK
        name = APK(file_path=apk_path).get_app_name()
        if name and name.strip():
            return name.strip()
    except Exception:
        pass
    return None


def _read_label_via_androguard(apk_path):
    try:
        from androguard.core.apk import APK
        label = APK(apk_path).get_app_name()
        if label and label.strip():
            return label.strip()
    except Exception:
        pass
    return None


def read_label_from_apk(apk_path):
    if _check_apkutils():
        name = _read_label_via_apkutils(apk_path)
        if name:
            return name
    if _check_androguard():
        name = _read_label_via_androguard(apk_path)
        if name:
            return name
    return None


def pull_apk(pkg, dest_dir=APK_TMP_DIR):
    try:
        ok, out = adb_run(["-s", ADB_TARGET, "shell", "pm", "path", pkg], timeout=10)
        if not ok:
            return None
        apk_path = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                apk_path = line[len("package:"):].strip()
                break
        if not apk_path:
            return None
        os.makedirs(dest_dir, exist_ok=True)
        local_apk = os.path.join(dest_dir, f"{pkg}.apk")
        if os.path.exists(local_apk):
            try:
                os.remove(local_apk)
            except Exception:
                pass
        ok, _ = adb_run(["-s", ADB_TARGET, "pull", apk_path, local_apk], timeout=180)
        if ok and os.path.exists(local_apk):
            return local_apk
    except Exception as e:
        log_debug("pull_apk 失败:", pkg, e)
    return None


def get_disk_size_from_pm_dump(pkg):
    try:
        ok, out = adb_run(["-s", ADB_TARGET, "shell", "pm", "dump", pkg], timeout=15)
        if not ok:
            return None
        code = data = cache = 0
        found = False
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("codeSize="):
                try:
                    code = int(line.split("=", 1)[1].strip()); found = True
                except Exception:
                    pass
            elif line.startswith("dataSize="):
                try:
                    data = int(line.split("=", 1)[1].strip()); found = True
                except Exception:
                    pass
            elif line.startswith("cacheSize="):
                try:
                    cache = int(line.split("=", 1)[1].strip()); found = True
                except Exception:
                    pass
        if found:
            return code + data + cache
    except Exception:
        pass
    return None


def get_apk_size_from_du(pkg):
    try:
        ok, out = adb_run(["-s", ADB_TARGET, "shell", "pm", "path", pkg], timeout=10)
        if not ok:
            return None
        apk_path = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                apk_path = line[len("package:"):].strip()
                break
        if not apk_path:
            return None
        ok, out = adb_run(["-s", ADB_TARGET, "shell", "du", "-sk", apk_path], timeout=10)
        if ok and out.strip():
            kb = int(out.strip().split()[0])
            return kb * 1024
    except Exception:
        pass
    return None


def get_app_sizes_from_device(packages, progress_cb=None):
    result = {}
    total = len(packages)
    for i, pkg in enumerate(packages):
        if progress_cb:
            progress_cb(f"正在读取大小 {i + 1}/{total}：{pkg}")
        size = get_disk_size_from_pm_dump(pkg)
        if size is None:
            size = get_apk_size_from_du(pkg)
        if size is not None:
            result[pkg] = size
    return result


def get_launch_activity(pkg):
    try:
        ok, out = adb_run(
            ["-s", ADB_TARGET, "shell", "cmd", "package", "resolve-activity",
             "--brief", "-c", "android.intent.category.LAUNCHER", pkg],
            timeout=10)
        if not ok:
            return None
        for line in out.splitlines():
            line = line.strip()
            if "/" in line and "priority=" not in line and "No activity" not in line:
                return line
        return None
    except Exception:
        return None


def fetch_app_info_full(pkg):
    info = {"pkg": pkg, "label": default_label(pkg), "version": "未知",
            "size": None, "launchable": False}
    try:
        _, d = adb_run(["-s", ADB_TARGET, "shell", "dumpsys", "package", pkg], timeout=15)
        for l in d.splitlines():
            l = l.strip()
            if l.startswith("versionName=") and info["version"] == "未知":
                info["version"] = l.split("=", 1)[1].strip()
                break
    except Exception:
        pass
    if get_launch_activity(pkg):
        info["launchable"] = True
    local_apk = pull_apk(pkg)
    if local_apk:
        label = read_label_from_apk(local_apk)
        if label:
            info["label"] = label
        try:
            os.remove(local_apk)
        except Exception:
            pass
    return info


class ListAppsWorker(QThread):
    finished_signal = pyqtSignal(list)
    progress_signal = pyqtSignal(str)

    def __init__(self, force_refresh=False):
        super().__init__()
        self.force_refresh = force_refresh

    def _get_current_version(self, pkg):
        try:
            _, d = adb_run(["-s", ADB_TARGET, "shell", "dumpsys", "package", pkg], timeout=10)
            for l in d.splitlines():
                l = l.strip()
                if l.startswith("versionName="):
                    return l.split("=", 1)[1].strip()
        except Exception:
            pass
        return "未知"

    def run(self):
        try:
            ok, _ = adb_connect_and_check(retries=2)
            if not ok:
                self.finished_signal.emit([])
                return
            self.progress_signal.emit("正在获取应用列表…")
            third_party = set()
            ok, out = adb_run(["-s", ADB_TARGET, "shell", "pm", "list", "packages", "-3"], timeout=30)
            if ok:
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("package:"):
                        third_party.add(line[len("package:"):].strip())
            whitelist_found = set()
            ok, out = adb_run(["-s", ADB_TARGET, "shell", "pm", "list", "packages"], timeout=30)
            if ok:
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("package:"):
                        p = line[len("package:"):].strip()
                        if p in VISIBLE_SYSTEM_APPS:
                            whitelist_found.add(p)
            packages = list(third_party) + list(whitelist_found - third_party)
            cache = {} if self.force_refresh else load_apps_cache()
            self.progress_signal.emit("正在读取应用大小…")
            sizes = get_app_sizes_from_device(packages, progress_cb=self.progress_signal.emit)
            result = []
            total = len(packages)
            for i, pkg in enumerate(packages):
                self.progress_signal.emit(f"正在读取 {i + 1}/{total}：{pkg}")
                cached = cache.get(pkg)
                if cached and not self.force_refresh:
                    cur_ver = self._get_current_version(pkg)
                    if cur_ver == cached.get("version"):
                        cached_label = cached.get("label", "")
                        if cached_label and cached_label != default_label(pkg):
                            entry = dict(cached)
                            if pkg in sizes:
                                entry["size"] = sizes[pkg]
                            result.append(entry)
                            continue
                info = fetch_app_info_full(pkg)
                if pkg in sizes:
                    info["size"] = sizes[pkg]
                cache[pkg] = info
                save_apps_cache(cache)
                result.append(info)
            for pkg in list(cache.keys()):
                if pkg not in packages:
                    del cache[pkg]
            save_apps_cache(cache)
            self.finished_signal.emit(result)
        except Exception as e:
            log_debug("ListAppsWorker 异常:", e, traceback.format_exc())
            self.finished_signal.emit([])


class LazyStorePage(QWidget):
    def __init__(self):
        super().__init__()
        self._initialized = False
        self._inner = None
        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._outer_layout.setSpacing(0)
        self._placeholder = QLabel("正在加载应用商店…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#8a8f99; font-size:14px;")
        self._outer_layout.addWidget(self._placeholder)

    def ensure_initialized(self):
        if self._initialized:
            return
        self._initialized = True
        self._outer_layout.removeWidget(self._placeholder)
        self._placeholder.deleteLater()
        from store_page_impl import StorePageImpl
        self._inner = StorePageImpl()
        self._outer_layout.addWidget(self._inner)


def make_chinese_webview():
    class ChineseWebView(QWebEngineView):
        def contextMenuEvent(self, event):
            menu = QMenu(self)
            back_action = QAction("后退", self)
            back_action.setEnabled(self.history().canGoBack())
            back_action.triggered.connect(self.back)
            menu.addAction(back_action)
            forward_action = QAction("前进", self)
            forward_action.setEnabled(self.history().canGoForward())
            forward_action.triggered.connect(self.forward)
            menu.addAction(forward_action)
            reload_action = QAction("刷新", self)
            reload_action.triggered.connect(self.reload)
            menu.addAction(reload_action)
            menu.addSeparator()
            page = self.page()
            copy_action = QAction("复制", self)
            copy_action.setEnabled(bool(page.selectedText()))
            copy_action.triggered.connect(lambda: page.triggerAction(page.WebAction.Copy))
            menu.addAction(copy_action)
            select_all_action = QAction("全选", self)
            select_all_action.triggered.connect(lambda: page.triggerAction(page.WebAction.SelectAll))
            menu.addAction(select_all_action)
            menu.exec(event.globalPos())
    return ChineseWebView


class AdbInstallWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, apk_path):
        super().__init__()
        self.apk_path = apk_path

    def run(self):
        ok, dev_out = adb_connect_and_check(log=self.log_signal.emit, retries=2)
        if not ok:
            self.log_signal.emit(dev_out.strip() if dev_out else "")
            self.finished_signal.emit(False,
                "ADB 连接失败，请打开 ADB 调试\n（WSA → 开发人员 → 开启 ADB 调试）")
            return
        cmd = [ADB_EXE, "-s", ADB_TARGET, "install", "-r", self.apk_path]
        self.log_signal.emit(f'$ adb -s {ADB_TARGET} install -r "{self.apk_path}"')
        success = False
        error_msg = ""
        try:
            proc = subprocess.Popen(
                cmd, cwd=ADB_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW)
            for line in proc.stdout:
                line = line.rstrip()
                self.log_signal.emit(line)
                if "Success" in line:
                    success = True
                elif "Failure" in line or "error" in line.lower():
                    error_msg = line
            proc.wait()
        except Exception as e:
            self.log_signal.emit(f"异常: {e}")
            self.finished_signal.emit(False, str(e))
            return
        if success:
            self.finished_signal.emit(True, "APK 安装成功")
        else:
            detail = error_msg if error_msg else "安装失败，请检查 ADB 调试是否开启"
            self.finished_signal.emit(False, detail)


class AdbDeviceChecker(QThread):
    result_signal = pyqtSignal(bool, str)

    def run(self):
        ok, dev_out = adb_connect_and_check(retries=2)
        if ok:
            addr = ADB_TARGET
            for line in dev_out.splitlines():
                line = line.strip()
                if line.startswith(ADB_TARGET) and "\t" in line:
                    addr = line.split("\t")[0].strip()
                    break
            self.result_signal.emit(True, addr)
        else:
            self.result_signal.emit(False, "未检测到设备")


class SideBar(QListWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(240)
        self.setObjectName("SideBar")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSpacing(2)
        items = [
            ("🏠  首页", "home"),
            ("📦  安装适用于 Android 的 Windows 子系统", "wsa"),
            ("🛍️  应用商店", "store"),
            ("📲  安装 APK 程序", "apk"),
            ("🗑️  应用卸载", "uninstall"),
            ("⚙️  设置", "settings"),
        ]
        for text, key in items:
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setSizeHint(QSize(0, 48))
            self.addItem(it)


class HomePage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(20)
        v.setContentsMargins(40, 40, 40, 40)

        v.addStretch(2)
        v.addWidget(make_icon_label(APP_ICON_PATH, size=120), alignment=Qt.AlignmentFlag.AlignCenter)

        title = QLabel("欢迎使用 AndroidBox")
        title.setObjectName("CardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        desc = QLabel(
            '教程请见 GitHub<br>'
            '<a href="https://github.com/Gle-Res/Android-Box" style="color:#2563eb;">'
            'https://github.com/Gle-Res/Android-Box</a>')
        desc.setObjectName("CardDesc")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        desc.setOpenExternalLinks(True)
        v.addWidget(desc)

        v.addStretch(3)
        outer.addWidget(card)


class WSAPage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(20)
        v.setContentsMargins(40, 40, 40, 40)

        v.addStretch(2)
        v.addWidget(make_icon_label(os.path.join(BASE_DIR, "icon-2.ico"), size=120),
                    alignment=Qt.AlignmentFlag.AlignCenter)

        title = QLabel("安装适用于 Android 的 Windows 子系统")
        title.setObjectName("CardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        tip = QLabel(
            '请在「启动或关闭 Windows 功能」里打开：<br>'
            '• 适用于 Linux 的 Windows 子系统<br>'
            '• 虚拟机平台')
        tip.setObjectName("CardDesc")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(tip)

        btn = QPushButton("开始安装")
        btn.setObjectName("PrimaryBtn")
        btn.setFixedSize(200, 44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self.start_install)
        v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        v.addStretch(3)
        outer.addWidget(card)

    def start_install(self):
        if not os.path.exists(WSA_BAT):
            QMessageBox.critical(self, "错误", f"未找到 WSA 安装脚本：\n{WSA_BAT}")
            return
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command",
                 f'Start-Process -FilePath "{WSA_BAT}" -Verb RunAs'],
                cwd=os.path.dirname(WSA_BAT))
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动失败：{e}")


class ApkPage(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.checker = None
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)

        top_bar = QFrame()
        top_bar.setObjectName("Card")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 16, 20, 16)
        top_layout.setSpacing(14)

        self.device_label = QLabel("🔴 未检测到设备")
        self.device_label.setObjectName("DeviceStatus")
        self.device_label.setFixedWidth(320)
        self.device_label.setStyleSheet("color:#dc2626; font-weight:600;")

        self.check_btn = QPushButton("检测设备")
        self.check_btn.setObjectName("SecondaryBtn")
        self.check_btn.setFixedSize(120, 38)
        self.check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_btn.clicked.connect(self.check_device)

        self.apk_btn = QPushButton("选择 APK 并安装")
        self.apk_btn.setObjectName("PrimaryBtn")
        self.apk_btn.setFixedSize(200, 38)
        self.apk_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apk_btn.clicked.connect(self.choose_apk)

        top_layout.addWidget(self.device_label)
        top_layout.addWidget(self.check_btn)
        top_layout.addStretch(1)
        top_layout.addWidget(self.apk_btn)

        term_card = QFrame()
        term_card.setObjectName("Card")
        term_layout = QVBoxLayout(term_card)
        term_layout.setContentsMargins(12, 12, 12, 12)
        term_layout.setSpacing(8)

        header = QHBoxLayout()
        term_title = QLabel("📟 ADB 终端输出")
        term_title.setObjectName("CardSubTitle")
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("SecondaryBtn")
        clear_btn.setFixedSize(72, 28)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(lambda: self.terminal.clear())
        header.addWidget(term_title)
        header.addStretch(1)
        header.addWidget(clear_btn)

        self.terminal = QTextEdit()
        self.terminal.setObjectName("Terminal")
        self.terminal.setReadOnly(True)
        self.terminal.setFont(QFont("Consolas", 10))
        self.terminal.setPlaceholderText("把 APK 文件拖到此处即可安装")

        term_layout.addLayout(header)
        term_layout.addWidget(self.terminal, 1)

        outer.addWidget(top_bar)
        outer.addWidget(term_card, 1)

        self.overlay = QFrame(self)
        self.overlay.setObjectName("DropOverlay")
        self.overlay.setVisible(False)
        ov_layout = QVBoxLayout(self.overlay)
        ov_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_layout.setSpacing(20)
        ov_icon = QLabel("📥")
        ov_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_icon.setStyleSheet("font-size:72px; background:transparent;")
        ov_title = QLabel("松手以安装")
        ov_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_title.setStyleSheet("font-size:32px; font-weight:700; color:#ffffff; background:transparent;")
        ov_sub = QLabel("仅支持 .apk 文件")
        ov_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_sub.setStyleSheet("font-size:15px; color:#c7d2fe; background:transparent;")
        ov_layout.addWidget(ov_icon)
        ov_layout.addWidget(ov_title)
        ov_layout.addWidget(ov_sub)

        QTimer.singleShot(800, self.check_device)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "overlay"):
            self.overlay.setGeometry(self.rect())

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "overlay"):
            self.overlay.setGeometry(self.rect())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._show_overlay()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._hide_overlay()
        event.accept()

    def dropEvent(self, event):
        self._hide_overlay()
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        apk_path = None
        for url in urls:
            path = url.toLocalFile()
            if path and path.lower().endswith(".apk"):
                apk_path = path
                break
        if not apk_path:
            QMessageBox.warning(self, "提示", "请拖拽 APK 安装包安装")
            event.acceptProposedAction()
            return
        event.acceptProposedAction()
        self._start_install(apk_path)

    def _show_overlay(self):
        self.overlay.setGeometry(self.rect())
        self.overlay.setVisible(True)
        self.overlay.raise_()

    def _hide_overlay(self):
        self.overlay.setVisible(False)

    def log(self, text):
        self.terminal.append(text)
        self.terminal.moveCursor(QTextCursor.MoveOperation.End)

    def check_device(self):
        if self.checker and self.checker.isRunning():
            return
        self.device_label.setText("🟡 检测中…")
        self.device_label.setStyleSheet("color:#d97706; font-weight:600;")
        self.checker = AdbDeviceChecker()
        self.checker.result_signal.connect(self.on_device_result)
        self.checker.start()

    def on_device_result(self, connected, info):
        if connected:
            self.device_label.setText(f"🟢 已连接：{info}")
            self.device_label.setStyleSheet("color:#16a34a; font-weight:600;")
        else:
            self.device_label.setText(f"🔴 {info}")
            self.device_label.setStyleSheet("color:#dc2626; font-weight:600;")

    def choose_apk(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示", "已有安装任务正在进行，请稍候。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 APK 文件", "", "安卓安装包 (*.apk)")
        if not path:
            return
        self._start_install(path)

    def _start_install(self, path):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示", "已有安装任务正在进行，请稍候。")
            return
        self.terminal.clear()
        self.log(f"===== 开始安装：{path} =====\n")
        self.apk_btn.setEnabled(False)
        self.apk_btn.setText("安装中…")
        self.worker = AdbInstallWorker(path)
        self.worker.log_signal.connect(self.log)
        self.worker.finished_signal.connect(self.on_install_finished)
        self.worker.start()

    def on_install_finished(self, ok, msg):
        self.apk_btn.setEnabled(True)
        self.apk_btn.setText("选择 APK 并安装")
        if ok:
            self.log(f"\n✅ {msg}")
            QMessageBox.information(self, "安装成功", msg)
            self.check_device()
        else:
            self.log(f"\n❌ {msg}")
            QMessageBox.critical(self, "安装失败", msg)


class AppRow(QFrame):
    open_signal = pyqtSignal(str)
    uninstall_signal = pyqtSignal(str)

    def __init__(self, pkg, label, version, launchable, size_bytes, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.launchable = launchable
        self.setObjectName("AppRow")
        self.setFixedHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 8, 12, 8)
        layout.setSpacing(14)

        info = QVBoxLayout()
        info.setSpacing(2)
        name_label = QLabel(label)
        name_label.setObjectName("AppName")
        sub_label = QLabel(f"{version}  |  {pkg}")
        sub_label.setObjectName("AppSub")
        info.addWidget(name_label)
        info.addWidget(sub_label)

        size_label = QLabel(human_size(size_bytes) if size_bytes else "—")
        size_label.setObjectName("AppSize")
        size_label.setFixedWidth(100)
        size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        more_btn = QPushButton("⋯")
        more_btn.setObjectName("MoreBtn")
        more_btn.setFixedSize(36, 36)
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        more_btn.clicked.connect(self.show_menu)

        layout.addLayout(info, 1)
        layout.addWidget(size_label)
        layout.addWidget(more_btn)

    def show_menu(self):
        menu = QMenu(self)
        open_action = QAction("打开", self)
        open_action.setEnabled(self.launchable)
        open_action.triggered.connect(lambda: self.open_signal.emit(self.pkg))
        menu.addAction(open_action)
        menu.addSeparator()
        uninstall_action = QAction("卸载", self)
        uninstall_action.triggered.connect(lambda: self.uninstall_signal.emit(self.pkg))
        menu.addAction(uninstall_action)
        menu.exec(self.mapToGlobal(self.rect().bottomRight()))


class UninstallPage(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self._loaded_once = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("应用")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("RefreshBtn")
        self.refresh_btn.setFixedSize(40, 40)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setToolTip("刷新（右键清空缓存）")
        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.refresh_btn.customContextMenuRequested.connect(self.show_refresh_menu)
        header.addWidget(self.refresh_btn)
        self.status_label = QLabel("点击右上角 ⟳ 刷新，加载已安装应用")
        self.status_label.setObjectName("StatusLabel")
        self.list_widget = QWidget()
        self.list_widget.setObjectName("ListContainer")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setObjectName("ListScroll")
        self.scroll.setWidget(self.list_widget)
        layout.addLayout(header)
        layout.addWidget(self.status_label)
        layout.addWidget(self.scroll, 1)

    def ensure_loaded(self):
        if not self._loaded_once:
            self._loaded_once = True
            QTimer.singleShot(50, self.refresh)

    def show_refresh_menu(self, pos):
        menu = QMenu(self)
        force_action = QAction("强制刷新（清空缓存）", self)
        force_action.triggered.connect(self.force_refresh)
        menu.addAction(force_action)
        menu.exec(self.refresh_btn.mapToGlobal(pos))

    def force_refresh(self):
        try:
            if os.path.exists(APPS_CACHE_FILE):
                os.remove(APPS_CACHE_FILE)
        except Exception:
            pass
        self.refresh(force=True)

    def refresh(self, force=False):
        if self.worker and self.worker.isRunning():
            return
        if not force:
            cache = load_apps_cache()
            if cache:
                self.status_label.setText(f"已找到 {len(cache)} 个应用（缓存）")
                self.render_apps(list(cache.values()))
        self.status_label.setText("正在同步…")
        self.worker = ListAppsWorker(force_refresh=force)
        self.worker.finished_signal.connect(self.on_loaded)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.start()

    def on_progress(self, text):
        self.status_label.setText(text)

    def clear_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def render_apps(self, apps):
        self.clear_list()
        for info in apps:
            pkg = info.get("pkg")
            label = info.get("label") or default_label(pkg)
            version = info.get("version", "未知")
            launchable = info.get("launchable", False)
            size = info.get("size")
            row = AppRow(pkg, label, version, launchable, size)
            row.open_signal.connect(self.open_app)
            row.uninstall_signal.connect(self.uninstall_app)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

    def on_loaded(self, apps):
        if not apps:
            self.status_label.setText("未找到已安装的应用（请确认 ADB 已连接）")
            return
        self.status_label.setText(f"已找到 {len(apps)} 个应用")
        self.render_apps(apps)

    def open_app(self, pkg):
        activity = get_launch_activity(pkg)
        if activity:
            ok, out = adb_run(["-s", ADB_TARGET, "shell", "am", "start", "-n", activity], timeout=15)
            if ok and "Error" not in out and "Exception" not in out:
                return
        ok, out = adb_run(["-s", ADB_TARGET, "shell", "monkey",
                           "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], timeout=15)
        if not ok or "No activities found" in out or "aborted" in out.lower():
            QMessageBox.warning(self, "打开失败", f"无法启动 {pkg}\n{out.strip()}")

    def uninstall_app(self, pkg):
        ret = QMessageBox.question(
            self, "确认卸载", f"确定要卸载 {pkg} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        ok, out = adb_run(["-s", ADB_TARGET, "uninstall", pkg], timeout=60)
        if "Success" in out:
            cache = load_apps_cache()
            if pkg in cache:
                del cache[pkg]
                save_apps_cache(cache)
            QMessageBox.information(self, "卸载成功", f"{pkg} 已卸载")
            self.refresh(force=False)
        else:
            QMessageBox.critical(self, "卸载失败", out.strip() or "未知错误")


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SettingsPage(QWidget):
    theme_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addWidget(self.stack)
        main_page = QWidget()
        main_layout = QVBoxLayout(main_page)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        main_layout.addLayout(header)

        apk_card = QFrame()
        apk_card.setObjectName("Card")
        apk_layout = QHBoxLayout(apk_card)
        apk_layout.setContentsMargins(20, 16, 20, 16)
        apk_label = QLabel("关联 APK 文件")
        apk_label.setObjectName("SettingLabel")
        self.apk_switch = IosSwitch()
        self.apk_switch.setChecked(self.settings.get("associate_apk", False))
        self.apk_switch.toggled.connect(self.on_apk_switch)
        apk_layout.addWidget(apk_label)
        apk_layout.addStretch(1)
        apk_layout.addWidget(self.apk_switch)
        main_layout.addWidget(apk_card)

        theme_card = QFrame()
        theme_card.setObjectName("Card")
        theme_layout = QHBoxLayout(theme_card)
        theme_layout.setContentsMargins(20, 16, 20, 16)
        theme_label = QLabel("主题")
        theme_label.setObjectName("SettingLabel")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["浅色", "深色"])
        theme_map = {"light": 0, "dark": 1}
        self.theme_combo.setCurrentIndex(theme_map.get(self.settings.get("theme", "light"), 0))
        self.theme_combo.currentIndexChanged.connect(self.on_theme_changed)
        theme_layout.addWidget(theme_label)
        theme_layout.addStretch(1)
        theme_layout.addWidget(self.theme_combo)
        main_layout.addWidget(theme_card)

        about_card = QFrame()
        about_card.setObjectName("Card")
        about_layout = QHBoxLayout(about_card)
        about_layout.setContentsMargins(20, 16, 20, 16)
        about_label = QLabel("关于")
        about_label.setObjectName("SettingLabel")
        about_btn = QPushButton("›")
        about_btn.setObjectName("ArrowBtn")
        about_btn.setFixedSize(36, 36)
        about_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        about_btn.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        about_layout.addWidget(about_label)
        about_layout.addStretch(1)
        about_layout.addWidget(about_btn)
        main_layout.addWidget(about_card)

        main_layout.addStretch(1)
        self.stack.addWidget(main_page)


        about_page = QWidget()
        about_page_layout = QVBoxLayout(about_page)
        about_page_layout.setContentsMargins(0, 0, 0, 0)
        about_page_layout.setSpacing(16)

        about_header = QHBoxLayout()
        back_btn = QPushButton("←")
        back_btn.setObjectName("ArrowBtn")
        back_btn.setFixedSize(36, 36)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        about_title = QLabel("关于")
        about_title.setObjectName("PageTitle")
        about_header.addWidget(back_btn)
        about_header.addWidget(about_title)
        about_header.addStretch(1)
        about_page_layout.addLayout(about_header)

        about_card = QFrame()
        about_card.setObjectName("Card")
        about_card_layout = QVBoxLayout(about_card)
        about_card_layout.setContentsMargins(24, 24, 24, 24)
        about_card_layout.setSpacing(12)

        self.current_ver_label = QLabel(f"当前版本：{CURRENT_VERSION}")
        self.current_ver_label.setObjectName("InfoText")
        self.latest_ver_label = QLabel("最新版本：检查中…")
        self.latest_ver_label.setObjectName("InfoText")

        self.update_btn = QPushButton("更新")
        self.update_btn.setObjectName("PrimaryBtn")
        self.update_btn.setFixedSize(120, 38)
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self.open_release)

        about_card_layout.addWidget(self.current_ver_label)
        about_card_layout.addWidget(self.latest_ver_label)
        about_card_layout.addWidget(self.update_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        about_page_layout.addWidget(about_card)
        about_page_layout.addStretch(1)

        self.copyright_label = ClickableLabel("© 2026 Gle-Res")
        self.copyright_label.setObjectName("CopyrightLabel")
        self.copyright_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copyright_label.clicked.connect(self._open_github)
        about_page_layout.addWidget(self.copyright_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.stack.addWidget(about_page)

        self._release_url = GITHUB_URL
        QTimer.singleShot(200, self._check_version)

    def _open_github(self):
        try:
            webbrowser.open(GITHUB_URL)
        except Exception as e:
            log_debug("打开链接失败:", e)

    def on_apk_switch(self, checked):
        self.settings["associate_apk"] = bool(checked)
        save_settings(self.settings)
        if checked:
            if not associate_apk():
                QMessageBox.warning(self, "失败", "关联 APK 文件失败，请检查权限。")
        else:
            if not unassociate_apk():
                QMessageBox.warning(self, "失败", "取消关联失败。")

    def on_theme_changed(self, idx):
        theme_map = {0: "light", 1: "dark"}
        theme = theme_map.get(idx, "light")
        self.settings["theme"] = theme
        save_settings(self.settings)
        self.theme_changed.emit(theme)

    def _check_version(self):
        self.checker = VersionCheckWorker()
        self.checker.result_signal.connect(self._on_version_result)
        self.checker.start()

    def _on_version_result(self, version, url):
        self.latest_ver_label.setText(f"最新版本：{version}")
        self._release_url = url
        if version != "检查失败" and version != CURRENT_VERSION:
            self.update_btn.setEnabled(True)
            self.update_btn.setText("立即更新")
        elif version == CURRENT_VERSION:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("已是最新")
        else:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("检查失败")

    def open_release(self):
        try:
            webbrowser.open(self._release_url)
        except Exception as e:
            log_debug("打开更新页失败:", e)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AndroidBox")
        self.resize(1240, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = SideBar()
        self.sidebar.currentRowChanged.connect(self.on_nav_changed)

        self.stack = QStackedWidget()
        self.home_page = HomePage()
        self.wsa_page = WSAPage()
        self.store_page = LazyStorePage()
        self.apk_page = ApkPage()
        self.uninstall_page = UninstallPage()
        self.settings_page = SettingsPage()
        self.settings_page.theme_changed.connect(self.apply_theme)

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.wsa_page)
        self.stack.addWidget(self.store_page)
        self.stack.addWidget(self.apk_page)
        self.stack.addWidget(self.uninstall_page)
        self.stack.addWidget(self.settings_page)

        root.addWidget(self.sidebar)
        root.addWidget(self.stack, 1)

        self.sidebar.setCurrentRow(0)

        theme = load_settings().get("theme", "light")
        self._current_theme = theme
        self.apply_style()

    def on_nav_changed(self, row):
        self.stack.setCurrentIndex(row)
        if row == 2 and isinstance(self.store_page, LazyStorePage):
            QTimer.singleShot(50, self.store_page.ensure_initialized)
        if row == 4 and isinstance(self.uninstall_page, UninstallPage):
            self.uninstall_page.ensure_loaded()

    def apply_theme(self, theme):
        self._current_theme = theme
        self.apply_style()

    def apply_style(self):
        theme = getattr(self, "_current_theme", "light")
        dark = (theme == "dark")

        if dark:
            bg = "#1e1e2e"
            card_bg = "#2a2a3c"
            text_primary = "#e8e8f0"
            text_secondary = "#a0a0b8"
            border = "#3a3a4c"
            item_hover = "#33334a"
            item_selected = "#3a3a5a"
            sel_text = "#8ab4ff"
            menu_bg = "#2a2a3c"
            input_bg = "#33334a"
            input_border = "#4a4a5c"
            scroll_bg = "#1e1e2e"
        else:
            bg = "#f5f6fa"
            card_bg = "#ffffff"
            text_primary = "#1a1a2e"
            text_secondary = "#667085"
            border = "#ebeef5"
            item_hover = "#eef2ff"
            item_selected = "#e0e9ff"
            sel_text = "#1a56db"
            menu_bg = "#ffffff"
            input_bg = "#f5f6fa"
            input_border = "#d0d5dd"
            scroll_bg = "#f5f6fa"

        self.setStyleSheet(f"""
            QMainWindow {{ background: {bg}; }}

            QListWidget#SideBar {{
                background: {card_bg}; border: none;
                border-right: 1px solid {border};
                padding: 14px 8px; outline: 0;
                color: {text_primary}; font-size: 14px;
            }}
            QListWidget#SideBar::item {{
                border-radius: 10px; padding-left: 12px;
                margin: 3px 4px; color: {text_primary};
            }}
            QListWidget#SideBar::item:hover {{ background: {item_hover}; }}
            QListWidget#SideBar::item:selected {{
                background: {item_selected}; color: {sel_text}; font-weight: 600;
            }}

            QFrame#Card {{
                background: {card_bg}; border-radius: 16px;
                border: none;
            }}

            QLabel#CardTitle, QLabel#PageTitle {{
                font-size: 22px; font-weight: 700; color: {text_primary};
                background: transparent;
            }}
            QLabel#CardSubTitle {{
                font-size: 14px; font-weight: 600; color: {text_primary};
                background: transparent;
            }}
            QLabel#CardDesc {{
                font-size: 14px; color: {text_secondary};
                background: transparent;
            }}
            QLabel#SettingLabel {{
                font-size: 15px; font-weight: 600; color: {text_primary};
                background: transparent;
            }}
            QLabel#InfoText {{
                font-size: 15px; color: {text_primary};
                background: transparent;
            }}
            QLabel#CopyrightLabel {{
                font-size: 12px; color: {text_secondary};
                background: transparent;
            }}
            QLabel#AppName {{
                font-size: 15px; font-weight: 600; color: {text_primary};
                background: transparent;
            }}
            QLabel#AppSub {{
                font-size: 12px; color: {text_secondary};
                background: transparent;
            }}
            QLabel#AppSize {{
                font-size: 13px; color: {text_secondary};
                background: transparent;
            }}
            QLabel#StatusLabel {{
                font-size: 14px; color: {text_secondary};
                background: transparent;
            }}

            QScrollArea#ListScroll {{
                background: transparent; border: none;
            }}
            QScrollArea#ListScroll > QWidget > QWidget {{
                background: {scroll_bg};
            }}
            QWidget#ListContainer {{
                background: {scroll_bg};
            }}
            QScrollArea {{
                background: transparent; border: none;
            }}
            QScrollArea > QWidget > QWidget {{
                background: {scroll_bg};
            }}

            QPushButton#PrimaryBtn {{
                background: #2563eb; color: #ffffff;
                border: none; border-radius: 10px;
                font-size: 14px; font-weight: 600;
            }}
            QPushButton#PrimaryBtn:hover   {{ background: #1d4ed8; }}
            QPushButton#PrimaryBtn:pressed {{ background: #1e40af; }}
            QPushButton#PrimaryBtn:disabled {{ background: #93b4f5; }}

            QPushButton#SecondaryBtn {{
                background: {item_hover}; color: {sel_text};
                border: none; border-radius: 10px;
                font-size: 14px; font-weight: 600;
            }}
            QPushButton#SecondaryBtn:hover {{ background: {item_selected}; }}

            QPushButton#CancelBtn {{
                background: #fee2e2; color: #b91c1c;
                border: none; border-radius: 8px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton#CancelBtn:hover {{ background: #fecaca; }}

            QPushButton#ArrowBtn {{
                background: transparent; color: {text_primary};
                border: none; border-radius: 8px;
                font-size: 22px; font-weight: 600;
                padding: 0;
            }}
            QPushButton#ArrowBtn:hover {{
                background: {item_hover}; color: {sel_text};
            }}

            QFrame#DownloadBar {{
                background: {card_bg}; border-bottom: 1px solid {border};
            }}
            QLabel#DlLabel {{ font-size: 13px; color: {text_primary}; }}
            QLabel#DlSpeed {{ font-size: 13px; color: #2563eb; font-weight: 600; }}
            QProgressBar#DlProgress {{
                background: {item_hover}; border: none; border-radius: 5px;
            }}
            QProgressBar#DlProgress::chunk {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #3b82f6, stop:1 #6366f1);
                border-radius: 5px;
            }}

            QTextEdit#Terminal {{
                background: #0f172a; color: #a5f3fc;
                border: none; border-radius: 10px; padding: 10px;
                selection-background-color: #1e40af;
            }}

            QFrame#AppRow {{
                background: {card_bg}; border: none;
                border-radius: 12px;
            }}
            QFrame#AppRow:hover {{
                background: {item_hover}; border: none;
            }}

            QPushButton#MoreBtn {{
                background: transparent; border: none;
                border-radius: 8px; font-size: 20px; color: {text_secondary};
            }}
            QPushButton#MoreBtn:hover {{
                background: {item_hover}; color: {sel_text};
            }}

            QPushButton#RefreshBtn {{
                background: {item_hover}; color: {sel_text};
                border: none; border-radius: 10px;
                font-size: 18px; font-weight: 600;
            }}
            QPushButton#RefreshBtn:hover {{ background: {item_selected}; }}

            QComboBox {{
                background: {input_bg};
                color: {text_primary};
                border: 1px solid {input_border};
                border-radius: 8px;
                padding: 6px 12px;
                min-width: 120px;
                font-size: 14px;
            }}
            QComboBox:hover {{ background: {item_hover}; }}
            QComboBox::drop-down {{
                border: none; width: 24px;
            }}
            QComboBox QAbstractItemView {{
                background: {menu_bg};
                color: {text_primary};
                border: 1px solid {input_border};
                border-radius: 8px;
                selection-background-color: {item_selected};
                selection-color: {sel_text};
                outline: 0;
                padding: 4px;
            }}

            QMenu {{
                background: {menu_bg}; border: 1px solid {border};
                border-radius: 8px; padding: 6px;
            }}
            QMenu::item {{
                padding: 6px 24px 6px 16px; border-radius: 6px;
                color: {text_primary}; font-size: 13px;
            }}
            QMenu::item:selected {{ background: {item_selected}; color: {sel_text}; }}
            QMenu::item:disabled {{ color: #888; }}
            QMenu::separator {{
                height: 1px; background: {border}; margin: 4px 8px;
            }}

            QFrame#DropOverlay {{
                background: rgba(15, 23, 42, 220);
                border: 2px dashed #60a5fa;
                border-radius: 16px;
            }}

            QScrollBar:vertical {{
                background: transparent; width: 10px; margin: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {border}; border-radius: 5px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {text_secondary}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)


def _excepthook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log_debug("未捕获异常:\n", msg)
    try:
        QMessageBox.critical(None, "程序异常", msg[-1500:])
    except Exception:
        pass


sys.excepthook = _excepthook


def _store_page_impl_loader():
    from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
    ChineseWebView = make_chinese_webview()

    class StorePageImpl(QWidget):
        def __init__(self):
            super().__init__()
            self._download = None
            self._last_bytes = 0
            self._last_time = time.time()
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(300)
            self._poll_timer.timeout.connect(self.update_progress)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            self.download_bar = QFrame()
            self.download_bar.setObjectName("DownloadBar")
            self.download_bar.setFixedHeight(64)
            self.download_bar.setVisible(False)
            dl_layout = QHBoxLayout(self.download_bar)
            dl_layout.setContentsMargins(18, 8, 18, 8)
            dl_layout.setSpacing(14)
            self.dl_label = QLabel("准备下载…")
            self.dl_label.setObjectName("DlLabel")
            self.dl_label.setFixedWidth(260)
            self.dl_progress = QProgressBar()
            self.dl_progress.setObjectName("DlProgress")
            self.dl_progress.setRange(0, 100)
            self.dl_progress.setValue(0)
            self.dl_progress.setFixedHeight(10)
            self.dl_progress.setTextVisible(False)
            self.dl_speed = QLabel("0 B/s")
            self.dl_speed.setObjectName("DlSpeed")
            self.dl_speed.setFixedWidth(160)
            self.dl_speed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.dl_cancel = QPushButton("取消")
            self.dl_cancel.setObjectName("CancelBtn")
            self.dl_cancel.setFixedSize(70, 30)
            self.dl_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            self.dl_cancel.clicked.connect(self.cancel_download)
            dl_layout.addWidget(self.dl_label)
            dl_layout.addWidget(self.dl_progress, 1)
            dl_layout.addWidget(self.dl_speed)
            dl_layout.addWidget(self.dl_cancel)
            self.view = ChineseWebView()
            self.view.setUrl(QUrl("https://www.wandoujia.com"))
            profile = self.view.page().profile()
            profile.downloadRequested.connect(self.on_download)
            layout.addWidget(self.download_bar)
            layout.addWidget(self.view, 1)

        def on_download(self, download: QWebEngineDownloadRequest):
            default_name = download.downloadFileName() or "download.apk"
            save_path, _ = QFileDialog.getSaveFileName(
                self, "选择 APK 保存位置",
                os.path.join(os.path.expanduser("~"), "Downloads", default_name),
                "安卓安装包 (*.apk);;所有文件 (*.*)")
            if not save_path:
                download.cancel()
                return
            download.setDownloadDirectory(os.path.dirname(save_path))
            download.setDownloadFileName(os.path.basename(save_path))
            download.accept()
            self._download = download
            self._last_bytes = 0
            self._last_time = time.time()
            self.dl_label.setText(f"下载中：{os.path.basename(save_path)}")
            self.dl_progress.setValue(0)
            self.dl_speed.setText("0 B/s")
            self.download_bar.setVisible(True)
            self._poll_timer.start()

        def update_progress(self):
            d = self._download
            if d is None:
                self._poll_timer.stop()
                return
            received = d.receivedBytes()
            total = d.totalBytes()
            if total > 0:
                self.dl_progress.setValue(int(received * 100 / total))
            now = time.time()
            dt = now - self._last_time
            if dt >= 0.5:
                speed = (received - self._last_bytes) / dt
                self._last_bytes = received
                self._last_time = now
                self.dl_speed.setText(f"{human_size(speed)}/s")
            if d.isFinished():
                self._poll_timer.stop()
                state = d.state()
                if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                    self.dl_progress.setValue(100)
                    self.dl_label.setText("✅ 下载完成")
                    QTimer.singleShot(3000, lambda: self.download_bar.setVisible(False))
                elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                    self.dl_label.setText("❌ 已取消")
                    QTimer.singleShot(2000, lambda: self.download_bar.setVisible(False))
                else:
                    self.dl_label.setText("❌ 下载失败")
                    QTimer.singleShot(3000, lambda: self.download_bar.setVisible(False))
                self._download = None

        def cancel_download(self):
            if self._download:
                self._download.cancel()

    return StorePageImpl


import types
_store_page_impl_module = types.ModuleType("store_page_impl")
_store_page_impl_module.StorePageImpl = None


def _get_store_page_impl():
    if _store_page_impl_module.StorePageImpl is None:
        _store_page_impl_module.StorePageImpl = _store_page_impl_loader()
    return _store_page_impl_module.StorePageImpl


sys.modules["store_page_impl"] = _store_page_impl_module


def _lazy_store_page_init(self):
    if self._initialized:
        return
    self._initialized = True
    self._outer_layout.removeWidget(self._placeholder)
    self._placeholder.deleteLater()
    StorePageImpl = _get_store_page_impl()
    self._inner = StorePageImpl()
    self._outer_layout.addWidget(self._inner)


LazyStorePage.ensure_initialized = _lazy_store_page_init

def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    apk_arg = None
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.lower().endswith(".apk") and os.path.exists(arg):
            apk_arg = arg

    if apk_arg:
        apk_name = os.path.basename(apk_arg)
        ret = QMessageBox.question(
            None,
            "AndroidBox",
            f"是否安装以下 APK？\n\n{apk_name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if ret != QMessageBox.StandardButton.Yes:
            sys.exit(0)

    if not os.path.exists(ADB_EXE):
        QMessageBox.critical(
            None, "缺少 ADB",
            f"未找到 adb.exe：\n{ADB_EXE}\n\n"
            "请确认程序目录下存在 adb 文件夹，且包含 adb.exe 及相关 dll。")
        sys.exit(1)

    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))

    win = MainWindow()
    if os.path.exists(APP_ICON_PATH):
        win.setWindowIcon(QIcon(APP_ICON_PATH))

    if apk_arg:
        win.sidebar.setCurrentRow(3)
        QTimer.singleShot(600, lambda: win.apk_page._start_install(apk_arg))

    app.aboutToQuit.connect(kill_adb_processes)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()