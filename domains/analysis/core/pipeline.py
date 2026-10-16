"""
pipeline.py — 串联所有分析步骤，带进度回调 + 数据诊断机制
支持自定义 API 端点
数据诊断：每步输出质量报告，失败时给出具体原因
"""
from __future__ import annotations
import os
import json
import subprocess
import sys
import re
from datetime import datetime
from pathlib import Path

from .config_manager import load_config, resolve_profile, migrate_old_config
from .file_loader import (
    load_headers,
    detect_file_type,
    is_multi_header,
    get_multi_header_sheets,
    estimate_tokens,
    parse_docx,
    read_excel_full_content,
    get_sheet_names,
)
from .llm_client import call_llm
from .intent_engine import IntentEngine, generate_column_profile
from .prompt_builder import (
    build_script_prompt,
    build_intent_prompt,
    build_analysis_prompt,
    build_report_prompt,
    build_direct_analysis_prompt,
    build_analysis_plan_prompt,
    build_tweak_prompt,
    SUPPORTED_CHART_TYPES,
)


def _get_python_exe() -> str:
    """
    获取真正的 Python 解释器路径。
    打包成 exe 后 sys.executable 指向 exe 本身，需要找真正的 python.exe。
    优先级：同目录 python.exe > 父目录 python.exe > sys.executable
    """
    exe_dir = Path(sys.executable).parent
    candidates = [
        exe_dir / "python.exe",
        exe_dir / "python3.exe",
        Path("C:/Users/chenzhichao/AppData/Local/Programs/Python/Python311/python.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable  # 兜底


class AnalysisPipeline:
    def __init__(self, progress_callback=None, log_callback=None, ai_log_callback=None,
                 analysis_done_callback=None):
        """
        progress_callback(step: int, total: int, msg: str)
        log_callback(msg: str, step_tag: str = "")
        step_tag: 当前日志所属步骤标识，用于 GUI 按步骤过滤显示
            可选值: "init", "step1", "step1.5", "step2", "step3", "step4", "step5", "finish"
        ai_log_callback(call_idx, role, model, call_time, prompt, response, elapsed, error)  # 逐次调用（写文件用）
        analysis_done_callback(session_info: dict)  # 一次分析完成时汇总发送（GUI展示用）
            session_info = {
                "session_id", "file_name", "start_time",
                "calls": [{"call_idx","role","model","call_time","prompt","response","elapsed","error"}, ...],
                "total_calls", "total_elapsed", "status",  # status = "ok" | "error"
            }
        """
        self.on_progress = progress_callback or (lambda s, t, m: None)
        self.on_log = log_callback or (lambda m, **kw: print(m))
        self.on_ai_log = ai_log_callback or (lambda *a: None)
        self.on_analysis_done = analysis_done_callback or (lambda *a, **kw: None)
        self.config = load_config()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._diagnostics = []  # 诊断记录
        self._session_calls = []  # 本次分析的所有 LLM 调用（汇总用）
        self._session_file_name = ""  # 当前分析的文件名
        self._session_start_time = ""  # 本次分析开始时间
        self._current_step_tag = "init"  # 当前步骤标签，用于日志过滤

    def _set_step(self, step_tag: str):
        """设置当前步骤标签，后续所有 _log() 调用都会带上这个 tag"""
        self._current_step_tag = step_tag

    def _log(self, msg: str):
        self.on_log(msg, step_tag=self._current_step_tag)
        if hasattr(self, "log_file"):
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%H:%M:%S}][{self._current_step_tag}] {msg}\n")

    def _diagnose(self, step: str, status: str, detail: str):
        """记录诊断信息"""
        entry = {"step": step, "status": status, "detail": detail}
        self._diagnostics.append(entry)
        prefix = "✅" if status == "OK" else "⚠️" if status == "WARN" else "❌"
        self._log(f"  [诊断] {prefix} {step}: {detail}")

    def _get_diagnostic_report(self) -> str:
        """生成诊断报告"""
        lines = ["\n========== 数据质量诊断报告 =========="]
        for d in self._diagnostics:
            prefix = "✅" if d["status"] == "OK" else "⚠️" if d["status"] == "WARN" else "❌"
            lines.append(f"  {prefix} [{d['step']}] {d['detail']}")
        lines.append("=" * 42)
        return "\n".join(lines)

    # ── 分析计划缓存 ──────────────────────────────────────

    def _get_plan_cache_path(self) -> Path:
        """获取计划缓存文件路径"""
        config_dir = Path(self.config.get("output_dir", str(Path.home() / "data_analyst_output")))
        config_dir = Path(config_dir)  # 确保是 Path 对象
        return config_dir.parent / ".workbuddy" / "plan_cache.json"

    def _compute_header_fingerprint(self, header_info: dict) -> str:
        """计算表头指纹（用于判断同类文件）"""
        import hashlib
        col_str = ""
        for sheet_name in sorted(header_info.keys()):
            s = header_info[sheet_name]
            if isinstance(s, dict) and "columns" in s:
                col_str += sheet_name + ":" + ",".join(sorted(s["columns"]))
        return hashlib.md5(col_str.encode("utf-8")).hexdigest()[:12]

    def _load_cached_plan(self, header_info: dict, analysis_focus: str) -> dict | None:
        """
        加载缓存的分析计划。
        缓存命中条件：表头指纹完全匹配（列名完全一致且顺序相同）。
        """
        cache_path = self._get_plan_cache_path()
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            current_fp = self._compute_header_fingerprint(header_info)
            cached_fp = cache.get("header_fingerprint", "")
            if current_fp == cached_fp:
                self._log(f"  [缓存] 表头指纹匹配 ({current_fp})，命中缓存")
                return cache.get("plan")
        except Exception as e:
            self._log(f"  [缓存] 读取失败: {e}")
        return None

    def _save_cached_plan(self, header_info: dict, analysis_focus: str, plan: dict):
        """保存分析计划到缓存"""
        cache_path = self._get_plan_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fp = self._compute_header_fingerprint(header_info)
            cache = {
                "header_fingerprint": fp,
                "plan": plan,
                "saved_at": datetime.now().isoformat(),
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"  [缓存] 保存失败: {e}")

    def _save(self, path: str, content: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _llm(self, role: str, prompt: str) -> str:
        """
        role: plan | script | analysis | report
        v2: 通过 step_{role}_profile 从 api_profiles 中获取连接参数
        每次调用均记录完整的 prompt + response 到 ai_calls.log
        """
        # 确保配置已迁移
        migrate_old_config(self.config)

        # role → profile 配置键映射
        profile_key_map = {
            "plan": "step_script_profile",
            "script": "step_script_profile",
            "analysis": "step_analysis_profile",
            "report": "step_report_profile",
        }
        profile_key = profile_key_map.get(role, "step_script_profile")
        profile_name = self.config.get(profile_key, "")
        prof = resolve_profile(self.config, profile_name)

        base_url = prof.get("url", "")
        api_key = prof.get("key", "")
        model = prof.get("model", "")
        timeout = int(prof.get("timeout", 300))
        thinking = bool(prof.get("thinking", False))

        if not base_url or not model:
            raise RuntimeError(
                f"LLM 配置缺失（role={role}, profile={profile_name}）："
                f"url={base_url!r}, model={model!r}。"
                f"请在「API 配置」Tab 中配置接口并选择。"
            )

        # 记录本次调用编号
        if not hasattr(self, "_llm_call_count"):
            self._llm_call_count = 0
        self._llm_call_count += 1
        call_idx = self._llm_call_count
        call_time = datetime.now().strftime("%H:%M:%S")

        # ── 写入 ai_calls.log（调用前：先记录请求） ──────────
        self._write_ai_call_log(
            call_idx=call_idx,
            role=role,
            model=model,
            base_url=base_url,
            call_time=call_time,
            prompt=prompt,
            response=None,  # 请求阶段，响应未知
            elapsed=None,
            error=None,
        )

        import time as _time
        t0 = _time.time()
        error_msg = None
        response = ""
        try:
            response = call_llm(
                base_url=base_url,
                model=model,
                api_key=api_key,
                prompt=prompt,
                log_callback=self._log,
                timeout=timeout,
                thinking=thinking,
            )
            return response
        except Exception as e:
            error_msg = str(e)
            raise
        finally:
            elapsed = _time.time() - t0
            # ── 写入 ai_calls.log（调用后：更新响应） ──────
            self._write_ai_call_log(
                call_idx=call_idx,
                role=role,
                model=model,
                base_url=base_url,
                call_time=call_time,
                prompt=prompt,
                response=response if not error_msg else None,
                elapsed=elapsed,
                error=error_msg,
            )

    def _write_ai_call_log(
        self,
        call_idx: int,
        role: str,
        model: str,
        base_url: str,
        call_time: str,
        prompt: str,
        response,
        elapsed,
        error,
    ):
        """将 AI 问答详情追加写入 ai_calls.log，格式清晰，便于查阅"""
        if not hasattr(self, "log_file"):
            return  # log_file 还未初始化，跳过

        ai_log_path = str(Path(self.log_file).parent / "ai_calls.log")
        sep = "═" * 72

        # 状态行
        if error:
            status_line = f"❌ 失败 | 耗时 {elapsed:.1f}s | 错误: {error}"
        elif elapsed is not None:
            status_line = f"✅ 成功 | 耗时 {elapsed:.1f}s | 响应 {len(response or '')} 字符"
        else:
            status_line = "⏳ 发送中..."

        # 日志文件写入完整内容（不截断，方便排查）
        prompt_display = prompt

        # 响应完整显示（不截断）
        resp_display = ""
        if response:
            resp_display = response
        elif error:
            resp_display = f"（调用失败，无响应）\n错误详情：{error}"
        else:
            resp_display = "（等待响应...）"

        lines = [
            f"\n{sep}",
            f"  【AI调用 #{call_idx:02d}】 {call_time}  |  角色: {role}  |  模型: {model}",
            f"  接入点: {base_url}",
            f"  状态: {status_line}",
            f"{sep}",
            f"",
            f"▼ PROMPT ({len(prompt)} 字符)",
            f"{'─' * 60}",
            prompt_display,
            f"",
        ]

        if elapsed is not None:
            lines += [
                f"▼ RESPONSE ({len(response or '')} 字符)",
                f"{'─' * 60}",
                resp_display,
                f"",
            ]

        block = "\n".join(lines)

        try:
            with open(ai_log_path, "a", encoding="utf-8") as f:
                f.write(block)
        except Exception:
            pass  # 日志写入失败不影响主流程

        # ── 调用完成后通知 GUI（仅当有 elapsed，即调用结束） ──
        if elapsed is not None:
            # 累积到本次分析会话的调用列表（用于最终汇总）
            self._session_calls.append({
                "call_idx": call_idx,
                "role": role,
                "model": model,
                "call_time": call_time,
                "prompt": prompt,
                "response": response or "",
                "elapsed": elapsed,
                "error": error or "",
            })
            try:
                self.on_ai_log(
                    call_idx,
                    role,
                    model,
                    call_time,
                    prompt,
                    response or "",
                    elapsed,
                    error or "",
                )
            except Exception:
                pass  # GUI 回调失败不影响主流程

    def _emit_analysis_summary(self, status: str = "ok"):
        """一次分析结束时，将所有 LLM 调用汇总发送给 GUI"""
        total_elapsed = sum(c["elapsed"] for c in self._session_calls)
        session_info = {
            "session_id": self.session_id,
            "file_name": self._session_file_name or "未知文件",
            "start_time": self._session_start_time or datetime.now().strftime("%H:%M:%S"),
            "calls": list(self._session_calls),
            "total_calls": len(self._session_calls),
            "total_elapsed": round(total_elapsed, 1),
            "status": status,
        }
        try:
            self.on_analysis_done(session_info)
        except Exception:
            pass

    def _parse_validation_rules(self, raw: str, default_sheet: str = "sheet1") -> list[dict]:
        """解析用户输入的校验规则
        支持格式：
          sheet名.字段=预期值  （如 sheet1.total_销售额=123）
          字段=预期值          （如 间凌=10808，自动匹配 default_sheet）
        """
        rules = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key_part, val_part = line.split("=", 1)
            key_part = key_part.strip()
            val_part = val_part.strip()

            # 解析 sheet 和 field
            if "." in key_part:
                sheet_name, field = key_part.split(".", 1)
            else:
                # 简写格式：自动补 sheet 前缀
                sheet_name = default_sheet
                field = key_part

            # 尝试转换为数字
            try:
                expected = float(val_part)
            except ValueError:
                expected = val_part
            rules.append({
                "sheet": sheet_name,
                "field": field,
                "expected": expected,
                "raw": line,
            })
        return rules

    def _extract_actual(self, summary_json: dict, sheet: str, field: str):
        """从 summary_json 中提取实际值"""
        sheet_data = summary_json.get(sheet, {})
        if field == "rows":
            return sheet_data.get("basic", {}).get("rows")
        if field == "cols":
            return sheet_data.get("basic", {}).get("cols")
        # total_列名 -> numeric_stats[列名].sum
        if field.startswith("total_"):
            col = field[6:]
            return sheet_data.get("numeric_stats", {}).get(col, {}).get("sum")
        # mean_列名 -> numeric_stats[列名].mean
        if field.startswith("mean_"):
            col = field[5:]
            return sheet_data.get("numeric_stats", {}).get(col, {}).get("mean")
        # min_列名
        if field.startswith("min_"):
            col = field[4:]
            return sheet_data.get("numeric_stats", {}).get(col, {}).get("min")
        # max_列名
        if field.startswith("max_"):
            col = field[4:]
            return sheet_data.get("numeric_stats", {}).get(col, {}).get("max")
        # 简写格式：字段名直接匹配列 → 返回该列的 sum（最常见的汇总值）
        # 先检查是否是一个列名
        if field in sheet_data.get("numeric_stats", {}):
            return sheet_data["numeric_stats"][field].get("sum")
        # 再尝试在 categorical_stats 中查找（如 top_values 的 count）
        cat_stats = sheet_data.get("categorical_stats", {}).get(field, {})
        if cat_stats:
            top = cat_stats.get("top_values", [])
            if top:
                return top[0].get("count", 0)
        # 最后尝试精确匹配 stats 中的键（如 "sum" / "mean" 作为 field）
        for col, stats in sheet_data.get("numeric_stats", {}).items():
            if field in stats:
                return stats[field]
        return None

    def _ensure_summary_completeness(self, summary_json: dict, file_path: str,
                                      sheets: list, header_info: dict) -> dict:
        """Step 3.3：自动补全 AI 脚本遗漏的统计项。

        AI 生成的脚本经常只统计部分数值列/分类列（如只取前6个数值列、
        硬编码5个分组字段），导致 Step 4 拿不到数据只能输出 null。
        此方法读取原始数据，补全缺失的 numeric_stats 和 categorical_stats。
        """
        import pandas as pd
        import numpy as np
        import json as _json

        patched = 0
        for sheet_name in sheets:
            if sheet_name not in summary_json:
                continue
            sheet_data = summary_json[sheet_name]
            if not isinstance(sheet_data, dict):
                continue

            try:
                # ── 读取原始数据（只读数值和分类列，跳过全文本列加速）──
                df = pd.read_excel(file_path, sheet_name=sheet_name,
                                   dtype_backend='numpy_nullable')
            except Exception:
                self._log(f"  [Step3.3] ⚠ 无法重新读取 {sheet_name}，跳过补全")
                continue

            # ── 1. 补全 numeric_stats（缺失的数值列）───────
            existing_num = set(sheet_data.get("numeric_stats", {}).keys())
            all_num_cols = df.select_dtypes(include=['number']).columns.tolist()
            missing_num = [c for c in all_num_cols if c not in existing_num]

            if missing_num:
                if "numeric_stats" not in sheet_data:
                    sheet_data["numeric_stats"] = {}
                # 批量 describe
                try:
                    desc = df[missing_num].describe(
                        percentiles=[.25, .5, .75]
                    ).transpose()
                except Exception:
                    desc = None

                for col in missing_num:
                    ser = df[col].dropna()
                    try:
                        stats = {}
                        if desc is not None and col in desc.index:
                            for k in ['min', 'max', 'mean', '50%', 'std', '25%', '75%']:
                                v = desc.loc[col, k]
                                stats[{'50%': 'median', '25%': 'q25',
                                       '75%': 'q75'}.get(k, k)] = \
                                    round(float(v), 2) if pd.notna(v) else None
                        stats['zeros'] = int((ser == 0).sum()) if len(ser) > 0 else 0
                        stats['sum'] = round(float(ser.sum()), 2) if len(ser) > 0 else 0
                        sheet_data["numeric_stats"][col] = stats
                        patched += 1
                    except Exception:
                        pass

                self._log(f"  [Step3.3] 补全 {len(missing_num)} 个数值列统计 "
                          f"(原有{len(existing_num)}, 总计{len(all_num_cols)})")

            # ── 2. 补全 categorical_stats（有价值的分类列）──
            existing_cat = set(sheet_data.get("categorical_stats", {}).keys())
            # 排除：ID/编号/单号类高基数无意义列，以及已统计过的
            skip_patterns = ['id', 'ID', '编号', '单号', '代码', 'Code', 'code',
                             '时间戳', 'timestamp', 'uuid', 'UUID']
            cat_candidates = []
            for col in df.columns:
                if col in existing_cat or col in all_num_cols:
                    continue
                dt = str(df[col].dtype)
                if 'int' in dt or 'float' in dt or 'datetime' in dt:
                    continue
                # 跳过明显是 ID 的列
                if any(p in col for p in skip_patterns):
                    continue
                cat_candidates.append(col)

            missing_cat = []
            for col in cat_candidates:
                ser = df[col].dropna()
                if ser.empty or ser.nunique() > 200 or len(ser) < 10:
                    continue
                missing_cat.append(col)

            if missing_cat:
                if "categorical_stats" not in sheet_data:
                    sheet_data["categorical_stats"] = {}
                for col in missing_cat:
                    try:
                        ser = df[col].dropna()
                        top_10 = ser.value_counts().head(10)
                        sheet_data["categorical_stats"][col] = {
                            "unique": int(ser.nunique()),
                            "top_values": [
                                {"value": str(v), "count": int(c),
                                 "pct": round(float(c / len(ser)), 4)}
                                for v, c in top_10.items()
                            ]
                        }
                        patched += 1
                    except Exception:
                        pass

                self._log(f"  [Step3.3] 补全 {len(missing_cat)} 个分类列统计")

            # ── 3. 检查 group_aggregations 是否过少 ──────
            existing_groups = sheet_data.get("group_aggregations", [])
            if len(existing_groups) < 2:
                # 自动补充分组：选 unique 在 2~200 之间的非数值列作为分组键
                auto_group_cols = []
                for col in df.columns:
                    if col in all_num_cols:
                        continue
                    if any(p in col for p in skip_patterns):
                        continue
                    nu = df[col].dropna().nunique()
                    if 2 <= nu <= 200:
                        auto_group_cols.append((col, nu))

                # 按 unique 数排序（适中的更好），取前4个
                auto_group_cols.sort(key=lambda x: min(abs(x[1] - 20),
                                                       abs(x[1] - 50)))
                auto_group_cols = auto_group_cols[:4]

                if auto_group_cols and "group_aggregations" not in sheet_data:
                    sheet_data["group_aggregations"] = []

                # 对每个新分组键，取前10个数值列聚合
                agg_cols = all_num_cols[:10]
                for grp_col, _ in auto_group_cols:
                    # 避免重复已有分组
                    grp_names = [g.get("group_by", "") for g in existing_groups]
                    if grp_col in grp_names:
                        continue
                    try:
                        grp_df = df.groupby(grp_col, observed=False)[agg_cols]\
                            .agg(['sum', 'mean']).reset_index()
                        grp_df.columns = [
                            str(c).replace(',', '_') for c in grp_df.columns
                        ]
                        if grp_df.shape[0] > 200:
                            grp_df = grp_df.head(200)
                        sheet_data["group_aggregations"].append({
                            "group_by": grp_col,
                            "aggregated_columns": agg_cols,
                            "data": grp_df.to_dict(orient='records')
                        })
                        patched += 1
                    except Exception:
                        pass

                if auto_group_cols:
                    added = len(auto_group_cols)
                    total_now = len(sheet_data.get("group_aggregations", []))
                    self._log(f"  [Step3.3] 自动补充 {added} 个分组聚合 "
                              f"(总计 {total_now} 个)")

        if patched > 0:
            self._diagnose("Step3_数据补全", "WARN",
                          f"AI脚本遗漏{patched}项统计，已自动补全")
        else:
            self._diagnose("Step3_数据补全", "OK", "统计数据完整，无需补全")

        return summary_json

    def _validate_summary(self, summary_json: dict, rules: list[dict]) -> tuple[bool, str]:
        """校验统计结果，返回 (是否通过, 差异描述)
        同时输出清晰的「预期 vs 实际」对比表格到日志
        """
        if not rules:
            return True, "（用户未提供校验规则）"

        lines = ["\n" + "─" * 56]
        lines.append(f"{'校验规则':<20} {'预期值':>12} {'实际值':>12} {'结果':>8}")
        lines.append("─" * 56)

        mismatches = []
        for r in rules:
            actual = self._extract_actual(summary_json, r["sheet"], r["field"])
            expected = r["expected"]

            # 数值型比较（允许 1% 或绝对值 1 的误差）
            is_match = True
            if actual is None:
                is_match = False
            elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                tolerance = max(abs(expected) * 0.01, 1.0)
                is_match = abs(float(actual) - float(expected)) <= tolerance
            elif str(actual) != str(expected):
                is_match = False

            result = "✅ 通过" if is_match else "❌ 不符"
            actual_str = f"{actual}" if actual is not None else "（未输出）"

            # 尽量把实际值对齐
            lines.append(
                f"{r['raw']:<20} {str(expected):>12} {actual_str:>12} {result:>8}"
            )

            if not is_match:
                if actual is None:
                    mismatches.append(
                        f"  [{r['raw']}] → 预期 {expected}, 实际（脚本未输出该值）"
                    )
                else:
                    mismatches.append(
                        f"  [{r['raw']}] → 预期 {expected}, 实际 {actual}"
                    )

        lines.append("─" * 56)

        # 输出到日志（对比表格）
        self._log("\n".join(lines))

        if mismatches:
            return False, "校验失败，以下值与预期不符：\n" + "\n".join(mismatches)
        return True, f"所有 {len(rules)} 条校验规则通过"

    def _sanitize_script(self, script_code: str) -> tuple[str, list[str]]:
        """
        执行前自动修复 AI 生成脚本中的已知错误模式。
        返回 (修复后的代码, 修复说明列表)
        """
        fixes = []

        # 1. 修复 read_excel 误用 encoding 参数
        # 匹配：pd.read_excel(任意参数, encoding=任意值, 任意参数) 或结尾
        pattern_encoding = re.compile(
            r'(pd\.read_excel\([^)]*?),\s*encoding\s*=\s*[\'"][^\'"]*[\'"]\s*([,)])',
            re.DOTALL
        )
        new_code, n = pattern_encoding.subn(r'\1\2', script_code)
        if n > 0:
            fixes.append(f"自动修复：移除了 read_excel() 中的 encoding 参数（共 {n} 处）")
            script_code = new_code

        # 2. 如果 encoding 参数是最后一个（结尾是 ) 被消掉了需要补回
        # 上面的正则 \2 已经保留了 ) 或 , 所以不需要额外处理

        # 3. 修复 select_dtypes 参数大小写（Object -> object）
        pattern_dtype = re.compile(r"select_dtypes\(include\s*=\s*['\"]Object['\"]")
        new_code2, n2 = pattern_dtype.subn(
            lambda m: m.group().replace("Object", "object"), script_code
        )
        if n2 > 0:
            fixes.append(f"自动修复：select_dtypes include 参数大小写（Object→object，共 {n2} 处）")
            script_code = new_code2

        # 4. 修复 df.describe(skipna=...) — pandas 4.x 已移除 describe() 的 skipna 参数
        # 匹配 .describe(任意参数, skipna=xxx, 任意参数) 或 .describe(skipna=xxx)
        pattern_describe = re.compile(
            r'\.describe\(([^)]*?),?\s*skipna\s*=\s*\w+\s*,?\s*([^)]*)\)',
            re.DOTALL
        )
        def _fix_describe(m):
            before = m.group(1).strip().rstrip(',')
            after  = m.group(2).strip().lstrip(',')
            inner  = ', '.join(p for p in [before, after] if p)
            return f'.describe({inner})'
        new_code3, n3 = pattern_describe.subn(_fix_describe, script_code)
        if n3 > 0:
            fixes.append(f"自动修复：移除 describe() 中的 skipna 参数（共 {n3} 处，pandas 4.x 已不支持）")
            script_code = new_code3

        # 5. 修复 pd.api.types.is_categorical_dtype（pandas 2.2+ 已废弃/移除）
        # 替换为 isinstance(df[col].dtype, pd.CategoricalDtype)
        # 简单处理：把 is_categorical_dtype(xxx) 替换为 hasattr(xxx, 'cat')
        pattern_cat = re.compile(r'pd\.api\.types\.is_categorical_dtype\(([^)]+)\)')
        new_code4, n4 = pattern_cat.subn(
            lambda m: f"isinstance({m.group(1)}.dtype, pd.CategoricalDtype)",
            script_code
        )
        if n4 > 0:
            fixes.append(f"自动修复：is_categorical_dtype → isinstance(x.dtype, pd.CategoricalDtype)（共 {n4} 处）")
            script_code = new_code4

        # 6. 修复 df.corr() 对非数值列报错（pandas 4.x 要求只传数值列）
        # 替换 df.corr() 为 df.select_dtypes(include='number').corr()
        pattern_corr = re.compile(r'\bdf\.corr\(\)')
        new_code5, n5 = pattern_corr.subn('df.select_dtypes(include="number").corr()', script_code)
        if n5 > 0:
            fixes.append(f"自动修复：df.corr() → df.select_dtypes(include='number').corr()（共 {n5} 处）")
            script_code = new_code5

        # 7. 修复 scipy/sklearn 等库直接 import 但未 _ensure 安装
        # 检测到 from scipy / import scipy / from sklearn 等，在脚本开头注入 _ensure 调用
        third_party_map = [
            (re.compile(r'^\s*(?:from|import)\s+scipy\b', re.MULTILINE), 'scipy'),
            (re.compile(r'^\s*(?:from|import)\s+sklearn\b', re.MULTILINE), 'scikit-learn', 'sklearn'),
            (re.compile(r'^\s*(?:from|import)\s+matplotlib\b', re.MULTILINE), 'matplotlib'),
            (re.compile(r'^\s*(?:from|import)\s+seaborn\b', re.MULTILINE), 'seaborn'),
            (re.compile(r'^\s*(?:from|import)\s+statsmodels\b', re.MULTILINE), 'statsmodels'),
        ]
        for entry in third_party_map:
            pat, pkg = entry[0], entry[1]
            import_name = entry[2] if len(entry) > 2 else None
            if pat.search(script_code):
                # 检查是否已经有对应的 _ensure 调用
                ensure_present = re.search(
                    r"_ensure\s*\(\s*['\"]" + re.escape(pkg) + r"['\"]",
                    script_code
                )
                if not ensure_present:
                    # 在第一个 import 或 from 语句之前，插入 _ensure 调用
                    # 找到 def _ensure 定义的位置，在其后追加调用
                    ensure_call = f"_ensure('{pkg}'" + (f", '{import_name}'" if import_name else "") + ")"
                    # 在 def _ensure 函数定义块结束后插入（找 _ensure 定义的下一个空行）
                    if '_ensure' in script_code:
                        # 在 _ensure 定义后插入调用行
                        script_code = re.sub(
                            r'(def _ensure\([^)]+\):[^\n]+\n(?:[ \t]+[^\n]*\n)*)',
                            lambda m: m.group(0) + f"{ensure_call}\n",
                            script_code,
                            count=1
                        )
                    else:
                        # 没有 _ensure 定义时，在脚本开头注入完整安装代码
                        inject = (
                            "import subprocess, sys\n"
                            "def _ensure(pkg, import_name=None):\n"
                            "    try:\n"
                            "        __import__(import_name or pkg)\n"
                            "    except ImportError:\n"
                            "        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg, '-q'])\n"
                            f"{ensure_call}\n"
                        )
                        script_code = inject + script_code
                    fixes.append(f"自动修复：为 {pkg} 注入 _ensure() 自动安装调用")

        # 8. 移除 read_excel 的 dtype=str 参数（会导致所有列变字符串，后续 corr()/数值操作全崩）
        # 匹配 dtype='str' / dtype="str" / dtype=str 三种写法
        pattern_dtype_str = re.compile(
            r'(pd\.read_excel\([^)]*?),\s*dtype\s*=\s*(?:[\'"]str[\'"]|str)\s*([,)])',
            re.DOTALL
        )
        new_code8, n8 = pattern_dtype_str.subn(r'\1\2', script_code)
        if n8 > 0:
            fixes.append(f"自动修复：移除 read_excel() 中的 dtype=str 参数（共 {n8} 处，会导致全字符串崩溃）")
            script_code = new_code8

        # 9. 修复 select_dtypes(include=['object']) 废弃警告（pandas 4.x）
        # 替换为 select_dtypes(include=['object', 'string'])
        pattern_sel_obj = re.compile(r"select_dtypes\(include\s*=\s*\['object'\]\)")
        new_code9, n9 = pattern_sel_obj.subn(
            "select_dtypes(include=['object', 'string'])", script_code
        )
        if n9 > 0:
            fixes.append(f"自动修复：select_dtypes(include=['object']) 添加 'string' 兼容（共 {n9} 处）")
            script_code = new_code9

        # 10. 修复 .agg() 中 f-string 或多位置参数错误
        # AI 常生成 .agg(f'sum', 'mean') 或 .agg('sum', 'mean') 这种错误写法
        # pandas agg() 的位置参数期望 callable，字符串会被误解；正确做法是用列表或命名参数
        # 匹配模式：.agg(f'...', '...' , ...) 或 .agg('func1', 'func2', ...) （多个字符串位置参数）

        def _fix_agg_to_list(m):
            """把 .agg('sum','mean') → .agg(['sum','mean'])"""
            full = m.group(0)
            inner = full[5:-1]  # 去掉 .agg( 和 )
            # 提取所有字符串内容（支持单引号和双引号）
            strs = re.findall(r"""['"]([^'"]+)['"]""", inner)
            if len(strs) >= 2:
                list_content = ', '.join(repr(s) for s in strs)
                return f'.agg([{list_content}])'
            return full

        # 10a. 匹配 .agg(f'...', '...', ...) — f-string 形式（第一个参数带 f 前缀，后续为普通字符串）
        pattern_agg_fstring = re.compile(
            r"""\.agg\(\s*f'[^']+'(?:\s*,\s*'[^']+')*\s*\)"""
        )

        # 10b. 匹配 .agg('str1', 'str2', ...) 或 .agg("str1", "str2", ...)
        # 关键特征：以 .agg( 开头，内部是逗号分隔的多个纯字符串字面量，不含=号（排除命名聚合）
        pattern_agg_multi = re.compile(
            r"""\.agg\(\s*'(?:[^'\\]|\\.)*'\s*,\s*'(?:[^'\\]|\\.)*'(?:\s*,\s*'(?:[^'\\]|\\.)*')*\s*\)"""
        )
        # 同样匹配双引号版本（修复：原正则嵌套层级错误导致无法匹配多位置参数）
        pattern_agg_multi_dq = re.compile(
            r'''\.agg\(\s*"(?:[^"\\]|\\.)*"(?:\s*,\s*"(?:[^"\\]|\\.)*")*\s*\)'''
        )

        new_code10a, n10a = pattern_agg_fstring.subn(_fix_agg_to_list, script_code)
        if n10a > 0:
            fixes.append(f"自动修复：.agg() f-string 参数改为列表形式（共 {n10a} 处）")
            script_code = new_code10a

        new_code10b, n10b = pattern_agg_multi.subn(_fix_agg_to_list, script_code)
        if n10b > 0:
            fixes.append(f"自动修复：.agg() 多位置参数(单引号)改为列表形式（共 {n10b} 处，pandas agg 要求列表/命名聚合）")
            script_code = new_code10b

        new_code10c, n10c = pattern_agg_multi_dq.subn(_fix_agg_to_list, script_code)
        if n10c > 0:
            fixes.append(f"自动修复：.agg() 多位置参数(双引号)改为列表形式（共 {n10c} 处，pandas agg 要求列表/命名聚合）")
            script_code = new_code10c

        # 11. 修复 .agg{...} 花括号语法错误 → .agg(...)
        # AI 常把 .agg(...) 误写为 .agg{...}（花括号），导致 SyntaxError
        # 匹配模式：.agg{任意内容} 或 .agg ( { 任意内容 } )
        pattern_agg_brace = re.compile(r'\.agg\s*\{')
        new_code11, n11 = pattern_agg_brace.subn('.agg(', script_code)
        if n11 > 0:
            fixes.append(f"自动修复：.agg{{}} 花括号改为圆括号 .agg()（共 {n11} 处，花括号会导致 SyntaxError）")
            script_code = new_code11

        # 12. 通用修复：groupby 后的 .agg({ 也需要处理（可能有空格）
        # 注意：排除以下合法情况：
        #   a) 命名聚合 .agg({"col": "sum"}) 格式（含 "key": value 的字典字面量模式）
        #   b) dict comprehension .agg({key: val for ...}) 模式（含 for ... in 关键字）
        # 2026-05-22 修复：旧版用 (?!.*?for) 负向前瞻排除 dict comprehension，
        #   但 .*? 先尝试空匹配 → 空串不含 for → 负向前瞻误通过 → { 被误删 → SyntaxError
        #   改为 callback 动态判断，彻底解决
        def _fix_agg_brace_with_guard(m):
            """callback: 只删除【非合法字典】的 { ，保留字面量和 dict comprehension"""
            after = m.string[m.end():]  # { 之后的内容
            # 排除 b) dict comprehension（含 for ... in 关键字）
            if re.search(r'\bfor\s+\w+\s+in\b', after):
                return m.group(0)  # 保留原样
            # 排除 a) 命名字典字面量（含 "key": 模式）
            if re.search(r'''["'][^'"]+["']\s*:''', after[:200]):
                return m.group(0)  # 保留原样
            return '.agg('  # 删除 {
        pattern_agg_brace2 = re.compile(r'\.agg\s*\(\s*\{')
        new_code12, n12 = pattern_agg_brace2.subn(_fix_agg_brace_with_guard, script_code)
        if n12 > 0:
            fixes.append(f"自动修复：.agg({{ 圆括号内嵌花括号改为 .agg(（共 {n12} 处）")
            script_code = new_code12

        # 15. [已移除旧通用版] 旧的 pattern_missing_brace / _add_dict_braces 已删除
        # 原因：该正则使用 (?!.*?[{\[]) 负向前瞻存在 .*? 空匹配 bug（同 Rule 12），
        #       且其 _add_dict_braces 回调的 f-string 存在语法错误 (single '}' not allowed)。
        #       精确版 pattern_agg_missing_dict_brace（下方 Rule 15）已完全覆盖 .agg() 场景。
        # 更精确的版本：只在 .agg() 内部修复
        # 2026-05-22 修复两个 regex bug:
        #   a) group4 [^,)\]] 排除了 ] 但 value 可能含 ]（如 metric["agg"]）→ 改为 [^,)]
        #   b) group5 for\s+[^,)]+?\s+in 排除了逗号，但 iter 变量间有逗号（for k, v in）
        #      → for 子句内改用 [^)] 允许逗号
        pattern_agg_missing_dict_brace = re.compile(
            r'(\.agg\()([^{]*?)([\w\.]+(?:\[[\'"][^\]]+[\'"]\])?)\s*:\s*([^,)]+?)\s+(for\s+[^)]+?\s+in\s+[^)]+)(\))'
            , re.DOTALL
        )
        def _fix_agg_dict_comp(m):
            """把 .agg(key: val for ...) → .agg({key: val for ...})"""
            return f'{m.group(1)}{m.group(2)}{{{m.group(3)}: {m.group(4)} {m.group(5)}}}{m.group(6)}'
        new_code15, n15 = pattern_agg_missing_dict_brace.subn(_fix_agg_dict_comp, script_code)
        if n15 > 0:
            fixes.append(f"自动修复：.agg() 内字典推导式补充缺失的 {{}} 花括号（共 {n15} 处）")
            script_code = new_code15

        # 13. 修复多表头 Excel 列名不匹配问题（sort_values / 列引用使用了不存在于 df.columns 的名称）
        # 问题：Layer 1 (file_loader.py) 已将 header_info.columns 修复为真实列名（如 "年累计毛利"），
        #       AI 拿到这些名字后写入脚本，但 pd.read_excel() 内部实际读出的 df.columns
        #       仍然是原始的 "Unnamed: N"（因为 Layer 1 只改了元数据，没改 pandas 读取行为）。
        # 数据源：file_loader.py 在多表头修复时保存了 _original_columns（pandas 原始输出列名列表），
        #         与 columns（修正后的显示列名列表）一一对应。
        header_info = getattr(self, '_header_info', None)
        if header_info:
            col_mapping = {}
            for sheet_name, sheet_info in header_info.items():
                original_cols = sheet_info.get("_original_columns")
                display_cols = sheet_info.get("columns")
                # 只有多表头文件才有 _original_columns 字段
                if not original_cols or not display_cols:
                    continue
                # 构建「原始 pandas 列名 → 显示列名」的映射
                for raw_col, display_col in zip(original_cols, display_cols):
                    raw_str = str(raw_col).strip()
                    disp_str = str(display_col).strip()
                    # 跳过：名称相同、原始名不是 Unnamed、显示名为空/无效
                    if raw_str == disp_str or not disp_str:
                        continue
                    if not raw_str.startswith("Unnamed:"):
                        continue
                    if disp_str in ("nan", "None", ""):
                        continue
                    col_mapping[raw_str] = disp_str

            if col_mapping:
                # 检查脚本是否引用了映射中的目标列名（即显示名/真实列名）
                # 2026-05-23 修复：旧正则 r'["\']([^"\']+)["\']' 会贪婪跨行匹配，
                #   当 AI 生成的脚本用多行列表格式写列名时（如 ['col1', 'col2',\n  'col3']），
                #   [^"']+ 会吞掉中间的 \n 和 ', ' 导致匹配到超长字符串，
                #   使得 set(col_mapping.values()) & set(matches) 交集为空 → Rule 13 不触发！
                # 修复：使用非贪婪 + 排除换行符的正则，确保只匹配单行内的引号字符串
                _quoted_strings = re.findall(r"""['"]([^'"\r\n]*)['"]""", script_code)
                referenced_cols = set(col_mapping.values()) & set(_quoted_strings)
                if referenced_cols:
                    mapping_json = json.dumps(col_mapping, ensure_ascii=False)
                    # 2026-05-22 重大修复：
                    #   旧方案：在脚本开头注入 rename 代码 + 'df' in locals() 检查
                    #   问题：注入时 df 还未定义（pd.read_excel 在后面），locals() 为空 → rename 被跳过
                    #   新方案：在每处 df = pd.read_excel(...) 之后注入 rename 调用
                    inject_rename_header = (
                        "\n# ── 自动修复：多表头列名映射定义（系统注入）──\n"
                        f"_col_map = {mapping_json}\n"
                        "def _auto_rename_df(df):\n"
                        "    _actual_cols = list(df.columns)\n"
                        "    _rename_dict = {k: v for k, v in _col_map.items() if k in _actual_cols}\n"
                        "    if _rename_dict:\n"
                        "        df = df.rename(columns=_rename_dict)\n"
                        "    return df\n"
                    )
                    # 在脚本最前面注入映射定义和辅助函数
                    script_code = inject_rename_header + "\n" + script_code
                    # 然后把每个 df = pd.read_excel(...) 替换为 df = _auto_rename_df(pd.read_excel(...))
                    # 捕获组只包含 pd.read_excel(...) 部分，df = 前缀在替换字符串中写死
                    pattern_read_excel_assign = re.compile(r'(df\s*=\s*)(pd\.read_excel\([^;)]+\))')
                    new_code13, n13_sub = pattern_read_excel_assign.subn(
                        r'\1_auto_rename_df(\2)', script_code
                    )
                    if n13_sub > 0:
                        script_code = new_code13

                    # Rule 13b: 修复 usecols 参数中的显示列名 → 原始 Unnamed 列名
                    # 问题：AI 用 usecols=['分销商全名称','年累计GMV',...] 但文件列是 Unnamed:N
                    #        pd.read_excel(usecols=[...]) 找不到列 → ValueError
                    # 修复：构建反向映射 display→raw，替换 usecols 列表中的显示名
                    reverse_map = {v: k for k, v in col_mapping.items()}  # display → Unnamed:N
                    if reverse_map:
                        def _fix_usecols(m):
                            """把 usecols=[...] 中的显示列名替换为原始 Unnamed:N 名"""
                            usecols_content = m.group(1)
                            # 提取列表中的字符串元素，替换显示名为原始名
                            def _replace_col(cm):
                                col_name = cm.group(1)
                                return f"'{reverse_map.get(col_name, col_name)}'"
                            fixed = re.sub(r"'([^']+)'", _replace_col, usecols_content)
                            fixed = re.sub(r'"([^"]+)"', lambda cm: f'"{reverse_map.get(cm.group(1), cm.group(1))}"', fixed)
                            return f"usecols={fixed}"
                        # re.DOTALL 使 [^\]]+ 也匹配换行，支持多行 usecols 列表
                        new_code13b, n13b = re.subn(r'usecols=(\[[^\]]+\])', _fix_usecols, script_code, flags=re.DOTALL)
                        # 同时修复变量赋值形式的列名列表，如 needed_columns = ['分销商全名称', ...]
                        def _fix_list_literal(m):
                            """把列表字面量中的显示列名替换为原始列名"""
                            lst = m.group(1)
                            def _r(cm2):
                                col_name = cm2.group(1)
                                return f"'{reverse_map.get(col_name, col_name)}'"
                            return f"[{re.sub(chr(39) + r'([^' + chr(39) + r']+)' + chr(39), _r, lst)}]"
                        # 只替换用于 usecols 相关变量名的列表
                        usecols_var_pattern = re.compile(
                            r'\b(?:needed_columns|usecols_list|cols_to_read|selected_cols|use_cols|cleaned_columns|columns_to_use|use_columns|cols|column_list)\s*=\s*(\[[^\]]+\])',
                            re.DOTALL
                        )
                        new_code13c, n13c = usecols_var_pattern.subn(
                            lambda m: m.group(0).replace(m.group(1), _fix_list_literal(m).replace(m.group(0), '')),
                            script_code
                        )
                        # 更安全的替换：直接处理变量的值部分
                        def _fix_usecols_var(m):
                            prefix = m.group(0)[:m.start(1)-m.start()]
                            return prefix + _fix_usecols_content(m.group(1))
                        def _fix_usecols_content(lst):
                            def _r(cm2):
                                col_name = cm2.group(1)
                                return f"'{reverse_map.get(col_name, col_name)}'"
                            return re.sub(r"'([^']+)'", _r, lst)
                        # 使用 re.sub 对变量赋值做替换
                        for var_name in ('needed_columns', 'usecols_list', 'cols_to_read', 'selected_cols', 'use_cols', 'cleaned_columns', 'columns_to_use', 'use_columns', 'cols', 'column_list'):
                            pat = re.compile(rf'\b{var_name}\s*=\s*(\[[^\]]+\])', re.DOTALL)
                            def _vfix(m, vn=var_name):
                                orig = m.group(1)
                                fixed_lst = _fix_usecols_content(orig)
                                return m.group(0).replace(orig, fixed_lst, 1)
                            new_code13c, n13c2 = pat.subn(_vfix, script_code)
                            if n13c2 > 0:
                                script_code = new_code13c
                                n13b += n13c2
                        if n13b > 0:
                            script_code = new_code13b if n13b > 0 else script_code
                            fixes.append(f"自动修复：usecols 中显示列名→原始列名（多表头兼容）")

                    fixes.append(f"自动修复：注入多表头列名映射（{len(col_mapping)} 个原始列→显示列名，防止 KeyError，已绑定 read_excel 输出）")

        # 14. 修复 .agg("非合法聚合函数名字符串") — AI 常把列名字符串传给 agg()
        #     pandas 会尝试把字符串当作聚合函数名（如 "sum"/"mean"），
        #     但如果传入的是列名（如 "授信额度(原币)"），就会在内部 aggregate() 报错。
        #     同时检测链式调用：.agg("col1").agg("col2")
        VALID_AGG_FUNC_NAMES = {
            'sum', 'mean', 'median', 'min', 'max', 'count', 'std', 'var',
            'first', 'last', 'size', 'prod', 'sem', 'skew', 'kurt',
            'quantile', 'nunique', 'idxmax', 'idxmin', 'mad', 'mode',
            'any', 'all', 'memory_usage', 'describe',
        }
        pattern_agg_single_str = re.compile(r'\.agg\(\s*([\'"])([^\'"]+)\1\s*\)')
        bad_agg_matches = pattern_agg_single_str.findall(script_code)
        if bad_agg_matches:
            bad_names = [name for quote, name in bad_agg_matches
                         if name not in VALID_AGG_FUNC_NAMES and not name.startswith('_')]
            if bad_names:
                # 尝试修复：将 .agg("列名") 标注为注释，阻止其执行
                # 因为这种写法的语义不明确（AI 可能想求和、可能想选择列）， safest 是删除
                def _comment_out_bad_agg(m):
                    full = m.group(0)
                    return f"  # [已移除错误写法] 原代码: {full}  （.agg() 不能接受列名字符串作为参数）"
                new_code14, n14 = pattern_agg_single_str.subn(_comment_out_bad_agg, script_code)
                # 只替换非法的（保留合法的如 sum/mean）
                # 上面 subn 会替换所有单字符串，需要恢复合法的
                for quote, name in bad_agg_matches:
                    if name in VALID_AGG_FUNC_NAMES or name.startswith('_'):
                        # 恢复合法的
                        original = f'.agg({quote}{name}{quote})'
                        commented = f'  # [已移除错误写法] 原代码: {original}  （.agg() 不能接受列名字符串作为参数）'
                        new_code14 = new_code14.replace(commented, original, 1)
                if n14 > 0 and len(bad_names) > 0:
                    fixes.append(f"自动修复：移除 .agg() 中 {len(bad_names)} 个非法列名字符串参数（{', '.join(bad_names[:3])}{'...' if len(bad_names) > 3 else ''}，会导致 pandas 内部 aggregate() 报错）")
                    script_code = new_code14

        # 16. 修复 agg_dict 里把聚合函数名作为列名 key 的错误
        #     错误写法：agg_dict['count'] = 'sum'  或  agg_dict['mean'] = ['sum','mean']
        #     这会让 pandas 在 grouped.agg(agg_dict) 时找列名 'count'/'mean'，抛出 KeyError
        #     正确写法：agg_dict[col] = ['sum', 'mean']  （key 必须是真实列名）
        AGG_FUNC_KEYS = {'sum', 'mean', 'count', 'std', 'var', 'min', 'max', 'median',
                         'first', 'last', 'size', 'nunique', 'sem', 'skew'}
        pattern_bad_agg_key = re.compile(
            r'''(agg_dict\s*\[)\s*(['"]\s*(?:''' + '|'.join(AGG_FUNC_KEYS) + r''')\s*['"])\s*(\])''',
            re.IGNORECASE
        )
        bad_agg_key_matches = pattern_bad_agg_key.findall(script_code)
        if bad_agg_key_matches:
            def _comment_bad_agg_key(m):
                return f"# [已禁用] agg_dict 的 key 必须是列名，不能是聚合函数名: {m.group(0)}"
            new_code16, n16 = pattern_bad_agg_key.subn(_comment_bad_agg_key, script_code)
            if n16 > 0:
                fixes.append(f"自动修复：注释掉 agg_dict 中 {n16} 个错误的聚合函数名 key（应使用真实列名作为 key）")
                script_code = new_code16

        return script_code, fixes

    def _llm_script(self, prompt: str, label: str = "script") -> str:
        """调用 AI 生成脚本，自动清理 markdown 代码块，空响应最多重试2次。"""
        for attempt in range(3):
            raw = self._llm(label, prompt)
            cleaned = re.sub(r'^```python\s*', '', raw, flags=re.MULTILINE)
            cleaned = re.sub(r'^```\s*', '', cleaned, flags=re.MULTILINE)
            cleaned = cleaned.strip()
            if cleaned:
                return cleaned
            self._log(f"  [Step2] AI 返回空响应 (第 {attempt+1}/3 次)，重试中...")
        raise RuntimeError("AI 连续3次返回空响应，脚本生成失败")

    def _run_script_with_retry(
        self,
        script_code: str,
        script_path: str,
        log_dir: Path,
        header_info: dict,
        file_path: str,
        sheets: list[str],
        data_description: str,
        analysis_focus: str,
        validation_rules: list[dict],
        max_retries: int = 1,
    ) -> dict:
        """执行脚本，失败或校验不通过时自动重试（重新生成脚本）"""
        for attempt in range(max_retries + 1):
            # 将 header_info 暴露给 _sanitize_script（Rule 13 多表头修复需要）
            self._header_info = header_info
            # 执行前自动修复已知错误模式
            script_code, auto_fixes = self._sanitize_script(script_code)
            if auto_fixes:
                for fix_msg in auto_fixes:
                    self._log(f"  [自动修复] {fix_msg}")
            self._save(script_path, script_code)
            self._log(f"  脚本已保存: {script_path}（{len(script_code)} 字符）")
            if attempt > 0:
                self._log(f"  [重试] 第 {attempt}/{max_retries} 次重新生成脚本...")
                self.on_progress(3, 6, f"脚本异常，第 {attempt} 次重试生成...")

            # 执行脚本（隐藏控制台窗口）
            self._set_step("step3")
            self.on_progress(4, 6, "本地执行数据提炼（pandas 统计）...")
            self._log("\n[Step 3] 本地执行 Python 提炼脚本")

            # ===== 最终防线：从磁盘读取脚本，强制消毒 =====
            # 无论内存中的 _sanitize_script 是否执行过，这里再扫一遍磁盘文件
            # 解决：GUI 未重启导致旧代码运行、缓存问题等所有边界情况
            if os.path.exists(script_path):
                with open(script_path, 'r', encoding='utf-8') as f:
                    disk_code = f.read()
                disk_fixed, disk_fixes = self._sanitize_script(disk_code)
                if disk_fixes:
                    for fix_msg in disk_fixes:
                        self._log(f"  [最终防线-修复] {fix_msg}")
                    with open(script_path, 'w', encoding='utf-8') as f:
                        f.write(disk_fixed)
                    self._log(f"  [最终防线] 脚本文件已消毒保存（共 {len(disk_fixes)} 处修复）")
            # ===== 最终防线结束 =====

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE

            result = subprocess.run(
                [_get_python_exe(), script_path],
                capture_output=True,
                text=True,
                timeout=1800,
                cwd=str(log_dir),
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.stderr:
                self._log(f"  [stderr] {result.stderr}")

            # 执行失败
            if result.returncode != 0:
                # 检测是否为模块缺失错误 → 自动安装后重新执行（不重新生成脚本）
                module_match = re.search(r"ModuleNotFoundError: No module named '(\S+)'", result.stderr)
                if module_match and attempt <= max_retries:
                    missing_module = module_match.group(1)
                    self._log(f"  [自动安装] 检测到缺少模块: {missing_module}，正在安装...")
                    try:
                        install_result = subprocess.run(
                            [_get_python_exe(), '-m', 'pip', 'install', missing_module, '-q'],
                            capture_output=True, text=True, timeout=120,
                        )
                        if install_result.returncode == 0:
                            self._log(f"  [自动安装] ✅ {missing_module} 安装成功，重新执行脚本...")
                            # 不 increment attempt，直接 continue 重新执行
                            continue
                        else:
                            self._log(f"  [自动安装] ❌ pip 安装失败: {install_result.stderr}")
                    except Exception as install_err:
                        self._log(f"  [自动安装] ❌ 安装异常: {install_err}")

                self._diagnose(f"Step3_脚本执行_尝试{attempt}", "FAIL",
                               f"退出码 {result.returncode}，错误: {result.stderr}")
                error_feedback = (
                    f"脚本执行失败（退出码 {result.returncode}）。\n"
                    f"错误信息：{result.stderr}\n"
                    f"请检查并修复脚本中的错误（如 select_dtypes 参数大小写、列名拼写、数据类型处理等），重新生成。"
                )
                if attempt < max_retries:
                    self._log(f"  执行失败，准备重试生成脚本...")
                    prompt_retry = build_script_prompt(
                        file_path=file_path,
                        sheets=sheets,
                        header_info=header_info,
                        data_description=data_description,
                        analysis_focus=analysis_focus,
                        error_feedback=error_feedback,
                        validation_fields=validation_rules,
                    )
                    self._save(str(log_dir / f"step2_prompt_retry{attempt}.txt"), prompt_retry)
                    script_code = self._llm_script(prompt_retry)
                    continue
                raise RuntimeError(f"脚本执行失败（退出码 {result.returncode}），已重试 {max_retries} 次仍未成功：\n{result.stderr}")

            stdout = result.stdout.strip()
            self._save(str(log_dir / f"step3_raw_output_{attempt}.txt"), stdout)

            # 提取 JSON（增强：同时支持对象 {...} 和数组 [...] 格式）
            json_match = re.search(r'(\{.*\}|\[.*\])', stdout, re.DOTALL)
            if not json_match:
                self._diagnose(f"Step3_JSON解析_尝试{attempt}", "FAIL", "脚本未输出有效 JSON")
                error_feedback = "脚本未输出有效的 JSON 数据，请确保最后一行是 print(json.dumps(result, ensure_ascii=False, default=str))。"
                if attempt < max_retries:
                    prompt_retry = build_script_prompt(
                        file_path=file_path, sheets=sheets, header_info=header_info,
                        data_description=data_description, analysis_focus=analysis_focus,
                        error_feedback=error_feedback,
                        validation_fields=validation_rules,
                    )
                    self._save(str(log_dir / f"step2_prompt_retry{attempt}.txt"), prompt_retry)
                    script_code = self._llm_script(prompt_retry)
                    continue
                raise RuntimeError("脚本未输出有效 JSON，已重试多次仍未成功")

            summary_json = json.loads(json_match.group())
            # ★ 防御：AI 脚本可能输出 [{"Sheet":{...}}] 数组而非对象
            if isinstance(summary_json, list):
                if len(summary_json) > 0 and isinstance(summary_json[0], dict):
                    summary_json = summary_json[0]
                    self._log("  [注意] 脚本输出 JSON 为数组格式，已自动提取第一个元素")
                else:
                    self._diagnose("Step3_JSON结构", "FAIL", "脚本的 JSON 输出为空数组或非法格式")
                    if attempt < max_retries:
                        error_feedback = "脚本的 JSON 输出格式错误（空数组或非法格式）。请确保输出格式为: print(json.dumps({'Sheet名': {...}}, ensure_ascii=False))"
                        prompt_retry = build_script_prompt(
                            file_path=file_path, sheets=sheets, header_info=header_info,
                            data_description=data_description, analysis_focus=analysis_focus,
                            error_feedback=error_feedback,
                            validation_fields=validation_rules,
                        )
                        self._save(str(log_dir / f"step2_prompt_retry{attempt}.txt"), prompt_retry)
                        script_code = self._llm_script(prompt_retry)
                        continue
                    raise RuntimeError(f"脚本输出 JSON 结构异常: {type(summary_json).__name__}")
            self._save(str(log_dir / f"step3_summary_{attempt}.json"),
                       json.dumps(summary_json, ensure_ascii=False, indent=2, default=str))

            # ── 数据完整性自动补全（Step 3.3）──────────────
            # AI 脚本可能遗漏数值列统计或分类列统计，在此兜底补全
            summary_json = self._ensure_summary_completeness(
                summary_json, file_path, sheets, header_info
            )
            self._save(str(log_dir / f"step3_summary_patched.json"),
                       json.dumps(summary_json, ensure_ascii=False, indent=2, default=str))

            # ── 校验值比对 ────────────────────────────
            if validation_rules:
                self.on_progress(4, 6, "数据校验：对比脚本统计值与用户预期...")
                self._log(f"\n[Step 3.5] 数据校验（第 {attempt + 1} 次尝试）")
                self._log(f"  对比 {len(validation_rules)} 条校验规则...")
                ok, msg = self._validate_summary(summary_json, validation_rules)
                if ok:
                    self._diagnose("Step3_数据校验", "OK", msg)
                    self._log(f"  ✓ {msg}")
                else:
                    self._diagnose(f"Step3_数据校验_尝试{attempt}", "WARN", msg)
                    self._log(f"  ⚠ {msg}")
                    if attempt < max_retries:
                        self.on_progress(4, 6, f"脚本逻辑错误，准备第 {attempt + 1} 次重试...")
                        self._log(f"  [重试] 脚本统计结果与预期不符，准备第 {attempt + 1} 次重新生成脚本...")
                        # 明确告知 AI：脚本逻辑本身有问题，不是值偏差
                        error_feedback = (
                            f"【脚本逻辑错误 — 必须从根本重新审视】\n"
                            f"前一次生成的脚本输出结果与用户提供的校验值不符，\n"
                            f"这说明脚本的计算逻辑本身有问题（不是简单的值偏差），必须重新设计。\n\n"
                            f"校验对比结果：\n{msg}\n\n"
                            "请从以下角度逐一排查，不要重复上一版的思路：\n"
                            "1. 列名是否与 Excel 表头完全一致？（含大小写、空格、全角半角）\n"
                            "2. 筛选条件是否正确？（是否漏掉了某些行/列，是否多筛选了不该包含的数据）\n"
                            "3. 数据类型是否正确处理？（数值列是否被当作文本？是否需要 fillna 或 to_numeric）\n"
                            "4. sum / mean 等聚合是否在正确的粒度上计算？\n"
                            "5. groupby 的分组键是否正确？\n"
                            f"以下字段是用户指定的校验字段，脚本必须完整输出：\n"
                            + "\n".join(f"  - {r['sheet']}.{r['field']}（预期: {r['expected']}）" for r in validation_rules) + "\n"
                        )
                        prompt_retry = build_script_prompt(
                            file_path=file_path, sheets=sheets, header_info=header_info,
                            data_description=data_description, analysis_focus=analysis_focus,
                            error_feedback=error_feedback,
                            validation_fields=validation_rules,  # 传递校验字段，强制要求输出
                        )
                        self._save(str(log_dir / f"step2_prompt_retry{attempt}.txt"), prompt_retry)
                        script_code = self._llm_script(prompt_retry)
                        continue
                    else:
                        # 校验未通过且已用尽重试次数 → 必须停止，不能继续
                        self._log("  ❌ 校验未通过，已用尽重试次数，停止分析！")
                        raise RuntimeError(
                            f"数据校验失败，已重试 {max_retries} 次仍未通过。\n"
                            f"校验详情：\n{msg}\n\n"
                            "请检查：\n"
                            "1. 校验规则格式是否正确（如 sheet1.total_销售额=xxx 或 间凌=10808）\n"
                            "2. 字段名是否与 Excel 列名一致（含大小写、空格）\n"
                            "3. 数据是否已更新（AI 可能使用了错误的筛选条件）\n"
                            "4. 是否需要修正校验值\n\n"
                            "修正后可重新提交分析。"
                        )
            else:
                self._log("\n[Step 3.5] 数据校验（跳过：用户未提供校验规则）")

            return summary_json

        raise RuntimeError("脚本执行与校验重试次数用尽")

    # ════════════════════════════════════════════════════════════
    # 直接分析路径（Word / 多表头 Excel / 小文件）
    # ════════════════════════════════════════════════════════════

    def _run_direct_analysis(
        self,
        file_path: str,
        file_type: str,
        sheets: list[str],
        data_description: str,
        analysis_focus: str,
        data_has_anomalies: bool = False,
        direct_token_threshold: int = 30000,
    ) -> str:
        """
        直接分析路径：跳过脚本执行，直接把原始内容喂给 AI 分析。
        适用于：Word 文件、Excel 多表头、预估 token 小于阈值的文件。
        """
        self._log("\n[直接分析路径] 文件类型/大小满足条件，跳过脚本执行")
        file_name = Path(file_path).name

        # 准备输出目录
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', Path(file_path).stem)[:30]
        session_dir = Path(self.config.get(
            "output_dir", str(Path.home() / "data_analyst_output")
        )) / f"{self.session_id}_{safe_name}"
        log_dir = session_dir / "logs"
        report_dir = session_dir / "reports"
        log_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = str(log_dir / "session.log")

        # Step 1: 解析文件内容
        self._set_step("step1")
        self.on_progress(1, 3, "解析文件内容...")
        self._log(f"\n[Step 1] 解析文件内容")
        try:
            if file_type == "word":
                content = parse_docx(file_path)
                self._log(f"  Word 文档解析完成，内容长度：{len(content)} 字符")
            else:
                # Excel/CSV：读取全量内容（用于直接分析）
                content = read_excel_full_content(file_path, sheets, max_rows_per_sheet=500)
                self._log(f"  Excel 内容读取完成，长度：{len(content)} 字符")

            tokens = estimate_tokens(content)
            token_info = f"{tokens // 1000}K" if tokens >= 1000 else str(tokens)
            self._log(f"  预估 Token：{token_info}（阈值：{direct_token_threshold // 1000}K）")
            self._log(f"  ⚠️ 直接分析模式，跳过数据校验（无脚本执行，无统计结果）")
        except Exception as e:
            self._diagnose("Step1_文件解析", "FAIL", f"解析失败: {e}")
            raise RuntimeError(f"文件解析失败: {e}")

        # Step 2: AI 直接分析（一次调用，替代原有 Step 2+3+4）
        self._set_step("step4")
        self.on_progress(2, 3, "AI 深度分析...")
        self._log("\n[Step 2] 调用 AI 进行直接分析（跳过脚本执行）")

        prompt = build_direct_analysis_prompt(
            file_content=content,
            data_description=data_description,
            analysis_focus=analysis_focus,
            file_type=file_type,
            data_has_anomalies=data_has_anomalies,
        )
        self._save(str(log_dir / "step2_direct_prompt.txt"), prompt)

        try:
            analysis_raw = self._llm("analysis", prompt)
            self._save(str(log_dir / "step4_raw_response.txt"), analysis_raw)

            # 提取 JSON
            json_match = re.search(r'\{.*\}', analysis_raw, re.DOTALL)
            if not json_match:
                self._diagnose("Step2_分析结果", "FAIL", "AI 未返回有效 JSON")
                raise RuntimeError("分析结果未返回有效 JSON，请检查 AI 输出格式")
            parsed = json.loads(json_match.group())
            if isinstance(parsed, list):
                if len(parsed) > 0 and isinstance(parsed[0], dict):
                    parsed = parsed[0]
                else:
                    self._diagnose("Step2_JSON结构", "FAIL", "AI 返回了 list 而非 dict")
                    raise RuntimeError("分析结果 JSON 顶层为数组，无法解析")
            analysis_json = parsed
            self._save(str(log_dir / "step4_analysis.json"),
                       json.dumps(analysis_json, ensure_ascii=False, indent=2, default=str))

            # 诊断章节质量
            chapters = analysis_json.get("chapters", [])
            if not chapters:
                self._diagnose("Step2_章节生成", "FAIL", "AI 未生成任何章节")
                raise RuntimeError("AI 分析结果中没有任何章节数据，请检查分析重点是否合理")

            for i, ch in enumerate(chapters):
                cid = ch.get("id", f"ch{i}")
                title = ch.get("title", "?")
                findings = len(ch.get("findings") or [])
                charts = len(ch.get("charts") or [])
                conc = bool(ch.get("conclusion"))
                status = "OK" if (findings and conc and charts) else "WARN"
                self._diagnose(f"Step2_章节_{cid}", status,
                              f"{title}: 发现{findings}条 | 图表{charts}个 | 结论{'有' if conc else '无'}")
            self._log("  分析结论 JSON 已保存")
        except json.JSONDecodeError as e:
            self._diagnose("Step2_解析", "FAIL", f"JSON 解析错误: {e}")
            raise RuntimeError(f"Step 2 失败 - 分析结果 JSON 解析错误: {e}\n原始响应: {analysis_raw}")
        except AttributeError as e:
            # ★ 'list' object has no attribute 'get' 等结构错误
            self._diagnose("Step2_解析", "FAIL", f"JSON 结构异常（AttributeError）: {e}")
            raise RuntimeError(f"Step 2 失败 - 分析结果 JSON 结构异常: {e}\n原始响应: {analysis_raw if analysis_raw else 'N/A'}")

        # Step 3: 生成报告（复用原有逻辑）
        return self._generate_html_from_analysis(
            analysis_json=analysis_json,
            content=content,
            data_description=data_description,
            file_name=file_name,
            session_dir=session_dir,
            log_dir=log_dir,
            report_dir=report_dir,
            file_type=file_type,
        )

    def _generate_html_from_analysis(
        self,
        analysis_json: dict,
        content: str,
        data_description: str,
        file_name: str,
        session_dir: Path,
        log_dir: Path,
        report_dir: Path,
        file_type: str,
    ) -> str:
        """直接分析路径的报告生成（复用原有 _generate_html 的核心逻辑）"""
        self._set_step("step5")
        self.on_progress(3, 3, "生成 HTML 报告...")
        self._log("\n[Step 3] 生成 HTML 报告")

        report_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        report_path = str(report_dir / "report.html")

        # 构建 sheet_stats（用于直接分析时展示基本信息）
        sheet_stats = []
        if file_type == "word":
            sheet_stats.append({
                "name": "Word文档",
                "rows": content.count("\n"),
                "cols": 0,
                "missing_rate": 0,
                "key_cols": [],
            })
        else:
            # Excel 内容统计
            import pandas as pd
            for sheet in (get_sheet_names(session_dir / "dummy") if False else []):
                pass  # 已直接在 read_excel_full_content 处理
            sheet_stats.append({
                "name": file_type.upper() if file_type else "文件",
                "rows": content.count("\n"),
                "cols": 0,
                "missing_rate": 0,
                "key_cols": [],
            })

        # 复用原有 _generate_html 的章节渲染逻辑
        # 通过动态构造 summary_json 兼容现有代码
        summary_json = {}
        for ch in analysis_json.get("chapters", []):
            sid = ch.get("sheet", "default")
            summary_json[sid] = {"basic": {}}

        try:
            html = self._generate_html(
                analysis_json=analysis_json,
                summary_json=summary_json,
                data_description=data_description,
                file_name=file_name,
                report_date=report_date,
            )
            self._save(report_path, html)
            self._log(f"  HTML 报告已生成: {report_path}（{len(html) // 1024} KB）")
        except Exception as e:
            raise RuntimeError(f"Step 3 失败 - HTML 生成错误: {e}")

        self._log(self._get_diagnostic_report())
        self._set_step("finish")
        self._log(f"\n=== 分析完成！报告路径: {report_path} ===")
        self.on_progress(3, 3, "分析完成！")
        return report_path

    def run(
        self,
        file_path: str,
        sheets: list[str],
        data_description: str,
        analysis_focus: str,
        validation_rules: str = "",
        data_has_anomalies: bool = False,
        analysis_mode: str = "script",
        direct_token_threshold: int = 30000,
    ) -> str:
        """
        运行完整分析流程，返回报告 HTML 文件路径。

        分析模式（analysis_mode）：
          "script"     — 强制脚本分析（默认，推荐用于大文件+数据校验）
          "direct"     — 强制直接分析（跳过脚本，适用于 Word/多表头/小文件）
          "auto"       — 自动选择（已废弃，默认走 script）
        """
        # ── Step 0: 文件类型检测 + 路由 + 会话初始化 ─────
        file_name = Path(file_path).name

        # 初始化本次分析会话（用于 AI 日志汇总）
        self._session_calls = []
        self._session_file_name = file_name
        self._session_start_time = datetime.now().strftime("%H:%M:%S")

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', Path(file_path).stem)[:30]
        session_dir = Path(self.config.get(
            "output_dir", str(Path.home() / "data_analyst_output")
        )) / f"{self.session_id}_{safe_name}"

        log_dir = session_dir / "logs"
        report_dir = session_dir / "reports"
        log_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        self.log_file = str(log_dir / "session.log")
        self._set_step("init")
        self._log(f"=== 分析开始 | 文件: {file_name} | 会话: {self.session_id} ===")
        self._log(f"输出目录: {session_dir}")

        file_type = detect_file_type(file_path)

        # ── 自动判断分析模式 ────────────────────────────────
        if analysis_mode == "direct":
            mode_desc = "强制直接分析"
        elif analysis_mode == "script":
            mode_desc = "强制脚本分析"
        else:
            # auto 模式：根据文件类型和内容自动选择
            if file_type == "word":
                mode_desc = "自动→直接分析（Word文档）"
            elif file_type in ("excel", "csv"):
                # 检测多表头
                excel_sheets = sheets if file_type == "excel" else ["Sheet1"]
                multi_sheets = get_multi_header_sheets(file_path)
                if multi_sheets:
                    mode_desc = f"自动→直接分析（{len(multi_sheets)}个Sheet为多表头）"
                else:
                    # 估算 token 量
                    preview = read_excel_full_content(file_path, excel_sheets, max_rows_per_sheet=100)
                    tokens = estimate_tokens(preview)
                    if tokens < direct_token_threshold:
                        mode_desc = f"自动→直接分析（预估{tokens//1000}K tokens < {direct_token_threshold//1000}K）"
                    else:
                        mode_desc = f"自动→脚本分析（预估{tokens//1000}K tokens ≥ {direct_token_threshold//1000}K）"
                        if validation_rules:
                            self._log(f"  脚本分析路径支持数据校验")
            else:
                mode_desc = "自动→直接分析（未知文件类型）"

        self._log(f"  文件类型：{file_type} | 分析模式：{mode_desc}")
        self._log(f"输出目录: {session_dir}")

        # ── 路由判断 ────────────────────────────────────────
        use_direct = False

        if analysis_mode == "direct":
            use_direct = True
        elif analysis_mode == "script":
            use_direct = False
        else:
            # auto 模式
            if file_type == "word":
                use_direct = True
            elif file_type in ("excel", "csv"):
                excel_sheets = sheets if file_type == "excel" else ["Sheet1"]
                multi_sheets = get_multi_header_sheets(file_path)
                if multi_sheets:
                    use_direct = True
                    self._log(f"  ⚠️ 多表头 Sheet：{', '.join(multi_sheets)} → 强制直接分析")
                else:
                    preview = read_excel_full_content(file_path, excel_sheets, max_rows_per_sheet=100)
                    tokens = estimate_tokens(preview)
                    use_direct = tokens < direct_token_threshold
                    if not use_direct:
                        self._log(f"  预估 Token {tokens} ≥ 阈值 {direct_token_threshold} → 脚本分析路径")
            else:
                use_direct = True

        # ── 执行对应路径 ────────────────────────────────────
        if use_direct:
            try:
                result = self._run_direct_analysis(
                    file_path=file_path,
                    file_type=file_type,
                    sheets=sheets,
                    data_description=data_description,
                    analysis_focus=analysis_focus,
                    data_has_anomalies=data_has_anomalies,
                    direct_token_threshold=direct_token_threshold,
                )
                self._emit_analysis_summary("ok")
                return result
            except Exception:
                self._emit_analysis_summary("error")
                raise
        else:
            # 脚本分析路径：原有流程
            try:
                result = self._run_script_analysis(
                    file_path=file_path,
                    sheets=sheets,
                    data_description=data_description,
                    analysis_focus=analysis_focus,
                    validation_rules=validation_rules,
                    data_has_anomalies=data_has_anomalies,
                    session_dir=session_dir,
                    log_dir=log_dir,
                    report_dir=report_dir,
                    file_name=file_name,
                )
                self._emit_analysis_summary("ok")
                return result
            except Exception:
                self._emit_analysis_summary("error")
                raise

    def run_tweak(
        self,
        analysis_json: dict,
        summary_json: dict,
        tweak_description: str,
        data_description: str = "",
        analysis_focus: str = "",
        report_date: str = None,
        output_dir: str = None,
    ) -> str:
        """
        微调报告：根据用户的文字描述，修改现有 analysis_json，重新生成报告。
        
        Token 消耗：约 1000~3000 tokens（比全流程节省 ~50%）
        
        流程：
        1. 调用 build_tweak_prompt() 构建 Prompt
        2. 调用 AI 获取修改后的 analysis_json
        3. 检查 _needs_data 字段，如需新数据则提示用户
        4. 调用 _generate_html() 重新生成报告
        
        返回：新报告 HTML 文件路径
        """
        import json as json_mod
        from datetime import datetime as dt
        from pathlib import Path as P

        if report_date is None:
            report_date = dt.now().strftime("%Y-%m-%d %H:%M")

        self._log("=== 开始微调报告 ===")
        self._log(f"微调描述：{tweak_description[:200]}")

        # ── Step T1: 调用 AI 微调 ──────────────────────────
        self._log("\n[微调 Step 1] 调用 AI 修改报告内容...")
        tweak_prompt = build_tweak_prompt(
            analysis_json=analysis_json,
            tweak_description=tweak_description,
            summary_json=summary_json,
            data_description=data_description,
            analysis_focus=analysis_focus,
        )
        # 保存 Prompt 到日志目录（如果有 session_dir）
        try:
            log_dir = getattr(self, '_log_dir', None)
            if log_dir:
                with open(P(log_dir) / "tweak_prompt.txt", "w", encoding="utf-8") as f:
                    f.write(tweak_prompt)
        except Exception:
            pass

        max_retries = 2
        tweaked_json = None
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    self._log(f"  [重试] 第 {attempt}/{max_retries} 次...")
                    retry_prompt = tweak_prompt + (
                        "\n\n【重要：上次输出格式错误】"
                        "\n你之前返回的内容无法解析为有效 JSON。请严格输出纯 JSON，"
                        "不要添加任何 markdown 代码块标记、说明文字或前缀后缀。"
                        "直接以 { 开头，以 } 结尾。"
                    )
                    raw = self._llm("analysis", retry_prompt)
                else:
                    raw = self._llm("analysis", tweak_prompt)

                # 保存原始响应
                try:
                    log_dir = getattr(self, '_log_dir', None)
                    if log_dir:
                        suffix = f"_retry{attempt}" if attempt > 0 else ""
                        with open(P(log_dir) / f"tweak_raw_response{suffix}.txt", "w", encoding="utf-8") as f:
                            f.write(raw if raw else "(empty)")
                except Exception:
                    pass

                # 提取 JSON
                json_match = re.search(r'(\{.*\}|\[.*\])', raw, re.DOTALL)
                if not json_match:
                    cleaned = re.sub(r'```json\s*', '', raw if raw else "")
                    cleaned = re.sub(r'```\s*$', '', cleaned)
                    json_match = re.search(r'(\{.*\}|\[.*\])', cleaned, re.DOTALL)
                if not json_match:
                    self._log(f"  ⚠️ 第 {attempt+1} 次：AI 返回内容非有效 JSON，准备重试...")
                    if attempt < max_retries:
                        continue
                    raise RuntimeError("微调 AI 未返回有效 JSON，已重试多次仍未成功")

                parsed = json_mod.loads(json_match.group())
                if isinstance(parsed, list):
                    if len(parsed) > 0 and isinstance(parsed[0], dict):
                        parsed = parsed[0]
                    else:
                        raise RuntimeError("微调 AI 返回了空数组或格式错误")
                if not isinstance(parsed, dict):
                    raise RuntimeError(f"微调 AI 返回了非字典类型: {type(parsed).__name__}")

                tweaked_json = parsed
                break

            except (json_mod.JSONDecodeError, AttributeError) as e:
                self._log(f"  ⚠️ 第 {attempt+1} 次解析失败: {e}")
                if attempt < max_retries:
                    continue
                raise RuntimeError(f"微调失败 - JSON 解析错误: {e}\n原始响应: {raw if 'raw' in dir() else 'N/A'}")

        # ── 检查是否需要新数据 ──────────────────────────────
        if "_needs_data" in tweaked_json:
            needs = tweaked_json.pop("_needs_data")
            self._log(f"  [提示] AI 表示需要新数据: {needs}")
            # 可以选择：1) 抛出异常让用户重新分析  2) 继续生成报告（可能部分数据为空）
            # 这里选择继续生成，但记录警告
            self._log("  [警告] 将生成报告，但部分数据可能为空。建议重新分析以获取完整结果。")

        # ── Step T2: 重新生成报告 ──────────────────────────
        self._log("\n[微调 Step 2] 重新生成 HTML 报告...")
        file_name = getattr(self, '_file_name', 'data')
        try:
            html = self._generate_html(
                analysis_json=tweaked_json,
                summary_json=summary_json,
                data_description=data_description,
                file_name=file_name,
                report_date=report_date + " (微调)",
            )
        except Exception as e:
            raise RuntimeError(f"微调失败 - HTML 生成错误: {e}")

        # 保存新报告（加 _tweaked 后缀）
        if output_dir is None:
            output_dir = Path(self.config.get(
                "output_dir", str(P.home() / "data_analyst_output")
            )) / f"{self.session_id}_tweaked"
        else:
            output_dir = P(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = str(output_dir / "report_tweaked.html")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)

        self._log(f"\n=== 微调完成 | 报告已保存：{report_path} ===")
        return report_path

    def _run_script_analysis(
        self,
        file_path: str,
        sheets: list[str],
        data_description: str,
        analysis_focus: str,
        validation_rules: str,
        data_has_anomalies: bool,
        session_dir: Path,
        log_dir: Path,
        report_dir: Path,
        file_name: str,
    ) -> str:
        """
        脚本分析路径（新流程 — 6 步）：
        Step 1:   读取表头
        Step 1.5: AI 分析规划（新增：指标/维度/分组/图表计划）
        Step 2:   AI 基于分析计划生成针对性统计脚本
        Step 3:   本地执行脚本（含自动重试 + 数据校验）
        Step 4:   AI 基于执行结果输出分析结论 + 图表数据
        Step 5:   AI 编写前端 HTML 呈现代码
        """
        TOTAL_STEPS = 6

        # ── Step 1: 读取表头 ──────────────────────────────
        self._set_step("step1")
        self.on_progress(1, TOTAL_STEPS, "读取文件表头...")
        self._log("\n[Step 1] 读取表头和样本数据")
        try:
            header_info = load_headers(file_path, sheets)
            self._save(str(log_dir / "step1_headers.json"),
                       json.dumps(header_info, ensure_ascii=False, indent=2, default=str))
            # 诊断：检查表头数据质量
            total_cols = sum(len(info.get("columns", [])) for info in header_info.values())
            total_rows = sum(len(info.get("sample_rows", [])) for info in header_info.values())
            self._diagnose("Step1_表头读取", "OK" if total_cols > 0 else "FAIL",
                           f"共 {len(sheets)} 个 Sheet，{total_cols} 个字段，{total_rows} 行样本")
            self._log(f"  表头读取完成，共 {len(sheets)} 个 Sheet")
        except Exception as e:
            self._diagnose("Step1_表头读取", "FAIL", f"读取失败: {e}")
            raise RuntimeError(f"Step 1 失败 - 读取文件错误: {e}")

        # ── Step 1.5: AI 分析规划（新增）──────────────────
        self._set_step("step1.5")
        self.on_progress(2, TOTAL_STEPS, "AI 分析规划：确定指标、维度、分组方案...")

        # ── 分析计划缓存检测 ──
        cached_plan = self._load_cached_plan(header_info, analysis_focus)
        if cached_plan:
            self._log("\n[Step 1.5] 命中分析计划缓存，跳过 AI 分析规划")
            self._log(f"  缓存目标: {cached_plan.get('objective', '')[:60]}")
            analysis_plan = cached_plan
        else:
            self._log("\n[Step 1.5] 调用 AI 进行分析规划（先思考再动手）")
            plan_prompt = build_analysis_plan_prompt(
                header_info=header_info,
                data_description=data_description,
                analysis_focus=analysis_focus,
            )
            self._save(str(log_dir / "step1.5_plan_prompt.txt"), plan_prompt)

            analysis_plan = None
            try:
                plan_raw = self._llm("plan", plan_prompt)
                self._save(str(log_dir / "step1.5_plan_raw_response.txt"), plan_raw)

                # 提取 JSON
                plan_match = re.search(r'\{.*\}', plan_raw, re.DOTALL)
                if plan_match:
                    analysis_plan = json.loads(plan_match.group())
                    # 防御：AI 可能返回数组
                    if isinstance(analysis_plan, list) and len(analysis_plan) > 0 and isinstance(analysis_plan[0], dict):
                        analysis_plan = analysis_plan[0]
                        self._log("  [注意] 分析计划 JSON 为数组格式，已自动提取第一个元素")
                    self._save(str(log_dir / "step1.5_plan.json"),
                               json.dumps(analysis_plan, ensure_ascii=False, indent=2))

                    # 保存缓存
                    self._save_cached_plan(header_info, analysis_focus, analysis_plan)
                    self._log("  [缓存] 分析计划已保存")

                    # 日志摘要（兼容新版字段名 objective/groupings/charts 和旧版 analysis_objective/grouping_schemes/chart_plan）
                    metrics_count = len(analysis_plan.get("metrics", []))
                    dims_count = len(analysis_plan.get("dimensions", []))
                    groups_count = len(analysis_plan.get("groupings", analysis_plan.get("grouping_schemes", [])))
                    charts_count = len(analysis_plan.get("charts", analysis_plan.get("chart_plan", [])))
                    objective = (analysis_plan.get("objective", analysis_plan.get("analysis_objective", "")) or "")[:80]
                    self._log(f"  ✅ 分析计划生成成功")
                    self._log(f"     目标: {objective}")
                    self._log(f"     指标: {metrics_count} 个 | 维度: {dims_count} 个 | 分组方案: {groups_count} 个 | 图表规划: {charts_count} 个")
                    self._diagnose("Step1.5_分析规划", "OK",
                                   f"指标{metrics_count}个/维度{dims_count}个/分组{groups_count}个/图表{charts_count}个")
                else:
                    self._diagnose("Step1.5_分析规划", "WARN", "AI 未返回有效 JSON 计划，将降级为全量统计模式")
                    self._log("  ⚠️ 分析计划解析失败，降级为原有全量统计模式（不影响主流程）")
            except Exception as e:
                self._diagnose("Step1.5_分析规划", "WARN", f"分析计划生成失败: {e}，降级为全量统计模式")
                self._log(f"  ⚠️ 分析计划步骤异常: {e}，降级为全量统计模式（不影响主流程）")

        # ── Step 2: AI 输出结构化分析意图 ───────────────
        # 提前解析校验规则，在生成意图时就注入强制输出要求
        default_sheet = sheets[0] if sheets else "sheet1"
        parsed_rules = self._parse_validation_rules(validation_rules, default_sheet=default_sheet)
        if parsed_rules:
            self._log(f"  用户提供了 {len(parsed_rules)} 条数据校验规则")
            self._log("  校验格式说明：")
            self._log("    · 间凌=10808           → 简写（自动匹配第一个 Sheet）")
            self._log("    · sheet1.total_销售额=1000 → 指定 Sheet")

        self.on_progress(3, TOTAL_STEPS, "AI 生成分析意图...")
        self._set_step("step2")
        self._log("\n[Step 2] 调用 AI 输出结构化分析意图（意图驱动模式）")

        # ── 生成列统计摘要（让 AI 基于真实数据分布做判断）──
        column_profiles = {}
        try:
            import pandas as pd
            for sheet in sheets:
                if file_path.endswith(".csv"):
                    df_tmp = pd.read_csv(file_path, dtype_backend="numpy_nullable")
                else:
                    df_tmp = pd.read_excel(file_path, sheet_name=sheet, dtype_backend="numpy_nullable")
                column_profiles[sheet] = generate_column_profile(df_tmp)
            self._log(f"  列统计摘要生成完成，共 {len(column_profiles)} 个 Sheet")
        except Exception as e:
            self._log(f"  ⚠️ 列统计摘要生成失败: {e}（不影响主流程）")
            column_profiles = {}

        prompt2 = build_intent_prompt(
            header_info=header_info,
            data_description=data_description,
            analysis_focus=analysis_focus,
            analysis_plan=analysis_plan,
            column_profiles=column_profiles,
        )
        self._save(str(log_dir / "step2_intent_prompt.txt"), prompt2)

        intent_json = None
        max_intent_retries = 2
        for intent_attempt in range(max_intent_retries + 1):
            try:
                if intent_attempt > 0:
                    self._log(f"  [重试] 第 {intent_attempt}/{max_intent_retries} 次重新请求意图...")
                    retry_prompt = prompt2 + (
                        f"\n\n【上次错误】意图 JSON 解析失败: {intent_error}\n"
                        "请严格输出纯 JSON 对象，不要添加 markdown 代码块标记。"
                    )
                    raw_intent = self._llm("intent", retry_prompt)
                else:
                    raw_intent = self._llm("intent", prompt2)

                self._save(str(log_dir / f"step2_intent_raw{'_retry'+str(intent_attempt) if intent_attempt > 0 else ''}.txt"), raw_intent)

                # 提取 JSON
                json_match = re.search(r'(\{.*\})', raw_intent, re.DOTALL)
                if not json_match:
                    raise ValueError("未找到有效的 JSON 对象")
                intent_json = json.loads(json_match.group())
                if isinstance(intent_json, list) and len(intent_json) > 0 and isinstance(intent_json[0], dict):
                    intent_json = intent_json[0]
                    self._log("  [注意] 意图 JSON 为数组格式，已自动提取第一个元素")

                # 基础校验
                if not isinstance(intent_json, dict):
                    raise ValueError(f"意图 JSON 类型错误: {type(intent_json).__name__}")

                # 确保 sheets 字段存在
                if "sheets" not in intent_json:
                    intent_json["sheets"] = sheets

                self._save(str(log_dir / "step2_intent.json"),
                           json.dumps(intent_json, ensure_ascii=False, indent=2))
                self._log(f"  ✅ 分析意图生成成功（重试{intent_attempt}次）")
                groupings = intent_json.get("groupings", [])
                self._log(f"     数值列: {len(intent_json.get('numeric_cols', []))} 个")
                self._log(f"     分类列: {len(intent_json.get('categorical_cols', []))} 个")
                self._log(f"     分组方案: {len(groupings)} 个")
                break

            except Exception as e:
                intent_error = str(e)
                self._diagnose(f"Step2_意图解析_尝试{intent_attempt}", "FAIL", f"意图解析错误: {e}")
                if intent_attempt < max_intent_retries:
                    self._log(f"  ⚠️ 意图解析失败: {e}，准备重试...")
                    continue
                raise RuntimeError(f"Step 2 失败 - 无法解析 AI 返回的分析意图（已重试 {max_intent_retries} 次）: {e}")

        # ── Step 3: 意图执行引擎（确定性统计）──────────────
        self.on_progress(4, TOTAL_STEPS, "执行数据统计（意图引擎）...")
        self._set_step("step3")
        self._log("\n[Step 3] 意图执行引擎：确定性 pandas 统计")

        try:
            engine = IntentEngine(file_path=file_path, sheets=sheets, header_info=header_info)
            summary_json = engine.run(intent_json)

            # 输出引擎日志
            for log_msg in engine.get_logs():
                self._log(f"  [引擎] {log_msg}")

            self._save(str(log_dir / "step3_summary.json"),
                       json.dumps(summary_json, ensure_ascii=False, indent=2, default=str))
            self._log(f"  ✅ 统计执行完成，共 {len(summary_json)} 个 Sheet")

        except Exception as e:
            self._diagnose("Step3_意图执行", "FAIL", f"引擎执行错误: {e}")
            raise RuntimeError(f"Step 3 失败 - 意图引擎执行错误: {e}")

        # ── Step 3.3：数据完整性补全（兜底）──────────────
        summary_json = self._ensure_summary_completeness(
            summary_json, file_path, sheets, header_info
        )
        self._save(str(log_dir / "step3_summary_patched.json"),
                   json.dumps(summary_json, ensure_ascii=False, indent=2, default=str))

        # ── 诊断：统计结果数据质量 ──────────────────────
        # ★ 结构安全网：确保 summary_json 和其值都是预期类型
        if isinstance(summary_json, list):
            self._log(f"  [警告] summary_json 顶层为 list（应为 dict），尝试自动修复...")
            if len(summary_json) > 0 and isinstance(summary_json[0], dict):
                summary_json = summary_json[0]
                self._log("  [修复] 已提取第一个元素")
            else:
                raise RuntimeError(
                    f"summary_json 结构异常: 顶层为 {type(summary_json).__name__}，"
                    f"无法转换为 dict。\n原始数据前500字符: {str(summary_json)[:500]}"
                )
        if not isinstance(summary_json, dict):
            raise RuntimeError(
                f"summary_json 类型错误: 期望 dict，实际 {type(summary_json).__name__}。\n"
                f"这通常意味着 AI 生成的脚本输出了非标准 JSON 格式。"
            )

        sheet_data_quality = []
        for sheet_name, info in summary_json.items():
            # ★ 防护：info 可能是 list 或其他非 dict 类型
            if not isinstance(info, dict):
                self._log(f"  [警告] Sheet '{sheet_name}' 的值为 {type(info).__name__} 而非 dict，跳过质量检测")
                sheet_data_quality.append(f"{sheet_name}(结构异常)")
                continue
            basic = info.get("basic", {}) or {}
            rows = basic.get("rows", 0)
            cols = basic.get("cols", 0)
            num_stats = len(info.get("numeric_stats", {}))
            cat_stats = len(info.get("categorical_stats", {}))
            has_data = rows > 0 and cols > 0

            if not has_data:
                sheet_data_quality.append(f"{sheet_name}(无数据)")
            elif num_stats == 0 and cat_stats == 0:
                sheet_data_quality.append(f"{sheet_name}(无统计)")
            else:
                sheet_data_quality.append(
                    f"{sheet_name}({rows}行×{cols}列,{num_stats}数值列,{cat_stats}类别列)"
                )

        quality_msg = " | ".join(sheet_data_quality)
        empty_sheets = [s for s in sheet_data_quality if "(无数据)" in s or "(无统计)" in s]
        if empty_sheets:
            self._diagnose("Step3_数据质量", "WARN",
                           f"部分 Sheet 数据异常: {', '.join(empty_sheets)}")
        else:
            self._diagnose("Step3_数据质量", "OK", f"统计完成: {quality_msg}")

        self._log(f"  数据提炼完成")

        # ── Step 4: AI 深度分析（基于执行结果 + 分析计划）─
        self._set_step("step4")
        self.on_progress(5, TOTAL_STEPS, "AI 深度推理分析...")
        self._log("\n[Step 4] 调用推理模型进行深度分析")
        prompt4 = build_analysis_prompt(
            summary_json=summary_json,
            data_description=data_description,
            analysis_focus=analysis_focus,
            file_name=file_name,
            data_has_anomalies=data_has_anomalies,
        )
        # 将分析计划也注入到分析 prompt 中（让 AI 参考自己的规划来做结论）
        if analysis_plan:
            plan_context = f"\n\n【参考：前序分析计划】\n{json.dumps(analysis_plan, ensure_ascii=False, indent=2, default=str)[:3000]}\n（分析结论应覆盖或修正此计划中的所有指标和图表）"
            prompt4 = prompt4.replace(
                "【统计摘要数据】",
                f"【分析计划参考（应据此输出结论和图表）】{plan_context}\n\n【统计摘要数据】"
            )
        self._save(str(log_dir / "step4_prompt.txt"), prompt4)

        # ── Step 4: AI 深度分析（带 JSON 重试机制）────────
        analysis_raw = None
        analysis_json = None
        max_analysis_retries = 2
        for analysis_attempt in range(max_analysis_retries + 1):
            try:
                if analysis_attempt > 0:
                    self._log(f"  [重试] 第 {analysis_attempt}/{max_analysis_retries} 次重新请求 AI 分析...")
                    self.on_progress(5, TOTAL_STEPS, f"AI 分析重试 {analysis_attempt}/{max_analysis_retries}...")
                    # 在 prompt 末尾追加重试提示
                    retry_prompt4 = prompt4 + (
                        "\n\n【重要：上次输出格式错误】"
                        "\n你之前返回的内容无法解析为有效 JSON。请严格输出纯 JSON，"
                        "不要添加任何 markdown 代码块标记、说明文字或前缀后缀。"
                        "直接以 { 开头，以 } 结尾。"
                    )
                    analysis_raw = self._llm("analysis", retry_prompt4)
                else:
                    analysis_raw = self._llm("analysis", prompt4)
                self._save(str(log_dir / f"step4_raw_response{'_retry'+str(analysis_attempt) if analysis_attempt > 0 else ''}.txt"), analysis_raw)

                # 提取 JSON（增强：同时支持对象 {...} 和数组 [...] 格式）
                json_match = re.search(r'(\{.*\}|\[.*\])', analysis_raw, re.DOTALL)
                if not json_match:
                    # 尝试去除 markdown 代码块后再提取
                    cleaned = re.sub(r'```json\s*', '', analysis_raw)
                    cleaned = re.sub(r'```\s*$', '', cleaned)
                    json_match = re.search(r'(\{.*\}|\[.*\])', cleaned, re.DOTALL)
                if not json_match:
                    self._diagnose(f"Step4_分析结果_尝试{analysis_attempt}", "FAIL", "AI 未返回有效 JSON")
                    if analysis_attempt < max_analysis_retries:
                        self._log("  ⚠️ AI 返回内容非有效 JSON，准备重试...")
                        continue
                    raise RuntimeError("分析结果未返回有效 JSON，已重试多次仍未成功")

                parsed = json.loads(json_match.group())
                # 防御：AI 可能返回 list 而非 dict
                if isinstance(parsed, list):
                    # 尝试取第一个元素，或者包装成 dict
                    if len(parsed) > 0 and isinstance(parsed[0], dict):
                        parsed = parsed[0]
                    else:
                        self._diagnose(f"Step4_JSON结构_尝试{analysis_attempt}", "FAIL", "AI 返回了 list 而非 dict")
                        if analysis_attempt < max_analysis_retries:
                            self._log("  ⚠️ AI 返回了数组而非对象，准备重试...")
                            continue
                        raise RuntimeError("分析结果 JSON 顶层为数组，无法解析")
                analysis_json = parsed
                # JSON 解析成功，跳出重试循环
                break
            except json.JSONDecodeError as e:
                self._diagnose(f"Step4_JSON解析_尝试{analysis_attempt}", "FAIL", f"JSON 解析错误: {e}")
                if analysis_attempt < max_analysis_retries:
                    self._log(f"  ⚠️ JSON 解析失败: {e}，准备重试...")
                    continue
                raise RuntimeError(f"Step 4 失败 - 分析结果 JSON 解析错误: {e}\n原始响应: {analysis_raw if analysis_raw else 'N/A'}")
            except AttributeError as e:
                # ★ 捕获 'list' object has no attribute 'get' 等常见错误
                self._diagnose(f"Step4_属性错误_尝试{analysis_attempt}", "FAIL", f"属性访问错误（可能是JSON结构问题）: {e}")
                if analysis_attempt < max_analysis_retries:
                    self._log(f"  ⚠️ 属性访问错误: {e}，准备重试并要求 AI 修正格式...")
                    # 追加更严格的格式要求
                    prompt4 = prompt4 + (
                        "\n\n【严重：上次返回的JSON结构导致程序崩溃】"
                        "\n错误信息: " + str(e) +
                        "\n请确保：1) 最外层是JSON对象{...}不是数组[...]"
                        " 2) chapters字段是数组 3) 每个章节有id/title/findings/charts字段"
                    )
                    continue
                raise RuntimeError(f"Step 4 失败 - 分析结果结构异常: {e}\n原始响应: {analysis_raw if analysis_raw else 'N/A'}")

        # ★ 最终安全网：确保 analysis_json 一定是 dict
        if not isinstance(analysis_json, dict):
            self._diagnose("Step4_JSON类型", "FAIL", f"analysis_json 类型为 {type(analysis_json).__name__}，预期为 dict")
            raise RuntimeError(
                f"分析结果 JSON 最终类型为 {type(analysis_json).__name__} 而非 dict。\n"
                f"这通常意味着 AI 返回了数组格式 [...] 而非对象格式 {{...}}。\n"
                f"原始响应: {analysis_raw if analysis_raw else 'N/A'}"
            )

        self._save(str(log_dir / "step4_analysis.json"),
                   json.dumps(analysis_json, ensure_ascii=False, indent=2, default=str))

        # ── 诊断：分析结论数据质量 ──────────────────────
        chapters = analysis_json.get("chapters", [])
        if not chapters:
            self._diagnose("Step4_章节生成", "FAIL", "AI 未生成任何章节")
            raise RuntimeError("AI 分析结果中没有任何章节数据，请检查分析重点是否合理")

        # 检查每个章节的数据质量
        chapter_diagnosis = []
        for ch in chapters:
            cid = ch.get("id", "?")
            title = ch.get("title", "?")
            findings_count = len(ch.get("findings") or [])
            charts_count = len(ch.get("charts") or [])
            kpis_count = len(ch.get("key_metrics") or [])
            conc = ch.get("conclusion") or ""
            has_conclusion = bool(str(conc).strip())

            # 检查图表数据是否有效
            chart_data_status = []
            for chart in ch.get("charts") or []:
                chart_type = chart.get("type", "?")
                has_x = bool(chart.get("x"))
                has_series = any(s.get("data") for s in chart.get("series", []) if s.get("data"))
                has_pie_data = any(d.get("value") is not None for d in chart.get("data", []) if isinstance(d, dict))
                if has_x or has_series or has_pie_data:
                    chart_data_status.append(f"{chart_type}(有数据)")
                else:
                    chart_data_status.append(f"{chart_type}(空)")

            # 判断章节内容充实度
            content_score = sum([
                findings_count > 0,
                kpis_count > 0,
                has_conclusion,
                charts_count > 0 and "空" not in str(chart_data_status)
            ])
            if content_score >= 3:
                status = "OK"
            elif content_score >= 1:
                status = "WARN"
            else:
                status = "FAIL"

            diag = {
                "id": cid, "title": title, "status": status,
                "findings": findings_count, "kpis": kpis_count,
                "conclusion": has_conclusion, "charts": charts_count,
                "chart_data": chart_data_status
            }
            chapter_diagnosis.append(diag)

            diag_emoji = "✅" if status == "OK" else "⚠️" if status == "WARN" else "❌"
            self._log(f"    {diag_emoji} 章节 {cid} [{title}]: "
                      f"发现{findings_count}条 | KPI{kpis_count}个 | "
                      f"结论{'有' if has_conclusion else '无'} | 图表{charts_count}个")

        # 汇总章节诊断
        fail_chapters = [d for d in chapter_diagnosis if d["status"] == "FAIL"]
        warn_chapters = [d for d in chapter_diagnosis if d["status"] == "WARN"]
        if fail_chapters:
            self._diagnose("Step4_章节质量", "FAIL",
                           f"{len(fail_chapters)} 个章节内容空洞: "
                           + ", ".join(f"{d['id']}({d['title']})" for d in fail_chapters))
        elif warn_chapters:
            self._diagnose("Step4_章节质量", "WARN",
                           f"{len(warn_chapters)} 个章节部分内容缺失: "
                           + ", ".join(f"{d['id']}" for d in warn_chapters))
        else:
            self._diagnose("Step4_章节质量", "OK",
                           f"所有 {len(chapters)} 个章节内容完整")

        self._log("  分析结论 JSON 已保存")

        # ── Step 5: 生成 HTML 报告 ────────────────────────
        self._set_step("step5")
        self.on_progress(6, TOTAL_STEPS, "生成 BI 风格 HTML 报告...")
        self._log("\n[Step 5] 生成 HTML 报告")

        report_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        report_path = str(report_dir / "report.html")

        try:
            html = self._generate_html(
                analysis_json=analysis_json,
                summary_json=summary_json,
                data_description=data_description,
                file_name=file_name,
                report_date=report_date,
            )
            self._save(report_path, html)
            self._log(f"  HTML 报告已生成: {report_path}（{len(html)//1024} KB）")
        except Exception as e:
            raise RuntimeError(f"Step 5 失败 - HTML 生成错误: {e}")

        self._log(self._get_diagnostic_report())
        self._set_step("finish")
        self._log(f"\n=== 分析完成！报告路径: {report_path} ===")
        self.on_progress(6, TOTAL_STEPS, "分析完成！")
        return report_path

    def _generate_html(
        self,
        analysis_json: dict,
        summary_json: dict,
        data_description: str,
        file_name: str,
        report_date: str,
    ) -> str:
        """将分析数据注入 HTML 模板，生成最终报告"""
        from pathlib import Path as P
        template_path = P(__file__).parent.parent / "templates" / "report_template.html"

        # 构建 sheet_stats
        sheet_stats = []
        for sheet_name, info in summary_json.items():
            # ★ 防护：info 可能是非 dict 类型
            if not isinstance(info, dict):
                continue
            basic = info.get("basic", {}) or {}
            missing_rates = [v.get("rate", 0) for v in info.get("missing", {}).values()]
            avg_missing = sum(missing_rates) / len(missing_rates) if missing_rates else 0
            key_cols = list(info.get("numeric_stats", {}).keys())[:3] + \
                       list(info.get("datetime_stats", {}).keys())[:2]
            sheet_stats.append({
                "name": sheet_name,
                "rows": basic.get("rows", 0),
                "cols": basic.get("cols", 0),
                "missing_rate": round(avg_missing, 4),
                "key_cols": key_cols[:5],
            })

        # ════════════════════════════════════════════════════════════
        # 章节有效性检查（非简单过滤，而是诊断 + 保留但标注）
        # 如果章节缺少数据，说明 AI 分析不充分，在 HTML 中标注"数据不足"
        # ════════════════════════════════════════════════════════════
        def _chapter_content_level(ch: dict) -> tuple[int, str]:
            """
            返回 (充实度分数, 缺失原因描述)
            充实度: 0=完全空洞, 1=部分缺失, 2=基本完整, 3=完整
            """
            score = 0
            reasons = []

            if ch.get("key_metrics") and len(ch["key_metrics"]) > 0:
                score += 1
            else:
                reasons.append("KPI指标缺失")

            if ch.get("findings") and len(ch["findings"]) > 0:
                score += 1
            else:
                reasons.append("分析发现缺失")

            conc = ch.get("conclusion", "")
            if conc and str(conc).strip():
                score += 1
            else:
                reasons.append("章节结论为空")

            # 检查图表数据有效性
            charts = ch.get("charts") or []
            valid_charts = 0
            empty_charts = []
            for c in charts:
                chart_type = c.get("type", "?")
                has_x = bool(c.get("x"))
                has_series = any(s.get("data") for s in c.get("series", []) if s.get("data"))
                has_pie_data = any(d.get("value") is not None for d in c.get("data", []) if isinstance(d, dict))
                # scatter/boxplot/waterfall 使用 data 数组格式（非字典）
                data_arr = c.get("data", [])
                has_array_data = (
                    data_arr and
                    chart_type in ("scatter", "boxplot", "waterfall") and
                    isinstance(data_arr, list) and
                    len(data_arr) > 0
                )
                # bubble 格式: [{"name":"","value":[x,y,size]},...]
                has_bubble_data = (
                    data_arr and chart_type == "bubble" and
                    isinstance(data_arr, list) and len(data_arr) > 0 and
                    isinstance(data_arr[0], dict) and
                    isinstance(data_arr[0].get("value"), list) and
                    len(data_arr[0].get("value", [])) >= 3
                )
                # gauge 格式: value + max
                has_gauge_data = chart_type == "gauge" and c.get("value") is not None
                # radar 格式: indicator + data
                has_radar_data = chart_type == "radar" and c.get("indicator") and data_arr
                if has_x or has_series or has_pie_data or has_array_data or has_bubble_data or has_gauge_data or has_radar_data:
                    valid_charts += 1
                else:
                    empty_charts.append(chart_type)

            if valid_charts > 0:
                score += 1
            else:
                reasons.append(f"图表数据为空{('(' + ','.join(set(empty_charts)) + ')' if empty_charts else '')}")

            table = ch.get("table") or {}
            has_table = bool(table.get("cols") and table.get("rows"))

            reason_str = "; ".join(reasons) if reasons else "全部数据完整"
            return score, reason_str

        raw_chapters = analysis_json.get("chapters", []) or []
        # ★ 防御：过滤掉 null 或非 dict 的章节（AI 偶发返回异常）
        all_chapters = [ch for ch in raw_chapters if isinstance(ch, dict)]
        if len(all_chapters) != len(raw_chapters):
            self._log(f"  [警告] 过滤了 {len(raw_chapters) - len(all_chapters)} 个无效章节（null 或非 dict）")

        # 记录每个章节的诊断信息
        chapter_levels = []
        for ch in all_chapters:
            score, reason = _chapter_content_level(ch)
            chapter_levels.append({"ch": ch, "score": score, "reason": reason})

        # 统计
        empty_chapters = [d for d in chapter_levels if d["score"] <= 1]  # 1分及以下视为空洞
        warn_chapters = [d for d in chapter_levels if d["score"] == 2]
        ok_chapters = [d for d in chapter_levels if d["score"] >= 3]

        if empty_chapters:
            self._diagnose("Step5_章节完整", "FAIL",
                           f"{len(empty_chapters)} 个章节内容空洞（数据不足）: "
                           + ", ".join(f"{d['ch'].get('id','?')}({d['reason']})" for d in empty_chapters))
        elif warn_chapters:
            self._diagnose("Step5_章节完整", "WARN",
                           f"{len(warn_chapters)} 个章节部分内容缺失: "
                           + ", ".join(f"{d['ch'].get('id','?')}({d['reason']})" for d in warn_chapters))
        else:
            self._diagnose("Step5_章节完整", "OK",
                           f"所有 {len(all_chapters)} 个章节内容完整")

        # 保留所有章节（不简单过滤），但空洞章节显示提示
        chapters = all_chapters

        if not chapters:
            self._log(self._get_diagnostic_report())
            raise RuntimeError(
                "所有章节均无实质内容（KPI/发现/结论/图表/表格），"
                "请检查 AI 分析结果或确认数据是否足够进行分析。"
            )

        # 构建导航 HTML（新版模板用 scrollToSection 锚点定位）
        nav_items = "\n".join(
            f'    <div class="nav-item" onclick="scrollToSection(\'{ch.get("id", f"ch{i}")}\',this)">'
            f'<span class="nav-dot"></span>{ch.get("title", f"章节{i+1}")}</div>'
            for i, ch in enumerate(chapters)
        )

        # 构建章节页 HTML — 空洞章节添加警告标记
        chapter_pages = []
        for i, ch in enumerate(chapters):
            cid = ch.get("id", f"ch{i}")
            # 查找该章节的诊断信息
            level_info = next((d for d in chapter_levels if d["ch"].get("id") == cid), None)
            is_sparse = level_info and level_info["score"] <= 1  # 空洞章节
            warn_reason = level_info["reason"] if level_info else ""

            chart_containers = ""
            charts = ch.get("charts") or []

            # 检查每个图表是否有效（兼容所有图表类型）
            valid_charts = []
            for ci, chart in enumerate(charts):
                if not isinstance(chart, dict):
                    continue  # 跳过 null 或非 dict 的图表项
                chart_type = chart.get("type", "?")
                has_x = bool(chart.get("x"))
                has_series = any(s.get("data") for s in chart.get("series", []) if s.get("data"))
                has_pie_data = any(d.get("value") is not None for d in chart.get("data", []) if isinstance(d, dict))
                # scatter/boxplot/waterfall: data 是数组
                data_arr = chart.get("data", [])
                has_array_data = (
                    data_arr and chart_type in ("scatter", "boxplot", "waterfall")
                    and isinstance(data_arr, list) and len(data_arr) > 0
                )
                # bubble: [{"name":"","value":[x,y,size]}]
                has_bubble_data = (
                    data_arr and chart_type == "bubble"
                    and isinstance(data_arr, list) and len(data_arr) > 0
                    and isinstance(data_arr[0], dict)
                    and isinstance(data_arr[0].get("value"), list)
                    and len(data_arr[0].get("value", [])) >= 3
                )
                # gauge: value + max
                has_gauge_data = chart_type == "gauge" and chart.get("value") is not None
                # radar: indicator + data
                has_radar_data = chart_type == "radar" and chart.get("indicator") and data_arr
                is_valid = has_x or has_series or has_pie_data or has_array_data or has_bubble_data or has_gauge_data or has_radar_data

                if len(charts) == 1:
                    if is_valid:
                        chart_containers += f'''
            <div class="card">
              <div class="card-header"><h2>{chart.get("title","图表")}</h2></div>
              <div class="card-body"><div class="chart-container tall" id="chart-{cid}-0"></div></div>
            </div>'''
                    else:
                        chart_containers += f'''
            <div class="card">
              <div class="card-header"><h2>{chart.get("title","图表")}</h2></div>
              <div class="card-body">
                <div class="empty-hint" style="height:120px;display:flex;align-items:center;justify-content:center;">
                  ⚠️ 图表数据为空（{chart_type}）
                </div>
              </div>
            </div>'''
                elif len(charts) >= 2:
                    if ci < 2:  # 只渲染前2个图表
                        if is_valid:
                            chart_containers += f'''
              <div class="card">
                <div class="card-header"><h2>{chart.get("title","图表" + str(ci+1))}</h2></div>
                <div class="card-body"><div class="chart-container" id="chart-{cid}-{ci}"></div></div>
              </div>'''
                        else:
                            chart_containers += f'''
              <div class="card">
                <div class="card-header"><h2>{chart.get("title","图表" + str(ci+1))}</h2></div>
                <div class="card-body">
                  <div class="empty-hint" style="height:120px;display:flex;align-items:center;justify-content:center;">
                    ⚠️ 图表数据为空（{chart_type}）
                  </div>
                </div>
              </div>'''

            # 数据明细表格已移除（用户反馈用处不大）
            table_html = ""

            sheet_tag = ch.get("sheet", "")

            # 空洞章节警告标签
            sparse_warning = ""
            if is_sparse:
                sparse_warning = f'''
    <div class="chapter-warn">
      ⚠️ 数据不足：{warn_reason}
    </div>'''

            chapter_pages.append(f'''
  <section class="page-section" id="page-{cid}" data-sparse="{'true' if is_sparse else 'false'}">
    <div class="page-header">
      <h1>{ch.get("title","")}</h1>
      <div class="meta"><span>Sheet: {sheet_tag}</span></div>
    </div>
    {sparse_warning}
    <div class="kpi-grid" id="kpi-{cid}"></div>
    <div class="card">
      <div class="card-header"><h2>分析发现</h2></div>
      <div class="card-body">
        <ul class="findings" id="findings-{cid}"></ul>
        <div class="conclusion" id="conclusion-{cid}"></div>
      </div>
    </div>
    {chart_containers}
    {table_html}
  </section>''')

        # 构建 report_data（必须在 chapter_levels 定义之后）
        report_data = {
            **analysis_json,
            "data_description": data_description,
            "sheet_stats": sheet_stats,
            # 注入诊断信息（用于前端展示）
            "_diagnostics": self._diagnostics,
            "_chapter_levels": [
                {"id": d["ch"].get("id"), "score": d["score"], "reason": d["reason"]}
                for d in chapter_levels
            ],
        }

        if template_path.exists():
            html = template_path.read_text(encoding="utf-8")
        else:
            html = self._minimal_html_template()

        sheet_count = len(summary_json)
        html = html.replace("{{REPORT_TITLE}}", f"{file_name} 分析报告")
        html = html.replace("{{REPORT_DATE}}", report_date)
        html = html.replace("{{FILE_NAME}}", file_name)
        html = html.replace("{{SHEET_COUNT}}", str(sheet_count))
        html = html.replace("{{NAV_ITEMS}}", nav_items)
        html = html.replace("{{CHAPTER_PAGES}}", "\n".join(chapter_pages))
        html = html.replace(
            "{{REPORT_DATA_JSON}}",
            json.dumps(report_data, ensure_ascii=False, default=str)
        )

        return html

    def _minimal_html_template(self) -> str:
        """当模板文件不存在时的后备方案"""
        return """<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>分析报告</title></head><body>
<script>const REPORT_DATA = {{REPORT_DATA_JSON}};</script>
<p>报告模板加载失败，请检查 templates/report_template.html 是否存在。</p>
</body></html>"""        # 15. [已移除旧通用版] 旧的 pattern_missing_brace / _add_dict_braces 已删除
        # 原因：该正则使用 (?!.*?[{\[]) 负向前瞰存在 .*? 空匹配 bug（同 Rule 12），
        #       且其 _add_dict_braces 回调函数的 f-string 存在语法风险 (single '\}' not allowed)。
        #       精确版 pattern_agg_missing_dict_brace（下方）已完全覆盖 .agg() 场景。
