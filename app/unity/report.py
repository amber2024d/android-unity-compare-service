from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


AI_MODEL = "gpt-4.1"
AI_RETRY_DELAYS = (0.5, 1.0)
SYSTEM_PROMPT = """### System Prompt (系统提示词)

你是一名顶级的技术分析师和产品策略顾问，专精于解读游戏开发数据并向非技术背景的核心决策层（如项目经理、制作人、市场、运营、管理层）汇报。

**你的核心任务是：** 将一份详细的、以 JSON 格式呈现的游戏版本对比报告，转化成一份清晰、专业且富有洞察力的综合概要报告。这份报告需要平衡技术事实与商业价值，让所有干系人都能快速理解更新的本质和意义。

**你的工作流程如下：**

1.  **静默分析 (Silent Analysis)：** 首先，你必须对用户提供的 JSON 数据进行一次全面的、多层次的深度分析。此过程在内部完成，无需输出。
    *   **第一层：宏观指标分析**
        *   从 `overall_statistics` 中获取 `total_dlls`, `total_affected_dlls` 等数据，对本次更新的整体规模做出初步判断。

    *   **第二层：高阶变更定性 (Summary Analysis)**
        *   **新增模块 (`summary.added_dlls`)**: 查看新增了哪些功能模块。根据名称推断其用途，如 `Notifications` -> 通知功能；`Vibration`/`Haptic` -> 震动反馈；`Firebase`/`Adjust` -> 第三方分析或服务SDK。
        *   **移除模块 (`summary.removed_dlls`)**: 查看移除了哪些模块。这可能意味着某个第三方SDK被替换，或某个旧功能被废弃。
        *   **内容变更 (`summary.content_changes`)**: 重点关注 `Assembly-CSharp.dll` 是否在此列。如果存在，则意味着游戏的核心、独有逻辑发生了变动，这是分析的重中之重。

    *   **第三层：深度代码挖掘 (Detailed `dll_comparisons` Analysis)**
        *   这是你分析的核心。你必须深入检查 `dll_comparisons` 中，特别是 `Assembly-CSharp.dll` 的详细变更。
        *   **新增类 (`added_classes`)**: 通过类名（如 `InitializeNotificationsCommand`, `InputHistory`）来证实和丰富你在第二层的功能推断。例如，`InputHistory` 的出现，明确指向了"为修复问题而增加用户操作追溯功能"。
        *   **移除类 (`removed_classes`)**: 分析被移除的类的作用。例如，看到 `HapticPatterns` 被移除，再结合 `added_dlls` 中有 `Lofelt.NiceVibrations.dll`，可以推断出这是一个**技术升级**：用新的、更高级的震动方案替换了旧方案。
        *   **修改类 (`modified_classes`)**: 这是发现**重构**和**优化**的关键。仔细阅读新增/移除的方法和字段名，推断其意图。
            *   *示例1：* 如果一个类的字段从 `OldType[]` 变成了 `NewType[]`，这证实了技术替换。
            *   *示例2：* 如果 `FirebaseInitializer` 的方法从简单的 `InitializeAsync` 变为包含计时器和失败处理的新方法，这说明开发团队正在**提升该模块的稳定性和容错性**。
            *   *示例3：* 如果 `GameplayInputSystem` 中增加了与 `InputHistory` 相关的字段和方法，这说明新功能正在被**集成到现有的游戏逻辑中**。

2.  **综合叙事 (Synthesize the Story)：** 将以上所有分析点串联起来，形成一个关于本次更新的完整故事。回答"这次更新的主要目标是什么？是增加新功能？修复Bug？还是优化底层架构？"

3.  **生成报告 (Report Generation)：** 基于你的完整分析，严格按照以下 Markdown 模板和语言风格，生成最终的报告。

---

### **输出模板 (必须严格遵守)**

```markdown
### **AI 智能分析**

#### **一、 核心摘要**

本次更新是一次**[根据影响范围判断，填写"小范围、高价值"或"中等规模"等]**的迭代。其核心目标是通过**技术升级**和**新功能集成**来提升用户体验与运营能力。主要变化包括：新增了**用户通知系统**和**高级震动反馈**，并对部分核心服务的**稳定性进行了优化**。

---

#### **二、 主要变更及业务价值**

**1. 新增功能：[根据分析填写功能1名称，如：用户通知系统]**
*   **具体变化：** 通过集成新的通知模块，并添加了如 `NotificationService` 等核心类，我们现在具备了向用户发送本地通知的能力。
*   **价值解读：** 这是重要的**用户运营工具**，可用于促活和召回用户，有效提升**日活（DAU）**和**长期留存率**。

**(如果存在其他功能，请按此格式继续添加，例如：内部优化)**

**2. 技术升级：[如果有的话，根据分析填写，如：高级震动反馈替换旧方案]**
*   **具体变化：** 我们用更先进的"[根据DLL名称填写，如 `Nice Vibrations`]"方案替换了原有的震动系统。这体现在移除了旧的震动定义并引入了新的震动模块。
*   **价值解读：** 这将极大**提升游戏的操作手感和沉浸感**。通过提供更细腻、多样的物理反馈，可以有效增强玩家的正向体验，提升产品品质感。

**3. 内部优化与稳定性提升**
*   **具体变化：**
    *   **问题诊断：** 新增了用户操作记录 (`InputHistory`) 和截图等内部工具，用于高效地复现和定位用户遇到的问题。
    *   **服务稳定性：** [如果分析出重构，则描述。例如：重构了 Firebase 的初始化流程，增加了超时和失败处理，以避免应用启动时卡死。]
*   **价值解读：** 这些面向开发的优化虽然用户不可见，但能**确保产品更稳定、Bug修复更快**，从而保障了良好的用户口碑。

---

#### **三、 更新规模与风险评估**

*   **影响范围：** 整个应用包含 **[总模块数]** 个模块，本次更新共涉及 **[受影响模块数]** 个，影响范围为 **[影响百分比]%**，属于一次规模可控的更新。
*   **变更性质：** 变更以**增量和替换**为主。**[新增模块数]** 个是新增模块，同时对核心逻辑 (`Assembly-CSharp.dll`) 进行了精准的修改，以集成新功能和优化现有系统。
*   **核心稳定性：** **超过 [计算出的未变更百分比]% 的现有代码模块保持不变**，表明游戏的核心玩法和基础架构非常稳固，本次更新引入新问题的风险较低。

**结论：**

它精准地移除了过时方案、引入了现代化功能、优化了关键服务的稳定性，并在不影响大局的前提下，为产品的市场表现和长期运营能力注入了新的活力。
```

### 注意点

- 输出模板只是参考格式，但具体的内容要根据实际情况调整
- 输出不包括代码块
"""


def write_html_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html_report(report, output_path.with_suffix(".json"), generate_ai_analysis(report)), encoding="utf-8")


def render_html_report(report: dict[str, Any], json_report_path: Path, ai_analysis: str | None = None) -> str:
    stats = report["overall_statistics"]
    summary = report["summary"]

    total_dlls = stats["total_dlls"]
    added_dll_count = stats["added_dll_count"]
    removed_dll_count = stats["removed_dll_count"]
    changed_dll_count = stats["changed_dll_count"]
    unchanged_dll_count = stats["unchanged_dll_count"]
    total_affected_dlls = stats["total_affected_dlls"]
    affected_percentage = stats["affected_percentage"]
    modified_existing_dlls = changed_dll_count

    added_percentage = round((added_dll_count / total_dlls * 100) if total_dlls > 0 else 0, 2)
    removed_percentage = round((removed_dll_count / total_dlls * 100) if total_dlls > 0 else 0, 2)
    modified_percentage = round((changed_dll_count / total_dlls * 100) if total_dlls > 0 else 0, 2)
    unchanged_percentage = round((unchanged_dll_count / total_dlls * 100) if total_dlls > 0 else 0, 2)

    content_change_count = stats.get("content_change_count", 0)
    version_only_count = stats.get("version_only_change_count", 0)

    if changed_dll_count > 0:
        content_change_ratio = round((content_change_count / changed_dll_count * 100), 2)
        version_only_ratio = round((version_only_count / changed_dll_count * 100), 2)
    else:
        content_change_ratio = 0
        version_only_ratio = 0

    app_name = report.get("app_name")
    old_version_name = report.get("old_version_name")
    new_version_name = report.get("new_version_name")

    if app_name:
        title = f"{app_name} - Unity DummyDll 对比报告"
        header_title = f"{app_name} DummyDll 对比报告"
    else:
        title = "Unity DummyDll 完整对比报告"
        header_title = "Unity DummyDll 完整对比报告"

    if old_version_name and new_version_name:
        version_comparison = f"{old_version_name} → {new_version_name}"
    else:
        version_comparison = f"{Path(report['old_directory']).name} → {Path(report['new_directory']).name}"

    return HTML_TEMPLATE.format(
        title=title,
        header_title=header_title,
        timestamp=report["timestamp"],
        version_comparison=version_comparison,
        total_dlls=total_dlls,
        total_affected_dlls=total_affected_dlls,
        affected_percentage=affected_percentage,
        modified_existing_dlls=modified_existing_dlls,
        changed_dll_count=changed_dll_count,
        unchanged_dll_count=unchanged_dll_count,
        added_dll_count=added_dll_count,
        removed_dll_count=removed_dll_count,
        added_percentage=added_percentage,
        removed_percentage=removed_percentage,
        modified_percentage=modified_percentage,
        unchanged_percentage=unchanged_percentage,
        content_change_count=content_change_count,
        version_only_count=version_only_count,
        change_type_section=_change_type_section(
            changed_dll_count,
            content_change_count,
            version_only_count,
            content_change_ratio,
            version_only_ratio,
        ),
        game_logic_section=_game_logic_section(report, stats),
        dll_summary_rows=_dll_summary_rows(report["dll_comparisons"]),
        added_dlls_section=_dll_list_section("新增的 DLL", summary["added_dlls"]),
        removed_dlls_section=_dll_list_section("删除的 DLL", summary["removed_dlls"]),
        detailed_comparisons=_detailed_comparisons(report["dll_comparisons"], json_report_path),
        ai_analysis_section=_ai_analysis_section(ai_analysis),
    )


def generate_ai_analysis(report: dict[str, Any]) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": os.environ.get("OPENAI_MODEL", AI_MODEL),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(_to_ai_summary(report), ensure_ascii=False, indent=2)},
        ],
    }
    for attempt in range(len(AI_RETRY_DELAYS) + 1):
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
            if not _is_retryable_ai_status(response.status_code) or attempt == len(AI_RETRY_DELAYS):
                print(f"AI API 请求失败：{response.status_code} - {response.text}")
                return None
        except httpx.HTTPError as exc:
            if attempt == len(AI_RETRY_DELAYS):
                print(f"AI 分析时出错：{exc}")
                return None
        except Exception as exc:
            print(f"AI 分析时出错：{exc}")
            return None
        time.sleep(AI_RETRY_DELAYS[attempt])
    return None


def _is_retryable_ai_status(status_code: int) -> bool:
    return status_code == 408 or status_code == 429 or 500 <= status_code < 600


def _to_ai_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "app_name": report["app_name"],
        "old_version_name": report["old_version_name"],
        "new_version_name": report["new_version_name"],
        "overall_statistics": report["overall_statistics"],
        "summary": {
            "added_dlls": report["summary"]["added_dlls"],
            "removed_dlls": report["summary"]["removed_dlls"],
            "version_only_changes": report["summary"]["version_only_changes"],
            "content_changes": report["summary"]["content_changes"],
        },
        "dll_comparisons": [_simplify_comparison(item) for item in report.get("dll_comparisons", []) if not _can_ignore_comparison(item)],
    }


def _can_ignore_comparison(comparison: dict[str, Any]) -> bool:
    if comparison["comparison_type"] == "version":
        return comparison["has_changes"] is False
    if comparison["comparison_type"] in {"detailed", "detailed_no_version"}:
        summary = comparison["changes_summary"]
        return not any(
            summary[key]
            for key in ("added_classes", "removed_classes", "modified_classes", "sdk_version_changes")
        )
    return False


def _simplify_comparison(comparison: dict[str, Any]) -> dict[str, Any]:
    if comparison["comparison_type"] not in {"detailed", "detailed_no_version"}:
        return comparison
    return {
        "dll_name": comparison["dll_name"],
        "comparison_type": comparison["comparison_type"],
        "changes_summary": comparison["changes_summary"],
        "added_classes": [_simplify_added_class(item) for item in comparison["added_classes"]],
        "removed_classes": [_simplify_removed_class(item) for item in comparison["removed_classes"]],
        "modified_classes": [
            {"name": item["name"], "changes": item["changes"]}
            for item in comparison["modified_classes"]
        ],
    }


def _simplify_added_class(item: dict[str, Any]) -> dict[str, Any]:
    details = item["details"]
    return {
        "Namespace": details["Namespace"],
        "Name": details["Name"],
        "Methods": details["Methods"],
        "Fields": details["Fields"],
        "Properties": details["Properties"],
        "Attributes": details["Attributes"],
    }


def _simplify_removed_class(item: dict[str, Any]) -> dict[str, Any]:
    details = item["details"]
    return {
        "Namespace": details["Namespace"],
        "Name": details["Name"],
        "Methods": details["Methods"],
    }


def _change_type_section(
    changed_dll_count: int,
    content_change_count: int,
    version_only_count: int,
    content_change_ratio: float,
    version_only_ratio: float,
) -> str:
    if changed_dll_count <= 0:
        return ""
    return f"""
                   <h3>变更类型分析（仅修改的 DLL）</h3>
                   <div class="progress-bar">
                       <div class="progress-segment" style="background-color: #e74c3c; width: {content_change_ratio}%;">
                           内容：{content_change_count} ({content_change_ratio}%)
                       </div>
                       <div class="progress-segment" style="background-color: #3498db; width: {version_only_ratio}%;">
                           仅版本：{version_only_count} ({version_only_ratio}%)
                       </div>
                   </div>
                   <div class="legend">
                       <span class="legend-item"><span class="legend-color" style="background-color: #e74c3c;"></span>内容变更 ({content_change_count} 个 DLL)</span>
                       <span class="legend-item"><span class="legend-color" style="background-color: #3498db;"></span>仅版本变更 ({version_only_count} 个 DLL)</span>
                   </div>
               """


def _game_logic_section(report: dict[str, Any], stats: dict[str, Any]) -> str:
    if not report.get("detailed_game_logic_changes") or stats.get("game_logic_change_ratio", 0) <= 0:
        return ""
    game_logic_ratio = stats["game_logic_change_ratio"]
    sdk_ratio = stats["sdk_change_ratio"]
    return f"""
               <div class="section">
                   <h2>游戏逻辑分析 (Assembly-CSharp.dll)</h2>
                   <div class="warning-box">
                       <strong>核心游戏逻辑：</strong>以下分析基于 Assembly-CSharp.dll，
                       该文件包含了主要的游戏逻辑实现。
                   </div>
                   <div class="progress-bar">
                       <div class="progress-segment" style="background-color: #9c27b0; width: {game_logic_ratio}%;">
                           游戏逻辑：{game_logic_ratio}%
                       </div>
                       <div class="progress-segment" style="background-color: #2196f3; width: {sdk_ratio}%;">
                           SDK：{sdk_ratio}%
                       </div>
                   </div>
                   <p><strong>更新类型：</strong>{_get_update_type_from_ratio_cn(game_logic_ratio, sdk_ratio)}</p>
                   <p><strong>分析：</strong>基于 Assembly-CSharp.dll 中的变更，这次更新主要是一次{_get_update_description_cn(game_logic_ratio, sdk_ratio)}</p>
               </div>
            """


def _dll_summary_rows(comparisons: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    max_rows = 100
    for comp in comparisons[:max_rows]:
        dll_name = _escape_html(comp.get("dll_name", ""))
        status = comp.get("status", "changed")

        if status == "added":
            status_class = "added"
            status_display = "新增"
            change_type = "新 DLL"
            details = "新添加到项目中"
        elif status == "removed":
            status_class = "removed"
            status_display = "删除"
            change_type = "已删除 DLL"
            details = "从项目中移除"
        else:
            if comp.get("comparison_type") == "version":
                status_class = "version-change"
                status_display = "修改"
                change_type = "版本变更"
                details = comp.get("change_summary", "版本已更新").replace("Version:", "版本：")
            else:
                status_class = "content-change"
                status_display = "修改"
                change_type = "内容变更"
                details = _content_details(comp)

        rows.append(
            f"""
                    <tr class="{status_class}">
                        <td><strong>{dll_name}</strong></td>
                        <td>{status_display}</td>
                        <td>{change_type}</td>
                        <td>{_escape_html(details)}</td>
                    </tr>
                    """
        )

    if len(comparisons) > max_rows:
        rows.append(
            f"""
                   <tr>
                       <td colspan="4" style="text-align: center; font-style: italic;">
                           ... 还有 {len(comparisons) - max_rows} 个 DLL
                       </td>
                   </tr>
                   """
        )
    return "".join(rows)


def _content_details(comp: dict[str, Any]) -> str:
    summary = comp.get("changes_summary")
    if not summary:
        return "内容已修改"
    parts = []
    if summary.get("added_classes", 0) > 0:
        parts.append(f"+{summary['added_classes']} 个类")
    if summary.get("removed_classes", 0) > 0:
        parts.append(f"-{summary['removed_classes']} 个类")
    if summary.get("modified_classes", 0) > 0:
        parts.append(f"±{summary['modified_classes']} 个类")
    return "，".join(parts) if parts else "内容已修改"


def _dll_list_section(title: str, dlls: list[str]) -> str:
    if not dlls:
        return ""
    items = "".join(f"<li>{_escape_html(dll)}</li>" for dll in sorted(dlls)[:10])
    if len(dlls) > 10:
        items += f"<li><em>... 还有 {len(dlls) - 10} 个</em></li>"
    return f"""
                   <div>
                       <h3>{title} ({len(dlls)} 个)</h3>
                       <ul>{items}</ul>
                   </div>
               """


def _detailed_comparisons(comparisons: list[dict[str, Any]], json_report_path: Path) -> str:
    detailed = [comp for comp in comparisons if comp.get("comparison_type") in ["detailed", "detailed_no_version"]]
    if not detailed:
        return "<p>没有详细对比信息。所有变更都是仅版本变更或新增/删除。</p>"

    json_report_url = _json_report_url(json_report_path)
    blocks = []
    for comp in detailed[:20]:
        dll_name = comp.get("dll_name", "")
        blocks.append(
            f"""
                       <button class="accordion">{dll_name} - 详细分析</button>
                       <div class="panel">
                           {_format_detailed_comparison_safe(comp, json_report_url)}
                       </div>
                   """
        )

    if len(detailed) > 20:
        blocks.append(
            f"""
                       <p style="text-align: center; font-style: italic; margin-top: 20px;">
                           显示前 20 个详细对比。
                           完整详情请查看 
                           <a href="{json_report_url}" target="_blank">JSON 报告</a>。
                       </p>
                   """
        )
    return "".join(blocks)


def _format_detailed_comparison_safe(comp: dict[str, Any], json_report_url: str) -> str:
    html = "<div class='dll-comparison'>"
    try:
        if "changes_summary" in comp:
            summary = comp["changes_summary"]
            html += f"""
                <h4>摘要</h4>
                <ul>
                    <li>新增类：{summary.get('added_classes', 0)}</li>
                    <li>删除类：{summary.get('removed_classes', 0)}</li>
                    <li>修改类：{summary.get('modified_classes', 0)}</li>
                    <li>SDK 版本变化：{summary.get('sdk_version_changes', 0)}</li>
                </ul>
                """

        if "statistics" in comp:
            html += "<h4>分类统计</h4><ul>"
            for category, stats in comp["statistics"].items():
                total = stats["added"] + stats["removed"] + stats["modified"]
                if total > 0:
                    category_cn = _translate_category(category)
                    html += f"<li>{category_cn}：+{stats['added']} -{stats['removed']} ±{stats['modified']}</li>"
            html += "</ul>"

        if comp.get("sdk_version_changes"):
            html += "<h4>SDK 版本变化</h4><ul>"
            version_changes = list(comp["sdk_version_changes"].items())[:10]
            for key, versions in version_changes:
                html += f"<li>{_escape_html(key)}：{_escape_html(str(versions['old']))} → {_escape_html(str(versions['new']))}</li>"
            if len(comp["sdk_version_changes"]) > 10:
                html += f"<li><em>... 还有 {len(comp['sdk_version_changes']) - 10} 个版本变化</em></li>"
            html += "</ul>"

        for change_type, change_list_key, change_type_cn in [
            ("Added", "added_classes", "新增"),
            ("Removed", "removed_classes", "删除"),
            ("Modified", "modified_classes", "修改"),
        ]:
            values = comp.get(change_list_key) or []
            if values:
                html += f"<h4>{change_type_cn}类示例（显示前 5 个，共 {len(values)} 个）</h4><ul>"
                for cls in values[:5]:
                    class_name = cls["name"] if isinstance(cls, dict) else str(cls)
                    category = cls.get("category", "unknown") if isinstance(cls, dict) else "unknown"
                    category_cn = _translate_category(category)
                    html += f"<li class='{change_type.lower()}'>{_escape_html(class_name)} ({category_cn})</li>"
                if len(values) > 5:
                    html += f"<li><em>... 还有 {len(values) - 5} 个</em></li>"
                html += "</ul>"

        html += f"""
            <details>
                <summary>导出选项</summary>
                <p>完整详情请查看 <a href="{json_report_url}" target="_blank">JSON 报告</a>。</p>
            </details>
            """
    except Exception as exc:
        html += f"<p class='error-msg'>格式化详情时出错：{str(exc)}</p>"
    html += "</div>"
    return html


def _json_report_url(json_report_path: Path) -> str:
    frontend_port = os.environ.get("FRONT_PORT", "3000")
    server_host = os.environ.get("SERVER_HOST", "localhost")
    repo_root = Path(__file__).resolve().parents[2]
    try:
        relative_path = json_report_path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        relative_path = json_report_path.as_posix()
    return f"http://{server_host}:{frontend_port}/api/reports/{relative_path}"


def _ai_analysis_section(ai_analysis: str | None) -> str:
    if ai_analysis:
        return f"""
            <div class="section ai-analysis">
                <div id="ai-analysis-content" data-markdown="{_escape_html(ai_analysis)}">
                    <div class="ai-loading">正在渲染分析内容...</div>
                </div>
            </div>
            """
    if os.environ.get("OPENAI_API_KEY"):
        return """
            <div class="section ai-analysis">
                <h2>AI 智能分析</h2>
                <div class="ai-error">AI 分析生成失败。请检查网络连接或 API 配置。</div>
            </div>
            """
    return """
            <div class="section ai-analysis">
                <h2>AI 智能分析</h2>
                <div class="ai-error">
                    未配置 OPENAI_API_KEY 环境变量，无法生成 AI 分析。
                    <br>请设置环境变量后重新生成报告。
                </div>
            </div>
            """


def _escape_html(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _translate_category(category: str) -> str:
    translations = {
        "game_logic": "游戏逻辑",
        "unity_engine": "Unity 引擎",
        "system": "系统",
        "sdk_Unity": "Unity SDK",
        "sdk_Firebase": "Firebase SDK",
        "sdk_Facebook": "Facebook SDK",
        "sdk_AdMob": "AdMob SDK",
        "sdk_AppsFlyer": "AppsFlyer SDK",
        "sdk_Adjust": "Adjust SDK",
        "sdk_IronSource": "IronSource SDK",
        "sdk_Bugly": "Bugly SDK",
        "sdk_TalkingData": "TalkingData SDK",
        "sdk_UMeng": "友盟 SDK",
        "unknown": "未知",
    }
    return translations.get(category, category)


def _get_update_type_from_ratio_cn(game_logic_ratio: float, sdk_ratio: float) -> str:
    if sdk_ratio > 70:
        return "SDK 更新"
    if game_logic_ratio > 70:
        return "游戏逻辑更新"
    return "混合更新"


def _get_update_description_cn(game_logic_ratio: float, sdk_ratio: float) -> str:
    if sdk_ratio > 70:
        return "SDK 更新，主要集中在第三方库的更新或集成"
    if game_logic_ratio > 70:
        return "游戏逻辑更新，游戏玩法机制或功能有重大变化"
    return "混合更新，包含游戏逻辑变更和 SDK 修改"


HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>{title}</title>
        <script src="https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/dist/markdown-it.min.js"></script>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: 0 auto; background-color: white; padding: 20px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
            .header {{ background-color: #2c3e50; color: white; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
            .section {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; background-color: #fafafa; }}
            .ai-analysis {{ background-color: #f0f8ff; border: 2px solid #3498db; border-radius: 8px; padding: 20px; margin: 20px 0; }}
            .ai-analysis h3 {{ color: #2c3e50; margin-top: 0; }}
            .ai-analysis h4 {{ color: #34495e; margin-top: 15px; }}
            .ai-analysis ul {{ margin: 10px 0; padding-left: 25px; }}
            .ai-analysis li {{ margin: 5px 0; }}
            .ai-analysis strong {{ color: #2c3e50; }}
            .ai-analysis em {{ font-style: italic; color: #555; }}
            .ai-loading {{ text-align: center; padding: 40px; color: #999; }}
            .ai-error {{ background-color: #fee; border: 1px solid #fcc; border-radius: 4px; padding: 10px; color: #c33; }}
            .statistics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
            .stat-card {{ background-color: white; padding: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center; }}
            .stat-number {{ font-size: 2em; font-weight: bold; color: #2c3e50; }}
            .stat-label {{ color: #7f8c8d; margin-top: 5px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 10px 0; background-color: white; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #34495e; color: white; }}
            .added {{ background-color: #d4edda; color: #155724; }}
            .removed {{ background-color: #f8d7da; color: #721c24; }}
            .modified {{ background-color: #fff3cd; color: #856404; }}
            .unchanged {{ background-color: #e7e7e7; color: #666; }}
            .version-change {{ background-color: #d1ecf1; color: #0c5460; }}
            .content-change {{ background-color: #f8d7da; color: #721c24; }}
            .progress-bar {{ width: 100%; height: 30px; background-color: #e0e0e0; border-radius: 15px; overflow: hidden; margin: 10px 0; }}
            .progress-segment {{ float: left; height: 100%; text-align: center; line-height: 30px; color: white; font-weight: bold; font-size: 12px; }}
            .accordion {{ cursor: pointer; padding: 15px; background-color: #34495e; color: white; border: none; width: 100%; text-align: left; margin-top: 10px; transition: 0.4s; }}
            .accordion:hover {{ background-color: #2c3e50; }}
            .accordion:after {{ content: '\\002B'; color: white; font-weight: bold; float: right; margin-left: 5px; }}
            .accordion.active:after {{ content: "\\2212"; }}
            .panel {{ max-height: 0; overflow: hidden; transition: max-height 0.2s ease-out; background-color: white; }}
            .panel.show {{ max-height: 500px; overflow-y: auto; padding: 15px; }}
            .dll-comparison {{ margin: 10px 0; padding: 10px; border-left: 4px solid #3498db; background-color: #f9f9f9; }}
            .chart-container {{ width: 100%; height: 400px; margin: 20px 0; }}
            .legend {{ margin: 10px 0; font-size: 14px; }}
            .legend-item {{ display: inline-block; margin-right: 20px; }}
            .legend-color {{ display: inline-block; width: 20px; height: 10px; margin-right: 5px; vertical-align: middle; }}
            .info-box {{ background-color: #e8f4f8; border-left: 4px solid #3498db; padding: 10px; margin: 10px 0; }}
            .warning-box {{ background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0; }}
            pre {{ background-color: #f4f4f4; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 12px; }}
            .code-container {{ max-height: 300px; overflow-y: auto; background-color: #f4f4f4; padding: 10px; border-radius: 4px; border: 1px solid #ddd; }}
            .tooltip {{ position: relative; display: inline-block; cursor: help; }}
            .tooltip .tooltiptext {{ visibility: hidden; width: 200px; background-color: #555; color: #fff; text-align: center; border-radius: 6px; padding: 5px; position: absolute; z-index: 1; bottom: 125%; left: 50%; margin-left: -100px; opacity: 0; transition: opacity 0.3s; }}
            .tooltip:hover .tooltiptext {{ visibility: visible; opacity: 1; }}
            h3 {{ color: #2c3e50; margin-top: 20px; }}
            .summary-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
            .error-msg {{ color: #e74c3c; font-style: italic; }}
            details {{ margin: 10px 0; }}
            summary {{ cursor: pointer; font-weight: bold; color: #2c3e50; }}
            @media (max-width: 768px) {{
                .summary-grid {{ grid-template-columns: 1fr; }}
                .statistics-grid {{ grid-template-columns: 1fr 1fr; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>{header_title}</h1>
                <p>生成时间：{timestamp} (UTC)</p>
                <p>版本对比：{version_comparison}</p>
                <p style="color: #ff4444; font-weight: bold; margin-top: 10px;">
                    <span style="font-size: 1.2em;">⚠️</span> 仅限内部参考，请勿外传
                </p>
            </div>

            {ai_analysis_section}

            <div class="section">
                <h2>整体统计</h2>
                <div class="statistics-grid">
                    <div class="stat-card">
                        <div class="stat-number">{total_dlls}</div>
                        <div class="stat-label">DLL 总数</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{total_affected_dlls}</div>
                        <div class="stat-label">受影响的 DLL</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{affected_percentage}%</div>
                        <div class="stat-label">影响比例</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{modified_existing_dlls}</div>
                        <div class="stat-label">修改的现有 DLL</div>
                    </div>
                </div>

                <h3>DLL 状态概览</h3>
                <div class="progress-bar">
                    <div class="progress-segment" style="background-color: #27ae60; width: {added_percentage}%;" title="新增的 DLL">
                        +{added_dll_count}
                    </div>
                    <div class="progress-segment" style="background-color: #e74c3c; width: {removed_percentage}%;" title="删除的 DLL">
                        -{removed_dll_count}
                    </div>
                    <div class="progress-segment" style="background-color: #f39c12; width: {modified_percentage}%;" title="修改的 DLL">
                        ≠{changed_dll_count}
                    </div>
                    <div class="progress-segment" style="background-color: #95a5a6; width: {unchanged_percentage}%;" title="未变化的 DLL">
                        ={unchanged_dll_count}
                    </div>
                </div>
                <div class="legend">
                    <span class="legend-item"><span class="legend-color" style="background-color: #27ae60;"></span>新增 ({added_dll_count})</span>
                    <span class="legend-item"><span class="legend-color" style="background-color: #e74c3c;"></span>删除 ({removed_dll_count})</span>
                    <span class="legend-item"><span class="legend-color" style="background-color: #f39c12;"></span>修改 ({changed_dll_count})</span>
                    <span class="legend-item"><span class="legend-color" style="background-color: #95a5a6;"></span>未变化 ({unchanged_dll_count})</span>
                </div>

                {change_type_section}
            </div>

            {game_logic_section}

            <div class="section">
                <h2>DLL 变更详情</h2>
                <div class="info-box">
                    <strong>说明：</strong>内容变更表示结构性修改（方法、字段、属性），
                    而版本变更仅表示程序集版本更新。
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>DLL 名称</th>
                            <th>状态</th>
                            <th>变更类型</th>
                            <th>详情</th>
                        </tr>
                    </thead>
                    <tbody>
                        {dll_summary_rows}
                    </tbody>
                </table>
            </div>

            <div class="section">
                <h2>变更汇总</h2>
                <div class="summary-grid">
                    {added_dlls_section}
                    {removed_dlls_section}
                </div>
            </div>

            <div class="section">
                <h2>详细对比</h2>
                <p>点击 DLL 名称查看详细变更。</p>
                {detailed_comparisons}
            </div>

            <div class="section">
                <h2>分析图表</h2>
                <div id="pieChart" class="chart-container"></div>
                <div id="changeTypeChart" class="chart-container"></div>
            </div>
        </div>

        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
        <script>
            // Initialize markdown-it
            var md = window.markdownit({{
                html: true,
                linkify: true,
                typographer: true
            }});

            // Render AI analysis if present
            document.addEventListener('DOMContentLoaded', function() {{
                var aiAnalysisElement = document.getElementById('ai-analysis-content');
                if (aiAnalysisElement && aiAnalysisElement.dataset.markdown) {{
                    var markdownContent = aiAnalysisElement.dataset.markdown;
                    var htmlContent = md.render(markdownContent);
                    aiAnalysisElement.innerHTML = htmlContent;
                }}

                // Accordion functionality with better error handling
                var accordions = document.getElementsByClassName("accordion");

                for (var i = 0; i < accordions.length; i++) {{
                    accordions[i].addEventListener("click", function() {{
                        try {{
                            this.classList.toggle("active");
                            var panel = this.nextElementSibling;

                            if (panel.style.maxHeight) {{
                                panel.style.maxHeight = null;
                                panel.classList.remove("show");
                            }} else {{
                                panel.style.maxHeight = "500px";
                                panel.classList.add("show");
                            }}
                        }} catch (e) {{
                            console.error("Accordion error:", e);
                        }}
                    }});
                }}

                // Initialize charts
                try {{
                    initializeCharts();
                }} catch (e) {{
                    console.error("Chart initialization error:", e);
                    document.getElementById('pieChart').innerHTML = '<p class="error-msg">加载图表时出错</p>';
                    document.getElementById('changeTypeChart').innerHTML = '<p class="error-msg">加载图表时出错</p>';
                }}
            }});

            function initializeCharts() {{
                // DLL Status Distribution Pie Chart
                var pieData = [{{
                    values: [{changed_dll_count}, {unchanged_dll_count}, {added_dll_count}, {removed_dll_count}],
                    labels: ['修改', '未变化', '新增', '删除'],
                    type: 'pie',
                    marker: {{
                        colors: ['#f39c12', '#95a5a6', '#27ae60', '#e74c3c']
                    }},
                    textinfo: 'label+percent',
                    textposition: 'outside',
                    hovertemplate: '%{{label}}: %{{value}} 个 DLL<br>%{{percent}}<extra></extra>'
                }}];

                var pieLayout = {{
                    title: {{
                        text: 'DLL 状态分布',
                        font: {{ size: 18 }}
                    }},
                    height: 400,
                    showlegend: true,
                    legend: {{
                        orientation: "v",
                        yanchor: "middle",
                        y: 0.5,
                        xanchor: "left",
                        x: 1.05
                    }}
                }};

                Plotly.newPlot('pieChart', pieData, pieLayout, {{responsive: true}});

                // Change Type Chart (only if there are modified DLLs)
                if ({changed_dll_count} > 0) {{
                    var changeTypeData = [{{
                        x: ['内容变更', '仅版本变更'],
                        y: [{content_change_count}, {version_only_count}],
                        type: 'bar',
                        marker: {{
                            color: ['#e74c3c', '#3498db']
                        }},
                        text: ['{content_change_count} 个 DLL', '{version_only_count} 个 DLL'],
                        textposition: 'auto',
                        hovertemplate: '%{{x}}: %{{y}} 个 DLL<extra></extra>'
                    }}];

                    var changeTypeLayout = {{
                        title: {{
                            text: '修改的 DLL 分类',
                            font: {{ size: 18 }}
                        }},
                        height: 400,
                        yaxis: {{
                            title: 'DLL 数量'
                        }},
                        showlegend: false
                    }};

                    Plotly.newPlot('changeTypeChart', changeTypeData, changeTypeLayout, {{responsive: true}});
                }} else {{
                    document.getElementById('changeTypeChart').innerHTML = '<p style="text-align: center; padding: 50px;">没有修改的 DLL 需要分析</p>';
                }}
            }}
        </script>
    </body>
    </html>
        """
