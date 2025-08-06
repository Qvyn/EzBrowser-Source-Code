# jacinto_ui_redesign.py (full external assets + restored logic)

import sys
import os
import json
import threading
import time
import requests
import subprocess

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QPushButton, QMessageBox, QLineEdit,
    QLabel, QInputDialog, QMenuBar, QMenu, QSplashScreen, QFrame,
    QGraphicsDropShadowEffect, QAction
)
from PyQt5.QtGui import QIcon, QPixmap, QFont, QColor, QPainter
from PyQt5.QtCore import QTimer, Qt, QPropertyAnimation, QEventLoop

import pygame
pygame.mixer.init()

BACKEND_URL = "https://jacinto-server.fly.dev"
HEARTBEAT_INTERVAL = 5
CONFIG_FILENAME = "owners.json"
OMEN_WAV = "return-of-the-omen-fixed.wav"
MAD_WORLD_WAV = "mad-world-fixed.wav"
COG_TAG_WAV = "gears-of-war-cog-tag-fixed.wav"

class JacintoLobbyBrowser(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jacinto 1.2 Server Browser")
        self.setWindowIcon(QIcon("Jacinto.ico"))
        self.setGeometry(200, 100, 800, 600)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(self.load_dark_theme())
        self.heartbeat_threads = []
        self.server_path = self.load_owner_path()
        self.is_muted = False

        self.click_sound = pygame.mixer.Sound(COG_TAG_WAV)
        self.click_sound.set_volume(0.3)
        self.background_music = pygame.mixer.Sound(OMEN_WAV)
        self.background_music.set_volume(0.2)
        self.bg_channel = pygame.mixer.Channel(1)
        self.fx_channel = pygame.mixer.Channel(2)

        self.setup_ui()
        self.ask_if_host()
        self.refresh_loop()

    def play_click(self):
        if self.is_muted:
            return
        if self.bg_channel.get_busy():
            self.bg_channel.pause()
        self.fx_channel.play(self.click_sound)
        while self.fx_channel.get_busy():
            pygame.time.wait(10)
        self.bg_channel.unpause()

    def start_background_music(self):
        if not self.is_muted:
            self.bg_channel.play(self.background_music, loops=-1)

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        pygame.mixer.pause() if self.is_muted else pygame.mixer.unpause()

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

    def load_dark_theme(self):
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

    def add_glow(self, button: QPushButton):
        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(20)
        glow.setOffset(0)
        glow.setColor(QColor(255, 0, 0, 160))
        button.setGraphicsEffect(glow)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.setLayout(layout)

        menubar = QMenuBar(self)
        hb_menu = QMenu("Heartbeat", self)
        hb_menu.addAction("Start Heartbeat", self.manual_start_heartbeat)
        hb_menu.addAction("Stop Heartbeat", self.manual_stop_heartbeat)
        menubar.addMenu(hb_menu)

        audio_menu = QMenu("Audio", self)
        mute_action = QAction("Toggle Mute", self)
        mute_action.triggered.connect(self.toggle_mute)
        audio_menu.addAction(mute_action)
        menubar.addMenu(audio_menu)

        layout.setMenuBar(menubar)

        self.server_list = QListWidget()
        self.server_list.setFrameShape(QFrame.Panel)
        self.server_list.setFrameShadow(QFrame.Sunken)
        self.server_list.setStyleSheet("background: transparent;")
        layout.addWidget(self.server_list)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("Add Your Server")
        self.edit_btn = QPushButton("Edit/Remove Server")
        self.launch_btn = QPushButton("Launch Selected")

        for btn in [self.add_btn, self.edit_btn, self.launch_btn]:
            self.add_glow(btn)
            btn.clicked.connect(self.play_click)

        self.add_btn.clicked.connect(self.add_server)
        self.edit_btn.clicked.connect(self.remove_server)
        self.launch_btn.clicked.connect(self.launch_server)

        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.launch_btn)
        layout.addLayout(btn_layout)

        hb_frame = QHBoxLayout()
        hb_frame.addWidget(QLabel("Heartbeat:"))
        self.hb_status = QLabel()
        self.hb_status.setFixedSize(20, 20)
        self.set_hb_color("red")
        hb_frame.addWidget(self.hb_status)
        layout.addLayout(hb_frame)

        self.hb_timer = QTimer(self)
        self.hb_timer.timeout.connect(self.check_heartbeat_status)
        self.hb_timer.start(3000)

    def set_hb_color(self, color):
        self.hb_status.setStyleSheet(f"background-color: {color}; border-radius: 10px;")

    def check_heartbeat_status(self):
        try:
            r = requests.get(f"{BACKEND_URL}/servers", timeout=2)
            servers = r.json()
            found = any("Caleb's Server" in s.get("name", "") for s in servers)
            self.set_hb_color("lime" if found else "red")
        except:
            self.set_hb_color("red")

    def refresh_loop(self):
        self.update_server_list()
        QTimer.singleShot(5000, self.refresh_loop)

    def update_server_list(self):
        try:
            res = requests.get(f"{BACKEND_URL}/servers")
            self.server_list.clear()
            for s in res.json():
                self.server_list.addItem(f"{s['name']} - {s['public_ip']}:{s['port']}")
        except Exception as e:
            print("[Server List Error]", e)

    def get_public_ip(self):
        try:
            return requests.get("https://api.ipify.org").text
        except:
            return "0.0.0.0"

    def ask_if_host(self):
        reply = QMessageBox.question(self, "Are you the host?", "Are you the host?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            pw, ok = QInputDialog.getText(self, "Password", "Enter host password:", echo=QLineEdit.Password)
            if ok and pw == "1207706":
                if os.path.exists("host_config.json"):
                    with open("host_config.json") as f:
                        cfg = json.load(f)
                    self.launch_heartbeat(cfg["name"], cfg["public_ip"], cfg["port"], cfg["map"])

    def add_server(self):
        name, ok1 = QInputDialog.getText(self, "Server Name", "Enter your server name:")
        public_ip, ok2 = QInputDialog.getText(self, "Public IP", "Enter your public IP:")
        local_ip, ok3 = QInputDialog.getText(self, "Local IP", "Enter your local IP:")
        port, ok4 = QInputDialog.getText(self, "Port", "Enter your server port:")
        map_name, ok5 = QInputDialog.getText(self, "Map", "Enter map name:")
        pw, ok6 = QInputDialog.getText(self, "Password", "Set a host password:", echo=QLineEdit.Password)

        if not all([ok1, name, ok2, public_ip, ok3, local_ip, ok4, port, ok5, map_name, ok6, pw]):
            QMessageBox.critical(self, "Error", "All fields are required.")
            return

        config = {"name": name, "public_ip": public_ip, "local_ip": local_ip, "port": int(port), "map": map_name, "password": pw}
        with open("host_config.json", "w") as f:
            json.dump(config, f)

        self.launch_heartbeat(name, public_ip, port, map_name)

    def launch_heartbeat(self, name, ip, port, map_name):
        stop_event = threading.Event()
        thread = threading.Thread(target=self.send_heartbeat, args=(stop_event, name, ip, port, map_name), daemon=True)
        thread.start()
        self.heartbeat_threads.append(stop_event)

    def send_heartbeat(self, stop_event, name, ip, port, map_name):
        while not stop_event.is_set():
            try:
                data = {"name": name, "public_ip": ip, "port": int(port), "map": map_name}
                res = requests.post(f"{BACKEND_URL}/add_server", json=data)
                print(f"[Heartbeat] Sent: {res.status_code} - {res.text}")
            except Exception as e:
                print("[Heartbeat Error]", e)
            stop_event.wait(HEARTBEAT_INTERVAL)

    def remove_server(self):
        if not os.path.exists("host_config.json"):
            QMessageBox.information(self, "Info", "No server configuration found.")
            return
        os.remove("host_config.json")
        QMessageBox.information(self, "Removed", "Server configuration removed.")

    def launch_server(self):
        if not os.path.exists(CONFIG_FILENAME):
            path, ok = QInputDialog.getText(self, "Executable Path", "Enter Jacinto 1.2 path:")
            if not ok or not os.path.exists(path):
                QMessageBox.critical(self, "Error", "Executable not found.")
                return
            json.dump({"exe_path": path}, open(CONFIG_FILENAME, "w"))

        with open(CONFIG_FILENAME, "r") as f:
            exe_path = json.load(f)["exe_path"]

        if not os.path.exists("host_config.json"):
            QMessageBox.critical(self, "Error", "No host config. Use Add Your Server.")
            return

        with open("host_config.json", "r") as f:
            cfg = json.load(f)

        pw, ok = QInputDialog.getText(self, "Join Password", "Enter host password (blank guest):", echo=QLineEdit.Password)

        if ok and pw == cfg["password"]:
            args = [exe_path, "server", f"{cfg['map']}.gear?game=koth?MaxPlayers=10?bots=6?", f"-port={cfg['port']}", "-useallavailablecores", "-log"]
            try:
                subprocess.Popen(args)
                time.sleep(10)
                subprocess.Popen([exe_path, f"{cfg['local_ip']}:{cfg['port']}"])
            except Exception as e:
                QMessageBox.critical(self, "Launch Error", str(e))
        else:
            try:
                subprocess.Popen([exe_path, f"{cfg['public_ip']}:{cfg['port']}"])
            except Exception as e:
                QMessageBox.critical(self, "Launch Error", str(e))

    def manual_start_heartbeat(self):
        if not os.path.exists("host_config.json"):
            QMessageBox.critical(self, "Error", "No host config found. Use 'Add Your Server' first.")
            return

        with open("host_config.json", "r") as f:
            cfg = json.load(f)
        self.launch_heartbeat(cfg["name"], cfg["public_ip"], cfg["port"], cfg["map"])

    def manual_stop_heartbeat(self):
        if self.heartbeat_threads:
            for stop_event in self.heartbeat_threads:
                stop_event.set()
            self.heartbeat_threads.clear()
            QMessageBox.information(self, "Stopped", "Heartbeat has been stopped.")
        else:
            QMessageBox.information(self, "No Active Heartbeat", "No heartbeat is currently running.")

    def load_owner_path(self):
        if os.path.exists(CONFIG_FILENAME):
            try:
                with open(CONFIG_FILENAME, "r") as f:
                    return json.load(f).get("exe_path")
            except:
                return None

    def closeEvent(self, event):
        for stop_event in self.heartbeat_threads:
            stop_event.set()
        pygame.mixer.music.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)

    splash_path = "splash.png"
    if os.path.exists(splash_path):
        splash_pix = QPixmap(splash_path)
        splash = QSplashScreen(splash_pix)
        splash.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint)
        splash.setMask(splash_pix.mask())
        splash.show()

        try:
            splash_channel = pygame.mixer.Channel(0)
            splash_sound = pygame.mixer.Sound(MAD_WORLD_WAV)
            splash_channel.play(splash_sound)
        except Exception as e:
            print("[Splash Sound Error]", e)

        animation = QPropertyAnimation(splash, b"windowOpacity")
        animation.setDuration(2000)
        animation.setStartValue(0.3)
        animation.setEndValue(1.0)
        animation.setLoopCount(2)
        animation.start()

        loop = QEventLoop()
        QTimer.singleShot(2500, loop.quit)
        loop.exec_()

    win = JacintoLobbyBrowser()
    win.show()

    if 'splash_channel' in locals():
        splash_channel.stop()
        win.start_background_music()

    if os.path.exists(splash_path):
        splash.finish(win)

    sys.exit(app.exec_())
