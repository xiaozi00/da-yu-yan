"""文件类工具：全部限定在工作区内，越界一律拒绝。"""

from __future__ import annotations

import os
from pathlib import Path

from ..errors import PathOutsideWorkspace
from .base import Tool
from .sandbox import WorkspaceSandbox

TEXT_EXTS = {
    ".txt", ".md", ".py", ".json", ".ini", ".cfg", ".csv",
    ".bat", ".log", ".xml", ".html", ".css", ".js", ".yaml", ".yml",
    ".tcss", ".toml", ".sh", ".mermaid", ".mmd",
}

DENIED = "拒绝访问：路径超出工作区"


class FileTools:
    def __init__(self, sandbox: WorkspaceSandbox, max_read_chars: int = 20000) -> None:
        self._sandbox = sandbox
        self._max_read_chars = max_read_chars

    # ---------- 内部 ----------

    def _resolve(self, raw: str) -> Path:
        return self._sandbox.resolve(raw)

    # ---------- 各工具实现 ----------

    def list_files(self, directory: str = ".") -> str:
        try:
            full = self._resolve(directory or ".")
        except PathOutsideWorkspace:
            return DENIED
        if not full.is_dir():
            return "目录不存在：%s" % full
        names = sorted(os.listdir(full))
        if not names:
            return "目录为空：%s" % full
        return "\n".join(
            "[目录] %s" % name if (full / name).is_dir() else "[文件] %s" % name
            for name in names
        )

    def get_file_type(self, path: str) -> str:
        try:
            full = self._resolve(path)
        except PathOutsideWorkspace:
            return DENIED
        if full.is_dir():
            return "这是一个目录，不是文件"
        if not full.is_file():
            return "文件不存在：%s" % full
        ext = full.suffix.lower()
        return ext if ext else "无扩展名"

    def read_file(self, path: str) -> str:
        try:
            full = self._resolve(path)
        except PathOutsideWorkspace:
            return DENIED
        if not full.is_file():
            return "文件不存在：%s" % full
        ext = full.suffix.lower()
        if ext not in TEXT_EXTS:
            return "仅支持纯文本文件，不接受 %s 类型" % (ext or "无扩展名")
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read(self._max_read_chars + 1)
        except UnicodeDecodeError:
            return "文件内容不是 UTF-8 纯文本，拒绝读取"
        except OSError as exc:
            return "读取失败：%s" % exc
        if len(content) > self._max_read_chars:
            return content[: self._max_read_chars] + "\n...(内容过长，已截断)"
        return content or "(空文件)"

    def create_file(self, directory: str, filename: str, content: str = "") -> str:
        try:
            full = self._sandbox.resolve((Path(directory or ".") / filename))
        except PathOutsideWorkspace:
            return DENIED
        if full.exists():
            return "文件已存在，拒绝覆盖：%s（如需修改请用 editFile）" % full
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        except OSError as exc:
            return "创建失败：%s" % exc
        return "已创建：%s（%d 字符）" % (full, len(content))

    def edit_file(self, path: str, content: str) -> str:
        try:
            full = self._resolve(path)
        except PathOutsideWorkspace:
            return DENIED
        if not full.is_file():
            return "文件不存在：%s（新建文件请用 createFile）" % full
        ext = full.suffix.lower()
        if ext not in TEXT_EXTS:
            return "仅支持编辑纯文本文件，不接受 %s 类型" % (ext or "无扩展名")
        try:
            full.write_text(content, encoding="utf-8")
        except OSError as exc:
            return "编辑失败：%s" % exc
        return "已保存：%s（%d 字符）" % (full, len(content))

    def create_directory(self, path: str) -> str:
        try:
            full = self._resolve(path)
        except PathOutsideWorkspace:
            return DENIED
        if full.exists():
            return "目录已存在：%s" % full
        try:
            full.mkdir(parents=True)
        except OSError as exc:
            return "创建失败：%s" % exc
        return "已创建目录：%s" % full

    def delete_directory(self, path: str) -> str:
        try:
            full = self._resolve(path)
            self._sandbox.ensure_not_root(full)
        except PathOutsideWorkspace as exc:
            return "拒绝删除：%s" % exc
        if not full.is_dir():
            return "目录不存在：%s" % full
        remaining = os.listdir(full)
        if remaining:
            return "目录非空（含 %d 个文件/子目录），拒绝删除：%s" % (len(remaining), full)
        try:
            full.rmdir()
        except OSError as exc:
            return "删除失败：%s" % exc
        return "已删除空目录：%s" % full

    def delete_file(self, path: str) -> str:
        try:
            full = self._resolve(path)
        except PathOutsideWorkspace:
            return DENIED
        if not full.is_file():
            return "文件不存在：%s" % full
        try:
            full.unlink()
        except OSError as exc:
            return "删除失败：%s" % exc
        return "已删除文件：%s" % full

    def rename_path(self, src: str, dst: str) -> str:
        try:
            source = self._resolve(src)
            target = self._resolve(dst)
        except PathOutsideWorkspace:
            return DENIED
        if not source.exists():
            return "源路径不存在：%s" % source
        if target.exists():
            return "目标已存在，拒绝覆盖：%s" % target
        try:
            source.rename(target)
        except OSError as exc:
            return "重命名失败：%s" % exc
        return "已重命名：%s -> %s" % (source, target)

    # ---------- 导出 ----------

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="listFiles",
                description="列出指定目录下的所有文件和子目录。",
                parameters={
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "目录路径，相对工作区或绝对路径均可，不填则为工作区根目录",
                        },
                    },
                    "required": [],
                },
                handler=self.list_files,
            ),
            Tool(
                name="getFileType",
                description="获取指定文件的文件类型（扩展名）。",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "文件路径"}},
                    "required": ["path"],
                },
                handler=self.get_file_type,
            ),
            Tool(
                name="readFile",
                description="读取指定文件的内容。仅支持纯文本格式文件。",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "文件路径"}},
                    "required": ["path"],
                },
                handler=self.read_file,
            ),
            Tool(
                name="createFile",
                description="根据指定目录和文件名创建新文件，可同时写入初始内容。文件已存在时报错。",
                parameters={
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "目标目录"},
                        "filename": {"type": "string", "description": "新文件名"},
                        "content": {"type": "string", "description": "初始内容，可选，默认为空"},
                    },
                    "required": ["directory", "filename"],
                },
                handler=self.create_file,
            ),
            Tool(
                name="editFile",
                description="编辑指定文件的内容（整篇覆盖写入）。仅支持纯文本格式文件。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要编辑的文件路径"},
                        "content": {"type": "string", "description": "新的完整内容"},
                    },
                    "required": ["path", "content"],
                },
                handler=self.edit_file,
            ),
            Tool(
                name="createDirectory",
                description="创建新目录，父目录不存在时会一并创建。",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "要创建的目录路径"}},
                    "required": ["path"],
                },
                handler=self.create_directory,
            ),
            Tool(
                name="deleteDirectory",
                description="删除目录。仅允许删除空目录，目录非空时会被拒绝。",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "要删除的目录路径"}},
                    "required": ["path"],
                },
                handler=self.delete_directory,
            ),
            Tool(
                name="deleteFile",
                description="删除指定文件。",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "要删除的文件路径"}},
                    "required": ["path"],
                },
                handler=self.delete_file,
            ),
            Tool(
                name="renamePath",
                description="重命名文件或目录（也可用于移动到工作区内的其他位置）。目标已存在时会被拒绝。",
                parameters={
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "原路径"},
                        "dst": {"type": "string", "description": "新路径"},
                    },
                    "required": ["src", "dst"],
                },
                handler=self.rename_path,
            ),
        ]
