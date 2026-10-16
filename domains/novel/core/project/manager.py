"""
ProjectManager - 书项目管理。

管理小说项目的创建、打开、列表、章节管理。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from domains.novel.core.pipeline import volumes as vol_mod
from domains.novel.utils.logger import logger


@dataclass
class NovelProject:
    """书项目元数据。"""
    name: str = ""
    author: str = ""
    genre: str = ""
    description: str = ""
    target_words: int = 1000000
    project_root: str = ""
    created_at: str = ""
    current_chapter: int = 0
    novel_category: str = ""  # 小说分类 key（见 core.novel_categories）
    writing_style: str = ""   # 本书统一文风（固定写法指令，整本一致；空=模型默认）
    tags: list = field(default_factory=list)  # 本书内容标签（金手指/扮猪吃老虎/女主等套路与题材元素）
    process_tags: list = field(default_factory=list)  # 本书流程约束标签（4相位/逐章顺序大纲/禁注入散文/事件账本等系统强制的写法约束）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NovelProject":
        return cls(
            name=d.get("name", ""),
            author=d.get("author", ""),
            genre=d.get("genre", ""),
            description=d.get("description", ""),
            target_words=d.get("target_words", 1000000),
            project_root=d.get("project_root", ""),
            created_at=d.get("created_at", ""),
            current_chapter=d.get("current_chapter", 0),
            novel_category=d.get("novel_category", ""),
            writing_style=d.get("writing_style", ""),
            tags=d.get("tags", []) or [],
            process_tags=d.get("process_tags", []) or [],
        )


class ProjectManager:
    """书项目管理：新建、打开、列表、删除。"""

    def __init__(self, config_root: Optional[str] = None):
        if config_root:
            self._config_root = Path(config_root)
        else:
            # 优先使用项目本地目录（manager.py 所在目录的上两级 = novelforge/）
            self._config_root = Path(__file__).resolve().parent.parent.parent / ".novelforge"
        self._config_root.mkdir(parents=True, exist_ok=True)
        self._projects_index = self._config_root / "projects.json"

    def create_project(
        self,
        name: str,
        genre: str,
        project_root: str,
        author: str = "",
        description: str = "",
        target_words: int = 1000000,
        novel_category: str = "",
        writing_style: str = "",
        tags: Optional[list] = None,
        process_tags: Optional[list] = None,
    ) -> NovelProject:
        """
        创建新书项目：
        1. 创建目录结构
        2. 初始化 .novelforge 内部数据
        3. 写入 project.json
        4. 更新项目索引
        """
        root = Path(project_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        if tags is None:
            tags = []
        if process_tags is None:
            process_tags = []

        # 题材强制必选校验：缺核心体系标签（如玄幻需「修炼境界体系」）则拦截，
        # 防止创建出设定不全的书（核心力量标尺缺失会导致全书无升级骨架）。
        from domains.novel.core.pipeline.tag_system import validate_tags_for_genre
        chosen_names = [t.get("name") if isinstance(t, dict) else str(t) for t in tags]
        missing = validate_tags_for_genre(genre, chosen_names)
        if missing:
            raise ValueError(
                f"题材「{genre}」强制必选标签缺失：{missing}。"
                f"请在书籍设置中先补选这些核心体系标签，再创建/保存项目。"
            )

        # 创建目录结构
        for subdir in ["chapters", "outline", "outline/chapters", "settings", "settings/characters",
                       "reviews", ".novelforge", ".novelforge/summaries", ".novelforge/commits",
                       ".novelforge/backups"]:
            (root / subdir).mkdir(parents=True, exist_ok=True)

        # 清理旧项目残留的文件（exist_ok 只会建目录不删旧文件，导致旧章节「回魂」）
        for f in (root / "chapters").glob("chapter_*.md"):
            f.unlink(missing_ok=True)
        old_outlines = root / ".novelforge" / "chapter_outlines.json"
        if old_outlines.exists():
            old_outlines.unlink()
        # 清理可能残留的记忆数据
        for f in (root / ".novelforge").glob("memory_*.db"):
            f.unlink(missing_ok=True)
        for f in (root / ".novelforge" / "summaries").iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)

        # 写入 project.json
        project = NovelProject(
            name=name,
            author=author,
            genre=genre,
            description=description,
            target_words=target_words,
            project_root=str(root),
            created_at=datetime.now().isoformat(),
            novel_category=novel_category,
            writing_style=writing_style,
            tags=tags,
            process_tags=process_tags,
        )
        self._save_project_meta(project)

        # 初始化 state.json
        state = {
            "current_chapter": 0,
            "chapters": [],
            "created_at": datetime.now().isoformat(),
        }
        with open(root / ".novelforge" / "state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        # 更新项目索引
        self._add_to_index(project)

        logger.info(f"项目创建成功: {name} ({root})")
        return project

    def open_project(self, project_root: str) -> NovelProject:
        """打开已有项目。"""
        root = Path(project_root).expanduser()
        meta_path = root / "project.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"项目元数据不存在: {meta_path}")

        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        project = NovelProject.from_dict(data)
        project.project_root = str(root)
        return project

    def list_projects(self) -> list[NovelProject]:
        """列出所有已注册的项目。"""
        if not self._projects_index.exists():
            return []
        try:
            with open(self._projects_index, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [NovelProject.from_dict(p) for p in data.get("projects", [])]
        except Exception:
            return []

    def remove_project(self, project_root: str) -> bool:
        """从索引中移除项目（不删除文件）。"""
        projects = self.list_projects()
        projects = [p for p in projects if p.project_root != project_root]
        self._save_index(projects)
        return True

    def update_project(self, project: NovelProject) -> None:
        """更新项目元数据。"""
        self._save_project_meta(project)
        # 更新索引
        projects = self.list_projects()
        for i, p in enumerate(projects):
            if p.project_root == project.project_root:
                projects[i] = project
                break
        else:
            projects.append(project)
        self._save_index(projects)

    # ===== 章节管理 =====

    def get_chapter_list(self, project_root: str) -> list[dict]:
        """获取章节列表。"""
        chapters_dir = Path(project_root) / "chapters"
        if not chapters_dir.exists():
            return []

        chapters = []
        for path in sorted(chapters_dir.glob("chapter_*.md")):
            # 从文件名提取章节号
            try:
                chapter_id = int(path.stem.split("_")[1])
            except (IndexError, ValueError):
                continue

            # 读取标题
            try:
                first_line = path.read_text(encoding="utf-8").split("\n")[0]
                title = first_line.replace("#", "").strip()
                if title.startswith(f"第{chapter_id}章"):
                    title = title[len(f"第{chapter_id}章"):].strip()
            except Exception:
                title = ""

            # 统计字数（仅正文，排除标题行，避免空正文章节误报字数）
            try:
                text = path.read_text(encoding="utf-8")
                lines = text.split("\n")
                # 跳过标题行(# 第X章 ...)与紧随的空行，只统计正文
                body = "\n".join(lines[2:]) if (lines and lines[0].lstrip().startswith("#")) else text
                word_count = len([c for c in body if c.strip()])
            except Exception:
                word_count = 0

            chapters.append({
                "chapter_id": chapter_id,
                "title": title,
                "word_count": word_count,
                "path": str(path),
            })
        return chapters

    def get_chapter_text(self, project_root: str, chapter_id: int) -> str:
        """读取章节正文。"""
        path = Path(project_root) / "chapters" / f"chapter_{chapter_id:04d}.md"
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        # 移除标题行
        lines = text.split("\n")
        if lines and lines[0].startswith("#"):
            return "\n".join(lines[2:])  # 跳过标题行和空行
        return text

    def save_chapter_text(self, project_root: str, chapter_id: int, title: str, text: str) -> None:
        """保存章节正文。"""
        import os as _os
        chapters_dir = Path(project_root) / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        path = chapters_dir / f"chapter_{chapter_id:04d}.md"
        file_content = f"# 第{chapter_id}章 {title}\n\n{text}"
        try:
            tmp_path = path.with_name(path.name + f".tmp_{_os.getpid()}")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(file_content)
                f.flush()
                _os.fsync(f.fileno())
            _os.replace(tmp_path, path)
        except PermissionError as e:
            raise PermissionError(
                f"【权限被拦】写入 {path} 被 macOS 拒绝。"
                f"\n请改用 Finder 双击 NovelForge.command 或系统 Terminal 启动，不要在 IDE 沙箱内启动。\n原错误：{e}"
            ) from e

    def delete_chapter(self, project_root: str, chapter_id: int) -> None:
        """删除章节。"""
        path = Path(project_root) / "chapters" / f"chapter_{chapter_id:04d}.md"
        if path.exists():
            path.unlink()

    # ===== 卷管理（范围元数据） =====

    def get_volumes(self, project_root: str, use_default: bool = True) -> list[dict]:
        """读取卷定义。use_default=True 时若无卷文件则合成覆盖全部章节的默认卷。"""
        if use_default:
            return vol_mod.get_volumes_or_default(project_root)
        return vol_mod.load_volumes(project_root)

    def save_volumes(self, project_root: str, volumes: list[dict]) -> None:
        """写回卷定义。"""
        vol_mod.save_volumes(project_root, volumes)

    def add_volume(
        self, project_root: str, title: str, theme: str,
        start_chapter: int, end_chapter: int, summary: str = "",
    ) -> dict:
        """新增一卷并保存，返回新建的卷 dict。"""
        vols = vol_mod.load_volumes(project_root)
        new_id = max([v.get("id", 0) for v in vols], default=0) + 1
        vol = {
            "id": new_id,
            "title": title,
            "theme": theme,
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "summary": summary,
        }
        vols.append(vol)
        vol_mod.save_volumes(project_root, vols)
        return vol

    def delete_volume(self, project_root: str, volume_id: int) -> None:
        """删除指定卷（按 id），重排 order。"""
        vols = vol_mod.load_volumes(project_root)
        vols = [v for v in vols if v.get("id") != volume_id]
        vol_mod.save_volumes(project_root, vols)

    # ===== 大纲管理 =====

    def save_outline(self, project_root: str, outline_type: str, content_str: str) -> None:
        """保存大纲。outline_type: master/volume_XX/chapter_XXX"""
        import os as _os
        outline_dir = Path(project_root) / "outline"
        outline_dir.mkdir(parents=True, exist_ok=True)
        path = outline_dir / f"{outline_type}.md"
        try:
            tmp_path = path.with_name(path.name + f".tmp_{_os.getpid()}")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content_str)
                f.flush()
                _os.fsync(f.fileno())
            _os.replace(tmp_path, path)
        except PermissionError as e:
            raise PermissionError(
                f"【权限被拦】写入 {path} 被 macOS 拒绝。"
                f"\n请改用 Finder 双击 NovelForge.command 或系统 Terminal 启动，不要在 IDE 沙箱内启动。\n原错误：{e}"
            ) from e

    def get_outline(self, project_root: str, outline_type: str) -> str:
        """读取大纲。"""
        path = Path(project_root) / "outline" / f"{outline_type}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # ===== 设定集管理 =====

    def save_setting(self, project_root: str, setting_type: str, content: str) -> None:
        """保存设定。setting_type: worldview/power_system/character_XXX"""
        settings_dir = Path(project_root) / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        path = settings_dir / f"{setting_type}.md"
        path.write_text(content, encoding="utf-8")

    def get_setting(self, project_root: str, setting_type: str) -> str:
        """读取设定。"""
        path = Path(project_root) / "settings" / f"{setting_type}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def list_settings(self, project_root: str) -> list[str]:
        """列出所有设定文件名。"""
        settings_dir = Path(project_root) / "settings"
        if not settings_dir.exists():
            return []
        return [f.stem for f in settings_dir.glob("*.md")]

    # ===== 内部方法 =====

    def _save_project_meta(self, project: NovelProject):
        root = Path(project.project_root)
        meta_path = root / "project.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)

    def _add_to_index(self, project: NovelProject):
        projects = self.list_projects()
        # 检查是否已存在
        for i, p in enumerate(projects):
            if p.project_root == project.project_root:
                projects[i] = project
                break
        else:
            projects.append(project)
        self._save_index(projects)

    def _save_index(self, projects: list[NovelProject]):
        data = {"projects": [p.to_dict() for p in projects]}
        with open(self._projects_index, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
