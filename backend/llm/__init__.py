"""LLM 层: 模型 API 调用 (按协议分文件, 不是按模型名)。

openai_client.py: GPT/DeepSeek/Qwen/Seedream (OpenAI 兼容)
claude_client.py: Anthropic 原生 (预留)
gemini_client.py: Gemini 原生 (预留)

factory.py: 根据 .env 创建 client 实例
"""
