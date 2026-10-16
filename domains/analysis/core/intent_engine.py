"""
intent_engine.py — 意图执行引擎

将 AI 输出的结构化分析意图翻译成确定性的 pandas 操作。
零代码生成、零语法错误、100% 可预测。

输出格式与原有脚本输出 100% 兼容，下游步骤无需修改。
"""
from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np


class IntentEngine:
    """
    意图执行引擎。

    用法：
        engine = IntentEngine(file_path, sheets, header_info)
        summary_json = engine.run(intent)
    """

    # 聚合函数白名单（安全函数，不会引发歧义）
    SAFE_AGG_FUNCS = {"sum", "mean", "median", "min", "max", "std", "count", "size", "nunique"}

    def __init__(self, file_path: str, sheets: list[str], header_info: dict):
        self.file_path = file_path
        self.sheets = sheets
        self.header_info = header_info
        self._dfs: dict[str, pd.DataFrame] = {}
        self._logs: list[str] = []

    # ── 公共接口 ──────────────────────────────────────────

    def run(self, intent: dict, validation_fields: list[dict] = None) -> dict:
        """
        执行完整意图，返回 summary_json。

        Args:
            intent: AI 输出的结构化意图
            validation_fields: 用户指定的校验规则，用于双轨计算交叉验证
        """
        # ── Schema 预校验 ──
        schema_ok, schema_errors = self._validate_schema(intent)
        if not schema_ok:
            for err in schema_errors:
                self._log(f"  [Schema校验] ⚠ {err}")
            # 自动修正：移除不存在的列名
            intent = self._auto_fix_intent(intent, schema_errors)

        result: dict[str, Any] = {}
        for sheet in self.sheets:
            df = self._get_df(sheet)
            if df is None:
                self._log(f"跳过 sheet '{sheet}'：读取失败")
                continue
            result[sheet] = self._analyze_sheet(df, sheet, intent, validation_fields)
        return result

    def get_logs(self) -> list[str]:
        return self._logs.copy()

    # ── 内部：数据加载 ────────────────────────────────────

    def _get_df(self, sheet: str) -> pd.DataFrame | None:
        if sheet in self._dfs:
            return self._dfs[sheet]
        try:
            if self.file_path.endswith(".csv"):
                df = pd.read_csv(self.file_path, dtype_backend="numpy_nullable")
            else:
                df = pd.read_excel(self.file_path, sheet_name=sheet, dtype_backend="numpy_nullable")

            # ★ 关键修复：与 load_headers 保持列名一致（多表头场景）
            # header_info 中保存了经 _detect_and_fix_multi_header_columns 修复后的列名
            hi = self.header_info.get(sheet, {})
            if hi and "columns" in hi:
                fixed_cols = hi["columns"]
                if len(fixed_cols) == len(df.columns):
                    # 只有在列名确实不同且不是全 Unnamed 时才重命名
                    current_cols = [str(c).strip() for c in df.columns]
                    if current_cols != fixed_cols:
                        df.columns = fixed_cols
                        self._log(f"  [列名对齐] Sheet '{sheet}' 列名已对齐为 header_info 修复后的名称")
                else:
                    self._log(f"  [列名对齐警告] Sheet '{sheet}' 列数不匹配: "
                              f"header_info={len(fixed_cols)}, df={len(df.columns)}")

            self._dfs[sheet] = df
            return df
        except Exception as e:
            self._log(f"读取 '{sheet}' 失败: {e}")
            return None

    # ── 内部：单 Sheet 分析 ───────────────────────────────

    def _analyze_sheet(self, df: pd.DataFrame, sheet_name: str, intent: dict,
                         validation_fields: list[dict] = None) -> dict:
        result: dict[str, Any] = {
            "basic": {
                "rows": len(df),
                "cols": len(df.columns),
            }
        }

        # 1. 应用过滤条件
        df = self._apply_filters(df, intent.get("filters", []))
        result["basic"]["rows_after_filter"] = len(df)

        # 2. 缺失值统计
        result["missing"] = self._calc_missing(df)

        # 3. 数值列统计（覆盖所有数值列）
        numeric_cols = self._resolve_numeric_cols(df, intent)
        result["numeric_stats"] = self._calc_numeric_stats(df, numeric_cols)

        # 4. 分类列统计
        cat_cols = self._resolve_categorical_cols(df, intent, numeric_cols)
        result["categorical_stats"] = self._calc_categorical_stats(df, cat_cols)

        # 5. 分组聚合
        groupings = intent.get("groupings", [])
        result["group_aggregations"] = self._calc_group_aggregations(df, groupings, numeric_cols)

        # 6. 异常值检测
        if intent.get("need_outliers", True):
            result["anomalies"] = self._calc_anomalies(df, numeric_cols)

        # 7. 相关性矩阵（可选）
        if intent.get("need_corr", False) and len(numeric_cols) >= 2:
            result["correlations"] = self._calc_correlations(df, numeric_cols)

        # 8. 日期列统计（可选）
        date_cols = self._resolve_date_cols(df, intent)
        if date_cols:
            result["datetime_stats"] = self._calc_datetime_stats(df, date_cols)

        # 9. 校验字段双轨计算（关键指标独立验证）
        if validation_fields:
            result["_validation"] = self._calc_validation_fields(
                df, sheet_name, validation_fields, numeric_cols
            )

        return result

    # ── Schema 预校验 ─────────────────────────────────────

    @staticmethod
    def _normalize_col_name(col: str) -> str:
        """规范化列名：去除换行符、回车符、首尾空格"""
        return col.replace("\n", " ").replace("\r", "").strip()

    def _validate_schema(self, intent: dict) -> tuple[bool, list[str]]:
        """
        预校验 intent 中的列名是否存在于真实数据中。
        返回 (是否通过, 错误列表)。
        """
        errors = []
        all_known_cols_raw = set()
        for sheet in self.sheets:
            df = self._get_df(sheet)
            if df is not None:
                all_known_cols_raw.update(df.columns.tolist())

        if not all_known_cols_raw:
            return True, []  # 没有数据可读，跳过校验

        # 构建规范化映射：原始列名 -> 规范化后的列名
        all_known_cols_norm = {self._normalize_col_name(c): c for c in all_known_cols_raw}
        all_known_cols = set(all_known_cols_raw)  # 保留原始列名集合用于精确匹配

        def _check_field(field_name: str, cols: list[str]) -> list[str]:
            """检查列名是否存在（支持原始匹配和规范化匹配）"""
            errs = []
            for col in cols:
                if col in all_known_cols:
                    continue
                # 尝试规范化匹配（处理换行符差异）
                norm_col = self._normalize_col_name(col)
                if norm_col in all_known_cols_norm:
                    continue
                errs.append(f"{field_name} 中的 '{col}' 不存在于数据中")
            return errs

        # 检查 numeric_cols
        errors.extend(_check_field("numeric_cols", intent.get("numeric_cols", [])))

        # 检查 categorical_cols
        errors.extend(_check_field("categorical_cols", intent.get("categorical_cols", [])))

        # 检查 groupings
        for i, grp in enumerate(intent.get("groupings", [])):
            errors.extend(_check_field(f"groupings[{i}].by", grp.get("by", [])))
            errors.extend(_check_field(f"groupings[{i}].agg_cols", grp.get("agg_cols", [])))

        # 检查 filters
        for i, f in enumerate(intent.get("filters", [])):
            col = f.get("col", "")
            if col:
                errors.extend(_check_field(f"filters[{i}].col", [col]))

        return len(errors) == 0, errors

    def _auto_fix_intent(self, intent: dict, errors: list[str]) -> dict:
        """
        自动修正 intent：移除不存在的列名，保留有效的部分。
        """
        intent = dict(intent)  # 浅拷贝
        bad_cols = set()
        for err in errors:
            # 提取错误信息中的列名
            m = re.search(r"'([^']+)'", err)
            if m:
                bad_cols.add(m.group(1))

        if bad_cols:
            self._log(f"  [自动修正] 移除不存在的列: {', '.join(bad_cols)}")
            intent["numeric_cols"] = [c for c in intent.get("numeric_cols", []) if c not in bad_cols]
            intent["categorical_cols"] = [c for c in intent.get("categorical_cols", []) if c not in bad_cols]
            intent["groupings"] = [
                grp for grp in intent.get("groupings", [])
                if not any(c in bad_cols for c in grp.get("by", []))
                and not any(c in bad_cols for c in grp.get("agg_cols", []))
            ]
            intent["filters"] = [
                f for f in intent.get("filters", [])
                if f.get("col", "") not in bad_cols
            ]
        return intent

    def _calc_validation_fields(self, df: pd.DataFrame, sheet_name: str,
                                validation_fields: list[dict], numeric_cols: list[str]) -> dict:
        """
        对用户指定的校验字段进行独立双轨计算。
        返回 {field_name: computed_value}，用于与主结果交叉比对。
        """
        result = {}
        for rule in validation_fields:
            sheet = rule.get("sheet", sheet_name)
            if sheet != sheet_name:
                continue
            field = rule.get("field", "")
            expected = rule.get("expected")

            try:
                if field == "rows":
                    computed = len(df)
                elif field == "cols":
                    computed = len(df.columns)
                elif field.startswith("total_"):
                    col = field.split("_", 1)[1]
                    if col in numeric_cols:
                        computed = round(float(df[col].sum()), 2)
                    else:
                        continue
                elif field.startswith("mean_"):
                    col = field.split("_", 1)[1]
                    if col in numeric_cols:
                        computed = round(float(df[col].mean()), 2)
                    else:
                        continue
                elif field.startswith("min_"):
                    col = field.split("_", 1)[1]
                    if col in numeric_cols:
                        computed = round(float(df[col].min()), 2)
                    else:
                        continue
                elif field.startswith("max_"):
                    col = field.split("_", 1)[1]
                    if col in numeric_cols:
                        computed = round(float(df[col].max()), 2)
                    else:
                        continue
                else:
                    # 简写：直接匹配列名求和
                    if field in numeric_cols:
                        computed = round(float(df[field].sum()), 2)
                    else:
                        continue

                result[field] = {
                    "computed": computed,
                    "expected": expected,
                    "match": abs(float(computed) - float(expected)) < 0.01 * max(abs(float(expected)), 1.0),
                }
            except Exception as e:
                self._log(f"  [校验计算] {field} 失败: {e}")
        return result

    # ── 辅助：列名解析 ────────────────────────────────────

    def _resolve_numeric_cols(self, df: pd.DataFrame, intent: dict) -> list[str]:
        """解析数值列：intent 指定的 + 自动检测的（均做数值兼容性验证）"""
        specified = set(c for c in intent.get("numeric_cols", []) if c in df.columns)

        # 验证 AI 指定的列是否真的能转成数值（过滤掉含货币代码、单位文本的列）
        for col in list(specified):
            try:
                converted = pd.to_numeric(df[col], errors="coerce")
                valid_ratio = converted.notna().sum() / max(len(df), 1)
                if valid_ratio < 0.5:  # 超过半数无法解析为数字，踢出
                    self._log(f"  [类型修正] '{col}' 数值兼容率仅 {valid_ratio:.0%}，从数值列移除")
                    specified.discard(col)
            except Exception:
                specified.discard(col)

        auto_detected = set(df.select_dtypes(include=["number"]).columns.tolist())
        # ★ 关键修复：对 auto_detected 也做数值兼容性验证
        # dtype_backend="numpy_nullable" 下 select_dtypes 可能将 string 列误判为 number
        for col in list(auto_detected):
            try:
                converted = pd.to_numeric(df[col], errors="coerce")
                valid_ratio = converted.notna().sum() / max(len(df), 1)
                if valid_ratio < 0.5:
                    self._log(f"  [类型修正] auto_detected '{col}' 数值兼容率仅 {valid_ratio:.0%}，从数值列移除")
                    auto_detected.discard(col)
            except Exception:
                auto_detected.discard(col)

        merged = sorted(specified | auto_detected)
        if not merged:
            self._log(f"未检测到数值列")
        return merged

    def _resolve_categorical_cols(self, df: pd.DataFrame, intent: dict, numeric_cols: list[str]) -> list[str]:
        """解析分类列：排除数值列、高基数ID列"""
        specified = set(c for c in intent.get("categorical_cols", []) if c in df.columns and c not in numeric_cols)

        # 自动检测：object/string/category 类型
        auto_candidates = []
        for col in df.columns:
            if col in numeric_cols:
                continue
            dt = str(df[col].dtype).lower()
            if any(k in dt for k in ["object", "string", "category", "bool"]):
                auto_candidates.append(col)

        # 排除高基数 ID 列（唯一值 > 行数*0.8 或 > 1000）
        skip_patterns = ["id", "编号", "单号", "代码", "code", "uuid", "timestamp", "时间戳"]
        filtered = []
        for col in auto_candidates:
            if any(p in col.lower() for p in skip_patterns):
                continue
            nunique = df[col].nunique(dropna=True)
            if nunique > len(df) * 0.8 or nunique > 1000:
                continue
            filtered.append(col)

        merged = sorted(specified | set(filtered))
        return merged

    def _resolve_date_cols(self, df: pd.DataFrame, intent: dict) -> list[str]:
        """解析日期列"""
        specified = [c for c in intent.get("datetime_cols", []) if c in df.columns]
        auto = []
        for col in df.columns:
            if col in specified:
                continue
            dt = str(df[col].dtype).lower()
            if "datetime" in dt or "date" in dt:
                auto.append(col)
            elif df[col].dtype == "object":
                # 尝试推断是否为日期
                try:
                    parsed = pd.to_datetime(df[col], errors="coerce")
                    if parsed.notna().sum() > len(df) * 0.5:
                        auto.append(col)
                except Exception:
                    pass
        return specified + auto

    # ── 辅助：数据过滤 ────────────────────────────────────

    def _apply_filters(self, df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
        for f in filters:
            col = f.get("col")
            op = f.get("op", "==")
            val = f.get("value")
            if col not in df.columns:
                self._log(f"过滤列 '{col}' 不存在，跳过")
                continue
            try:
                if op == "==":
                    df = df[df[col] == val]
                elif op == "!=":
                    df = df[df[col] != val]
                elif op == ">":
                    df = df[df[col] > val]
                elif op == ">=":
                    df = df[df[col] >= val]
                elif op == "<":
                    df = df[df[col] < val]
                elif op == "<=":
                    df = df[df[col] <= val]
                elif op == "in":
                    df = df[df[col].isin(val if isinstance(val, list) else [val])]
                elif op == "notnull":
                    df = df[df[col].notna()]
            except Exception as e:
                self._log(f"过滤条件 {col} {op} {val} 失败: {e}")
        return df

    # ── 统计计算 ──────────────────────────────────────────

    def _calc_missing(self, df: pd.DataFrame) -> dict:
        result = {}
        for col in df.columns:
            missing = df[col].isna().sum()
            if missing > 0:
                result[col] = {
                    "count": int(missing),
                    "rate": round(float(missing / len(df)), 4) if len(df) > 0 else 0.0,
                }
        return result

    def _calc_numeric_stats(self, df: pd.DataFrame, numeric_cols: list[str]) -> dict:
        result = {}
        if not numeric_cols:
            return result

        # ★ 关键修复：StringDtype 等文本类型即使内容像数字，describe() 也会报 'min'/'mean' 错误
        # 先构建一个全数值视图，确保后续统计操作安全
        numeric_view = df[numeric_cols].copy()
        for col in numeric_cols:
            if not pd.api.types.is_numeric_dtype(numeric_view[col]):
                numeric_view[col] = pd.to_numeric(numeric_view[col], errors="coerce")

        try:
            desc = numeric_view.describe(percentiles=[0.25, 0.5, 0.75]).transpose()
        except Exception:
            desc = None

        for col in numeric_cols:
            try:
                # 再次确保该列可数值化（兼容 object/StringDtype 列混数字的情况）
                ser_raw = df[col]
                if not pd.api.types.is_numeric_dtype(ser_raw):
                    ser = pd.to_numeric(ser_raw, errors="coerce").dropna()
                else:
                    ser = ser_raw.dropna()

                if len(ser) == 0:
                    result[col] = {"note": "无可解析的数值数据"}
                    continue

                stats = {}
                if desc is not None and col in desc.index:
                    for k in ["min", "max", "mean", "50%", "std", "25%", "75%"]:
                        v = desc.loc[col, k]
                        key = {"50%": "median", "25%": "q25", "75%": "q75"}.get(k, k)
                        stats[key] = round(float(v), 2) if pd.notna(v) else None
                else:
                    # fallback：逐个计算
                    stats["min"] = round(float(ser.min()), 2) if len(ser) > 0 else None
                    stats["max"] = round(float(ser.max()), 2) if len(ser) > 0 else None
                    stats["mean"] = round(float(ser.mean()), 2) if len(ser) > 0 else None
                    stats["median"] = round(float(ser.median()), 2) if len(ser) > 0 else None
                    stats["std"] = round(float(ser.std()), 2) if len(ser) > 0 else None
                    stats["q25"] = round(float(ser.quantile(0.25)), 2) if len(ser) > 0 else None
                    stats["q75"] = round(float(ser.quantile(0.75)), 2) if len(ser) > 0 else None

                stats["zeros"] = int((ser == 0).sum()) if len(ser) > 0 else 0
                stats["sum"] = round(float(ser.sum()), 2) if len(ser) > 0 else 0

                # 添加异常标记
                q1 = stats.get("q25")
                q3 = stats.get("q75")
                if q1 is not None and q3 is not None:
                    iqr = q3 - q1
                    lower = q1 - 1.5 * iqr
                    upper = q3 + 1.5 * iqr
                    outliers = ser[(ser < lower) | (ser > upper)]
                    stats["outlier_count"] = int(len(outliers))
                    stats["outlier_pct"] = round(len(outliers) / len(ser) * 100, 2) if len(ser) > 0 else 0

                result[col] = stats
            except Exception as e:
                self._log(f"  [数值统计] 列 '{col}' 计算失败: {e}")
                result[col] = {"note": f"计算失败: {e}"}
        return result

    def _calc_categorical_stats(self, df: pd.DataFrame, cat_cols: list[str]) -> dict:
        result = {}
        for col in cat_cols:
            vc = df[col].value_counts(dropna=True).head(10)
            total = df[col].notna().sum()
            result[col] = {
                "unique": int(df[col].nunique(dropna=True)),
                "top_values": [
                    {
                        "value": str(v),
                        "count": int(c),
                        "pct": round(float(c / total), 4) if total > 0 else 0.0,
                    }
                    for v, c in vc.items()
                ],
            }
        return result

    def _calc_group_aggregations(self, df: pd.DataFrame, groupings: list[dict], numeric_cols: list[str]) -> list[dict]:
        result = []
        for grp in groupings:
            by_cols = grp.get("by", [])
            if isinstance(by_cols, str):
                by_cols = [by_cols]
            by_cols = [c for c in by_cols if c in df.columns]
            if not by_cols:
                continue

            agg_cols = grp.get("agg_cols", numeric_cols)
            agg_cols = [c for c in agg_cols if c in df.columns and c in numeric_cols]
            if not agg_cols:
                continue

            # ★ 关键修复：聚合前再做一层数值兼容性验证
            # 防止 select_dtypes 误判或 nullable string 类型混入 numeric_cols
            safe_agg_cols = []
            for col in agg_cols:
                try:
                    converted = pd.to_numeric(df[col], errors="coerce")
                    valid_ratio = converted.notna().sum() / max(len(df), 1)
                    if valid_ratio >= 0.5:
                        safe_agg_cols.append(col)
                    else:
                        self._log(f"  [分组聚合] 列 '{col}' 数值兼容率仅 {valid_ratio:.0%}，跳过")
                except Exception:
                    self._log(f"  [分组聚合] 列 '{col}' 数值化失败，跳过")
            if not safe_agg_cols:
                self._log(f"  [分组聚合] 分组 {by_cols} 无安全数值列可聚合，跳过")
                continue
            agg_cols = safe_agg_cols

            agg_funcs = grp.get("agg_funcs", ["sum", "mean"])
            agg_funcs = [f for f in agg_funcs if f in self.SAFE_AGG_FUNCS]
            if not agg_funcs:
                agg_funcs = ["sum", "mean"]

            label = grp.get("label", f"按{','.join(by_cols)}分组")

            try:
                # ★ 关键修复：groupby().agg() 对 StringDtype 直接报 'mean'/'min' 错误
                # 先构建数值化副本，仅转换需要聚合的列
                df_numeric = df[by_cols + agg_cols].copy()
                for c in agg_cols:
                    if not pd.api.types.is_numeric_dtype(df_numeric[c]):
                        df_numeric[c] = pd.to_numeric(df_numeric[c], errors="coerce")

                # 构建安全的 agg_dict
                agg_dict = {c: agg_funcs for c in agg_cols}
                grouped = df_numeric.groupby(by_cols, observed=True)
                agg_res = grouped.agg(agg_dict)

                # 扁平化列名：("年累计GMV", "sum") → "年累计GMV_sum"
                agg_res.columns = [f"{c}_{f}" for c, f in agg_res.columns]
                agg_res = agg_res.reset_index()

                result.append({
                    "group_by": ",".join(by_cols),
                    "aggregated_columns": agg_cols,
                    "label": label,
                    "data": self._records_safe(agg_res),
                })
            except Exception as e:
                self._log(f"  [分组聚合失败] {by_cols}: {e}")
                # fallback：只计算 sum
                try:
                    agg_res = df.groupby(by_cols, observed=True)[agg_cols].sum().reset_index()
                    result.append({
                        "group_by": ",".join(by_cols),
                        "aggregated_columns": agg_cols,
                        "label": label + "(仅sum)",
                        "data": self._records_safe(agg_res),
                    })
                except Exception as e2:
                    self._log(f"  [fallback 分组聚合也失败] {by_cols}: {e2}")
        return result

    def _calc_anomalies(self, df: pd.DataFrame, numeric_cols: list[str]) -> list[dict]:
        result = []
        for col in numeric_cols:
            try:
                ser_raw = df[col].dropna()
                if len(ser_raw) < 10:
                    continue
                # 强制数值化，防止字符串列混入导致 quantile 返回字符串
                if not pd.api.types.is_numeric_dtype(ser_raw):
                    ser = pd.to_numeric(ser_raw, errors="coerce").dropna()
                else:
                    ser = ser_raw
                if len(ser) < 10:
                    continue
                q1 = float(ser.quantile(0.25))
                q3 = float(ser.quantile(0.75))
                iqr = q3 - q1
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                outliers = ser[(ser < lower) | (ser > upper)]
                if len(outliers) > 0:
                    result.append({
                        "column": col,
                        "outlier_count": int(len(outliers)),
                        "outlier_pct": round(len(outliers) / len(ser) * 100, 2),
                        "lower": round(float(lower), 2),
                        "upper": round(float(upper), 2),
                    })
            except Exception as e:
                self._log(f"  [异常检测] 列 '{col}' 计算失败: {e}")
        return result

    def _calc_correlations(self, df: pd.DataFrame, numeric_cols: list[str]) -> dict:
        """计算数值列之间的相关性矩阵"""
        try:
            # ★ 关键修复：StringDtype 不支持 corr()，先转数值
            numeric_view = df[numeric_cols].copy()
            for col in numeric_cols:
                if not pd.api.types.is_numeric_dtype(numeric_view[col]):
                    numeric_view[col] = pd.to_numeric(numeric_view[col], errors="coerce")
            corr = numeric_view.corr().round(3)
            # 转换为记录列表，便于前端展示
            records = []
            for i, row in corr.iterrows():
                for j, val in row.items():
                    if i != j and abs(val) > 0.5:  # 只保留强相关
                        records.append({
                            "col1": str(i),
                            "col2": str(j),
                            "corr": float(val),
                        })
            return {
                "matrix": self._records_safe(corr.reset_index()),
                "strong_pairs": sorted(records, key=lambda x: abs(x["corr"]), reverse=True)[:20],
            }
        except Exception as e:
            self._log(f"相关性计算失败: {e}")
            return {}

    def _calc_datetime_stats(self, df: pd.DataFrame, date_cols: list[str]) -> dict:
        result = {}
        for col in date_cols:
            try:
                ser = pd.to_datetime(df[col], errors="coerce").dropna()
                if len(ser) == 0:
                    continue
                # 强制转换为标准 Timestamp，防止 ArrowDtype/字符串导致相减失败
                try:
                    t_min = pd.Timestamp(ser.min())
                    t_max = pd.Timestamp(ser.max())
                    range_days = int((t_max - t_min).days)
                except Exception:
                    t_min = None
                    t_max = None
                    range_days = None
                result[col] = {
                    "min": str(t_min) if t_min is not None else None,
                    "max": str(t_max) if t_max is not None else None,
                    "range_days": range_days,
                    "unique_dates": int(ser.nunique()),
                }
            except Exception as e:
                self._log(f"  [日期统计] 列 '{col}' 计算失败: {e}")
        return result

    # ── 工具方法 ──────────────────────────────────────────

    def _records_safe(self, df: pd.DataFrame) -> list[dict]:
        """将 DataFrame 转换为 JSON 安全的记录列表"""
        records = df.to_dict("records")
        return [self._json_safe(r) for r in records]

    def _json_safe(self, obj: Any) -> Any:
        """将 pandas/numpy 类型转换为 JSON 可序列化类型"""
        if isinstance(obj, dict):
            return {k: self._json_safe(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._json_safe(v) for v in obj]
        if pd.isna(obj):
            return None
        if isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64, np.float32)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if hasattr(obj, "item"):
            return obj.item()
        return obj

    def _log(self, msg: str) -> None:
        self._logs.append(msg)


# ════════════════════════════════════════════════════════════
# 列统计摘要生成器（供 Step2 意图 Prompt 使用）
# ════════════════════════════════════════════════════════════

def generate_column_profile(df: pd.DataFrame) -> dict:
    """
    生成轻量列统计摘要，让 AI 在规划意图前了解真实数据分布。
    每列最多 4 个指标，紧凑格式，总 token < 500。
    """
    profile = {}
    total = max(len(df), 1)

    for col in df.columns:
        ser = df[col]
        null_rate = ser.isna().sum() / total
        nunique = ser.nunique(dropna=True)

        # 尝试数值转换
        numeric_ser = pd.to_numeric(ser, errors="coerce")
        numeric_ratio = numeric_ser.notna().sum() / total

        # 尝试日期转换
        date_ser = pd.to_datetime(ser, errors="coerce")
        date_ratio = date_ser.notna().sum() / total

        info = {"null": f"{null_rate:.0%}", "uniq": int(nunique)}

        if numeric_ratio > 0.5:
            info["type"] = "num"
            vmin = numeric_ser.min()
            vmax = numeric_ser.max()
            # 数值格式化：大数转万/亿，保留2位
            info["range"] = _fmt_num_range(vmin, vmax)
            info["mean"] = _fmt_num(numeric_ser.mean())
        elif date_ratio > 0.5:
            info["type"] = "date"
            dmin = date_ser.min()
            dmax = date_ser.max()
            info["range"] = f"{dmin.strftime('%Y-%m')}~{dmax.strftime('%Y-%m')}"
        else:
            info["type"] = "cat"
            if nunique > total * 0.8:
                info["note"] = "ID列(唯一值≈行数)"
            elif nunique <= 10:
                vc = ser.value_counts(dropna=True).head(3)
                info["top"] = [f"{k}({v})" for k, v in vc.items()]
            else:
                info["note"] = f"{nunique}类"

        profile[col] = info

    return profile


def _fmt_num(v) -> str:
    """格式化数值：大数转万/亿"""
    if pd.isna(v):
        return "-"
    v = float(v)
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v/1e4:.2f}万"
    return f"{v:.2f}"


def _fmt_num_range(vmin, vmax) -> str:
    """格式化数值范围"""
    return f"{_fmt_num(vmin)}~{_fmt_num(vmax)}"
