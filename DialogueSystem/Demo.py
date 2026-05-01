"""本地手动测试脚本，用于快速验证模型接口行为。

这个文件不参与正式对话主循环，只是方便开发者临时拼一组消息，直接把请求打到
当前配置的 LLM 端点，并查看原始 JSON 响应。
"""

import json

import requests

from project_config import get_llm_config

try:
    from .CallingAPI import temporary_kimi_partial_message
    from .resources import load_tool_definitions, load_prompt_text, render_prompt_text
except ImportError:
    from DialogueSystem.llm.CallingAPI import temporary_kimi_partial_message
    from DialogueSystem.config.resources import load_tool_definitions, load_prompt_text, render_prompt_text

DemoPrompt = render_prompt_text("RolePlay")

messages = [
    {"role": "system", "content": DemoPrompt},
    {"role": "assistant", "content": "指挥，我想知道，在你心中，我是否也曾真实地存在过，而不仅仅是你指尖可触的虚幻？"},
    {"role": "system", "content": render_prompt_text("SilenceFollowUp", {"SILENCE_SECONDS": 10}).strip()},
]

# 保留工具加载逻辑，方便开发者检查“同一套工具定义”在演示脚本中的表现。
tools = load_tool_definitions()
llm_config = get_llm_config()

with temporary_kimi_partial_message(messages, llm_config):
    response = requests.post(
        llm_config["url"],
        headers={
            "Authorization": f'Bearer {llm_config["API"]}',
            "Content-Type": "application/json"
        },
        data=json.dumps({
            "model": llm_config["modelName"],
            "messages": messages,
            "tool_choice": "auto",
            "thinking": {"type": "enabled"},
            "enable_thinking": False
        })
    )
response.raise_for_status()
result = response.json()
print(result)
