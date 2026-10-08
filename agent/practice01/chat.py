import configparser

from openai import OpenAI

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")

client = OpenAI(
    base_url=config["llm"]["base_url"],
    api_key=config["llm"]["api_key"],
)

messages = []

while True:
    prompt = input("please input your prompt\n")

    print("---")

    messages.append({"role": "user", "content": prompt})

    stream = client.chat.completions.create(
        model=config["llm"]["model"],
        messages=messages,
        stream=True,
    )

    reply = ""
    for chunk in stream:
        piece = chunk.choices[0].delta.content
        if piece:
            print(piece, end="", flush=True)
            reply += piece

    print()

    messages.append({"role": "assistant", "content": reply})
