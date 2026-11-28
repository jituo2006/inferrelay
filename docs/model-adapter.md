# 模型适配：样例源码与 Feedback 验收

## 本次范围

沿用 I1 已有的 `Model(task, feedback) → ModelReply` 接口。模型适配器负责组装模型请求、传递源码和验证反馈、读取原始候选及元数据；不负责路由、重试策略、补丁范围检查或执行模型生成的代码。本次不修改共享类型、调度器或验证器，也不引入第三方依赖。

编码任务首次调用发送以下信息：

- 原始 `Task.prompt`；
- `task_id`、`base_revision`、`allowed_paths`、`check_ids`；
- `files` 中每个文件的相对路径和完整源码；
- 输出约定：只返回针对原始基线的完整 unified diff，不返回 Markdown 围栏或说明。

后续调用继续发送同一份原始源码，并传入上一轮候选、验证是否通过、稳定失败代码、失败类型与摘要。保留已有的 `previous_candidate` 和 `verification` 字段，补充 `verification_passed`、`verification_code`、`verification_kind`。没有源码文件的原有文本任务保持原始首条请求格式。

`ModelReply.text` 保留供应商返回的原始内容，不剥离围栏、不自动修正补丁。模型标识、停止原因和 Token 用量独立返回；缺失的可选字段保持 `None`。这些提示约定不是安全检查，候选是否合法仍由验证器判定。

## 离线验收

在项目根目录，使用 Python 3.10+ 与本地 Git：

```powershell
py -3.11 -m examples.pagination.model_adapter_demo
py -3.11 -m unittest discover -s tests -v
py -3.11 -m examples.pagination.demo
```

非 Windows 环境可把 `py -3.11` 换成 `python3`。

新演示启动仅绑定 `127.0.0.1` 的临时 HTTP 服务，经过真正的 `ChatCompletionsModel` HTTP 请求路径，返回仓库已有的两份补丁。它不访问外部供应商，不读取真实 API Key，不产生模型费用，退出时关闭服务。输出明确标记 `backend=local_stub` 与 `live_provider_called=false`。

1. 用样例 Task 和 `feedback=None` 调用适配器，模拟 API 返回 `partial.patch`。
2. 现有验证器将补丁应用到干净源码，实际执行分页检查，报告 `test_invalid_page` 和 `test_invalid_page_size` 失败。
3. 用同一份 Task 和预设 Feedback 再次调用适配器，模拟 API 返回 `complete.patch`。
4. 验证器再次从干净源码开始，完整补丁通过全部分页检查。

这是输入传递、HTTP 调用、响应读取和样例补丁交接的验收，不是模型理解反馈或自主修复能力的证明。模拟服务按照调用次序返回固定补丁；集成测试另行检查实际 HTTP 请求是否包含源码和预设 Feedback。

原有 `examples.pagination.demo` 仍验证调度链路 `fast → fast → strong` 与 `retry → escalate → accept`；新演示只验证模型适配，不改变该链路。

## 测试覆盖

`tests/test_model_adapter.py` 覆盖：

- 首轮源码、基线及路径约束，多文件与中文源码；
- 预设 Feedback、空候选反馈，以及修订仍针对原始基线；
- unified diff 原样返回，停止原因和可选 Token 元数据；
- 空输出、截断、无效响应、非 JSON、HTTP 错误、超时和传输故障；
- 本地真实 HTTP 请求及两份补丁的真实分页检查。

## 真实模型联调：DeepSeek 4.1 Flash

认领人已选择只使用 DeepSeek 4.1 Flash。独立联调入口为：

```powershell
py -3.11 -m examples.pagination.deepseek_demo
```

运行时交互输入 API Key，输入不回显，程序不将 Key 写入文件。固定 API 前缀为 `https://api.deepseek.com`、模型 ID 为 `deepseek-flash`，调用超时为 180 秒；无需六个快强模型环境变量。若本机已有 `DEEPSEEK_API_KEY`，则跳过输入。

该命令发起两次真实生成请求，可能产生费用：同一份分页原始源码分别以无 Feedback、预设 Feedback 调用。输出 `status=responses_received` 仅表示两次响应已收到；`patch_verification=not_run` 明确表示未验证真实补丁。程序不自动执行模型生成的代码。

自动化测试使用模拟客户端，不访问真实供应商。认领人另已亲自运行此入口并确认平台记录了实际 API 调用；真实联调结果见下方记录。此入口不修改双模型路由或调度。

分页示例验证器只接受两份仓库内预设补丁，不能判断任意真实模型补丁。真实补丁自动验收仍需与验证模块负责人确认，本次不跨模块新增通用验证器。

官方模型 ID 与版本对应关系见 [DeepSeek 官方文档](https://api-docs.deepseek.com/)；查询时 `deepseek-flash` 对应 DeepSeek-V4.1-Flash-1017。不要将版本展示名称直接当作 API 模型 ID。

通用双模型 API 配置仍沿用 README 的 `AI4SE_FAST_*` / `AI4SE_STRONG_*` 环境变量。原有 `python3 -m hybrid_inference --backend api` 运行文本题，不是分页修复题。
## 验收记录（2026-11-24）

### 自动化与离线验收

- `py -3.11 -m unittest discover -s tests -v`：33 项测试通过，包括 17 项适配器测试和 6 项 DeepSeek 入口测试。
- `py -3.11 -m examples.pagination.model_adapter_demo`：本地 HTTP 模拟服务返回两份预设补丁，首轮报告参数校验失败，预设 Feedback 轮通过分页检查。
- `py -3.11 -m examples.pagination.demo`：原有链路仍为 `fast → fast → strong`、`retry → escalate → accept`，最终通过。
- `git diff --check`：通过。

### 认领人的真实调用

认领人亲自运行 DeepSeek 入口，提供了两次响应，并确认平台已有实际调用记录。以下为用户提供的响应统计，不是自动化测试伪造的结果，也不是助手持有用户 Key 后发起的请求。

| 阶段 | 返回模型 | 停止原因 | 输入 tokens | 输出 tokens | 其中推理 tokens |
| --- | --- | --- | ---: | ---: | ---: |
| `initial` | `deepseek-flash` | `stop` | 265 | 771 | 665 |
| `preset_feedback` | `deepseek-flash` | `stop` | 435 | 695 | 588 |

输出 tokens 已包含其中的推理 tokens，不应重复相加。两份候选都修正了从 1 开始的页码偏移，并对小于 1 的页码、页大小抛出 `ValueError`。

`preset_feedback` 使用仓库 `partial.patch` 及其预设失败反馈，不是对首次真实回复的验证结果。首次真实回复本身已包含完整逻辑修复；这次联调证明有反馈和无反馈两种请求都能返回候选，不证明模型根据首次真实失败自主修复。

### 真实候选的独立复核与已知边界

助手根据聊天中粘贴的 `reply.text` 重建了两份候选，去除聊天传输层的 Markdown 转义；未获得原始响应文件，不能将重建文本等同于原始响应字节。复核不再调用 API，在独立临时目录中复制基线和检查脚本，没有改动仓库样例源码或验证器。

| 候选文本 | 首次回复 | 预设 Feedback 回复 |
| --- | --- | --- |
| 按粘贴文本重建、不含末尾换行 | `git apply --check` 报 `corrupt patch`，未执行测试 | 同左 |
| 显式补一个末尾 `\n` 的独立副本 | Git 应用成功，7 项分页测试通过 | Git 应用成功，7 项分页测试通过 |

这表示候选的修复逻辑通过了复核，但不能把未经处理的原始补丁标为已验证通过。`ModelReply.text` 仍原样返回；适配器不静默添加换行。新增回归测试覆盖缺少末尾换行时仍保留原文。是否允许补丁格式归一化及其记录方式，需要与验证模块负责人确认，不能借此跨模块添加通用验证器。

真实入口输出仍为 `patch_verification=not_run`：该标记准确描述入口本身没有执行验证，不会因外部人工复核而改变。

## 提交与协作

认领人已亲自确认真实 API 调用，并于 2026-11-24 明确授权整理并上传到个人 fork。提交目标为 `h123123174/inferrelay` 的 `feat/model-adapter-validation` 功能分支；本次不推送组长的 `jituo2006/inferrelay`，不创建 PR，也不修改个人 fork 的 `main`。

后续向组长仓库贡献时，需要认领人另行确认；由项目负责人审核合并。直接推送组长仓库需要写权限，无写权限时使用 fork 分支发 PR。
