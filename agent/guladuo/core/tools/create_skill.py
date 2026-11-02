"""createSkill 工具：让 Agent 按用户的自然语言要求创建新技能。

行为：
- 在 <项目根>/.agents/skills/<name>/SKILL.md 写入规范化后的内容；
- skillContext 可以带也可以不带 YAML frontmatter：
  - 带了：校验 description（缺了就从正文首行自动生成），name 强制与参数一致；
  - 没带：自动生成 frontmatter，description 取正文第一个标题/首行；
- 同名技能已存在时拒绝覆盖，明确提示；
- 创建成功后立刻注册进 SkillRegistry，本轮对话即可 loadSkill 加载。

附加文件（references/、scripts/ 等）不在本工具职责内，
由模型继续调用文件工具（createDirectory / createFile）完成。
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ToolError
from ..skills import (
    MAX_NAME_CHARS,
    NAME_PATTERN,
    SKILLS_DIR,
    SKILL_FILE,
    Skill,
    SkillRegistry,
)
from .base import Tool

MAX_SKILL_CHARS = 100_000      # skillContext 上限，防止异常大内容
MAX_DESC_CHARS = 1024          # 与 discovery 的校验保持一致


def build_create_skill_tool(registry: SkillRegistry) -> Tool:
    """构造 createSkill 工具。registry 为启动时发现的技能集合（会被原地扩充）。"""

    def create_skill(name: str, skillContext: str = "") -> str:
        skill_name = (name or "").strip()
        if not skill_name:
            raise ToolError(
                "name 不能为空。规范：小写字母、数字、连字符，如 daily-report"
            )
        if not NAME_PATTERN.match(skill_name) or len(skill_name) > MAX_NAME_CHARS:
            raise ToolError(
                "name %r 不符合规范：只能由小写字母、数字和连字符组成，最长 %d 字符"
                "（示例：code-review、daily-report）" % (skill_name, MAX_NAME_CHARS)
            )

        content = (skillContext or "").strip()
        if not content:
            raise ToolError(
                "skillContext 不能为空：请把 SKILL.md 的完整内容（正文，可含 frontmatter）传进来"
            )
        if len(content) > MAX_SKILL_CHARS:
            raise ToolError(
                "skillContext 过长（%d 字符，上限 %d），请精简内容"
                % (len(content), MAX_SKILL_CHARS)
            )

        skill_dir = registry.root / SKILLS_DIR / skill_name
        skill_file = skill_dir / SKILL_FILE
        if skill_file.exists():
            raise ToolError(
                "技能 %r 已存在（%s），为避免覆盖原有内容已停止创建。"
                "如确需更新，请先征得用户同意并删除旧文件后重试。"
                % (skill_name, skill_file)
            )

        text, notes = _normalize(skill_name, content)

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            raise ToolError("写入 %s 失败：%s" % (skill_file, exc)) from exc

        description = _description_of(text)
        registry.add(Skill(name=skill_name, description=description, directory=skill_dir, path=skill_file))

        lines = [
            "已创建技能 %s" % skill_name,
            "文件: %s" % skill_file,
            "",
            "frontmatter:",
            "---",
            "name: %s" % skill_name,
            'description: "%s"' % description,
            "---",
        ]
        if notes:
            lines.append("")
            lines.append("处理说明:")
            lines.extend("- %s" % note for note in notes)
        lines.append("")
        lines.append(
            "该技能已注册到当前会话，可直接用 loadSkill(name=\"%s\") 加载；重启后也会被自动发现。"
            % skill_name
        )
        lines.append(
            "如需 references/、scripts/ 等附加文件，请继续调用文件工具创建（技能目录: %s）。"
            % skill_dir
        )
        return "\n".join(lines)

    return Tool(
        name="createSkill",
        description=(
            "根据用户的自然语言要求创建一个新技能。"
            "在项目的 .agents/skills/<name>/SKILL.md 中生成规范文件："
            "name 必须是小写字母、数字、连字符；skillContext 传 SKILL.md 的完整内容，"
            "可以带也可以不带 YAML frontmatter（不带时自动生成，description 取正文第一行）。"
            "同名技能已存在时拒绝覆盖。"
            "创建成功后技能立即可用 loadSkill 加载；"
            "如需附加文件（references/、scripts/ 等），请继续用文件工具创建。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "技能名称，小写字母、数字、连字符（如 code-review）",
                },
                "skillContext": {
                    "type": "string",
                    "description": (
                        "SKILL.md 的完整内容：给模型看的规范、步骤、格式要求。"
                        "建议第一行用一个简短标题（会作为 description 的兜底来源）"
                    ),
                },
            },
            "required": ["name", "skillContext"],
        },
        handler=create_skill,
    )


def _normalize(skill_name: str, content: str) -> tuple[str, list[str]]:
    """把 skillContext 规范化成带合法 frontmatter 的 SKILL.md 内容。

    返回 (最终文本, 处理说明列表)。
    """
    notes: list[str] = []
    meta: dict[str, str] = {}
    body = content

    if content.startswith("---"):
        lines = content.splitlines()
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            notes.append("skillContext 里的 frontmatter 缺少结束的 ---，已整体视为正文")
        else:
            for line in lines[1:end_idx]:
                if ":" in line and not line[:1].isspace():
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip().strip('"').strip("'")
            body = "\n".join(lines[end_idx + 1:]).strip()

    description = meta.get("description", "").strip()
    if not description:
        description = _auto_description(body)
        if meta:
            notes.append("frontmatter 缺少 description，已根据正文自动生成")
        else:
            notes.append("skillContext 没有 frontmatter，已自动生成（description 取自正文首行）")

    if len(description) > MAX_DESC_CHARS:
        description = description[:MAX_DESC_CHARS]
        notes.append("description 超过 %d 字符，已截断" % MAX_DESC_CHARS)

    frontmatter = "---\nname: %s\ndescription: \"%s\"\n---\n\n" % (
        skill_name,
        description.replace("\\", "\\\\").replace('"', '\\"'),
    )
    return frontmatter + body + "\n", notes


def _auto_description(body: str) -> str:
    """从正文取第一个标题或首个非空行作为 description 兜底。"""
    for line in body.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("#"):
            text = text.lstrip("#").strip()
        if text:
            return text[:200]
    return "（待补充描述）"


def _description_of(text: str) -> str:
    """从规范化的 SKILL.md 文本里取回 description（用于注册元数据）。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "---" and i == 0:
            continue
        if line.strip().startswith("description:"):
            return line.split(":", 1)[1].strip().strip('"')
        if line.strip() == "---" and i > 0:
            break
    return ""
