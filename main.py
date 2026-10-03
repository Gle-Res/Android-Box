import os
import sys
import time
import subprocess
from PyQt6.QtCore import Qt, QUrl, QSize, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor, QPixmap, QIcon, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QLabel, QPushButton,
    QFileDialog, QMessageBox, QFrame, QProgressBar, QTextEdit, QMenu
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineDownloadRequest


def get_base_dir():
    """打包后返回 exe 所在目录；源码运行返回脚本目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
ADB_DIR = os.path.join(BASE_DIR, "adb")
ADB_EXE = os.path.join(ADB_DIR, "adb.exe")
WSA_BAT = os.path.join(BASE_DIR, "WSA", "Run.bat")

ADB_TARGET = "127.0.0.1:58526"


def human_size(num):
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
    return pix.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )


def adb_run(args, timeout=30):
    """执行 adb 命令，返回 (成功, 输出)。成功以输出内容判断为准，不依赖退出码。"""
    try:
        r = subprocess.run(
            [ADB_EXE] + args, cwd=ADB_DIR,
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "ADB 执行超时"
    except FileNotFoundError:
        return False, f"未找到 adb.exe：{ADB_EXE}"
    except Exception as e:
        return False, str(e)


def adb_connect_and_check(log=None, retries=2):
    """
    启动 adb server → connect → 用 devices 验证
    返回 (是否连接成功, devices 输出)
    """
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
    """关闭 adb server 并强杀所有 adb.exe 进程"""
    try:
        subprocess.run(
            [ADB_EXE, "kill-server"],
            cwd=ADB_DIR,
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception:
        pass

    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "adb.exe"],
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception:
        pass


class ChineseWebView(QWebEngineView):
    """中文右键菜单：后退 / 前进 / 刷新 / 复制 / 全选"""

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
            self.finished_signal.emit(
                False,
                "ADB 连接失败，请打开 ADB 调试\n"
                "（WSA → 开发人员 → 开启 ADB 调试）"
            )
            return

        self.log_signal.emit(f'$ adb install -r "{self.apk_path}"')
        success = False
        try:
            proc = subprocess.Popen(
                [ADB_EXE, "install", "-r", self.apk_path],
                cwd=ADB_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            for line in proc.stdout:
                self.log_signal.emit(line.rstrip())
                if "Success" in line:
                    success = True
            proc.wait()
        except Exception as e:
            self.log_signal.emit(f"异常: {e}")
            self.finished_signal.emit(False, str(e))
            return

        if success:
            self.finished_signal.emit(True, "APK 安装成功")
        else:
            self.finished_signal.emit(False, "安装失败，请检查 ADB 调试是否开启")


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
        ]
        for text, key in items:
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setSizeHint(QSize(0, 48))
            self.addItem(it)


class HomePage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("Card")
        card.setFixedSize(580, 340)
        v = QVBoxLayout(card)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(16)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = load_icon_pixmap(os.path.join(BASE_DIR, "icon-1.ico"), 96)
        if pix:
            icon_label.setPixmap(pix)
        else:
            icon_label.setText("📦")
            icon_label.setStyleSheet("font-size:64px;")
        v.addWidget(icon_label)

        title = QLabel("欢迎使用 AndroidBox")
        title.setObjectName("CardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        desc = QLabel(
            '教程请见 Github<br>'
            '<a href="https://github.com/Gle-Res/Android-Box" style="color:#2563eb;">'
            'https://github.com/Gle-Res/Android-Box</a>'
        )
        desc.setObjectName("CardDesc")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        desc.setOpenExternalLinks(True)

        v.addWidget(title)
        v.addWidget(desc)
        layout.addWidget(card)


class WSAPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("Card")
        card.setFixedSize(660, 440)
        v = QVBoxLayout(card)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(18)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = load_icon_pixmap(os.path.join(BASE_DIR, "icon-2.ico"), 96)
        if pix:
            icon_label.setPixmap(pix)
        else:
            icon_label.setText("📦")
            icon_label.setStyleSheet("font-size:64px;")
        v.addWidget(icon_label)

        title = QLabel("安装适用于 Android 的 Windows 子系统")
        title.setObjectName("CardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        tip = QLabel(
            '请在「启动或关闭 Windows 功能」里打开：<br>'
            '• 适用于 Linux 的 Windows 子系统<br>'
            '• 虚拟机平台'
        )
        tip.setObjectName("CardDesc")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn = QPushButton("开始安装")
        btn.setObjectName("PrimaryBtn")
        btn.setFixedSize(200, 44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self.start_install)

        v.addWidget(title)
        v.addWidget(tip)
        v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(card)

    def start_install(self):
        if not os.path.exists(WSA_BAT):
            QMessageBox.critical(self, "错误", f"未找到 WSA 安装脚本：\n{WSA_BAT}")
            return
        try:
            subprocess.Popen(
                ["powershell", "-Command",
                 f'Start-Process -FilePath "{WSA_BAT}" -Verb RunAs'],
                cwd=os.path.dirname(WSA_BAT), shell=True
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动失败：{e}")


class StorePage(QWidget):
    def __init__(self):
        super().__init__()
        self._download = None
        self._last_bytes = 0
        self._last_time = time.time()

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
            "安卓安装包 (*.apk);;所有文件 (*.*)"
        )
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

        download.receivedBytesChanged.connect(self.update_progress)
        download.totalBytesChanged.connect(self.update_progress)
        download.isFinishedChanged.connect(self.on_finished)

    def update_progress(self):
        d = self._download
        if d is None:
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

    def on_finished(self):
        if self._download and self._download.isFinished():
            state = self._download.state()
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

    def cancel_download(self):
        if self._download:
            self._download.cancel()


class ApkPage(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.checker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

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

        term_title = QLabel("📟 ADB 终端输出")
        term_title.setObjectName("CardSubTitle")

        self.terminal = QTextEdit()
        self.terminal.setObjectName("Terminal")
        self.terminal.setReadOnly(True)
        self.terminal.setFont(QFont("Consolas", 10))

        term_layout.addWidget(term_title)
        term_layout.addWidget(self.terminal, 1)

        layout.addWidget(top_bar)
        layout.addWidget(term_card, 1)

        QTimer.singleShot(800, self.check_device)

    def log(self, text):
        self.terminal.append(text)
        self.terminal.moveCursor(QTextCursor.MoveOperation.End)

    def check_device(self):
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
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 APK 文件", "", "安卓安装包 (*.apk)"
        )
        if not path:
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
        self.store_page = StorePage()
        self.apk_page = ApkPage()

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.wsa_page)
        self.stack.addWidget(self.store_page)
        self.stack.addWidget(self.apk_page)

        root.addWidget(self.sidebar)
        root.addWidget(self.stack, 1)

        self.sidebar.setCurrentRow(0)
        self.apply_style()

    def on_nav_changed(self, row):
        self.stack.setCurrentIndex(row)

    def apply_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #f5f6fa; }

            QListWidget#SideBar {
                background: #ffffff;
                border: none;
                border-right: 1px solid #e8e8ef;
                padding: 14px 8px;
                outline: 0;
                color: #333;
                font-size: 14px;
            }
            QListWidget#SideBar::item {
                border-radius: 10px;
                padding-left: 12px;
                margin: 3px 4px;
                color: #444;
            }
            QListWidget#SideBar::item:hover { background: #eef2ff; }
            QListWidget#SideBar::item:selected {
                background: #e0e9ff;
                color: #1a56db;
                font-weight: 600;
            }

            QFrame#Card {
                background: #ffffff;
                border-radius: 16px;
                border: 1px solid #ebeef5;
            }
            QLabel#CardTitle {
                font-size: 22px;
                font-weight: 700;
                color: #1a1a2e;
            }
            QLabel#CardSubTitle {
                font-size: 14px;
                font-weight: 600;
                color: #333;
            }
            QLabel#CardDesc {
                font-size: 14px;
                color: #667085;
            }

            QPushButton#PrimaryBtn {
                background: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#PrimaryBtn:hover   { background: #1d4ed8; }
            QPushButton#PrimaryBtn:pressed { background: #1e40af; }
            QPushButton#PrimaryBtn:disabled { background: #93b4f5; }

            QPushButton#SecondaryBtn {
                background: #f1f5ff;
                color: #2563eb;
                border: 1px solid #cddafc;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#SecondaryBtn:hover { background: #e0e9ff; }

            QPushButton#CancelBtn {
                background: #fee2e2;
                color: #b91c1c;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#CancelBtn:hover { background: #fecaca; }

            QFrame#DownloadBar {
                background: #ffffff;
                border-bottom: 1px solid #e8e8ef;
            }
            QLabel#DlLabel { font-size: 13px; color: #333; }
            QLabel#DlSpeed { font-size: 13px; color: #2563eb; font-weight: 600; }
            QProgressBar#DlProgress {
                background: #eef2ff;
                border: none;
                border-radius: 5px;
            }
            QProgressBar#DlProgress::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #3b82f6, stop:1 #6366f1);
                border-radius: 5px;
            }

            QTextEdit#Terminal {
                background: #0f172a;
                color: #a5f3fc;
                border: none;
                border-radius: 10px;
                padding: 10px;
                selection-background-color: #1e40af;
            }

            QMenu {
                background: #ffffff;
                border: 1px solid #e8e8ef;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 6px 24px 6px 16px;
                border-radius: 6px;
                color: #333;
                font-size: 13px;
            }
            QMenu::item:selected {
                background: #e0e9ff;
                color: #1a56db;
            }
            QMenu::item:disabled { color: #bbb; }
            QMenu::separator {
                height: 1px;
                background: #e8e8ef;
                margin: 4px 8px;
            }
        """)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))

    app_icon_path = os.path.join(BASE_DIR, "icon-1.ico")
    if os.path.exists(app_icon_path):
        app.setWindowIcon(QIcon(app_icon_path))

    profile = QWebEngineProfile.defaultProfile()
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )

    win = MainWindow()
    if os.path.exists(app_icon_path):
        win.setWindowIcon(QIcon(app_icon_path))

    app.aboutToQuit.connect(kill_adb_processes)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()