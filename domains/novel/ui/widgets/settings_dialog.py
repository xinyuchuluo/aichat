"""API 设置对话框。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QLabel, QGroupBox, QSpinBox,
    QDoubleSpinBox, QMessageBox, QTabWidget, QWidget, QCheckBox,
    QTextEdit, QPlainTextEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal

from domains.novel.config import Config, LLMEndpoint
from domains.novel.core.project.manager import NovelProject
from domains.novel.core.pipeline.style_presets import (
    STYLE_PRESETS, preset_keys, UNSPECIFIED, CUSTOM, resolve_style,
)
from domains.novel.core.pipeline.tag_system import (
    TagSpec, KIND_LABELS, list_genres, get_genre_tags, get_universal_process_tags,
)
from domains.novel.ui.workers.llm_worker import TestConnectionWorker
from domains.novel.ui.styles import SKINS, get_skin


class _TagEditor(QWidget):
    """结构化标签编辑器：列表 + 名称/类别/定义/贯穿锚点字段，支持增删。

    维护 self.specs: list[TagSpec]，get_specs() 返回去空名后的规格列表。
    """

    def __init__(self, specs: list, parent=None, is_process: bool = False):
        super().__init__(parent)
        self.specs = [TagSpec.from_any(s) for s in (specs or [])]
        self._is_process = is_process
        self._build_ui()
        self._refresh_list()
        if self.specs:
            self.list_widget.setCurrentRow(0)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 顶部工具行：从系统级题材库一键加载预设标签
        top = QHBoxLayout()
        top.addWidget(QLabel("从系统题材库加载:"))
        self._genre_combo = QComboBox()
        self._genre_combo.addItems(list_genres())
        top.addWidget(self._genre_combo)
        load_genre_btn = QPushButton("加载该题材标签")
        load_genre_btn.clicked.connect(self._on_load_genre)
        top.addWidget(load_genre_btn)
        load_proc_btn = QPushButton("加载通用流程")
        load_proc_btn.clicked.connect(self._on_load_process)
        top.addWidget(load_proc_btn)
        top.addStretch()
        layout.addLayout(top)

        # 中部：左侧列表 + 右侧字段
        mid = QHBoxLayout()
        # 左侧：标签列表
        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_select)
        left.addWidget(self.list_widget)
        btn_row = QHBoxLayout()
        add_from_lib_btn = QPushButton("从选项新增")
        add_from_lib_btn.clicked.connect(self._on_add_from_lib)
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._on_del)
        btn_row.addWidget(add_from_lib_btn)
        btn_row.addWidget(del_btn)
        left.addLayout(btn_row)
        mid.addLayout(left, 1)

        # 右侧：字段编辑
        right = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("如：混沌神体体系")
        self.name_edit.textChanged.connect(self._on_field_changed)
        right.addRow("名称:", self.name_edit)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(list(KIND_LABELS.keys()))
        self.kind_combo.currentTextChanged.connect(self._on_field_changed)
        right.addRow("类别:", self.kind_combo)

        self.doctrine_edit = QTextEdit()
        self.doctrine_edit.setPlaceholderText(
            "该标签在本作中的实质定义（体系/线索的核心，会注入每章系统提示并落地档案）"
        )
        self.doctrine_edit.setMaximumHeight(120)
        self.doctrine_edit.textChanged.connect(self._on_field_changed)
        right.addRow("定义:", self.doctrine_edit)

        self.anchors_edit = QPlainTextEdit()
        self.anchors_edit.setPlaceholderText("线索类：贯穿全书的锚点，每行一个（如：前期…/中期…/末期…）")
        self.anchors_edit.setMaximumHeight(120)
        self.anchors_edit.textChanged.connect(self._on_field_changed)
        right.addRow("贯穿锚点:", self.anchors_edit)

        right_widget = QWidget()
        right_widget.setLayout(right)
        mid.addWidget(right_widget, 2)
        layout.addLayout(mid)

    def _refresh_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for s in self.specs:
            self.list_widget.addItem(f"{s.name}（{KIND_LABELS.get(s.kind, s.kind)}）")
        self.list_widget.blockSignals(False)

    def _on_select(self, row: int):
        if 0 <= row < len(self.specs):
            s = self.specs[row]
            self.name_edit.setText(s.name)
            self.kind_combo.setCurrentText(s.kind if s.kind in KIND_LABELS else "套路")
            self.doctrine_edit.setPlainText(s.doctrine)
            self.anchors_edit.setPlainText("\n".join(s.anchors))
        else:
            self.name_edit.clear()
            self.doctrine_edit.clear()
            self.anchors_edit.clear()

    def _on_field_changed(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.specs):
            s = self.specs[row]
            s.name = self.name_edit.text().strip()
            s.kind = self.kind_combo.currentText()
            s.doctrine = self.doctrine_edit.toPlainText().strip()
            s.anchors = [a.strip() for a in self.anchors_edit.toPlainText().splitlines() if a.strip()]
            self.list_widget.blockSignals(True)
            self.list_widget.item(row).setText(f"{s.name}（{KIND_LABELS.get(s.kind, s.kind)}）")
            self.list_widget.blockSignals(False)

    def _on_add_from_lib(self):
        """从系统标签库选项中勾选新增（而非自由手填一个名字）。

        content 编辑器只列内容类（排除流程），process 编辑器只列流程类。
        """
        kind_filter = "process" if self._is_process else "content"
        existing = {s.name for s in self.specs}
        from domains.novel.ui.widgets.tag_picker import pick_tags
        picked = pick_tags(self, existing_names=existing, kind_filter=kind_filter)
        if not picked:
            return
        added = 0
        for spec in picked:
            if spec.name in existing:
                continue
            self.specs.append(TagSpec.from_any(spec.to_dict()))
            existing.add(spec.name)
            added += 1
        if added:
            self._refresh_list()
            self.list_widget.setCurrentRow(len(self.specs) - 1)

    def _on_del(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.specs):
            spec = self.specs[row]
            if getattr(spec, "required", False):
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "不可删除",
                    f"「{spec.name}」是本题材强制必选的核心体系标签，不可删除。"
                    f"删除会导致全书设定骨架缺失。"
                )
                return
            self.specs.pop(row)
            self._refresh_list()
            if self.specs:
                self.list_widget.setCurrentRow(min(row, len(self.specs) - 1))

    def _on_load_genre(self):
        """从系统级题材库加载该题材的预设内容标签（去重追加）。"""
        genre = self._genre_combo.currentText().strip()
        existing = {s.name for s in self.specs}
        added = 0
        for spec in get_genre_tags(genre):
            if spec.name not in existing:
                self.specs.append(TagSpec.from_any(spec.to_dict()))
                existing.add(spec.name)
                added += 1
        if added:
            self._refresh_list()
            self.list_widget.setCurrentRow(len(self.specs) - 1)

    def _on_load_process(self):
        """加载所有题材通用的流程约束标签（去重追加）。"""
        existing = {s.name for s in self.specs}
        added = 0
        for spec in get_universal_process_tags():
            if spec.name not in existing:
                self.specs.append(TagSpec.from_any(spec.to_dict()))
                existing.add(spec.name)
                added += 1
        if added:
            self._refresh_list()
            self.list_widget.setCurrentRow(len(self.specs) - 1)

    def get_specs(self) -> list:
        return [s for s in self.specs if s.name]


class SettingsDialog(QDialog):
    """API 配置对话框。"""

    config_saved = pyqtSignal(Config)

    def __init__(self, config: Config, parent=None,
                 project: Optional[NovelProject] = None,
                 project_manager=None):
        super().__init__(parent)
        self._config = config
        self._project = project
        self._project_manager = project_manager
        self._endpoints: list[LLMEndpoint] = [LLMEndpoint(
            name=e.name, base_url=e.base_url, api_key=e.api_key,
            models=list(e.models), is_default=e.is_default
        ) for e in config.endpoints]

        self._test_worker: TestConnectionWorker | None = None

        self.setWindowTitle("设置 - NovelForge")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)

        self._init_ui()
        self._refresh_endpoint_list()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Tab 切换
        tabs = QTabWidget()

        # ===== API 端点 Tab =====
        api_tab = QWidget()
        api_layout = QHBoxLayout(api_tab)

        # 左侧：端点列表
        left = QVBoxLayout()
        left.addWidget(QLabel("API 端点列表"))
        self.endpoint_list = QListWidget()
        self.endpoint_list.currentRowChanged.connect(self._on_endpoint_selected)
        left.addWidget(self.endpoint_list)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._on_add_endpoint)
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._on_del_endpoint)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(del_btn)
        left.addLayout(btn_layout)

        api_layout.addLayout(left, 1)

        # 右侧：端点详情
        right = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("如: qiniu, sufy")
        right.addRow("端点名称:", self.name_input)

        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("如: https://api.qnaigc.com/v1")
        right.addRow("Base URL:", self.base_url_input)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-xxx")
        right.addRow("API Key:", self.api_key_input)

        self.models_input = QLineEdit()
        self.models_input.setPlaceholderText("逗号分隔，如: deepseek-chat, qwen-max")
        right.addRow("可用模型:", self.models_input)

        self.default_checkbox = QCheckBox("设为默认端点")
        right.addRow(self.default_checkbox)

        # 测试按钮
        test_layout = QHBoxLayout()
        self.test_chat_btn = QPushButton("测试聊天连接")
        self.test_chat_btn.clicked.connect(self._on_test_chat)
        self.test_embed_btn = QPushButton("测试Embedding")
        self.test_embed_btn.clicked.connect(self._on_test_embed)
        test_layout.addWidget(self.test_chat_btn)
        test_layout.addWidget(self.test_embed_btn)
        right.addRow(test_layout)

        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)
        right.addRow(self.test_result_label)

        self.save_endpoint_btn = QPushButton("保存此端点")
        self.save_endpoint_btn.clicked.connect(self._on_save_endpoint)
        right.addRow(self.save_endpoint_btn)

        api_layout.addLayout(right, 2)

        tabs.addTab(api_tab, "API 端点")

        # ===== 模型配置 Tab =====
        model_tab = QWidget()
        model_layout = QFormLayout(model_tab)

        self.chat_model_input = QLineEdit()
        self.chat_model_input.setPlaceholderText("如: deepseek-chat")
        self.chat_model_input.setText(self._config.default_chat_model)
        model_layout.addRow("默认聊天模型:", self.chat_model_input)

        self.embed_model_input = QLineEdit()
        self.embed_model_input.setPlaceholderText("如: text-embedding-3-small（留空则用BM25检索）")
        self.embed_model_input.setText(self._config.default_embed_model)
        model_layout.addRow("默认Embedding模型:", self.embed_model_input)

        self.embed_endpoint_input = QLineEdit()
        self.embed_endpoint_input.setPlaceholderText("留空则用默认端点")
        self.embed_endpoint_input.setText(self._config.default_embed_endpoint)
        model_layout.addRow("Embedding端点名:", self.embed_endpoint_input)

        tabs.addTab(model_tab, "模型配置")

        # ===== 写作参数 Tab =====
        param_tab = QWidget()
        param_layout = QFormLayout(param_tab)

        self.temp_input = QDoubleSpinBox()
        self.temp_input.setRange(0.0, 2.0)
        self.temp_input.setSingleStep(0.1)
        self.temp_input.setValue(self._config.temperature)
        param_layout.addRow("温度 (temperature):", self.temp_input)

        self.max_tokens_input = QSpinBox()
        self.max_tokens_input.setRange(256, 32768)
        self.max_tokens_input.setSingleStep(256)
        self.max_tokens_input.setValue(self._config.max_tokens)
        param_layout.addRow("最大生成Token:", self.max_tokens_input)

        self.context_window_input = QSpinBox()
        self.context_window_input.setRange(4096, 1000000)
        self.context_window_input.setSingleStep(1000)
        self.context_window_input.setValue(self._config.context_window)
        param_layout.addRow("上下文窗口大小:", self.context_window_input)

        self.recent_summary_input = QSpinBox()
        self.recent_summary_input.setRange(1, 30)
        self.recent_summary_input.setValue(self._config.recent_summary_count)
        param_layout.addRow("注入最近N章摘要:", self.recent_summary_input)

        self.rag_topk_input = QSpinBox()
        self.rag_topk_input.setRange(1, 30)
        self.rag_topk_input.setValue(self._config.rag_top_k)
        param_layout.addRow("RAG检索Top-K:", self.rag_topk_input)

        self.chunk_size_input = QSpinBox()
        self.chunk_size_input.setRange(100, 2000)
        self.chunk_size_input.setSingleStep(50)
        self.chunk_size_input.setValue(self._config.chunk_size)
        param_layout.addRow("文本分块大小:", self.chunk_size_input)

        self.validation_level_combo = QComboBox()
        self.validation_level_combo.addItems([
            "严格（任何问题都重写）",
            "标准（中/高严重度才重写）",
            "宽松（仅高严重度重写）",
            "仅报告（不自动重写）",
        ])
        _level_map = {"strict": 0, "normal": 1, "lenient": 2, "off": 3}
        self.validation_level_combo.setCurrentIndex(_level_map.get(self._config.validation_level, 1))
        param_layout.addRow("校验等级:", self.validation_level_combo)

        self.adjacent_window_input = QSpinBox()
        self.adjacent_window_input.setRange(0, 10)
        self.adjacent_window_input.setToolTip("写每章时，额外注入前后各 N 章的完整大纲（核心事件/钩子/伏笔），防止小范围吃书。0=不注入。")
        self.adjacent_window_input.setValue(self._config.adjacent_outline_window)
        param_layout.addRow("注入前后N章大纲:", self.adjacent_window_input)

        self.lan_port_input = QSpinBox()
        self.lan_port_input.setRange(1024, 65535)
        self.lan_port_input.setToolTip("「📱 手机阅读」局域网分享服务监听的端口。若被占用可在路由器/防火墙放行，或改此端口后重启软件。")
        self.lan_port_input.setValue(self._config.lan_port)
        param_layout.addRow("局域网分享端口:", self.lan_port_input)

        self.char_inject_input = QSpinBox()
        self.char_inject_input.setRange(1, 60)
        self.char_inject_input.setToolTip("单章注入的人物档案上限。长篇小说人物可能上百，全注入既爆 token 又淹没主角；只注入与本章程相关 + 近期在场者。")
        self.char_inject_input.setValue(self._config.character_inject_max)
        param_layout.addRow("单章人物上限:", self.char_inject_input)

        self.foreshadow_input = QSpinBox()
        self.foreshadow_input.setRange(0, 200)
        self.foreshadow_input.setToolTip("活跃伏笔注入上限（0=不限制）。按优先级保留前 N 条，防长线作品伏笔无限膨胀。")
        self.foreshadow_input.setValue(self._config.foreshadow_max)
        param_layout.addRow("活跃伏笔上限:", self.foreshadow_input)

        tabs.addTab(param_tab, "写作参数")

        # ===== 质量增强 Tab =====
        quality_tab = QWidget()
        quality_layout = QFormLayout(quality_tab)

        self.quality_tier_combo = QComboBox()
        self.quality_tier_combo.addItems([
            "简洁（仅 L0/L1 硬约束，写作提示最短，速度最快）",
            "标准（注入角色声音等少量 L2 模块）",
            "极致（注入全部质量增强模块，小说最精彩，提示最长）",
        ])
        _tier_map = {"concise": 0, "standard": 1, "ultimate": 2}
        self.quality_tier_combo.setCurrentIndex(_tier_map.get(self._config.quality_tier, 1))
        self.quality_tier_combo.setToolTip(
            "质量档位控制写作系统提示里注入多少「质量增强」规则。\n"
            "简洁=最少规则（快）；极致=最多规则（更精彩但 token 略多）。\n"
            "无论选哪个，纯中文/反AI味/钩子等硬约束始终存在。"
        )
        quality_layout.addRow("质量档位:", self.quality_tier_combo)

        self.budget_input = QSpinBox()
        self.budget_input.setRange(600, 4000)
        self.budget_input.setSingleStep(100)
        self.budget_input.setToolTip(
            "写作系统提示的 token 上限。L0/L1 硬约束常驻不可裁剪，\n"
            "超出上限的低优先级 L2 模块会被自动丢弃，保证提示词不会无限变长。"
        )
        self.budget_input.setValue(self._config.system_prompt_budget)
        quality_layout.addRow("系统提示预算:", self.budget_input)

        self.enable_review_check = QCheckBox("写后运行质量审查（钩子/去重/反AI味）")
        self.enable_review_check.setChecked(self._config.enable_quality_review)
        self.enable_review_check.setToolTip(
            "每章写完后独立跑一轮审查：检测结尾钩子是否缺失、是否与前后文重复、有无 AI 套话。\n"
            "不影响成章，仅生成质量报告。"
        )
        quality_layout.addRow(self.enable_review_check)

        self.auto_fix_check = QCheckBox("审查发现问题后自动单轮修订")
        self.auto_fix_check.setChecked(self._config.quality_auto_fix)
        self.auto_fix_check.setToolTip(
            "默认关闭（仅报告）。开启后，若审查发现可自动修复项（如缺失结尾钩子），\n"
            "会自动再让 AI 补强一轮。会多消耗一次生成。"
        )
        quality_layout.addRow(self.auto_fix_check)

        tabs.addTab(quality_tab, "质量增强")

        # ===== 本书风格 Tab（每本小说独立，可选手作者预设）=====
        if self._project is not None:
            style_tab = QWidget()
            style_layout = QFormLayout(style_tab)

            style_layout.addRow(
                QLabel("设定本书的统一文风。该风格会作为固定写法指令注入每一章，整本保持一致。")
            )

            self.style_combo = QComboBox()
            self.style_combo.addItems(preset_keys())
            # 选中当前项目的风格：能匹配预设则选预设，否则落入自定义
            cur = self._project.writing_style
            matched = UNSPECIFIED
            for k, v in STYLE_PRESETS.items():
                if v == cur:
                    matched = k
                    break
            if cur == "":
                matched = UNSPECIFIED
            elif matched == UNSPECIFIED:
                matched = CUSTOM
            self.style_combo.setCurrentText(matched)
            style_layout.addRow("文风/作者:", self.style_combo)

            self.style_custom = QLineEdit()
            self.style_custom.setPlaceholderText("自定义文风指令（如：冷峻白描，对话精炼）")
            self.style_custom.setText(cur if matched == CUSTOM else "")
            self.style_custom.setVisible(matched == CUSTOM)
            style_layout.addRow("自定义文风:", self.style_custom)

            def _on_style_pick(text: str):
                self.style_custom.setVisible(text == CUSTOM)
            self.style_combo.currentTextChanged.connect(_on_style_pick)

            # 内容标签体系编辑器（结构化：名称/类别/定义/锚点）
            style_layout.addRow(
                QLabel("内容标签体系：金手指/扮猪吃老虎/女主/体系/贯穿线索等。每个标签可带定义与锚点，"
                       "注入每章系统提示并落地档案，真正发生作用。")
            )
            self.content_tag_editor = _TagEditor(self._project.tags, self, is_process=False)
            style_layout.addRow(self.content_tag_editor)

            # 流程约束编辑器
            style_layout.addRow(
                QLabel("流程约束：本工程对本书强制施行的写法流程（4相位/逐章顺序大纲/禁注入散文/事件账本等）。")
            )
            self.process_tag_editor = _TagEditor(self._project.process_tags, self, is_process=True)
            style_layout.addRow(self.process_tag_editor)

            tabs.addTab(style_tab, "本书风格")

        # ===== 皮肤选择 Tab =====
        skin_tab = QWidget()
        skin_layout = QVBoxLayout(skin_tab)
        skin_layout.setSpacing(8)

        skin_hint = QLabel("选择软件界面主题。切换后保存设置即可生效，重启后保持。")
        skin_hint.setWordWrap(True)
        skin_layout.addWidget(skin_hint)

        from PyQt6.QtWidgets import QButtonGroup, QRadioButton
        self._skin_group = QButtonGroup(self)
        self._skin_buttons: dict[str, QRadioButton] = {}
        current_skin = self._config.skin if hasattr(self._config, "skin") else "mocha"

        for key, skin in SKINS.items():
            radio = QRadioButton(f"{skin.name}  —  {skin.desc}")
            radio.setToolTip(f"色板预览：底色 {skin.colors.base} / 文字 {skin.colors.text} / 强调 {skin.colors.accent}")
            if key == current_skin:
                radio.setChecked(True)
            # 色板预览条
            preview = QLabel()
            colors = [skin.colors.base, skin.colors.mantle, skin.colors.surface0,
                      skin.colors.surface1, skin.colors.text, skin.colors.subtext,
                      skin.colors.accent, skin.colors.green, skin.colors.yellow,
                      skin.colors.red, skin.colors.mauve]
            preview.setText("".join(f'<span style="background-color:{c};">&nbsp;&nbsp;&nbsp;&nbsp;</span>' for c in colors))
            preview.setStyleSheet(f"font-size:14px; background:{skin.colors.base}; padding:6px 8px; border-radius:4px;")
            row = QHBoxLayout()
            row.addWidget(radio)
            row.addWidget(preview)
            row.addStretch()
            skin_layout.addLayout(row)
            self._skin_group.addButton(radio)
            self._skin_buttons[key] = radio

        skin_layout.addStretch()
        tabs.addTab(skin_tab, "皮肤")

        layout.addWidget(tabs)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("保存并关闭")
        ok_btn.clicked.connect(self._on_save_all)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _refresh_endpoint_list(self):
        self.endpoint_list.clear()
        for ep in self._endpoints:
            label = f"{ep.name}{' (默认)' if ep.is_default else ''}"
            item = QListWidgetItem(label)
            self.endpoint_list.addItem(item)

    def _on_endpoint_selected(self, row: int):
        if 0 <= row < len(self._endpoints):
            ep = self._endpoints[row]
            self.name_input.setText(ep.name)
            self.base_url_input.setText(ep.base_url)
            self.api_key_input.setText(ep.api_key)
            self.models_input.setText(", ".join(ep.models))
            self.default_checkbox.setChecked(ep.is_default)

    def _on_add_endpoint(self):
        ep = LLMEndpoint(name="新端点", base_url="", api_key="", models=[])
        self._endpoints.append(ep)
        self._refresh_endpoint_list()
        self.endpoint_list.setCurrentRow(len(self._endpoints) - 1)

    def _on_del_endpoint(self):
        row = self.endpoint_list.currentRow()
        if 0 <= row < len(self._endpoints):
            self._endpoints.pop(row)
            self._refresh_endpoint_list()

    def _on_save_endpoint(self):
        row = self.endpoint_list.currentRow()
        if not (0 <= row < len(self._endpoints)):
            return

        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "端点名称不能为空")
            return

        ep = self._endpoints[row]
        ep.name = name
        ep.base_url = self.base_url_input.text().strip()
        ep.api_key = self.api_key_input.text().strip()
        ep.models = [m.strip() for m in self.models_input.text().split(",") if m.strip()]
        ep.is_default = self.default_checkbox.isChecked()

        # 如果设为默认，取消其他默认
        if ep.is_default:
            for i, e in enumerate(self._endpoints):
                if i != row:
                    e.is_default = False

        self._refresh_endpoint_list()

    def _on_test_chat(self):
        self._save_endpoint_current()
        self._build_config()
        self.test_result_label.setText("正在测试...")
        self._test_worker = TestConnectionWorker(self._llm_for_test)
        self._test_worker.finished_signal.connect(
            lambda ok, msg: self.test_result_label.setText(f"{'✓' if ok else '✗'} {msg}")
        )
        self._test_worker.start()

    def _on_test_embed(self):
        self._save_endpoint_current()
        self._build_config()
        self.test_result_label.setText("正在测试...")
        self._test_worker = TestConnectionWorker(self._llm_for_test, test_embed=True)
        self._test_worker.finished_signal.connect(
            lambda ok, msg: self.test_result_label.setText(f"{'✓' if ok else '✗'} {msg}")
        )
        self._test_worker.start()

    def _save_endpoint_current(self):
        """保存当前编辑的端点到内存。"""
        row = self.endpoint_list.currentRow()
        if not (0 <= row < len(self._endpoints)):
            return
        ep = self._endpoints[row]
        ep.name = self.name_input.text().strip() or ep.name
        ep.base_url = self.base_url_input.text().strip()
        ep.api_key = self.api_key_input.text().strip()
        ep.models = [m.strip() for m in self.models_input.text().split(",") if m.strip()]
        ep.is_default = self.default_checkbox.isChecked()
        if ep.is_default:
            for i, e in enumerate(self._endpoints):
                if i != row:
                    e.is_default = False

    _llm_for_test = None

    def _build_config(self):
        """构建临时 Config 和 LLMClient 用于测试。"""
        from domains.novel.core.llm_client import LLMClient
        cfg = self._get_config()
        self._llm_for_test = LLMClient(cfg)

    def _get_config(self) -> Config:
        cfg = Config()
        cfg.endpoints = self._endpoints
        cfg.default_chat_model = self.chat_model_input.text().strip()
        cfg.default_embed_model = self.embed_model_input.text().strip()
        cfg.default_embed_endpoint = self.embed_endpoint_input.text().strip()
        cfg.temperature = self.temp_input.value()
        cfg.max_tokens = self.max_tokens_input.value()
        cfg.context_window = self.context_window_input.value()
        cfg.recent_summary_count = self.recent_summary_input.value()
        cfg.rag_top_k = self.rag_topk_input.value()
        cfg.chunk_size = self.chunk_size_input.value()
        _levels = ["strict", "normal", "lenient", "off"]
        cfg.validation_level = _levels[self.validation_level_combo.currentIndex()]
        cfg.adjacent_outline_window = self.adjacent_window_input.value()
        cfg.lan_port = self.lan_port_input.value()
        cfg.character_inject_max = self.char_inject_input.value()
        cfg.foreshadow_max = self.foreshadow_input.value()
        # 质量增强
        _tiers = ["concise", "standard", "ultimate"]
        cfg.quality_tier = _tiers[self.quality_tier_combo.currentIndex()]
        cfg.system_prompt_budget = self.budget_input.value()
        cfg.enable_quality_review = self.enable_review_check.isChecked()
        cfg.quality_auto_fix = self.auto_fix_check.isChecked()
        # 皮肤
        for key, btn in getattr(self, "_skin_buttons", {}).items():
            if btn.isChecked():
                cfg.skin = key
                break
        return cfg

    def _on_save_all(self):
        self._save_endpoint_current()
        cfg = self._get_config()
        cfg.save()
        # 本书风格：就地修改当前项目对象并持久化到 project.json
        if self._project is not None:
            self._project.writing_style = resolve_style(
                self.style_combo.currentText(), self.style_custom.text().strip()
            )
            # 内容标签 / 流程约束：序列化为结构化 dict 列表（名称/类别/定义/锚点）
            self._project.tags = [s.to_dict() for s in self.content_tag_editor.get_specs()]
            self._project.process_tags = [s.to_dict() for s in self.process_tag_editor.get_specs()]
            if self._project_manager is not None:
                self._project_manager.update_project(self._project)
        self.config_saved.emit(cfg)
        # 即时应用皮肤
        try:
            from PyQt6.QtWidgets import QApplication
            from domains.novel.ui.styles import apply_skin
            _app = QApplication.instance()
            if _app is not None:
                apply_skin(_app, cfg.skin)
        except Exception:
            pass
        self.accept()
