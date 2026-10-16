"""
prompt_builder.py — 构建各步骤的 Prompt（零幻觉·精简·健壮版）

优化原则：
  1. 去幻觉：所有实体名/数值必须来自统计数据，用 GROUNDING_RULES 约束
  2. 精简：删除重复/冗余说明，减少 token 浪费；信息密度最大化
  3. 健壮：Step2 脚本 prompt 枚举最常见错误模式，显著减少运行时报错
  4. 贴合重点：Step1.5 计划 → Step2 脚本 → Step4 分析 三步传递，全程围绕 analysis_focus
  5. 防空数据：Step4 禁止任何字段输出空值/null/空数组
"""
from __future__ import annotations
import json

# ════════════════════════════════════════════════════════════
# 支持的图表类型
# ════════════════════════════════════════════════════════════
SUPPORTED_CHART_TYPES = {
    "bar":       {"name": "柱状图",   "desc": "对比分类数据大小",       "example": "各区域销售额对比"},
    "line":      {"name": "折线图",   "desc": "展示趋势变化",           "example": "月度销售走势"},
    "pie":       {"name": "饼图",     "desc": "展示占比关系",           "example": "各渠道销售占比"},
    "rose":      {"name": "玫瑰图",   "desc": "类别差异大的占比",       "example": "各月销售额差异"},
    "scatter":   {"name": "散点图",   "desc": "两变量相关性",           "example": "广告投入vs销售额"},
    "bubble":    {"name": "气泡图",   "desc": "三变量关系",             "example": "价格/满意度/销量"},
    "funnel":    {"name": "漏斗图",   "desc": "转化漏斗",               "example": "销售漏斗转化"},
    "waterfall": {"name": "瀑布图",   "desc": "累计变化过程",           "example": "收入→成本→净利润"},
    "boxplot":   {"name": "箱型图",   "desc": "分布与异常值",           "example": "各城市销售额分布"},
    "radar":     {"name": "雷达图",   "desc": "多维度对比",             "example": "竞品多维度评分"},
    "gauge":     {"name": "仪表盘",   "desc": "目标达成率",             "example": "季度目标完成率"},
    "map":       {"name": "地图",     "desc": "地理分布",               "example": "各省份销售分布"},
}

# ════════════════════════════════════════════════════════════
# 图表格式说明（Step4 必须遵守，否则图表空白）
# ════════════════════════════════════════════════════════════
_CHART_FORMAT_RULES = (
    "CHART_FORMAT(严格遵守否则图表空白):\n"
    "bar/line: {x:[...],series:[{name,data:[num,...]}]}\n"
    "pie/rose/funnel: {data:[{name,value:num}]}  ←严禁用series\n"
    "scatter: {data:[[x,y],...]}  ←严禁用{name,value}\n"
    "bubble: {data:[{name,value:[x,y,size]}]}\n"
    "waterfall: {x:[...],data:[num含正负]}\n"
    "boxplot: {x:[...],data:[[min,Q1,med,Q3,max],...]}\n"
    "radar: {indicator:[...],data:[{name,value:[score,...]}]}\n"
    "gauge: {value:num,max:num,name:str}\n"
    "map: {region:'china|world',data:[{name,value}]}  ←严禁用series"
)

# ════════════════════════════════════════════════════════════
# 防幻觉铁律（所有 AI 调用共用）
# ════════════════════════════════════════════════════════════
_GROUNDING_RULES = (
    "GROUNDING_RULES(最高优先级,不可违背):\n"
    "G1. 实体名(公司/人/产品/地区/渠道)100%来自统计数据,禁止编造\n"
    "G2. 数值基于统计数据的sum/mean/min/max/count推导,禁止凭空捏造\n"
    "G3. table每行每格必须填入真实数据值,来源:group_aggregations或categorical_stats.top_values\n"
    "G4. 禁止输出null/undefined/'数据不足'/'暂无'/[]空数组——必须找替代数据填满\n"
    "G5. 字段没有分组数据时,用categorical_stats.top_values的前N条填充table行\n"
    "G6. findings/kpis/charts引用的每个名称和数字都必须能在统计数据中溯源"
)


def _strip_symbols(text: str) -> str:
    """移除 AI 无法理解的 emoji/特殊符号"""
    symbols = "❌⚠️✅✓🔴⚡📝⏳★☆➤↵▶◀◆▼▲●○■□◎△▽⊕⊗※§†‡°"
    for s in symbols:
        text = text.replace(s, "")
    return text


# ════════════════════════════════════════════════════════════
# Step 1.5：分析规划 Prompt
# ════════════════════════════════════════════════════════════

def build_analysis_plan_prompt(
    header_info: dict,
    data_description: str,
    analysis_focus: str,
) -> str:
    """
    Step 1.5：让 AI 根据字段列表和分析重点输出结构化分析计划。
    输出供 Step 2 脚本生成使用，确保脚本统计的字段和分组与用户需求对齐。
    """
    # 极简 header：列名 + 单字母类型（n/d/s），不包含 sample_rows（减少 token）
    slim_header = {}
    for sheet_name, sheet_data in header_info.items():
        if isinstance(sheet_data, dict) and "columns" in sheet_data:
            cols = sheet_data.get("columns", [])
            dtypes = sheet_data.get("dtypes", {})
            type_map = {}
            for c in cols:
                t = str(dtypes.get(c, "")).lower()
                if any(k in t for k in ["int", "float", "double", "decimal", "number"]):
                    type_map[c] = "n"
                elif any(k in t for k in ["datetime", "date", "time"]):
                    type_map[c] = "d"
                else:
                    type_map[c] = "s"
            slim_header[sheet_name] = {"cols": cols, "types": type_map}
        else:
            slim_header[sheet_name] = sheet_data

    header_str = json.dumps(slim_header, ensure_ascii=False, separators=(",", ":"))
    focus = analysis_focus.strip() or "全面分析，挖掘核心业务指标"
    desc_line = f"DATA_SCOPE: {data_description.strip()}\n" if data_description.strip() else ""

    return (
        "你是资深数据分析师，根据字段信息制定分析计划（不写代码）。\n\n"
        "【字段】\n" + header_str + "\n\n"
        + desc_line
        + "【分析重点】" + focus + "\n\n"
        "【任务】输出纯JSON对象（无markdown）：\n"
        '{"objective":"一句话目标",'
        '"metrics":[{"field":"数值列名","aggs":["sum","mean","max"],"label":"显示名"}],'
        '"dimensions":[{"field":"分类/日期列名","type":"cat|date|num"}],'
        '"groupings":[{"by":["分组列"],"metrics":[{"field":"数值列","agg":"sum"}],"label":"分析描述"}],'
        '"focus_columns":["与分析重点最相关的列名列表"],'
        '"charts":[{"type":"bar|line|pie|rose|scatter|radar","title":"图表标题","x":"维度列","y":["指标列"]}],'
        '"notes":"补充说明"}\n\n'
        "要求: "
        "metrics覆盖所有数值列(至少sum+mean); "
        "dimensions覆盖分类列和日期列; "
        "groupings>=3(优先选有业务意义的列如地区/渠道/产品/客户类型); "
        "charts>=4且类型多样; "
        "focus_columns至少列出5个与分析重点最相关的字段。"
    )


# ════════════════════════════════════════════════════════════
# Step 2：脚本生成 Prompt
# ════════════════════════════════════════════════════════════

def build_script_prompt(
    file_path: str,
    sheets: list,
    header_info: dict,
    data_description: str,
    analysis_focus: str,
    error_feedback: str = "",
    validation_fields: list = None,
    analysis_plan: dict = None,
) -> str:
    """
    Step 2：生成 Python pandas 数据提炼脚本。
    - 健壮性优先：枚举所有高频报错模式，要求脚本在生成时就规避
    - 贴合重点：通过 analysis_plan 引导脚本优先统计与 analysis_focus 相关字段
    """

    # ── 表头精简（三级压缩）──
    slim_header = {}
    for sheet_name, sheet_data in header_info.items():
        if not (isinstance(sheet_data, dict) and "columns" in sheet_data):
            slim_header[sheet_name] = sheet_data
            continue

        columns = sheet_data.get("columns", [])
        dtypes = sheet_data.get("dtypes", {})
        sample_rows = sheet_data.get("sample_rows", [])
        col_count = len(columns)

        # 类型压缩为单字母
        def _type_code(c):
            t = str(dtypes.get(c, "")).lower()
            if any(k in t for k in ["int", "float", "double", "decimal", "number"]):
                return "n"
            if any(k in t for k in ["datetime", "date", "time"]):
                return "d"
            return "s"

        if col_count > 25:
            # 超紧凑：3行文本
            types_str = ",".join(_type_code(c) for c in columns)
            sample_str = ""
            if sample_rows:
                sample_str = ",".join(str(sample_rows[0].get(c, ""))[:15] for c in columns)
            slim_header[sheet_name] = {
                "__compact__": (
                    f"# {sheet_name}({col_count}列)\n"
                    f"列: {','.join(columns)}\n"
                    f"型: {types_str}\n"
                    + (f"样: {sample_str}\n" if sample_str else "")
                )
            }
        elif col_count > 15:
            slim_header[sheet_name] = {
                "columns": columns,
                "dtypes": {c: _type_code(c) for c in columns},
                "sample_rows": sample_rows[:1],
            }
        else:
            s = dict(sheet_data)
            s["sample_rows"] = sample_rows[:2]
            s.pop("_original_columns", None)
            slim_header[sheet_name] = s

    # 序列化
    header_parts = []
    for sn, sd in slim_header.items():
        if isinstance(sd, dict) and "__compact__" in sd:
            header_parts.append(sd["__compact__"])
        else:
            header_parts.append(f"{sn}: {json.dumps(sd, ensure_ascii=False, indent=2)}")
    header_str = "\n".join(header_parts)

    # ── 分析计划注入（重点字段 + 分组方案）──
    plan_inject = ""
    if analysis_plan:
        focus_cols = analysis_plan.get("focus_columns", [])
        metrics = analysis_plan.get("metrics", [])
        groupings = analysis_plan.get("groupings", analysis_plan.get("grouping_schemes", []))
        objective = (analysis_plan.get("objective", analysis_plan.get("analysis_objective", "")) or "")[:150]

        # 关键指标字段
        metric_lines = []
        for m in metrics[:25]:
            name = (m.get("field", m.get("column", m.get("name", ""))) or "").strip()
            if not name:
                continue
            aggs = m.get("aggs", m.get("aggregations", ["sum", "mean"]))
            metric_lines.append(f"  {name}: {'+'.join(str(a) for a in aggs[:4])}")

        # 分组方案
        group_lines = []
        for g in groupings[:8]:
            by = g.get("by", g.get("group_by", []))
            if isinstance(by, str):
                by = [by]
            by = [str(b).strip() for b in by if b and str(b).strip()]
            if not by:
                continue
            label = g.get("label", "")
            group_lines.append(f"  按[{','.join(by)}]分组" + (f" ({label})" if label else ""))

        # 优先分析列
        focus_col_names = [str(c).strip() for c in focus_cols[:10] if str(c).strip()]

        if metric_lines or group_lines or focus_col_names:
            plan_inject = f"\n【分析计划（必须全部覆盖）】\n目标: {objective}\n"
            if focus_col_names:
                plan_inject += f"重点列: {', '.join(focus_col_names)}\n"
            if metric_lines:
                plan_inject += "指标统计:\n" + "\n".join(metric_lines) + "\n"
            if group_lines:
                plan_inject += "分组方案:\n" + "\n".join(group_lines) + "\n"
            plan_inject += (
                "执行要求: 脚本必须计算上述所有指标和分组；"
                "重点列须出现在numeric_stats/categorical_stats中；"
                "分组方案须全部出现在group_aggregations中。\n"
            )

    # ── 校验字段注入 ──
    validation_inject = ""
    if validation_fields:
        lines = []
        for r in validation_fields:
            sheet = r.get("sheet", "")
            field = r.get("field", "")
            if field == "rows":
                lines.append(f"  {sheet}.rows(行数)")
            elif field == "cols":
                lines.append(f"  {sheet}.cols(列数)")
            elif field.startswith(("total_", "mean_", "min_", "max_")):
                prefix, col = field.split("_", 1)
                hint = {"total": "总和", "mean": "均值", "min": "最小", "max": "最大"}[prefix]
                lines.append(f"  {sheet}.{field}(列'{col}'的{hint})")
            else:
                lines.append(f"  {sheet}.{field}")
        validation_inject = "\n【强制输出字段(不可遗漏)】\n" + "\n".join(lines) + "\n"

    # ── 错误反馈注入 ──
    retry_section = ""
    if error_feedback:
        retry_section = (
            "\n【上次错误(必须修复)】\n"
            + error_feedback.strip() + "\n"
        )

    focus = analysis_focus.strip() or "全面统计分析"
    desc_line = f"DATA_SCOPE: {data_description.strip()}\n" if data_description.strip() else ""

    return (
        "你是数据工程师。编写Python脚本统计提炼Excel/CSV数据，结果JSON输出到stdout。\n\n"
        "FILE: " + file_path + "\n"
        "SHEETS: " + json.dumps(sheets, ensure_ascii=False) + "\n"
        + desc_line
        + "FOCUS: " + focus + "\n\n"
        "【表头】\n" + header_str
        + plan_inject
        + validation_inject
        + retry_section + "\n\n"

        # ── 输出JSON结构 ──
        "【JSON输出结构】每个Sheet一个key:\n"
        "{\n"
        '  "Sheet名": {\n'
        '    "basic": {"rows":行数,"cols":列数,"memory_mb":内存},\n'
        '    "missing": {"列名":{"count":缺失数,"rate":缺失率},...},\n'
        '    "numeric_stats": {"数值列名":{"min","max","mean","median","std","q25","q75","zeros","sum"},...},\n'
        '    "categorical_stats": {"分类列名":{"unique":唯一数,"top_values":[{"value","count","pct"},...]},...},\n'
        '    "group_aggregations": [{"group_by":"列名","aggregated_columns":["数值列"],"data":[{...}]},...],\n'
        '    "anomalies": [{"column":"列名","outlier_count":数量,"outlier_pct":比例,"lower":下界,"upper":上界}]\n'
        "  }\n"
        "}\n\n"

        # ── 完整性要求 ──
        "【完整性要求】\n"
        "1. numeric_stats: 覆盖全部数值列，不可截断或遗漏\n"
        "2. categorical_stats: 覆盖unique<=200且行数>=10的分类列(排除ID/编号/单号/uuid类)\n"
        "3. group_aggregations: >=3组，优先选地区/渠道/产品/类型等业务列做group_by；"
        "每组aggregated_columns取核心数值列(<=10个)\n"
        "4. anomalies: 对每个数值列用IQR法检测\n\n"

        # ── pandas 4.x 兼容+高频错误防范 ──
        "【pandas兼容规则(违反必然报错)】\n"
        "1. pd.read_excel(): 禁止encoding参数(xlsx无此参数,用engine='openpyxl')\n"
        "2. df.describe(): 禁止skipna参数(pandas4已移除)\n"
        "3. df.corr(): 改为df.select_dtypes(include='number').corr()\n"
        "4. .agg(): 只接受列表或dict——.agg(['sum','mean']) 或 .agg(s='sum')，禁止.agg('sum','mean')\n"
        "5. pd.read_excel(usecols=...): usecols只能传原始列名(Excel实际列名),不能传display名\n"
        "6. groupby().agg(): 多列agg要用dict格式——{col: ['sum','mean']}，key必须是列名，禁止用'count'/'mean'等聚合函数名作为key\n"
        "7. df[col].value_counts().head(10): 分类统计标准写法\n"
        "8. 所有数值运算前用pd.to_numeric(df[col], errors='coerce')防止字符串报错\n"
        "9. groupby后加observed=True(pandas2.2+要求)\n"
        "10. json.dumps末行加ensure_ascii=False,default=str(防Timestamp报错)\n"
        "11. 分组聚合正确写法: agg_dict={col:['sum','mean'] for col in numeric_cols}，禁止agg_dict['count']='sum'这种以聚合函数名为key的写法\n\n"

        # ── 效率规则 ──
        "【效率规则】\n"
        "禁止iterrows/itertuples; 全用向量化; groupby后一次.agg()算完; "
        "用describe()批量统计; usecols按需读取列。\n\n"

        # ── 脚本结构要求 ──
        "【脚本结构】\n"
        "1. 首行注释: # 数据约束:[摘要]\n"
        "2. 自动安装缺包:\n"
        "   import subprocess,sys\n"
        "   def _ensure(pkg,n=None):\n"
        "     try: __import__(n or pkg)\n"
        "     except: subprocess.check_call([sys.executable,'-m','pip','install',pkg,'-q'])\n"
        "   在每个第三方import前调用_ensure('openpyxl')\n"
        "3. 数值保留2位小数; 末行: print(json.dumps(result,ensure_ascii=False,default=str))\n"
        "4. 纯代码,无markdown,无注释冗余,直接运行。"
    )


# ════════════════════════════════════════════════════════════
# Step 2 New：意图生成 Prompt（替代脚本生成）
# ════════════════════════════════════════════════════════════

def build_intent_prompt(
    header_info: dict,
    data_description: str,
    analysis_focus: str,
    analysis_plan: dict = None,
    error_feedback: str = "",
    column_profiles: dict = None,
) -> str:
    """
    Step 2 New：让 AI 输出结构化分析意图 JSON（而非 Python 脚本）。
    程序端用 IntentEngine 将意图翻译为确定性的 pandas 操作。
    """
    # 极简表头（复用 Step1.5 的压缩逻辑）
    slim_header = {}
    for sheet_name, sheet_data in header_info.items():
        if not (isinstance(sheet_data, dict) and "columns" in sheet_data):
            slim_header[sheet_name] = sheet_data
            continue
        columns = sheet_data.get("columns", [])
        dtypes = sheet_data.get("dtypes", {})
        type_map = {}
        for c in columns:
            t = str(dtypes.get(c, "")).lower()
            if any(k in t for k in ["int", "float", "double", "decimal", "number"]):
                type_map[c] = "n"
            elif any(k in t for k in ["datetime", "date", "time"]):
                type_map[c] = "d"
            else:
                type_map[c] = "s"
        slim_header[sheet_name] = {"cols": columns, "types": type_map}

    header_str = json.dumps(slim_header, ensure_ascii=False, separators=(",", ":"))
    focus = analysis_focus.strip() or "全面分析"
    desc_line = f"DATA_SCOPE: {data_description.strip()}\n" if data_description.strip() else ""

    # 分析计划注入
    plan_inject = ""
    if analysis_plan:
        plan_json = json.dumps(analysis_plan, ensure_ascii=False, separators=(",", ":"))[:2000]
        plan_inject = f"\n【分析计划参考】\n{plan_json}\n"

    # 错误反馈注入（重试场景）
    retry_section = ""
    if error_feedback:
        retry_section = f"\n【上次错误(必须修正)】\n{error_feedback.strip()}\n"

    # 列统计摘要注入（让 AI 基于真实数据分布做判断）
    profile_section = ""
    if column_profiles:
        lines = []
        for sheet_name, profile in column_profiles.items():
            lines.append(f"--- {sheet_name} ---")
            for col, info in profile.items():
                parts = [f"{col}:", info["type"], f"非空{info['null']}", f"唯一{info['uniq']}"]
                if "range" in info:
                    parts.append(f"范围{info['range']}")
                if "mean" in info:
                    parts.append(f"均值{info['mean']}")
                if "top" in info:
                    parts.append(f"TOP:{','.join(info['top'])}")
                if "note" in info:
                    parts.append(f"[{info['note']}]")
                lines.append(" | ".join(parts))
        profile_section = "\n【列统计摘要(真实数据分布)】\n" + "\n".join(lines) + "\n"

    # 意图 JSON 格式说明
    intent_format = (
        '{\n'
        '  "sheets": ["Sheet1"],\n'
        '  "numeric_cols": ["数值列1","数值列2"],\n'
        '  "categorical_cols": ["分类列1","分类列2"],\n'
        '  "datetime_cols": ["日期列"],\n'
        '  "groupings": [\n'
        '    {"by": ["分组列"], "agg_cols": ["数值列"], "agg_funcs": ["sum","mean"], "label": "分组描述"}\n'
        '  ],\n'
        '  "filters": [{"col": "列名", "op": "==", "value": "值"}],\n'
        '  "need_outliers": true,\n'
        '  "need_corr": false,\n'
        '  "notes": "补充说明"\n'
        '}'
    )

    return (
        "你是数据分析师。根据字段信息和分析重点，输出结构化分析意图JSON。\n"
        "程序将用你的意图自动执行统计，不需要你写代码。\n\n"
        "【字段】\n" + header_str + "\n\n"
        + desc_line
        + "【分析重点】" + focus + "\n"
        + plan_inject
        + profile_section
        + retry_section + "\n\n"
        "【任务】输出纯JSON对象（无markdown）：\n"
        + intent_format + "\n\n"
        "要求:\n"
        "1. numeric_cols: 必须参考【列统计摘要】中的 num 类型列；\n"
        "   禁止把 cat 类型（如币种、地区代码）放入 numeric_cols\n"
        "2. categorical_cols: 参考【列统计摘要】中的 cat 类型列；\n"
        "   标记为 ID列 的不要放入 categorical_cols\n"
        "3. datetime_cols: 参考【列统计摘要】中的 date 类型列\n"
        "4. groupings>=3: 按业务意义强的列分组（如地区/渠道/产品/客户类型）\n"
        "   每组 agg_funcs 只选 ['sum','mean'] 或 ['sum'] 或 ['mean']\n"
        "5. filters: 如有明确筛选条件（如'只看有效合同'）则填写，否则省略\n"
        "6. need_outliers: true（默认检测异常值）\n"
        "7. need_corr: 仅在分析重点明确提到'相关性'时才 true\n"
        "8. 列名必须与【字段】中的名称完全一致（含大小写、空格）"
    )


# ════════════════════════════════════════════════════════════
# Step 4 复杂度检测
# ════════════════════════════════════════════════════════════

def _analyze_focus_complexity(analysis_focus: str) -> dict:
    """分析用户 analysis_focus 的复杂度，返回结构化指令。"""
    focus = analysis_focus.strip()
    if not focus:
        return {"is_complex": False, "reason": "无分析重点"}

    dim_kw = ["维度", "角度", "方面", "层面", "评估", "评价体系", "规模", "盈利", "风险",
              "增长", "运营", "效率", "价值", "质量", "能力", "表现", "健康度"]
    entity_kw = ["具体名称", "列示", "逐个", "每个", "分别", "逐一", "排名", "TOP", "top", "前N", "对比", "差异"]
    quant_kw = ["占比", "率", "增长率", "均值", "中位数", "分布", "区间", "阈值", "分级", "分类", "梯队"]
    radar_kw = ["雷达", "评分", "打分", "综合", "矩阵", "象限"]

    has_multi_dim = any(kw in focus for kw in dim_kw)
    has_entity_req = any(kw in focus for kw in entity_kw)
    has_quant_req = any(kw in focus for kw in quant_kw)
    has_radar_hint = any(kw in focus for kw in radar_kw)
    is_complex = len(focus) >= 100 or has_multi_dim or has_entity_req or has_quant_req

    return {
        "is_complex": is_complex,
        "has_multi_dim": has_multi_dim,
        "has_entity_req": has_entity_req,
        "has_quant_req": has_quant_req,
        "has_radar_hint": has_radar_hint,
        "focus_length": len(focus),
    }


# ════════════════════════════════════════════════════════════
# Step 4：深度分析 Prompt
# ════════════════════════════════════════════════════════════

def build_analysis_prompt(
    summary_json: dict,
    data_description: str,
    analysis_focus: str,
    file_name: str,
    data_has_anomalies: bool = False,
) -> str:
    """
    Step 4：零幻觉深度分析，输出结构化分析结论 JSON。
    所有实体名/数值必须能在 summary_json 中溯源。
    """
    complexity = _analyze_focus_complexity(analysis_focus)

    # ── 统计数据截断（按复杂度）──
    summary_str = json.dumps(summary_json, ensure_ascii=False, indent=2)
    if complexity["is_complex"]:
        summary_limit = 24000
        min_kpis = "6-10"
        min_findings = "8-12"
        min_chf = "4-6"
        min_charts = "3-5"
        quantity_note = (
            "COMPLEX_MODE: findings按分析重点的维度分组，每维度>=2条; "
            "多维评估生成radar图; 排名需求→table按主指标降序，findings含TOP3/BOTTOM3名称+数值; "
            "table行数>=8。"
        )
    else:
        summary_limit = 12000
        min_kpis = "4-6"
        min_findings = "4-6"
        min_chf = "2-3"
        min_charts = "2-3"
        quantity_note = ""

    # ── 截断统计数据 + 说明 ──
    summary_truncated = summary_str[:summary_limit]
    if len(summary_str) > summary_limit:
        summary_truncated += "...[已截断,仅用截断前数据]"

    # ── 数据预警 ──
    anomaly_note = (
        "ANOMALY=true: 结论加'初步判断/可能/待验证'等措辞; "
        "data_caveats列出受影响字段; anomalies列出具体异常项。\n"
        if data_has_anomalies else ""
    )

    # ── 辅助提示：统计数据中有哪些可用实体 ──
    # 从 summary_json 提取 categorical_stats 的 top_values，作为"合法实体池"提示
    entity_hints = []
    for sheet_name, sheet_data in summary_json.items():
        if not isinstance(sheet_data, dict):
            continue
        cat = sheet_data.get("categorical_stats", {})
        for col, col_data in list(cat.items())[:5]:  # 最多取5个分类列
            if isinstance(col_data, dict):
                tops = col_data.get("top_values", [])
                names = [str(t.get("value", "")) for t in tops[:5] if t.get("value")]
                if names:
                    entity_hints.append(f"{col}: {', '.join(names)}")
    entity_hint_block = ""
    if entity_hints:
        entity_hint_block = (
            "\n【合法实体池(findings/table只能使用这里的名称)】\n"
            + "\n".join(entity_hints[:8]) + "\n"
        )

    focus = analysis_focus.strip() or "全面分析"
    desc_line = f"DATA_SCOPE: {data_description.strip()}\n" if data_description.strip() else ""

    # ── JSON 模板 ──
    json_tpl = (
        '{"kpis":[{"label":"指标名","value":数字,"change":"同比+N%","change_dir":"up|down|neutral","sub":"单位/说明"},...>='
        + min_kpis + '个],'
        '"executive_summary":"3-5句核心结论",'
        '"ai_findings":["带数值+实体名的自动发现结论"],'
        '"findings":["每条含数值+实体名",...>=' + min_findings + '条],'
        '"overview_trend":{"x":["时间序列/分类"],"series":[{"name":"系列","data":[num,...]}]},'
        '"overview_pie":{"data":[{"name":"分类","value":num},...]},'
        '"chapters":[{'
        '"id":"ch0","title":"章节标题","sheet":"Sheet名",'
        '"key_metrics":[{"name":"指标名","value":数字,"trend":"up|down|neutral","change":"环比+N%"},...>=3个],'
        '"findings":["带数值的发现",...>=' + min_chf + '条],'
        '"ai_insights":"AI自动挖掘的洞察",'
        '"conclusion":"150字内结论",'
        '"charts":[{'
        '"type":"bar|line|pie|rose|scatter|bubble|waterfall|boxplot|radar|gauge|map|funnel",'
        '"title":"图表标题",'
        '"x":["分类或时间"],'
        '"series":[{"name":"系列名","data":[num,...]}],'
        '"data":[{"name":"名称","value":num}],'
        '"indicator":["维度名"],'
        '"region":"china|world"'
        '},...>=' + min_charts + '个],'
        '"table":{"cols":["列头"],"rows":[["行数据",...],...]}},...每个Sheet一章],'
        '"anomalies":[],'
        '"recommendations":[{"title":"标题","detail":"100字内"},...3-5条],'
        '"data_caveats":[],'
        '"overall_conclusion":"200字内总评"}'
    )

    prompt = (
        # ══ 防幻觉铁律（第一位，最高优先级）══
        _GROUNDING_RULES + "\n\n"

        # ══ 角色 + 任务定义 ══
        "你是数据分析师。严格基于下方【统计数据】输出分析结论JSON，不得引入任何数据之外的信息。\n\n"
        "FILE: " + file_name + "\n"
        + desc_line
        + "FOCUS: " + focus + "\n"
        + anomaly_note
        + (quantity_note + "\n" if quantity_note else "")
        + entity_hint_block

        # ══ 统计数据 ══
        + "\n【统计数据(唯一数据来源)】\n"
        + summary_truncated + "\n\n"

        # ══ 输出规则 ══
        "OUTPUT_RULES:\n"
        "1. 纯JSON对象({开头),无markdown,无解释文字\n"
        "2. KPI.value必须是数字(禁止'多个'/'N/A'等文字)\n"
        "3. 每个Sheet对应一个chapter; table>=5行且每格非空\n"
        "4. overview_trend.x和series.data长度一致; overview_pie.data非空\n"
        "5. QUANTITY: KPI>=" + min_kpis + " | findings>=" + min_findings
        + " | chapter.findings>=" + min_chf + " | chapter.charts>=" + min_charts + "\n\n"

        # ══ 图表格式（防空白）══
        + _CHART_FORMAT_RULES + "\n\n"

        # ══ 主动发现规则 ══
        "AUTO_DISCOVER(仅当统计数据中存在时):\n"
        "异常值->boxplot/scatter | 相关性->scatter/bubble | 地理列->map | 财务分解->waterfall | 多维->radar\n\n"

        # ══ JSON 结构 ══
        "【JSON结构】\n" + json_tpl
    )
    return _strip_symbols(prompt)


# ════════════════════════════════════════════════════════════
# 直接分析 Prompt（Word / 多表头 Excel / 小文件）
# ════════════════════════════════════════════════════════════

def build_direct_analysis_prompt(
    file_content: str,
    data_description: str,
    analysis_focus: str,
    file_type: str = "word",
    data_has_anomalies: bool = False,
) -> str:
    """直接分析路径：跳过脚本执行，直接把原始内容喂给 AI 分析。"""

    source_labels = {
        "word": "Word文档(表格已转Markdown)",
        "excel_multi": "Excel多表头(直接分析)",
    }
    source_note = source_labels.get(file_type, "Excel小文件")

    anomaly_note = (
        "ANOMALY=true: 结论加不确定措辞; data_caveats列出影响字段。\n"
        if data_has_anomalies else ""
    )

    complexity = _analyze_focus_complexity(analysis_focus)
    quant_hint = (
        "COMPLEX_MODE: findings>=8条/章; charts>=3个/章; 多维->radar; 排名->TOP3/BOTTOM3名称+数值。\n"
        if complexity["is_complex"] else ""
    )

    focus = analysis_focus.strip() or "全面分析"
    desc_line = f"DATA_SCOPE: {data_description.strip()}\n" if data_description.strip() else ""

    json_tpl = (
        '{"kpis":[{"label":"","value":数字,"change":"","change_dir":"up|down|neutral","sub":""}],'
        '"executive_summary":"核心结论",'
        '"ai_findings":["数值+实体名"],'
        '"findings":["每条含数值+实体名"],'
        '"overview_trend":{"x":[],"series":[{"name":"","data":[]}]},'
        '"overview_pie":{"data":[{"name":"","value":0}]},'
        '"chapters":[{"id":"ch0","title":"","sheet":"","key_metrics":[],'
        '"findings":[],"ai_insights":"","conclusion":"150字内",'
        '"charts":[{"type":"bar|line|pie|radar|map...","title":"","x":[],'
        '"series":[{"name":"","data":[]}],"data":[{"name":"","value":0}],'
        '"indicator":[],"region":"china"}],'
        '"table":{"cols":[],"rows":[]}}],'
        '"anomalies":[],"recommendations":[{"title":"","detail":""}],'
        '"data_caveats":[],"overall_conclusion":"200字内"}'
    )

    return (
        _GROUNDING_RULES + "\n\n"
        "你是数据分析师，基于下方【原始内容】输出分析结论JSON。\n\n"
        "SOURCE: " + source_note + "\n"
        + desc_line
        + "FOCUS: " + focus + "\n"
        + anomaly_note
        + quant_hint
        + "\n【原始内容】\n"
        + file_content[:50000] + ("...[截断]" if len(file_content) > 50000 else "")
        + "\n\n"
        "OUTPUT: 纯JSON对象({开头),无markdown。KPI.value必须是数字; table>=5行; 每格非空。\n\n"
        + _CHART_FORMAT_RULES + "\n\n"
        "【JSON结构】\n" + json_tpl
    )


# ════════════════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════════════════

def estimate_tokens_short(char_count: int) -> str:
    tokens = int(char_count * 1.5)
    if tokens < 1000:
        return str(tokens)
    return f"{tokens // 1000}K"


def _get_chart_types_text() -> str:
    return "\n".join(
        f"{k}: {v['name']} — {v['desc']}"
        for k, v in SUPPORTED_CHART_TYPES.items()
    )


def build_report_prompt(
    analysis_json: dict,
    summary_json: dict,
    data_description: str,
    file_name: str,
    report_date: str,
) -> str:
    """Step 5：基于分析结论生成完整 HTML 报告（通常不用，模板渲染替代）"""
    analysis_str = json.dumps(analysis_json, ensure_ascii=False)
    summary_brief = json.dumps(
        {k: {sk: sv for sk, sv in v.items() if sk in ("basic", "missing")}
         for k, v in summary_json.items()},
        ensure_ascii=False
    )

    return (
        "你是前端工程师，将分析数据填入HTML模板，生成完整单文件BI风格报告。\n\n"
        "【分析数据JSON】\n" + analysis_str[:10000] + "\n\n"
        "【文件名】" + file_name + " | 【日期】" + report_date + "\n"
        + (f"【数据说明】{data_description[:500]}\n" if data_description.strip() else "")
        + "【Sheet基本信息】" + summary_brief[:2000] + "\n\n"
        "要求:\n"
        "1. 替换REPORT_DATA_JSON为实际数据\n"
        "2. 使用ECharts CDN(cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js)\n"
        "3. 暗色BI风格\n"
        "4. 只输出完整HTML代码，无额外说明"
    )


# ════════════════════════════════════════════════════════════
# 微调报告 Prompt — 增量修改，Token 消耗降低 ~50%
# ════════════════════════════════════════════════════════════

def build_tweak_prompt(
    analysis_json: dict,
    tweak_description: str,
    summary_json: dict = None,
    data_description: str = "",
    analysis_focus: str = "",
) -> str:
    """
    微调报告 Prompt：让 AI 根据用户的文字描述，修改现有的 analysis_json。

    Token 优化策略：
    1. 不重传完整原始数据，只传统计数据摘要
    2. AI 做「修改」而非「从头生成」，Prompt 更短
    3. 如果微调需要新数据，AI 在 _needs_data 字段说明，由调用方决定是否重新计算
    """
    # ── 现有报告 JSON（精简：截断长数组，减少 Token）────
    import copy
    slim_json = copy.deepcopy(analysis_json)
    # 截断图表 data 数组（保留前 20 条，让 AI 看到结构即可）
    for ch in slim_json.get("chapters", []):
        for c in ch.get("charts", []):
            if "data" in c and isinstance(c["data"], list) and len(c["data"]) > 20:
                c["data"] = c["data"][:20] + [{"_note": f"共{len(c['data'])}条，已截断"}]
            if "series" in c:
                for s in c["series"]:
                    if "data" in s and isinstance(s["data"], list) and len(s["data"]) > 20:
                        s["data"] = s["data"][:20] + [{"_note": f"共{len(s['data'])}条，已截断"}]
    analysis_str = json.dumps(slim_json, ensure_ascii=False, separators=(",", ":"))

    # ── 统计数据摘要（只传关键信息，不传原始行）────
    summary_brief = ""
    if summary_json:
        brief = {}
        for sheet_name, info in summary_json.items():
            if not isinstance(info, dict):
                continue
            s_brief = {}
            # 基本信息和缺失率
            basic = info.get("basic", {})
            if basic:
                s_brief["basic"] = {"rows": basic.get("rows"), "cols": basic.get("cols")}
            missing = info.get("missing", {})
            if missing:
                s_brief["missing"] = {k: v.get("rate") for k, v in list(missing.items())[:10]}
            # 数值列统计（截断）
            num_stats = info.get("numeric_stats", {})
            if num_stats:
                s_brief["numeric_stats"] = {}
                for i, (col, st) in enumerate(num_stats.items()):
                    if i >= 10:
                        s_brief["numeric_stats"]["_note"] = f"共{len(num_stats)}列，仅显示前10"
                        break
                    s_brief["numeric_stats"][col] = {
                        "mean": st.get("mean"), "sum": st.get("sum"),
                        "min": st.get("min"), "max": st.get("max"),
                        "count": st.get("count"),
                    }
            # 分类列 top 值（截断）
            cat_stats = info.get("categorical_stats", {})
            if cat_stats:
                s_brief["categorical_stats"] = {}
                for i, (col, st) in enumerate(cat_stats.items()):
                    if i >= 10:
                        s_brief["categorical_stats"]["_note"] = f"共{len(cat_stats)}列，仅显示前10"
                        break
                    top = st.get("top_values", [])[:8]
                    s_brief["categorical_stats"][col] = {"top_values": top, "unique": st.get("unique")}
            brief[sheet_name] = s_brief
        summary_brief = json.dumps(brief, ensure_ascii=False, separators=(",", ":"))

    focus = (analysis_focus or "全面分析").strip()
    desc = (data_description or "").strip()

    # ── 输出格式模板（简洁版）────
    json_tpl = (
        '{"title":"","subtitle":"","overview_kpis":[],"overview_trend":{"x":[],"series":[]},'
        '"overview_pie":{"data":[]},"chapters":[{"id":"","title":"","key_metrics":[],'
        '"findings":[],"conclusion":"","charts":[{"type":"","title":"","x":[],"series":[],'
        '"data":[],"indicator":[],"region":""}],"table":{"cols":[],"rows":[]}}],'
        '"anomalies":[],"recommendations":[],"data_caveats":[],"overall_conclusion":""}'
    )

    return (
        "你是数据分析报告编辑助手。根据用户的微调描述，修改现有报告JSON。\n\n"
        "GROUNDING_RULES(最高优先级):\n"
        "G1. 实体名100%来自统计数据,禁止编造\n"
        "G2. 数值基于统计数据推导,禁止凭空捏造\n"
        "G3. 只修改用户要求修改的部分,其他部分完全保持不变\n"
        "G4. 禁止输出null/undefined/'数据不足' — 找不到数据时保留原值\n"
        "G5. 输出完整纯JSON(以{开头},以}结尾),无markdown代码块\n\n"
        "【现有报告JSON】(已截断长数组,供参考结构)\n"
        + analysis_str[:8000] + ("\n[...截断,总长度:" + str(len(analysis_str)) + "]\n" if len(analysis_str) > 8000 else "\n")
        + ("\n【统计数据摘要】(如需新计算,参考此数据)\n" + summary_brief[:3000] + "\n" if summary_brief else "")
        + ("\n【数据说明】" + desc[:300] + "\n" if desc else "")
        + ("\n【原始分析重点】" + focus[:200] + "\n" if focus else "")
        + "\n【用户微调要求】(请仔细阅读,只修改这些内容)\n"
        + tweak_description.strip() + "\n\n"
        "任务:\n"
        "1. 仔细阅读【用户微调要求】,理解用户想怎么改\n"
        "2. 只修改用户要求修改的部分,未要求修改的部分100%保持原样\n"
        "3. 如果你需要新的统计数据才能完成修改(比如用户要求'加上同比增长',但现有数据没有),"
        "请在输出JSON中添加字段: "
        '"_needs_data": "说明需要哪些新数据/新计算"\n'
        "4. 输出完整JSON(和输入结构相同),以{开头,以}结尾\n"
        "5. 图表格式遵守: "
        "bar/line用{x:[],series:[{name,data:[]}]}; "
        "pie/rose/funnel用{data:[{name,value:num}]}; "
        "scatter用{data:[[x,y],...]}\n\n"
        "【输出JSON结构参考】\n" + json_tpl + "\n\n"
        "重要: 直接输出JSON,不要有任何前缀后缀说明文字。"
    )
