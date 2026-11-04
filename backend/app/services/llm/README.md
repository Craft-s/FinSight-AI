# 模型适配器约束

此目录实现同步适配器，由现有RQ任务调用；没有生产Fake Provider。离线测试位于`backend/tests/test_llm_providers.py`，其中所有响应均为明确合成的协议夹具，不代表真实在线调用。

## 已核查的官方资料（2026-10-29）

- [OpenAI Responses Python API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)：使用官方SDK的Responses文本接口、`max_output_tokens`、显式高级参数及`store=false`；拒答或未完成响应不会采用。
- [OpenAI模型列表](https://developers.openai.com/api/reference/resources/models/methods/list)：基础列表没有可靠的完整模型能力信息，缺失能力保留未知。
- [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/)及[Gemini模型列表](https://ai.google.dev/api/models)：使用官方SDK遍历分页，按`supported_actions/generateContent`过滤，读取实际token上限，缺少元数据时不猜测。
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)及[模型列表](https://api-docs.deepseek.com/api/list-models/)：使用独立协议字段转换，消费实际返回的可选名称、上下文与输出模态。temperature在thinking模式下无效，只有显式`reasoning_effort=none`时才接受温度配置。
- [Ollama Chat](https://docs.ollama.com/api/chat)及[Tags](https://docs.ollama.com/api/tags)：从已安装模型列表获取ID，以`num_predict`限制输出，采用非流式chat与JSON输出格式。

## 参数与部署边界

统一选项为`max_output_tokens`（16–8192，默认2048）、`timeout_seconds`（5–120，默认30）。OpenAI显式reasoning参数映射为`reasoning.effort`；DeepSeek按协议传递effort，Gemini/Ollama不接受此跨厂商参数。temperature仅显式提供时发送。协议字段存在不代表任意模型支持；新配置必须以相同选项完成实际小文本调用测试。默认不发送温度或推理强度，不按模型名推测能力或硬编码“最新模型”。

云端目标固定为对应服务商官方origin，所有HTTP客户端关闭环境代理和重定向，传输层也校验每次请求的origin（包括SDK分页）。Ollama目标只能由上层从部署者允许配置取得，不接受浏览器任意URL；部署者负责受控网络与主机配置。输入总量限制24000字符，输出预算有上限；连接测试发送实际短文本请求，输出预算512，并要求有效非空且无截断或拒答。

适配器禁用自动重试，避免不确定状态下重复计费；失败由现有任务流程显式重试。异常只保留固定错误码和安全文案，不返回厂商正文、异常字符串或密钥。计数仅采用厂商报告的非负整数，不推测价格或缺失用量。配置版本、密钥加密、授权、任务状态与业务证据验证由上层负责。
