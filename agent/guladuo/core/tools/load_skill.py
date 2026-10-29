"""loadSkill 工具：按需加载技能正文与技能目录内的附加文件。

两种用法：
- loadSkill(name="notice")                → 返回 SKILL.md 全文
- loadSkill(name="notice", filename="scripts/x.py") → 只返回该附加文件

filename 的解析顺序（先精确、后模糊）：
1. 绝对路径；
2. 相对 Skill 目录（scripts/x.py）；
3. 相对项目根（.agents/skills/notice/scripts/x.py）；
4. 都不是现存文件时，按「文件名/路径后缀」在该 Skill 目录内递归搜索：
   唯一命中直接读；多个同名返回候选路径让 LLM 自己选。

安全：所有读取都必须落在该 Skill 目录内部（resolve 后做 relative_to 校验），
任何越界（../、绝对路径指向外部、符号链接逃逸）一律拒绝。
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ToolError
from ..skills import SKILL_FILE, Skill, SkillRegistry
from .base import Tool

MAX_FILE_CHARS = 200_000     # 单个文件返回上限，防止异常大文件撑爆上下文
MAX_LISTED_FILES = 30


def build_load_skill_tool(registry: SkillRegistry) -> Tool:
    """构造 loadSkill 工具。registry 为启动时发现的技能集合。"""

    def load_skill(name: str, filename: str = "") -> str:
        skill_name = (name or "").strip()
        if not skill_name:
            raise ToolError(
                "name 不能为空。当前可用技能：%s" % (", ".join(registry.names) or "（无）")
            )

        skill = registry.get(skill_name)
        if skill is None:
            raise ToolError(
                "找不到技能 %r。当前可用技能：%s"
                % (skill_name, ", ".join(registry.names) or "（无）")
            )

        target = (filename or "").strip().strip('"').strip("'")
        if not target:
            return _read_skill_md(skill)
        return _load_attachment(skill, registry.root, target)

    return Tool(
        name="loadSkill",
        description=(
            "加载技能的完整说明或技能附带的文件。"
            "只传 name 时返回该技能 SKILL.md 的全文；"
            "同时传 filename 时只返回该文件（不再重复返回 SKILL.md）。"
            "filename 支持相对技能目录的路径、项目相对路径或绝对路径；"
            "只写文件名时会在技能目录内递归查找，若有多个同名文件会返回候选路径。"
            "同一段对话中同一技能加载一次即可，内容会保留在上下文里；"
            "返回的技能目录绝对路径可直接用于 runcli 执行技能自带脚本。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "技能名称，必须是系统提示中列出的技能之一",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "可选。要读取的附加文件，如 scripts/run.py、references/guide.md；"
                        "不传则返回 SKILL.md 全文"
                    ),
                },
            },
            "required": ["name"],
        },
        handler=load_skill,
    )


def _read_skill_md(skill: Skill) -> str:
    """返回 SKILL.md 全文（按规范就是整个文件，含 frontmatter）。"""
    content = _read_text(skill.path, skill.directory, SKILL_FILE)
    header = "技能: %s\n目录: %s\n\n" % (skill.name, skill.directory)
    return header + content


def _load_attachment(skill: Skill, project_root: Path, filename: str) -> str:
    exact = _resolve_exact(skill, project_root, filename)
    if exact is not None:
        return _render_file(skill, exact)

    matches = _search_by_name(skill.directory, filename)
    if len(matches) == 1:
        return _render_file(skill, matches[0])

    if len(matches) > 1:
        lines = [
            "技能 %s 内有 %d 个文件匹配 %r，请用更精确的 filename 重新调用："
            % (skill.name, len(matches), filename),
            "",
        ]
        for path in matches[:MAX_LISTED_FILES]:
            lines.append("- %s" % path.relative_to(skill.directory).as_posix())
        return "\n".join(lines)

    available = _list_files(skill.directory)
    raise ToolError(
        "技能 %s 内找不到文件 %r。可用文件：\n%s"
        % (skill.name, filename, available or "（该技能没有附加文件）")
    )


def _resolve_exact(skill: Skill, project_root: Path, filename: str) -> Path | None:
    """按 绝对路径 → 技能目录相对 → 项目根相对 顺序做精确匹配。

    任一候选命中但落在技能目录外时直接报错，不再继续找，
    避免"越界当成没找到"后又被后续搜索绕回去。
    """
    raw = Path(filename)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(skill.directory / raw)
        candidates.append(project_root / raw)

    for candidate in candidates:
        if not candidate.exists():
            continue
        if not candidate.is_file():
            raise ToolError("%r 存在但不是文件：%s" % (filename, candidate))
        if not _inside(candidate, skill.directory):
            raise ToolError(
                "拒绝读取技能目录之外的文件：%s（只允许读取 %s 内部的文件）"
                % (candidate, skill.directory)
            )
        return candidate
    return None


def _search_by_name(skill_dir: Path, filename: str) -> list[Path]:
    """在技能目录内递归查找：先按完整相对路径后缀，再按文件名。"""
    normalized = filename.replace("\\", "/").strip("/")
    name = Path(normalized).name
    if not name:
        return []

    by_suffix: list[Path] = []
    by_name: list[Path] = []
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel == normalized or rel.endswith("/" + normalized):
            by_suffix.append(path)
        elif path.name == name:
            by_name.append(path)
    return by_suffix or by_name


def _render_file(skill: Skill, path: Path) -> str:
    rel = path.relative_to(skill.directory).as_posix()
    content = _read_text(path, skill.directory, rel)
    return "技能: %s\n文件: %s\n绝对路径: %s\n\n%s" % (skill.name, rel, path, content)


def _read_text(path: Path, skill_dir: Path, label: str) -> str:
    resolved = path.resolve()
    if not _inside(resolved, skill_dir):
        raise ToolError("拒绝读取技能目录之外的文件：%s" % resolved)

    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise ToolError("读取 %s 失败：%s" % (label, exc)) from exc

    if b"\x00" in data[:2000]:
        raise ToolError("%s 是二进制文件，无法作为文本加载" % label)

    text = _decode(data)
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + "\n\n…（已截断：原文件 %d 字符）" % len(text)
    return text


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _list_files(skill_dir: Path) -> str:
    files = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file():
            files.append("- %s" % path.relative_to(skill_dir).as_posix())
        if len(files) >= MAX_LISTED_FILES:
            files.append("- …")
            break
    return "\n".join(files)
