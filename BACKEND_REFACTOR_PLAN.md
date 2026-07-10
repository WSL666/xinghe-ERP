# Backend 重塑方案（backend-new）

> 本文档记录所有已达成共识的设计决策 + vibe-engine-server 架构精华融合。
> 上下文满了也能照这个文档继续。
> 最后更新：2026-07-10

---

## 一、vibe-engine-server 架构学习总结

研究了 vibe-engine-server（732个py文件，生产级 agent 框架），提取了以下值得借鉴的设计：

### 1. 统一 ToolResult + 每个 tool 有专属 Result 子类（★ 借鉴）

vibe 的做法：
- `rockagent/tool/tool_result.py`：基类 ToolResult（success/error/_internal_errors）
- `tools/model/seedream_result.py`：`class SeedreamResult(ToolResult)` 继承基类，加自己的字段
- 每个 tool 返回自己的 Result 类型，但都继承 ToolResult

**我们的方案**：ToolResult 放 `tools/tool_result.py`，每个 tool 脚本可以定义自己的子类。
但我们的场景简单，直接用 ToolResult 不需要子类（除非某个 tool 有特殊字段需求）。

### 2. 统一错误分类（★ 借鉴）

vibe 的 `rockagent/tool/tool_error.py`：
```python
class ToolErrorType(str, Enum):
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_ERROR = "authentication_error"
    ...

class ServiceType(str, Enum):  # 标记是哪个服务出的错
    CLAUDE_SERVICE = "claude"
    GEMINI_SERVICE = "gemini"
    ...
```

**我们的方案**：`core/errors.py` 定义 ErrorCode 枚举，比 vibe 的更精简（我们的服务少）。

### 3. 模型工厂 + 统一 LLM 数据结构（★ 借鉴）

vibe 的 `rockagent/model/brain.py`：
```python
class LLM(BaseModel):
    provider: str = ''
    model_type: str = 'claude'  # 协议类型: claude/gemini/openai
    model_url: str = ''
    model_name: str = ''
    api_key: str = ''
    read_timeout_ms: int = -1

class Brain(BaseModel):
    default_llm: LLM
    failover_llms: list[LLM] = []  # ★ 降级链
```

vibe 的 `rockagent/llm/utils.py` 的路由：
```python
async def stream_generate_with_llm(llm, req):
    if llm.model_type == "claude":
        generator = ClaudeLLM(llm).generate(req)
    elif llm.model_type == "gemini":
        generator = GeminiLLM(llm).generate(req)
    elif llm.model_type == "openai":
        generator = OpenaiLLM(llm).generate(req)
```

**我们的方案**：
- `llm/` 按协议保留 openai_client.py / claude_client.py / gemini_client.py（解答了之前"只剩一个太薄"的问题）
- 每个 client 只做"建 client + 裸调用"，零重试
- tools 脚本根据模型协议 import 对应的 client
- 用 model_type 字段路由（openai/claude/gemini）

### 4. failover 降级链（★ 借鉴，增强）

vibe 的 Brain 有 `default_llm` + `failover_llms`。

**我们的方案**：用户 key 失败时，用平台默认 key（.env）兜底。放在 tools 层。

### 5. 配置中心（Nacos）→ 我们的简化版

vibe 用 Nacos 做配置中心（动态改配置不重启）。

**我们的方案**：不需要 Nacos。用户配置存数据库（user_model_configs 表），平台默认配置存 .env。

### 6. 不借鉴的部分

- gRPC 微服务：我们是单体，不需要
- Prometheus + Kafka 监控：太重，我们用结构化日志 + trace_id
- tool_server（远程 tool 执行）：我们是单体本地执行
- 异步 agent executor：我们 pipeline 是固定步骤，不需要动态 agent 循环

---

## 二、当前痛点（已确认）

1. **重试/兜底逻辑散落 3 层**：llm + tools + pipeline，改一个动三处
2. **`api_key_pool/` 是平台共享 key 设计**，用户 BYOK 后完全不需要
3. **所有模型共用一套重试参数**，不合理
4. **tool 命名太死板**，加 Qwen 翻译还要改 DeepSeek 的脚本

---

## 三、核心设计决策（已确认 + 融合 vibe 精华）

### 决策 1：插件式 tools（用户确认）

每个 tool = `{provider}_{task}.py`，完全自包含（重试、超时、退避全在脚本里）：
```
tools/
├── deepseek_translate.py     # 重试2次, 超时60s, 退避2s
├── qwen_translate.py         # 重试3次, 超时120s, 退避4s
├── qwen_multimodal.py
├── seedream_image.py         # 批量生图 n>1
├── gpt_image.py              # 单张生图
```

### 决策 2：dispatch 路由（用户确认）

pipeline 不直接调具体脚本，通过 dispatch 根据「用户配置 + 任务类型」路由：
```python
module = dispatch.get_tool("translate", user_cfg["title"]["provider"])
result = module.translate(user_cfg, titles, prompt)  # → ToolResult
```

### 决策 3：ToolResult 统一返回（用户确认 + vibe 借鉴）

放在 `tools/tool_result.py`（不放 schemas/，跟 tools 一家人）：
```python
class ToolResult:
    status: str          # "success" | "error" | "partial"
    data: dict           # 实际结果
    error: str | None    # 失败原因
    error_code: str | None  # 统一错误码
    metadata: dict       # 耗时、token、模型名、重试次数、trace_id
```

### 决策 4：llm/ 层按协议分文件（vibe 借鉴，解答了之前的疑问）

**不是只剩一个 client.py，而是按 API 协议保留多个**：
```
llm/
├── openai_client.py     # OpenAI 兼容: GPT/DeepSeek/Qwen/Seedream → chat()/analyze()/generate_one()
├── claude_client.py     # Claude 原生 → chat()/analyze()
├── gemini_client.py     # Gemini 原生 → chat()/analyze()
└── model_type.py        # 路由: 根据 model_type 字段选 client
```

每个 client **只做"建 client + 裸调用"**，零重试。重试逻辑全在 tools 脚本里。
tools 脚本根据模型协议 import 对应的 client。

### 决策 5：删除 api_key_pool（用户确认）

- 用户 BYOK，每人一个 key
- 配置存数据库 user_model_configs 表，API Key 加密存储
- 设置页面：选 provider → 填 key → 测试连接

### 决策 6：统一错误码（用户确认 + vibe 借鉴）

```python
# core/errors.py
class ErrorCode(str, Enum):
    API_KEY_INVALID     = "E001"
    RATE_LIMITED        = "E002"
    MODEL_TIMEOUT       = "E003"
    INSUFFICIENT_QUOTA  = "E004"
    NETWORK_ERROR       = "E005"
    PARSE_ERROR         = "E006"
    INTERNAL_ERROR      = "E007"
```

### 决策 7：结构化日志 + trace_id（用户确认）

放在 `core/base.py` 的 `log()` 改造，一次 pipeline 生成一个 trace_id 贯穿所有 step。

### 决策 8：先国内模型，再中转站（用户确认）

- 第一批：DeepSeek 翻译 + Qwen 多模态
- 跑通后加中转站模型

### 决策 9：降级链（vibe 借鉴）

用户 key 失败 → 自动用平台默认 key（.env）兜底。放在 tools 层。

---

## 四、目标目录

```
backend-new/
├── main.py / worker.py / config.py / .env
│
├── core/                          ← 基础设施
│   ├── base.py                    # log() 加 trace_id
│   ├── app.py                     # HTTP 路由
│   ├── images.py                  # 图片下载/编码
│   ├── oss.py                     # OSS 上传
│   └── errors.py                  # ★ ErrorCode 枚举
│
├── llm/                           ← 模型调用层（按协议分文件, 零重试）
│   ├── openai_client.py           # OpenAI 兼容: chat/analyze/generate_one
│   ├── claude_client.py           # Claude 原生 (预留)
│   ├── gemini_client.py           # Gemini 原生 (预留)
│   └── model_type.py              # 路由: model_type → 选 client
│
├── tools/                         ← 任务层（核心重塑区）
│   ├── tool_result.py             # ★ ToolResult 定义
│   ├── dispatch.py                # ★ 路由: provider+task → 脚本
│   ├── deepseek_translate.py      # 自带重试, 返回 ToolResult
│   ├── qwen_multimodal.py
│   ├── gpt_image.py
│   └── seedream_image.py
│
├── store/                         ← 数据库层
│   ├── pool.py / migrations.py / user.py / import_.py
│   └── model_config.py            # ★ 用户模型配置 CRUD
│
├── billing/                       ← 计费 (去 key pool)
├── mq/                            ← 队列
├── security/                      ← 认证
├── services/                      ← SMS
├── schemas/                       ← product.py
├── agent/                         ← stub
└── platforms/temu/                ← 业务编排
    ├── pipeline.py                # 简化: 调 dispatch, 删 key round
    └── prompts/
```

---

## 五、数据库新增表

```sql
CREATE TABLE user_model_configs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_type   TEXT NOT NULL,    -- 'title' | 'multimodal' | 'image'
    provider    TEXT NOT NULL,    -- 'deepseek' | 'qwen' | 'seedream' ...
    model_name  TEXT NOT NULL,    -- 'deepseek-chat' | 'qwen-plus' ...
    model_type  TEXT NOT NULL,    -- 'openai' | 'claude' | 'gemini' (协议)
    base_url    TEXT NOT NULL,
    api_key_enc TEXT NOT NULL,    -- 加密存储
    enabled     BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, task_type)
);
```

---

## 六、落地步骤（10 步）

| 步骤 | 内容 | 风险 |
|------|------|------|
| 1 | `cp -r backend/ backend-new/`，删 api_key_pool/ + 旧 llm/ + 旧 tools/ | 低 |
| 2 | `core/errors.py` + `tools/tool_result.py` + `core/base.py` 加 trace_id | 低 |
| 3 | `llm/` 层：openai_client(裸调用) + model_type 路由 | 低 |
| 4 | `store/model_config.py` + 建表 | 低 |
| 5 | 第一批 tool 脚本: deepseek_translate + qwen_multimodal | 低 |
| 6 | `tools/dispatch.py` 路由 | 低 |
| 7 | 改 `pipeline.py`：删 key round，改用 dispatch + ToolResult | 中 |
| 8 | 改 `billing/router`：去 api_key_pool 引用 | 低 |
| 9 | 设置页前端：模型配置表单 + 连接测试 | 中 |
| 10 | 全链路测试 → 切 systemd 到 backend-new/ | — |

---

## 七、可选增强

| 功能 | 说明 |
|------|------|
| Token 用量持久化 | ToolResult.metadata 带 tokens |
| 降级链 | 用户 key 失败 → 平台默认 key 兜底 |
| 幂等保护 | request_id 防重复入队 |
| Provider 预设 | 选 DeepSeek 自动填 base_url |
| 连接测试按钮 | 配完 key 点测试验证有效 |
