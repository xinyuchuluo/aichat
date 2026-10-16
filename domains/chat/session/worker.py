"""聊天后台 Worker — QThread，避免 UI 卡死。"""
from __future__ import annotations
from PyQt6.QtCore import QThread, pyqtSignal
from ..protocol.types import Session


class ChatWorker(QThread):
    """后台聊天 Worker。"""

    chunk_ready = pyqtSignal(str)   # 流式块
    done = pyqtSignal(str)          # 完整响应
    error = pyqtSignal(str)         # 错误

    def __init__(self, agent, session: Session, user_text: str,
                 memory_results=None, parent=None):
        super().__init__(parent)
        self._agent = agent
        self._session = session
        self._user_text = user_text
        self._memory_results = memory_results

    def run(self):
        try:
            full = ""
            for event in self._agent.chat(
                self._session, self._user_text, self._memory_results
            ):
                etype = event.get("type", "")
                content = event.get("content", "")
                if etype == "chunk":
                    full += content
                    self.chunk_ready.emit(content)
                elif etype == "done":
                    full = content if content else full
                    self.done.emit(full)
                elif etype == "error":
                    self.error.emit(content)
                    return
            if full:
                self.done.emit(full)
        except Exception as e:
            self.error.emit(str(e))
