# InferRelay

本项目实现应用层的双模型推理。路由器在请求开始时选择模型，调度器在每次生成结束后使用验证结果决定接受、重试或升级。

## 运行演示

在项目根目录使用 Python 3.10 或更新版本。演示使用预设模型回复和一个可自动判定的 Python 代码理解题。

```sh
python3 -m hybrid_inference
python3 -m hybrid_inference --policy fixed
python3 -m hybrid_inference --score 0.9
python3 -m unittest discover -s tests -v
```

前两条命令共用阈值路由，初始调用快模型。默认策略遇到连续两次相同失败时升级，固定策略用满三次快模型尝试后升级。两条路径最终都由强模型返回正确结果。第三条命令的初始分数高于阈值，直接调用强模型。以上调用次数用于验收状态流转。

I1 的分页修复示例使用本地 Git 和 Python。在项目根目录运行 `python3 -m examples.pagination.demo`，可查看两份预设补丁经过真实测试后产生的重试、升级和接受记录。接口及示例说明见 [I1 共享接口与分页修复示例](docs/i1-contract.md)。

运行 `python3 -m examples.pagination.demo --scenario strong-failure` 可查看升级后仍未通过验证而停止的记录。命令以退出码 1 表示未通过。

## 模块接口

| 模块 | 输入与输出 |
| --- | --- |
| 路由 | `Task → Route`。参考 [RouteLLM](https://github.com/lm-sys/RouteLLM) 的分数与阈值设计，原型由调用方提供难度分数，默认阈值为 0.7。 |
| 模型 | `Task + Feedback → ModelReply`。重试和升级请求携带上一轮候选及验证摘要。 |
| 验证 | 编码任务使用 `Task + 补丁 → Check`，原有文本演示使用 `候选文本 → Check`。验证器给出稳定的失败类型和代码，供调度器判断是否重复失败。 |
| 调度 | `run_task(...) → Run`。记录初始路由、每轮验证和决策、模型调用耗时及总耗时。模型服务故障和验证环境故障分别以 `model_error` 和 `verification_error` 结束。 |

## 接入模型 API

在本机环境中设置下面的变量，再运行 `python3 -m hybrid_inference --backend api`。Base URL 填到接口前缀，例如供应商要求的 `https://<host>/v1`，程序会追加 `/chat/completions`。

| 模型角色 | Base URL | 模型 ID | API Key |
| --- | --- | --- | --- |
| 快模型 | `AI4SE_FAST_BASE_URL` | `AI4SE_FAST_MODEL` | `AI4SE_FAST_API_KEY` |
| 强模型 | `AI4SE_STRONG_BASE_URL` | `AI4SE_STRONG_MODEL` | `AI4SE_STRONG_API_KEY` |

API 模式运行相同的代码理解题。适配器读取最终输出、停止原因，以及供应商提供的输入、完成和推理 Token 用量。缺失字段在输出中显示为 `null`。调度使用每轮输出和验证反馈，在模型调用之间发生。
