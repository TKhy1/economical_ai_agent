"""Live connectivity check for the configured chat backend.

Reads the same `.env` / environment variables as the app, prints which provider and model
are in effect, then makes one real API call. This isolates "is my key/endpoint/model
right?" from the Flask app.

    venv\\Scripts\\python.exe check_llm.py

Exit code 0 = the key, endpoint and model all work.
"""
import sys
import time

import config
import llm_agent

# A prompt that should not trigger a tool call: this is a wiring test, not a chat test.
PROMPT = "请只回复两个字母：OK"


def _print_target(status):
    print("provider   : %s (%s)" % (status["provider"], status.get("label", "")))
    model_line = "model      : %s" % status["model"]
    if status.get("model_requested"):
        model_line += "   (配置里写的是 %s)" % status["model_requested"]
    print(model_line)
    if status.get("base_url"):
        print("base_url   : %s" % status["base_url"])
    if status.get("thinking"):
        print(
            "thinking   : %s (设置=%s)" % (status["thinking"], status.get("thinking_setting"))
        )
    print("max_tokens : %s" % config.AI_MAX_TOKENS)
    print("configured : %s" % status["configured"])


def main():
    status = llm_agent.provider_status()
    _print_target(status)

    if not status["configured"]:
        print(
            "\n未检测到可用的 API Key。\n"
            "  1) 打开项目根目录的 .env；\n"
            "  2) 把 DEEPSEEK_API_KEY=Enter DeepSeek API Key 换成真实 Key；\n"
            "  3) 重新运行本脚本。"
        )
        return 1

    started = time.time()
    try:
        provider = llm_agent.build_provider()
        turn = provider.complete(
            llm_agent.SYSTEM_PROMPT, [{"role": "user", "content": PROMPT}]
        )
    except llm_agent.LLMError as exc:
        print("\n调用失败：%s" % exc)
        print(
            "排查：401 → Key 不对；402 → 余额不足；404 → AI_BASE_URL 写错；"
            "超时/URLError → 网络或代理。"
        )
        return 1
    elapsed = time.time() - started

    print("\n--- 模型回复 ---")
    print(turn.text or "（空回复）")
    if turn.tool_calls:
        print("模型还返回了工具调用（说明 function calling 也通）：")
        for call in turn.tool_calls:
            print("  - %s %s" % (call.name, call.raw_arguments or call.arguments))
    print("--- 耗时 %.2fs ---" % elapsed)

    if not turn.text and not turn.tool_calls:
        print("回复为空：可能是 max_tokens 太小或被截断，可调大 AI_MAX_TOKENS 再试。")
        return 1
    print("\nOK：Key、端点、模型都可以用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
