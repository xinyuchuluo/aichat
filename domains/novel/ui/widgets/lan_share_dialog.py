"""局域网分享阅读对话框。

点击「📱 手机阅读」后弹出：显示手机访问地址、二维码（扫码即开）、复制、本机预览、停止分享。
打开即启动服务，关闭即停止服务（只读，端口会在关闭时释放）。
"""
from __future__ import annotations

import base64

from PyQt6.QtCore import Qt, QByteArray, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QMessageBox,
)

from domains.novel.core.lan.server import LanViewerServer
from domains.novel.ui.styles import (
    BTN_PRIMARY, BTN_SECONDARY, BTN_DANGER, HINT_STYLE,
)


class LanShareDialog(QDialog):
    def __init__(self, lan_server: LanViewerServer, parent=None):
        super().__init__(parent)
        self._server = lan_server
        self.setWindowTitle("📱 局域网分享阅读")
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        tip = QLabel(
            "手机 / 平板浏览器扫码即可打开阅读页（正文 + 章节大纲，只读）。\n"
            "请确保手机与电脑连接在同一 Wi‑Fi / 局域网下。"
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # —— 二维码（扫码即开，IP 变化也无所谓）——
        qr_title = QLabel("手机扫码即开：")
        qr_title.setStyleSheet(HINT_STYLE)
        lay.addWidget(qr_title)

        self._qr_label = QLabel()
        self._qr_label.setStyleSheet(
            "background:#ffffff; border-radius:6px; padding:8px;"
        )
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(220, 220)
        lay.addWidget(self._qr_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # —— 手机访问地址（局域网 IP，兼容性最好）——
        lan_title = QLabel("或手动输入地址（当前局域网 IP）：")
        lan_title.setStyleSheet(HINT_STYLE)
        lay.addWidget(lan_title)

        self._lan_label = QLabel()
        self._lan_label.setStyleSheet(
            "font-size:18px; font-weight:bold; color:#89b4fa; padding:6px 0;"
        )
        self._lan_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._lan_label.setWordWrap(True)
        lay.addWidget(self._lan_label)

        row = QHBoxLayout()
        copy_lan_btn = QPushButton("复制手机访问地址")
        copy_lan_btn.setStyleSheet(BTN_PRIMARY)
        copy_lan_btn.clicked.connect(self._copy_lan)
        preview_btn = QPushButton("本机预览")
        preview_btn.setStyleSheet(BTN_SECONDARY)
        preview_btn.clicked.connect(self._preview)
        row.addWidget(copy_lan_btn)
        row.addWidget(preview_btn)
        lay.addLayout(row)

        # —— 补充：电脑名网址（部分网络 / 苹果设备可用）——
        fixed_title = QLabel("补充（电脑名网址，部分安卓/路由不支持 mDNS，扫/输上面的 IP 即可）：")
        fixed_title.setStyleSheet(HINT_STYLE)
        fixed_title.setWordWrap(True)
        lay.addWidget(fixed_title)

        self._fixed_label = QLabel()
        self._fixed_label.setStyleSheet(HINT_STYLE)
        self._fixed_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._fixed_label.setWordWrap(True)
        lay.addWidget(self._fixed_label)

        copy_fixed_btn = QPushButton("复制电脑名网址（补充）")
        copy_fixed_btn.setStyleSheet(BTN_SECONDARY)
        copy_fixed_btn.clicked.connect(self._copy_fixed)
        lay.addWidget(copy_fixed_btn)

        hint = QLabel(
            "提示：手机浏览器直接输入地址即可，无需安装任何 App；端口默认 8765，可在「设置 → 写作参数」修改。"
        )
        hint.setStyleSheet(HINT_STYLE)
        lay.addWidget(hint)

        stop_btn = QPushButton("停止分享并关闭")
        stop_btn.setStyleSheet(BTN_DANGER)
        stop_btn.clicked.connect(self.reject)
        lay.addWidget(stop_btn)

    def _refresh(self):
        # 二维码（编码当前局域网 IP 网址，扫码永远是当前正确地址）
        try:
            uri = self._server.make_qr_data_uri(self._server.lan_url)
            b64 = uri.split(",", 1)[1]
            pix = QPixmap()
            pix.loadFromData(QByteArray(base64.b64decode(b64)))
            self._qr_label.setPixmap(pix)
        except Exception as e:  # noqa: BLE001
            self._qr_label.setText(f"二维码生成失败：{e}")

        self._lan_label.setText(self._server.lan_url)
        self._fixed_label.setText(f"电脑名网址：{self._server.fixed_url}　|　本机预览：{self._server.local_url}")

    def _copy_fixed(self):
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._server.fixed_url)
            self._fixed_label.setText("已复制电脑名网址到剪贴板 ✓")

    def _copy_lan(self):
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._server.lan_url)
            self._fixed_label.setText("已复制手机访问地址到剪贴板 ✓")

    def _preview(self):
        QDesktopServices.openUrl(QUrl(self._server.local_url))

    def exec(self):
        if not self._server.is_running:
            try:
                self._server.start()
            except OSError as e:
                QMessageBox.critical(
                    self, "启动失败",
                    f"无法在端口 {self._server.port} 启动分享服务：\n{e}\n\n"
                    "可能是端口被占用，可在「设置 → 写作参数」中修改局域网端口后重试。"
                )
                return
        self._refresh()
        super().exec()

    def reject(self):
        self._server.stop()
        super().reject()
