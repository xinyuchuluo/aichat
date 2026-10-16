"""
file_loader.py — 读取 Excel/CSV/Word，返回表头样本 + 完整内容
支持直接分析路径（Word / 多表头 Excel / 小文件）
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path
from datetime import datetime


def _json_safe(val):
    """将值转换为 JSON 安全类型"""
    if pd.isna(val):
        return None
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.isoformat()
    if hasattr(val, 'item'):  # numpy 类型
        return val.item()
    return val


def _json_safe_dict(d):
    """递归转换字典中的日期类型"""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _json_safe_dict(v)
        elif isinstance(v, list):
            result[k] = [_json_safe(item) for item in v]
        else:
            result[k] = _json_safe(v)
    return result


# ─── 文件类型检测 ─────────────────────────────────────────

def detect_file_type(file_path: str) -> str:
    """
    根据文件扩展名判断文件类型。
    返回: "excel" | "csv" | "word" | "unknown"
    """
    suffix = Path(file_path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return "excel"
    elif suffix == ".csv":
        return "csv"
    elif suffix in (".docx", ".doc"):
        return "word"
    return "unknown"


# ─── Token 估算（轻量近似，不依赖 tiktoken）─────────────

def estimate_tokens(text: str) -> int:
    """
    近似估算 token 数（适用于中文+英文混合文本）
    中文：1字 ≈ 2 tokens（保守估计）
    英文：1字符 ≈ 0.25 tokens
    数字/符号：1字符 ≈ 0.25 tokens
    """
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 2 + other_chars * 0.25)


# ─── Word 文档解析 ───────────────────────────────────────

def _auto_convert_doc_to_docx(doc_path: str) -> str:
    """
    Windows 下自动调用 Word COM 接口将 .doc 转为 .docx（临时文件）。
    返回生成的 .docx 文件路径。失败时抛出异常。
    """
    import tempfile
    import os
    try:
        import win32com.client as win32
    except ImportError:
        raise ImportError(
            "自动转换 .doc 需要安装 pywin32：pip install pywin32\n"
            "或手动用 Word 另存为 .docx 格式。"
        )

    abs_path = os.path.abspath(doc_path)
    tmp_dir = tempfile.gettempdir()
    tmp_name = os.path.splitext(os.path.basename(doc_path))[0] + "_converted.docx"
    tmp_path = os.path.join(tmp_dir, tmp_name)

    word = win32.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(abs_path)
        doc.SaveAs2(tmp_path, FileFormat=16)  # 16 = wdFormatXMLDocument (docx)
        doc.Close()
    finally:
        word.Quit()
    return tmp_path


def parse_docx(file_path: str) -> str:
    """
    解析 Word .docx 文件，提取文字和表格，转换为 Markdown 格式。
    返回纯文本内容供直接分析使用。

    对 .doc 旧格式会自动尝试通过 Word COM 接口转换为 .docx 后再解析。
    """
    path = Path(file_path)
    actual_path = file_path

    if path.suffix.lower() == ".doc":
        actual_path = _auto_convert_doc_to_docx(file_path)

    try:
        from docx import Document
    except ImportError:
        raise ImportError("解析 Word 文件需要安装 python-docx：pip install python-docx")

    doc = Document(actual_path)
    parts = []

    # 提取段落文字
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)

    # 提取表格
    for i, table in enumerate(doc.tables):
        rows = table.rows
        if not rows:
            continue
        parts.append(f"\n## 表格 {i + 1}")
        # 表头行
        header = [cell.text.strip() for cell in rows[0].cells]
        parts.append("| " + " | ".join(header) + " |")
        parts.append("|" + "|".join("---" for _ in header) + "|")
        # 数据行（最多50行，防止过长）
        for row in rows[1:51]:
            cells = [cell.text.strip() for cell in row.cells]
            parts.append("| " + " | ".join(cells) + " |")
        if len(rows) > 51:
            parts.append(f"\n...（共 {len(rows)} 行，仅展示前50行）")

    return "\n\n".join(parts)


# ─── Excel 多表头检测 ───────────────────────────────────

def is_multi_header(file_path: str, sheet_name: str = None) -> bool:
    """
    检测 Excel 文件是否为多表头。
    返回 True 表示多表头（应走直接分析路径），False 表示标准单表头。

    多表头判断条件（满足任一即为多表头）：
    1. 存在合并单元格
    2. 第一行（表头行）有空值
    3. 前两行都有非空值（说明是两行表头）
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True)
    try:
        if sheet_name:
            ws = wb[sheet_name]
        else:
            ws = wb.active

        # 1. 合并单元格检测
        if ws.merged_cells.ranges:
            return True

        # 读取前3行
        rows_data = []
        for i, row in enumerate(ws.iter_rows(max_row=3, values_only=True)):
            if i >= 3:
                break
            rows_data.append(list(row))

        if not rows_data:
            return False

        # 2. 表头行空值检测（第一行有 None 或空字符串）
        header_row = rows_data[0]
        if any(v is None or str(v).strip() == "" for v in header_row):
            return True

        # 3. 两行表头检测（前两行都有非空值，且内容不完全相同）
        if len(rows_data) >= 2:
            row1 = [str(v).strip() if v is not None else "" for v in rows_data[0]]
            row2 = [str(v).strip() if v is not None else "" for v in rows_data[1]]
            row2_has_content = any(v for v in row2)
            if row2_has_content and row1 != row2:
                return True

        return False
    finally:
        wb.close()


def get_multi_header_sheets(file_path: str) -> list[str]:
    """
    返回文件所有 Sheet 中被判定为多表头的 Sheet 名称列表。
    """
    if detect_file_type(file_path) == "csv":
        return []
    if detect_file_type(file_path) == "word":
        return []

    sheets = get_sheet_names(file_path)
    return [s for s in sheets if is_multi_header(file_path, s)]


# ─── Excel 完整内容读取（用于直接分析） ──────────────────

def read_excel_full_content(
    file_path: str,
    sheets: list[str] = None,
    max_rows_per_sheet: int = 200,
) -> str:
    """
    读取 Excel 文件全量内容，转换为 Markdown 表格格式。
    用于直接分析路径（跳过硬编码的表头解析逻辑）。
    max_rows_per_sheet: 每个 Sheet 最多读取行数，防止内容过长
    """
    path = Path(file_path)
    parts = []

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(file_path, encoding="utf-8-sig")
        parts.append("## Sheet1\n")
        parts.append(_df_to_markdown(df.head(max_rows_per_sheet)))
        return "\n".join(parts)

    if sheets is None:
        sheets = get_sheet_names(file_path)

    for sheet in sheets:
        df = pd.read_excel(file_path, sheet_name=sheet, engine="openpyxl")
        # 如果是多表头，不做 header 处理，直接读取原始内容
        if is_multi_header(file_path, sheet):
            df_raw = pd.read_excel(file_path, sheet_name=sheet, header=None, engine="openpyxl")
            parts.append(f"## {sheet}\n")
            parts.append(_df_to_markdown(df_raw.head(max_rows_per_sheet)))
        else:
            parts.append(f"## {sheet}\n")
            parts.append(_df_to_markdown(df.head(max_rows_per_sheet)))

    content = "\n".join(parts)
    if len(sheets) > 1:
        total_rows = sum(
            len(pd.read_excel(file_path, sheet_name=s, engine="openpyxl"))
            for s in sheets
        )
        content += f"\n\n（共 {len(sheets)} 个 Sheet，约 {total_rows} 行数据）"
    return content


def _df_to_markdown(df: pd.DataFrame) -> str:
    """将 DataFrame 转换为 Markdown 表格"""
    if df.empty:
        return ""
    # 清洗列名
    df = df.copy()
    df.columns = [str(c).strip() if c is not None else f"列{i+1}"
                  for i, c in enumerate(df.columns)]
    lines = []
    lines.append("| " + " | ".join(str(c) for c in df.columns) + " |")
    lines.append("|" + "|".join("---" for _ in df.columns) + "|")
    for _, row in df.iterrows():
        cells = [str(_json_safe(v)) for v in row.values]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ─── 文件预览信息（GUI 使用） ────────────────────────────

def get_file_preview(file_path: str) -> dict:
    """
    获取文件预览信息，用于 GUI 显示。
    返回 {
        file_type: str,           # "excel" | "csv" | "word" | "unknown"
        sheets: list[str],        # Sheet 名称（Word 返回 ["Word文档"]）
        multi_header_sheets: list[str],  # 多表头 Sheet 列表
        estimated_tokens: int,    # 预估 token 数
        row_count: int,           # 总行数（Word 返回段落数）
        size_kb: int,             # 文件大小 KB
    }
    """
    path = Path(file_path)
    file_type = detect_file_type(file_path)
    size_kb = path.stat().st_size // 1024

    result = {
        "file_type": file_type,
        "sheets": [],
        "multi_header_sheets": [],
        "estimated_tokens": 0,
        "row_count": 0,
        "size_kb": size_kb,
    }

    if file_type == "word":
        try:
            content = parse_docx(file_path)
            result["sheets"] = ["Word文档"]
            result["row_count"] = content.count("\n") + content.count("|")
            result["estimated_tokens"] = estimate_tokens(content)
        except Exception:
            result["sheets"] = ["Word文档(.doc旧格式)"]
            result["row_count"] = 0
            result["estimated_tokens"] = 0
        return result

    if file_type in ("excel", "csv"):
        sheets = get_sheet_names(file_path)
        result["sheets"] = sheets
        if file_type == "csv":
            df = pd.read_csv(file_path, encoding="utf-8-sig")
            result["row_count"] = len(df)
            content = _df_to_markdown(df.head(200))
            result["estimated_tokens"] = estimate_tokens(content)
        else:
            multi = get_multi_header_sheets(file_path)
            result["multi_header_sheets"] = multi
            total_rows = 0
            content_parts = []
            for sheet in sheets[:3]:  # 只取前3个 Sheet 估算
                df = pd.read_excel(file_path, sheet_name=sheet, engine="openpyxl")
                total_rows += len(df)
                content_parts.append(_df_to_markdown(df.head(50)))
            result["row_count"] = total_rows
            result["estimated_tokens"] = estimate_tokens("\n".join(content_parts))

    return result


# ─── 原有接口（向后兼容） ─────────────────────────────────

def get_sheet_names(file_path: str) -> list[str]:
    """获取 Excel 的所有 Sheet 名称"""
    path = Path(file_path)
    if path.suffix.lower() == ".csv":
        return ["Sheet1"]
    xf = pd.ExcelFile(file_path, engine="openpyxl")
    return xf.sheet_names


def _is_numeric_like(val: str) -> bool:
    """判断字符串是否像数值（数字/百分比/小数等）"""
    if val is None:
        return False
    s = str(val).strip().replace("%", "").replace(",", "").replace("，", "")
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _detect_and_fix_multi_header_columns(df: pd.DataFrame, file_path: str, sheet: str) -> tuple[pd.DataFrame, list[str] | None]:
    """
    检测并修复多级表头 Excel 的列名问题。

    问题：多级表头（合并单元格 + 两行表头）导致 pd.read_excel(header=0)
          读出的 columns 大量是 "Unnamed: N"，真实列名藏在第一行数据中。
    修复：当 Unnamed 列占比超过 40% 时，用 header=None 重读，取第二行作为列名，
          并跳过原表头行，返回修正后的 DataFrame。

    返回: (修正后的 DataFrame, 原始 pandas 列名列表 | None)
           原始列名列表供 Rule 13 构建重命名映射（仅在多表头命中时返回）
    """
    cols = list(df.columns)
    unnamed_count = sum(1 for c in cols if str(c).startswith("Unnamed:"))
    total_cols = len(cols)

    # 判定条件：Unnamed 列占比 > 40% 且总列数 > 5
    if total_cols <= 5 or unnamed_count / total_cols <= 0.4:
        return df, None  # 正常单表头，无需修复

    # 多表头检测命中 — 用 header=None 重读
    try:
        df_raw = pd.read_excel(
            file_path, sheet_name=sheet,
            nrows=10, engine="openpyxl", header=None
        )
        if len(df_raw) < 2:
            return df, None  # 数据不足，放弃修复

        # 第二行（index=1）通常是真实的子列名
        row1 = df_raw.iloc[1]
        new_columns = []
        for i, original_col in enumerate(cols):
            if i < len(row1):
                candidate = str(row1.iloc[i]).strip()
                # 用有意义的名称替换 Unnamed
                if str(original_col).startswith("Unnamed:") and candidate and candidate != "nan":
                    new_columns.append(candidate)
                else:
                    new_columns.append(str(original_col).strip())
            else:
                new_columns.append(str(original_col).strip())

        df.columns = new_columns

        # 跳过表头行：找到第一个真正的数据行位置
        # 多表头 Excel 结构通常是：
        #   row 0: 大分类标题（合并单元格展开后重复）
        #   row 1: 真实子列名（已用于替换 columns）
        #   row 2: "总计" / "合计" 行
        #   row 3+: 实际数据行
        first_data_row = None
        for idx in range(min(8, len(df))):
            row_vals = df.iloc[idx]
            non_null_count = row_vals.notna().sum()

            # 数据行应该有足够多的非空值（> 列数的25%）
            if non_null_count <= len(new_columns) * 0.25:
                continue

            str_vals = [str(v).strip() for v in row_vals.dropna().values if str(v).strip()]
            if len(str_vals) < 3:
                continue  # 太少非空值，可能是空行

            # 检查是否是"表头重复行"：大量值与列名完全相同
            header_match_count = sum(
                1 for v in str_vals
                if any(v == c or v in c for c in new_columns)
            )
            if header_match_count > len(str_vals) * 0.6:
                continue  # 这是表头重复行，跳过

            # 检查是否是"总计/合计"行：第一个非空字符串字段包含这些关键词
            first_str = next((v for v in str_vals if not _is_numeric_like(v)), "")
            if first_str and any(kw in first_str for kw in ("总", "合", "计", "平均")):
                # 进一步确认：如果这行大部分数值列的值看起来像汇总数据（大数字或百分比）
                numeric_vals = [v for v in str_vals if _is_numeric_like(v)]
                # 如果数值很多且包含"总计"关键词，跳过
                if len(numeric_vals) > 3:
                    continue

            # 通过所有检查 → 这就是数据行
            first_data_row = idx
            break

        if first_data_row is not None and first_data_row > 0:
            df = df.iloc[first_data_row:].reset_index(drop=True)

        return df, cols  # 返回 (修正后的 df, 原始 pandas 列名列表)
    except Exception:
        return df, None  # 修复失败，返回原始 df + None


def load_headers(file_path: str, sheets: list[str], sample_rows: int = 5) -> dict:
    """
    读取每个 Sheet 的列名、数据类型、前 N 行样本。
    返回字典供 Prompt 使用，所有值已转为 JSON 安全类型。

    新增能力：自动检测多级表头 Excel（合并单元格），将 Unnamed 列替换为真实列名，
    确保 AI 生成的脚本可以正确引用列名。
    """
    path = Path(file_path)
    result = {}

    for sheet in sheets:
        try:
            if path.suffix.lower() == ".csv":
                df = pd.read_csv(file_path, nrows=sample_rows, encoding="utf-8-sig")
            else:
                df = pd.read_excel(
                    file_path, sheet_name=sheet,
                    nrows=sample_rows + 3,  # 多读几行用于多表头检测
                    engine="openpyxl"
                )

            # 清洗列名（去除多余空白）
            df.columns = [str(c).strip() for c in df.columns]

            # ★ 核心修复：检测并修复多级表头列名问题
            df, original_columns = _detect_and_fix_multi_header_columns(df, file_path, sheet)

            # 转换样本数据中的日期类型
            sample_dicts = df.head(3).to_dict(orient="records")
            sample_safe = [_json_safe_dict(d) for d in sample_dicts]

            sheet_info = {
                "columns": list(df.columns),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "sample_rows": sample_safe,
                "total_sample_cols": len(df.columns),
            }
            # ★ 关键：保存原始 pandas 列名，供 pipeline Rule 13 构建重命名映射
            if original_columns is not None:
                sheet_info["_original_columns"] = [str(c).strip() for c in original_columns]
            result[sheet] = sheet_info
        except Exception as e:
            result[sheet] = {"error": str(e)}

    return result


def get_file_meta(file_path: str, sheets: list[str]) -> dict:
    """获取文件的行列基本信息（不加载全量数据）"""
    path = Path(file_path)
    meta = {}
    for sheet in sheets:
        try:
            if path.suffix.lower() == ".csv":
                df = pd.read_csv(file_path, encoding="utf-8-sig")
            else:
                df = pd.read_excel(file_path, sheet_name=sheet, engine="openpyxl")
            meta[sheet] = {"rows": len(df), "cols": len(df.columns)}
        except Exception as e:
            meta[sheet] = {"error": str(e)}
    return meta
