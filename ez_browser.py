# ez_browser.py — fixed strings, splash/audio, per-server game paths, multi-server, heartbeat LED

from __future__ import annotations

import sys
import os
import json
import uuid
import threading
import time
import subprocess
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import requests
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QPushButton, QMessageBox, QLineEdit,
    QLabel, QInputDialog, QMenuBar, QMenu, QFrame, QAction, QSplitter, QSplashScreen,
    QFileDialog
)
from PyQt5.QtGui import QIcon, QPixmap, QColor, QPainter
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QEventLoop, QPropertyAnimation

# Audio init
import pygame
try:
    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
except Exception:
    pass
try:
    pygame.mixer.init()
except Exception:
    pass

# ---------- Constants ----------
BACKEND_URL = "https://jacinto-server.fly.dev"
HEARTBEAT_INTERVAL = 5
HOSTS_DB = "hosts.json"
LEGACY_HOST_CFG = "host_config.json"

OMEN_WAV = "return-of-the-omen-fixed.wav"
MAD_WORLD_WAV = "mad-world-fixed.wav"
COG_TAG_WAV = "gears-of-war-cog-tag-fixed.wav"
SPLASH_PNG = "splash.png"
APP_ICON = "Jacinto.ico"

# ---------- Utils ----------
def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", None)
    if base_path:
        return os.path.join(base_path, relative_path)
    if os.path.exists(relative_path):
        return os.path.abspath(relative_path)
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(here, relative_path)


def load_sound(name: str) -> Optional["pygame.mixer.Sound"]:
    path = resource_path(name)
    try:
        if os.path.exists(path):
            return pygame.mixer.Sound(path)
    except Exception:
        pass
    return None


def choose_exe_path(title: str) -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(None, title, "", "Executable (*.exe);;All Files (*)")
    if not path:
        return None
    if not os.path.exists(path):
        QMessageBox.critical(None, "Error", "Selected path does not exist.")
        return None
    return path

# ---------- Models ----------
@dataclass
class Host:
    id: str
    name: str
    public_ip: str
    local_ip: str
    port: int
    map: str
    password: str
    exe_path: str  # per-server path

    @staticmethod
    def from_prompt() -> Optional["Host"]:
        prompts = [
            ("Server Name", "Enter your server name:"),
            ("Public IP", "Enter your public IP:"),
            ("Local IP", "Enter your local IP:"),
            ("Port", "Enter your server port:"),
            ("Map", "Enter map name:"),
        ]
        vals: List[str] = []
        for title, msg in prompts:
            text, ok = QInputDialog.getText(None, title, msg)
            if not ok or not text.strip():
                return None
            vals.append(text.strip())
        pw, ok = QInputDialog.getText(None, "Password", "Set a host password:", echo=QLineEdit.Password)
        if not ok:
            return None
        try:
            port = int(vals[3])
        except ValueError:
            QMessageBox.critical(None, "Error", "Port must be an integer.")
            return None
        exe = choose_exe_path("Select game EXE for this server")
        if not exe:
            return None
        return Host(
            id=str(uuid.uuid4()),
            name=vals[0], public_ip=vals[1], local_ip=vals[2], port=port, map=vals[4], password=pw or "", exe_path=exe,
        )

# ---------- Storage ----------

def load_hosts() -> List[Host]:
    if os.path.exists(HOSTS_DB):
        try:
            data = json.load(open(HOSTS_DB, "r"))
            items: List[Host] = []
            for h in data:
                if "exe_path" not in h:
                    h["exe_path"] = ""
                items.append(Host(**h))
            return items
        except Exception:
            pass
    if os.path.exists(LEGACY_HOST_CFG):
        try:
            legacy = json.load(open(LEGACY_HOST_CFG, "r"))
            host = Host(
                id=str(uuid.uuid4()),
                name=legacy["name"], public_ip=legacy["public_ip"], local_ip=legacy["local_ip"],
                port=int(legacy["port"]), map=legacy["map"], password=legacy.get("password", ""), exe_path="",
            )
            save_hosts([host])
            try:
                os.remove(LEGACY_HOST_CFG)
            except OSError:
                pass
            return [host]
        except Exception:
            pass
    return []


def save_hosts(items: List[Host]) -> None:
    json.dump([asdict(h) for h in items], open(HOSTS_DB, "w"), indent=2)

# ---------- UI ----------
class JacintoLobbyBrowser(QWidget):
    hb_color_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jacinto 1.3 Server Browser")
        icon_path = resource_path(APP_ICON)
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setGeometry(200, 100, 980, 620)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(self._theme())

        self.is_muted = False
        self.click = load_sound(COG_TAG_WAV)
        self.bg = load_sound(OMEN_WAV)
        self.splash_sfx = load_sound(MAD_WORLD_WAV)
        self.bg_ch = pygame.mixer.Channel(1) if pygame.mixer.get_init() else None
        self.fx_ch = pygame.mixer.Channel(2) if pygame.mixer.get_init() else None

        self.available_timer = QTimer(self)
        self.available_timer.timeout.connect(self._refresh_available)
        self.available_timer.start(5000)
        self.hb_threads: Dict[str, threading.Event] = {}

        self.hb_color_signal.connect(self._set_hb_color)

        self._setup_ui()
        self._refresh_available()
        self._refresh_mine()

    def _theme(self) -> str:
        return """
        QWidget { background-color: rgba(18,18,18,200); color: #e0e0e0; font-family: Segoe UI; font-size: 14px; }
        QPushButton { background-color: #2a2a2a; border: 1px solid #3e3e3e; padding: 8px; border-radius: 6px; color: white; }
        QPushButton:hover { background-color: #3a3a3a; border: 1px solid #ff0000; }
        QLineEdit, QLabel { border: 1px solid #3e3e3e; border-radius: 4px; padding: 6px; background-color: #1e1e1e; }
        QListWidget { background: transparent; border: 1px solid #444; border-radius: 6px; }
        QMenuBar { background-color: #1e1e1e; }
        QMenuBar::item:selected { background-color: #2a2a2a; }
        QMenu { background-color: #1e1e1e; }
        """

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setLayout(layout)

        bar = QMenuBar(self)
        hb = QMenu("Heartbeat", self)
        hb.addAction("Start (selected in My Servers)", self.manual_start_heartbeat)
        hb.addAction("Stop All", self.manual_stop_heartbeat)
        bar.addMenu(hb)

        audio = QMenu("Audio", self)
        mute_action = QAction("Toggle Mute", self)
        mute_action.triggered.connect(self.toggle_mute)
        audio.addAction(mute_action)
        bar.addMenu(audio)
        layout.setMenuBar(bar)

        splitter = QSplitter(Qt.Horizontal)
        left = QVBoxLayout(); right = QVBoxLayout()
        left_box = QWidget(); left_box.setLayout(left)
        right_box = QWidget(); right_box.setLayout(right)

        left.addWidget(QLabel("Available Servers"))
        self.available_list = QListWidget(); self.available_list.setFrameShape(QFrame.Panel)
        left.addWidget(self.available_list)

        right.addWidget(QLabel("My Servers"))
        self.my_list = QListWidget(); self.my_list.setFrameShape(QFrame.Panel)
        right.addWidget(self.my_list)

        splitter.addWidget(left_box); splitter.addWidget(right_box)
        layout.addWidget(splitter)

        btns = QHBoxLayout()
        self.btn_add = QPushButton("Add My Server")
        self.btn_edit = QPushButton("Edit / Remove Selected")
        self.btn_launch = QPushButton("Launch Selected")
        for b in (self.btn_add, self.btn_edit, self.btn_launch):
            b.clicked.connect(self._click)
            btns.addWidget(b)
        layout.addLayout(btns)

        self.btn_add.clicked.connect(self.add_server)
        self.btn_edit.clicked.connect(self.edit_or_remove)
        self.btn_launch.clicked.connect(self.launch_selected)

        hb_row = QHBoxLayout()
        hb_row.addWidget(QLabel("Heartbeat:"))
        self.hb_status = QLabel(); self.hb_status.setFixedSize(20, 20)
        self._set_hb_color("red")
        hb_row.addWidget(self.hb_status)
        layout.addLayout(hb_row)

    def _click(self):
        if self.is_muted or not self.fx_ch or not self.click:
            return
        if self.bg_ch and self.bg_ch.get_busy():
            self.bg_ch.pause()
        self.fx_ch.play(self.click)
        t0 = time.time()
        while self.fx_ch.get_busy() and time.time() - t0 < 1.0:
            pygame.time.wait(10)
        if self.bg_ch:
            self.bg_ch.unpause()

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        try:
            pygame.mixer.pause() if self.is_muted else pygame.mixer.unpause()
        except Exception:
            pass

    def _set_hb_color(self, color: str):
        self.hb_status.setStyleSheet(f"background-color: {color}; border-radius: 10px;")

    def _refresh_available(self):
        try:
            res = requests.get(f"{BACKEND_URL}/servers", timeout=3)
            self.available_list.clear()
            for s in res.json():
                self.available_list.addItem(f"{s['name']} - {s['public_ip']}:{s['port']}")
        except Exception as e:
            print("[Available List Error]", e)

    def _refresh_mine(self):
        self.my_list.clear()
        for h in load_hosts():
            note = " (EXE set)" if h.exe_path else " (EXE not set)"
            self.my_list.addItem(f"{h.name} - {h.public_ip}:{h.port} (map={h.map}){note}")

    # ---------- CRUD ----------
    def add_server(self):
        new_host = Host.from_prompt()
        if not new_host:
            return
        hosts = load_hosts()
        if any(h.public_ip == new_host.public_ip and h.port == new_host.port for h in hosts):
            QMessageBox.warning(self, "Duplicate", "A server with the same public IP and port already exists.")
            return
        hosts.append(new_host)
        save_hosts(hosts)
        self._refresh_mine()

    def edit_or_remove(self):
        idx = self.my_list.currentRow()
        if idx < 0:
            QMessageBox.information(self, "Select", "Select a server in 'My Servers' first.")
            return
        hosts = load_hosts()
        host = hosts[idx]

        action, ok = QInputDialog.getText(self, "Edit or Remove", "Type 'edit' to edit fields, 'path' to change game path, or 'remove' to delete:")
        if not ok:
            return
        action = action.strip().lower()
        if action == "remove":
            del hosts[idx]
            save_hosts(hosts)
            self._refresh_mine()
            return
        if action == "path":
            exe = choose_exe_path("Select game EXE for this server")
            if not exe:
                return
            host.exe_path = exe
            hosts[idx] = host
            save_hosts(hosts)
            self._refresh_mine()
            return
        if action != "edit":
            QMessageBox.information(self, "No Change", "Cancelled.")
            return

        edited = Host.from_prompt()
        if not edited:
            return
        edited.id = host.id
        hosts[idx] = edited
        save_hosts(hosts)
        self._refresh_mine()

    # ---------- Launch / Join ----------
    def _ensure_host_exe(self, host: Host) -> Optional[str]:
        if host.exe_path and os.path.exists(host.exe_path):
            return host.exe_path
        exe = choose_exe_path("Set game EXE for this server")
        if not exe:
            return None
        hosts = load_hosts()
        for i, h in enumerate(hosts):
            if h.id == host.id:
                h.exe_path = exe
                hosts[i] = h
                break
        save_hosts(hosts)
        return exe

    def launch_selected(self):
        idx = self.my_list.currentRow()
        if idx >= 0:
            hosts = load_hosts()
            cfg = hosts[idx]
            exe = self._ensure_host_exe(cfg)
            if not exe:
                QMessageBox.critical(self, "Error", "Executable is required for this server.")
                return
            pw, ok = QInputDialog.getText(self, "Join Password", "Enter host password (blank guest):", echo=QLineEdit.Password)
            if ok and pw == cfg.password:
                try:
                    subprocess.Popen([exe, "server", f"{cfg.map}.gear?game=koth?MaxPlayers=10?bots=6?", f"-port={cfg.port}", "-useallavailablecores", "-log"])  # noqa: E501
                    time.sleep(10)
                    subprocess.Popen([exe, f"{cfg.local_ip}:{cfg.port}"])
                except Exception as e:
                    QMessageBox.critical(self, "Launch Error", str(e))
            else:
                try:
                    subprocess.Popen([exe, f"{cfg.public_ip}:{cfg.port}"])
                except Exception as e:
                    QMessageBox.critical(self, "Launch Error", str(e))
            return

        aidx = self.available_list.currentRow()
        if aidx < 0:
            QMessageBox.information(self, "Select", "Select a server to launch/join.")
            return
        text = self.available_list.currentItem().text()
        try:
            endpoint = text.split(" - ", 1)[1]
        except Exception:
            QMessageBox.critical(self, "Parse Error", "Could not parse selected server endpoint.")
            return
        exe = choose_exe_path("Select game EXE to join this server")
        if not exe:
            return
        try:
            subprocess.Popen([exe, endpoint])
        except Exception as e:
            QMessageBox.critical(self, "Launch Error", str(e))

    # ---------- Heartbeat ----------
    def manual_start_heartbeat(self):
        idx = self.my_list.currentRow()
        if idx < 0:
            QMessageBox.information(self, "Select", "Select a server in 'My Servers' to heartbeat.")
            return
        hosts = load_hosts()
        host = hosts[idx]
        if host.id in self.hb_threads and not self.hb_threads[host.id].is_set():
            QMessageBox.information(self, "Already Running", "Heartbeat already running for this server.")
            return
        stop_event = threading.Event()
        self.hb_threads[host.id] = stop_event
        self.hb_color_signal.emit("#ffaa00")
        t = threading.Thread(target=self._hb_loop, args=(host, stop_event), daemon=True)
        t.start()

    def manual_stop_heartbeat(self):
        any_running = False
        for ev in list(self.hb_threads.values()):
            if not ev.is_set():
                ev.set(); any_running = True
        self.hb_threads.clear()
        if any_running:
            self.hb_color_signal.emit("red")
            QMessageBox.information(self, "Stopped", "All heartbeats stopped.")
        else:
            QMessageBox.information(self, "No Active Heartbeat", "No heartbeat is currently running.")

    def _hb_loop(self, host: Host, stop_event: threading.Event):
        failures = 0
        while not stop_event.is_set():
            try:
                payload = {"name": host.name, "public_ip": host.public_ip, "port": int(host.port), "map": host.map}
                r = requests.post(f"{BACKEND_URL}/add_server", json=payload, timeout=3)
                if 200 <= r.status_code < 300:
                    failures = 0
                    self.hb_color_signal.emit("lime")
                else:
                    failures += 1
            except Exception:
                failures += 1
            if failures >= 2:
                self.hb_color_signal.emit("red")
            stop_event.wait(HEARTBEAT_INTERVAL)

    # ---------- Paint/close ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        glow_color = QColor(255, 0, 0, 180)
        for i in range(8, 0, -1):
            glow_color.setAlpha(20 * i)
            painter.setPen(QColor(glow_color))
            painter.drawRoundedRect(self.rect().adjusted(i, i, -i, -i), 12, 12)
        painter.setPen(QColor(255, 0, 0))
        painter.drawRoundedRect(self.rect().adjusted(4, 4, -4, -4), 12, 12)

    def closeEvent(self, event):
        for ev in self.hb_threads.values():
            ev.set()
        try:
            pygame.mixer.stop()
        except Exception:
            pass
        event.accept()

# ---------- Main ----------
if __name__ == "__main__":
    app = QApplication(sys.argv)

    splash_pix_path = resource_path(SPLASH_PNG)
    splash = None
    splash_channel = None
    if os.path.exists(splash_pix_path):
        splash_pix = QPixmap(splash_pix_path)
        splash = QSplashScreen(splash_pix)
        splash.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint)
        splash.setMask(splash_pix.mask())
        splash.show()
        anim = QPropertyAnimation(splash, b"windowOpacity")
        anim.setDuration(1200)
        anim.setStartValue(0.2)
        anim.setEndValue(1.0)
        anim.start()
        sfx = load_sound(MAD_WORLD_WAV)
        if sfx and pygame.mixer.get_init():
            splash_channel = pygame.mixer.Channel(0)
            splash_channel.play(sfx)
        loop = QEventLoop(); QTimer.singleShot(1500, loop.quit); loop.exec_()

    win = JacintoLobbyBrowser()
    win.show()

    if pygame.mixer.get_init():
        bg = load_sound(OMEN_WAV)
        if bg:
            try:
                ch = pygame.mixer.Channel(1)
                ch.play(bg, loops=-1)
            except Exception:
                pass

    if splash:
        splash.finish(win)
        try:
            if splash_channel:
                splash_channel.stop()
        except Exception:
            pass

    sys.exit(app.exec_())
