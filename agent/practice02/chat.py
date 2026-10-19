import configparser
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
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }
]


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


def run_tool(name, arguments):
    if name == "getDate":
        return get_date()
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
            print("[tool] %s -> %s" % (call["name"], output))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": output,
                }
            )
