[English](./CONTRIBUTING.md)

# Selena 贡献指南

谢谢你愿意来折腾 Selena。

这个项目还在持续演化，所以最有帮助的贡献不一定非得是“大功能”，把一个 bug 讲清楚、补一段文档、拆掉一处难懂逻辑，都是很实在的贡献。

## 开始之前

- 先确认你改的是不是已经有人提过。
- 如果是比较大的功能改动，建议先开个 issue 或 discussion，把方向对一下。
- 如果你的改动会影响行为、配置项、提示词或者文档，请把相关说明一起更新。

## 本地开发

### 后端

```bash
cp config.example.json config.json
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
docker compose up -d
python -m DialogueSystem.main
```

### 前端

```bash
cd DialogueSystem/frontend
pnpm install
pnpm dev
```

## 提 issue 时尽量带上这些信息

- 你想做什么，或者你以为它本来应该怎么工作。
- 实际发生了什么。
- 能不能稳定复现，复现步骤是什么。
- 相关日志、报错截图、配置片段。
- 如果贴 `config.json` 片段，请先把 API Key、token、本地路径等敏感信息打码。

## 提 PR 前的建议检查

目前仓库还没有完整 CI，所以提交前建议你至少自己过一遍：

- 后端能不能正常启动。
- 如果改了前端，跑一下 `pnpm check` 和 `pnpm build`。
- 如果改了提示词、技能、配置说明，相关文档有没有同步。
- 如果改动影响用户可见行为，PR 描述里最好写明“改了什么、为什么改、怎么验证”。

## PR 风格

- 尽量一个 PR 只解决一件主要的事。
- 小步提交比一口气堆很多不相关改动更容易 review。
- 不要提交真实密钥、运行时日志、历史对话、数据库文件和本地产物。
- 如果是实验性质改动，最好在 PR 里直说，不用装成已经完全稳定。

## 比较受欢迎的贡献方向

- 文档补全和例子补全
- 自动化测试 / smoke test / CI
- `DialogueSystem/main.py` 继续拆模块
- 前端可观测性和调试体验
- 技能系统、MCP、浏览器代理相关能力扩展

## 行为边界

参与协作默认遵守 [CODE_OF_CONDUCT.zh-CN.md](./CODE_OF_CONDUCT.zh-CN.md)。

如果你不确定某个改动适不适合直接做，开个 issue 先聊就行，不用憋着。
