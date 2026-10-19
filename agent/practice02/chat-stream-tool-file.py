import configparser
import json
import os
import subprocess

from openai import OpenAI

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")

client = OpenAI(
    base_url=config["llm"]["base_url"],
    api_key=config["llm"]["api_key"],
)

# ---------------- 工具定义 ----------------

tools = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间。用户询问今天几号、星期几、现在几点时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断某一年是否是闰年。用户询问某个年份是不是闰年时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "要判断的年份，例如 2024"},
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCwd",
            "description": "获取当前工作目录的完整路径。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listFiles",
            "description": "列出指定目录下的所有文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "目录路径，相对当前工作目录或绝对路径均可，不填则为当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的文件类型（扩展名）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "读取指定文件的内容。仅支持纯文本格式文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "根据指定目录和文件名创建新文件，可同时写入初始内容。文件已存在时报错。",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "目标目录"},
                    "filename": {"type": "string", "description": "新文件名"},
                    "content": {"type": "string", "description": "初始内容，可选，默认为空"},
                },
                "required": ["directory", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFile",
            "description": "编辑指定文件的内容（整篇覆盖写入）。仅支持纯文本格式文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要编辑的文件路径"},
                    "content": {"type": "string", "description": "新的完整内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDirectory",
            "description": "创建新目录，父目录不存在时会一并创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDirectory",
            "description": "删除目录。仅允许删除空目录，目录非空时会被拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的目录路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除指定文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renamePath",
            "description": "重命名文件或目录（也可用于移动到工作区内的其他位置）。目标已存在时会被拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "原路径"},
                    "dst": {"type": "string", "description": "新路径"},
                },
                "required": ["src", "dst"],
            },
        },
    },
]

# ---------------- 日期时间工具（调用本地 date 命令） ----------------

def get_date():
    """调用本机 date 命令取日期时间，不用 Python 的 datetime。"""
    if os.name == "nt":
        # Windows 的坑：裸跑 date 会进入"输入新日期"的交互模式并一直等输入，
        # 必须加 /t 才是"只显示"；time /t 同理。cmd 输出是 GBK，按 UTF-8 解会乱码。
        result = subprocess.run(
            ["cmd", "/c", "date /t & time /t"],
            capture_output=True,
        )
        text = result.stdout.decode("gbk", errors="replace")
    else:
        result = subprocess.run(["date"], capture_output=True)
        text = result.stdout.decode("utf-8", errors="replace")

    text = " ".join(text.split())  # 把换行/多空格压成一个空格
    if not text:
        return "获取日期失败，date 命令返回码 %d" % result.returncode
    return text


# ---------------- 文件操作工具 ----------------

# 视为纯文本的扩展名白名单
TEXT_EXTS = {
    ".txt", ".md", ".py", ".json", ".ini", ".cfg", ".csv",
    ".bat", ".log", ".xml", ".html", ".css", ".js", ".yaml", ".yml",
}

MAX_READ_CHARS = 20000  # 防止大文件撑爆对话上下文


def resolve_in_workspace(path):
    """把路径解析到当前工作目录内；越界返回 None（防止模型读写工作区之外的文件）。"""
    base = os.path.abspath(os.getcwd())
    full = os.path.abspath(os.path.join(base, path))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def get_cwd():
    return os.getcwd()


def list_files(directory):
    base = os.path.abspath(os.getcwd())
    full = os.path.abspath(os.path.join(base, directory or "."))
    if not os.path.isdir(full):
        return "目录不存在：%s" % full
    names = sorted(os.listdir(full))
    if not names:
        return "目录为空：%s" % full
    lines = []
    for name in names:
        p = os.path.join(full, name)
        tag = "[目录]" if os.path.isdir(p) else "[文件]"
        lines.append("%s %s" % (tag, name))
    return "\n".join(lines)


def get_file_type(path):
    full = resolve_in_workspace(path)
    if full is None:
        return "拒绝访问：路径超出当前工作目录"
    if os.path.isdir(full):
        return "这是一个目录，不是文件"
    if not os.path.isfile(full):
        return "文件不存在：%s" % full
    ext = os.path.splitext(full)[1].lower()
    return ext if ext else "无扩展名"


def read_file(path):
    full = resolve_in_workspace(path)
    if full is None:
        return "拒绝访问：路径超出当前工作目录"
    if not os.path.isfile(full):
        return "文件不存在：%s" % full
    ext = os.path.splitext(full)[1].lower()
    if ext not in TEXT_EXTS:
        return "仅支持纯文本文件，不接受 %s 类型（扩展名白名单：%s）" % (
            ext or "无扩展名", " ".join(sorted(TEXT_EXTS)),
        )
    try:
        with open(full, "r", encoding="utf-8") as f:
            content = f.read(MAX_READ_CHARS + 1)
    except UnicodeDecodeError:
        return "文件内容不是 UTF-8 纯文本，拒绝读取"
    except OSError as e:
        return "读取失败：%s" % e
    if len(content) > MAX_READ_CHARS:
        return content[:MAX_READ_CHARS] + "\n...(内容过长，已截断)"
    return content if content else "(空文件)"


def create_file(directory, filename, content=""):
    base = os.path.abspath(os.getcwd())
    full = os.path.abspath(os.path.join(base, directory or ".", filename))
    if not full.startswith(base + os.sep):
        return "拒绝访问：路径超出当前工作目录"
    if os.path.exists(full):
        return "文件已存在，拒绝覆盖：%s（如需修改内容请用 editFile）" % full
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return "创建失败：%s" % e
    return "已创建：%s（%d 字符）" % (full, len(content))


def edit_file(path, content):
    full = resolve_in_workspace(path)
    if full is None:
        return "拒绝访问：路径超出当前工作目录"
    if not os.path.isfile(full):
        return "文件不存在：%s（新建文件请用 createFile）" % full
    ext = os.path.splitext(full)[1].lower()
    if ext not in TEXT_EXTS:
        return "仅支持编辑纯文本文件，不接受 %s 类型" % (ext or "无扩展名")
    try:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return "编辑失败：%s" % e
    return "已保存：%s（%d 字符）" % (full, len(content))


def create_directory(path):
    full = resolve_in_workspace(path)
    if full is None:
        return "拒绝访问：路径超出当前工作目录"
    if os.path.exists(full):
        return "目录已存在：%s" % full
    try:
        os.makedirs(full)  # 父目录不存在会一并创建
    except OSError as e:
        return "创建失败：%s" % e
    return "已创建目录：%s" % full


def delete_directory(path):
    full = resolve_in_workspace(path)
    if full is None:
        return "拒绝访问：路径超出当前工作目录"
    if full == os.path.abspath(os.getcwd()):
        return "拒绝删除：不能删除当前工作目录本身"
    if not os.path.isdir(full):
        return "目录不存在：%s" % full
    remaining = os.listdir(full)
    if remaining:
        return "目录非空（含 %d 个文件/子目录），拒绝删除：%s" % (len(remaining), full)
    try:
        os.rmdir(full)
    except OSError as e:
        return "删除失败：%s" % e
    return "已删除空目录：%s" % full


def delete_file(path):
    full = resolve_in_workspace(path)
    if full is None:
        return "拒绝访问：路径超出当前工作目录"
    if not os.path.isfile(full):
        return "文件不存在：%s" % full
    try:
        os.remove(full)
    except OSError as e:
        return "删除失败：%s" % e
    return "已删除文件：%s" % full


def rename_path(src, dst):
    s = resolve_in_workspace(src)
    d = resolve_in_workspace(dst)
    if s is None or d is None:
        return "拒绝访问：路径超出当前工作目录"
    if not os.path.exists(s):
        return "源路径不存在：%s" % s
    if os.path.exists(d):
        return "目标已存在，拒绝覆盖：%s" % d
    try:
        os.rename(s, d)
    except OSError as e:
        return "重命名失败：%s" % e
    return "已重命名：%s -> %s" % (s, d)


# ---------------- 工具分发 ----------------

def run_tool(name, arguments):
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return "参数不是合法 JSON：%r" % arguments

    if name == "getDate":
        return get_date()

    if name == "isLeapYear":
        try:
            year = int(args["year"])
        except (KeyError, TypeError, ValueError):
            return "参数错误：需要整数 year，收到 %r" % (args,)
        if year < 1:
            return "参数错误：年份必须为正整数，收到 %d" % year
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return "%d 年%s闰年" % (year, "是" if leap else "不是")

    if name == "getCwd":
        return get_cwd()

    if name == "listFiles":
        return list_files(args.get("directory", "."))

    if name == "getFileType":
        return get_file_type(args.get("path", ""))

    if name == "readFile":
        return read_file(args.get("path", ""))

    if name == "createFile":
        return create_file(
            args.get("directory", ""), args.get("filename", ""), args.get("content", "")
        )

    if name == "editFile":
        return edit_file(args.get("path", ""), args.get("content", ""))

    if name == "createDirectory":
        return create_directory(args.get("path", ""))

    if name == "deleteDirectory":
        return delete_directory(args.get("path", ""))

    if name == "deleteFile":
        return delete_file(args.get("path", ""))

    if name == "renamePath":
        return rename_path(args.get("src", ""), args.get("dst", ""))

    return "未知工具：%s" % name


# ---------------- 单轮流式请求 ----------------

def stream_once(messages):
    stream = client.chat.completions.create(
        model=config["llm"]["model"],
        messages=messages,
        tools=tools,
        stream=True,
    )

    content = ""
    calls = {}

    for chunk in stream:
        delta = chunk.choices[0].delta

        if delta.content:
            print(delta.content, end="", flush=True)
            content += delta.content

        # 流式返回里 tool_calls 是分片到达的，要按 index 自己拼
        for tc in delta.tool_calls or []:
            call = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                call["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    call["name"] = tc.function.name
                if tc.function.arguments:
                    call["arguments"] += tc.function.arguments

    return content, calls


# ---------------- 主循环 ----------------

messages = []

while True:
    prompt = input("please input your prompt\n")

    print("---")

    messages.append({"role": "user", "content": prompt})

    # 一轮提问里模型可能连续调多次工具，所以再套一层循环
    while True:
        content, calls = stream_once(messages)

        if not calls:
            print()
            messages.append({"role": "assistant", "content": content})
            break

        # 把"模型决定调工具"这条 assistant 消息记进历史
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"],
                        },
                    }
                    for _, call in sorted(calls.items())
                ],
            }
        )

        print()

        # 执行工具，把结果作为 tool 消息回传给模型，让它基于结果继续回答
        for _, call in sorted(calls.items()):
            output = run_tool(call["name"], call["arguments"])
            shown = output if len(output) <= 200 else output[:200] + "...(截断显示)"
            print("[tool] %s(%s) -> %s" % (call["name"], call["arguments"], shown))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": output,
                }
            )
