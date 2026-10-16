"""讨论功能后台线程：聊天 / 提案 / 采纳写入，均不在主线程跑。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from ...core.discussion.agent import DiscussionAgent
from ...core.discussion.proposal import apply_proposal
from ...core.llm_client import LLMClient
from ...core.memory.engine import MemoryEngine


class DiscussionWorker(QThread):
    progress = pyqtSignal(str)
    stream = pyqtSignal(str)
    finished_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        agent: DiscussionAgent,
        mode: str,  # chat | propose | apply
        history: list[dict],
        user_msg: str,
        context: str = "",
        proposal: Optional[dict] = None,
        project_root: str = "",
        memory: Optional[MemoryEngine] = None,
        llm_client: Optional[LLMClient] = None,
        chapter_id: int = 1,
        parent=None,
    ):
        super().__init__(parent)
        self._agent = agent
        self._mode = mode
        self._history = history
        self._user_msg = user_msg
        self._context = context
        self._proposal = proposal
        self._project_root = project_root
        self._memory = memory
        self._llm_client = llm_client
        self._chapter_id = chapter_id

    def run(self):
        try:
            if self._mode == "chat":
                msgs = self._agent.build_messages(self._history, self._user_msg, self._context)
                buf = []
                for chunk in self._agent.stream_chat(msgs):
                    buf.append(chunk)
                    self.stream.emit(chunk)
                self.finished_signal.emit({"type": "chat", "content": "".join(buf)})
            elif self._mode == "propose":
                msgs = self._agent.build_messages(self._history, self._user_msg, self._context)
                data = self._agent.propose(msgs)
                self.finished_signal.emit({"type": "propose", "proposal": data})
            elif self._mode == "apply":
                result = apply_proposal(
                    self._memory, self._project_root, self._proposal,
                    self._llm_client, self._chapter_id,
                )
                self.finished_signal.emit({"type": "apply", "result": result})
            elif self._mode == "agent_loop":
                # 三阶段流水线：规划 → 检索（留证据）→ 思考；阶段进度通过 progress 信号透传给 UI
                data = self._agent.run_three_stage(
                    self._user_msg, self._history,
                    current_chapter_id=self._chapter_id or None,
                    on_stage=lambda stage, msg: self.progress.emit(msg),
                )
                self.finished_signal.emit({"type": "agent_loop", "result": data})
        except Exception as e:
            self.error_signal.emit(str(e))
