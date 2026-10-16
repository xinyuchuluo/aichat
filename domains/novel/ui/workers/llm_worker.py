"""
LLM Worker - QThread 后台调用 LLM。

避免在主线程中调用 LLM 导致 UI 卡顿。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from ...core.pipeline.writing import WritingPipeline, PipelineResult, PipelineStage
from ...core.memory.engine import MemoryEngine
from ...core.memory.archive_layer import ArchiveLayer
from ...core.llm_client import LLMClient


class WritingWorker(QThread):
    """写作流水线后台线程。"""

    progress = pyqtSignal(str)  # 阶段名称
    stream = pyqtSignal(str)    # 流式文本
    finished_signal = pyqtSignal(object)  # PipelineResult

    def __init__(
        self,
        pipeline: WritingPipeline,
        chapter_id: int,
        chapter_title: str,
        writing_instruction: str,
        config: dict,
        mode: str = "write",  # write | continue | rewrite
        existing_text: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._chapter_id = chapter_id
        self._chapter_title = chapter_title
        self._writing_instruction = writing_instruction
        self._config = config
        self._mode = mode
        self._existing_text = existing_text
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        on_progress = lambda stage: self.progress.emit(stage.value)
        on_stream = lambda chunk: self.stream.emit(chunk)

        if self._mode == "continue":
            result = self._pipeline.continue_writing(
                chapter_id=self._chapter_id,
                chapter_title=self._chapter_title,
                existing_text=self._existing_text,
                continue_instruction=self._writing_instruction,
                config=self._config,
                on_progress=on_progress,
                on_stream=on_stream,
            )
        elif self._mode == "rewrite":
            result = self._pipeline.rewrite_chapter(
                chapter_id=self._chapter_id,
                chapter_title=self._chapter_title,
                writing_instruction=self._writing_instruction,
                config=self._config,
                on_progress=on_progress,
                on_stream=on_stream,
                problem_mode=True,
            )
        else:
            result = self._pipeline.write_chapter(
                chapter_id=self._chapter_id,
                chapter_title=self._chapter_title,
                writing_instruction=self._writing_instruction,
                config=self._config,
                on_progress=on_progress,
                on_stream=on_stream,
            )

        self.finished_signal.emit(result)


class FactExtractionWorker(QThread):
    """手动触发事实提取的后台线程。"""

    finished_signal = pyqtSignal(dict)  # facts
    error_signal = pyqtSignal(str)

    def __init__(self, archive: ArchiveLayer, chapter_id: int, chapter_text: str, parent=None):
        super().__init__(parent)
        self._archive = archive
        self._chapter_id = chapter_id
        self._chapter_text = chapter_text

    def run(self):
        try:
            facts = self._archive.extract_facts(self._chapter_id, self._chapter_text)
            report = self._archive.apply_facts(self._chapter_id, facts)
            self.finished_signal.emit({"facts": facts, "report": report})
        except Exception as e:
            self.error_signal.emit(str(e))


class TestConnectionWorker(QThread):
    """测试API连接的后台线程。"""

    finished_signal = pyqtSignal(bool, str)

    def __init__(self, llm_client: LLMClient, endpoint_name: Optional[str] = None, test_embed: bool = False, parent=None):
        super().__init__(parent)
        self._llm = llm_client
        self._endpoint = endpoint_name
        self._test_embed = test_embed

    def run(self):
        if self._test_embed:
            success, msg = self._llm.test_embedding(self._endpoint)
        else:
            success, msg = self._llm.test_connection(self._endpoint)
        self.finished_signal.emit(success, msg)
