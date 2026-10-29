"""Skill 发现与注册（遵循 Agent Skills 规范）。

目录约定：以程序启动时的工作目录为项目根，扫描 `.agents/skills/<skill>/SKILL.md`。
每个 SKILL.md 开头用 YAML frontmatter 声明 `name` 和 `description`（均为必填）。

初始化阶段**只解析 frontmatter**：逐行读到结束的 `---` 就停，
不读取正文，也不 import 技能里的任何代码，保证启动开销与上下文占用都很小。
正文的按需加载属于后续步骤，由专门的加载工具负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKILLS_DIR = Path(".agents") / "skills"
SKILL_FILE = "SKILL.md"

MAX_FRONTMATTER_BYTES = 8 * 1024   # frontmatter 读取上限，防止畸形文件
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024
# Agent Skills 规范：小写字母、数字、连字符
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Skill:
    """一条技能元数据：初始化时只保留 name / description 和路径。"""

    name: str
    description: str
    directory: Path
    path: Path


@dataclass(frozen=True)
class SkippedSkill:
    """被跳过的技能目录及原因，供前端提示或调试。"""

    directory: Path
    reason: str


@dataclass
class SkillRegistry:
    """已注册技能的只读集合。"""

    root: Path
    skills: tuple[Skill, ...] = ()
    skipped: tuple[SkippedSkill, ...] = ()
    _by_name: dict[str, Skill] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_name = {skill.name: skill for skill in self.skills}

    # ---------- 只读访问 ----------

    @property
    def names(self) -> list[str]:
        return [skill.name for skill in self.skills]

    def get(self, name: str) -> Skill | None:
        return self._by_name.get((name or "").strip())

    def __len__(self) -> int:
        return len(self.skills)

    def __iter__(self):
        return iter(self.skills)

    # ---------- 给模型/前端看的摘要（只有 name + description） ----------

    def summary(self) -> str:
        if not self.skills:
            return "当前项目没有可用技能（%s 为空或不存在）。" % self.root
        lines = ["可用技能 %d 个：" % len(self.skills)]
        for skill in self.skills:
            lines.append("- %s: %s" % (skill.name, skill.description))
        return "\n".join(lines)

    def to_prompt(self) -> str:
        """拼成注入 System Prompt 的技能清单块；没有技能时返回空串。

        只放 name + description，正文由 loadSkill 工具按需加载，
        避免把所有技能正文一次性塞进上下文。
        """
        if not self.skills:
            return ""
        lines = [PROMPT_HEADER, ""]
        for skill in self.skills:
            lines.append("- `%s`: %s" % (skill.name, skill.description))
        lines.append("")
        lines.append(PROMPT_HINT)
        return "\n".join(lines)


PROMPT_HEADER = "You have access to the following skills:"
PROMPT_HINT = (
    "Use the `loadSkill` tool to load the full instructions of a skill when needed. "
    "You can also load additional files referenced by a skill."
)


def build_system_prompt(base: str, registry: SkillRegistry) -> str:
    """把技能清单追加到用户配置的 system_prompt 之后。"""
    block = registry.to_prompt()
    if not block:
        return base.strip()
    return ("%s\n\n%s" % (base.strip(), block)).strip()


def discover_skills(project_root: str | Path) -> SkillRegistry:
    """扫描项目根下的 .agents/skills/，加载所有合法技能的元数据。

    目录不存在属于正常情况（项目没有技能），返回空注册表而不是报错。
    """
    root = Path(project_root).expanduser().resolve()
    skills_dir = root / SKILLS_DIR
    if not skills_dir.is_dir():
        return SkillRegistry(root=root)

    skipped: list[SkippedSkill] = []
    candidates: list[Skill] = []

    for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        skill_file = entry / SKILL_FILE
        if not skill_file.is_file():
            skipped.append(SkippedSkill(entry, "缺少 SKILL.md"))
            continue

        meta = read_frontmatter(skill_file)
        if meta is None:
            skipped.append(SkippedSkill(entry, "SKILL.md 开头没有合法的 YAML frontmatter"))
            continue

        name = (meta.get("name") or "").strip()
        description = (meta.get("description") or "").strip()

        if not name:
            skipped.append(SkippedSkill(entry, "frontmatter 缺少必填字段 name"))
            continue
        if not description:
            skipped.append(SkippedSkill(entry, "frontmatter 缺少必填字段 description"))
            continue
        if not NAME_PATTERN.match(name) or len(name) > MAX_NAME_CHARS:
            skipped.append(
                SkippedSkill(entry, "name 不符合规范（小写字母/数字/连字符，最长 %d 字符）：%r" % (MAX_NAME_CHARS, name))
            )
            continue
        if len(description) > MAX_DESCRIPTION_CHARS:
            skipped.append(
                SkippedSkill(entry, "description 超过 %d 字符" % MAX_DESCRIPTION_CHARS)
            )
            continue

        candidates.append(
            Skill(name=name, description=description, directory=entry, path=skill_file)
        )

    # 重名处理：优先保留「目录名与技能名一致」的那个，其次保留先出现的
    chosen: dict[str, Skill] = {}
    for skill in candidates:
        kept = chosen.get(skill.name)
        if kept is None:
            chosen[skill.name] = skill
            continue
        if skill.directory.name == skill.name and kept.directory.name != kept.name:
            chosen[skill.name] = skill
            skipped.append(
                SkippedSkill(kept.directory, "技能名 %r 重复，已改用目录同名版本 %s" % (skill.name, skill.directory.name))
            )
        else:
            skipped.append(
                SkippedSkill(skill.directory, "技能名 %r 与 %s 重复，本目录被跳过" % (skill.name, kept.directory.name))
            )

    skills = sorted(chosen.values(), key=lambda s: s.name.lower())
    skipped.sort(key=lambda s: s.directory.name.lower())
    return SkillRegistry(root=root, skills=tuple(skills), skipped=tuple(skipped))


def read_frontmatter(path: Path) -> dict[str, str] | None:
    """只读 frontmatter 就返回，不加载正文。

    失败（文件打不开、没有以 --- 开头、没有结束标记）返回 None。
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
            if first.strip() != "---":
                return None

            meta: dict[str, str] = {}
            read_bytes = len(first.encode("utf-8", "replace"))
            for line in handle:
                read_bytes += len(line.encode("utf-8", "replace"))
                if read_bytes > MAX_FRONTMATTER_BYTES:
                    return None
                if line.strip() == "---":
                    return meta
                if ":" not in line:
                    continue
                # 只处理顶层 key: value，忽略缩进的嵌套结构
                if line[:1].isspace():
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                if not key or key.startswith("#"):
                    continue
                meta[key] = _unquote(value.strip())
            return None  # 没有结束的 ---，视为不合法
    except OSError:
        return None


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1]:
        if value[0] == "'":
            return value[1:-1].replace("''", "'")
        if value[0] == '"':
            return value[1:-1].replace('\\"', '"')
    return value
