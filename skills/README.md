# AI Skills

本目录存放可供 AI Agent / Codex 使用的技能说明。

| Skill | 用途 |
|---|---|
| [`mijia-geek-bridge`](mijia-geek-bridge/SKILL.md) | 通过 Bridge REST/SSE 或 MCP 只读审计、预览、生成并保存米家极客版规则 |

## 使用方式

- Codex：把 `skills/mijia-geek-bridge` 复制到 `~/.codex/skills/`；
- 其他支持 `SKILL.md` 的 Agent：直接指向该文件；
- 不支持 Skill 的 Agent：把 `SKILL.md` 内容贴进系统提示词。

## 安全原则

- 登录码只通过 Bridge Web UI / `POST /api/connect` 输入，不写入提示词或日志；
- 新规则默认停用；
- 保存前做结构校验，保存后读回验证；
- 官方编辑器仍是最终人工确认入口。

## 参考项目（均为 MIT）

- [oh-my-sage](https://github.com/allocnode/oh-my-sage)
- [mijia-geek-assistant-fnnas](https://github.com/609293964/mijia-geek-assistant-fnnas)
- [mijia-geek-automation-skills](https://github.com/lixinyu02/mijia-geek-automation-skills)
- [mijiaflow](https://github.com/xmx-emm/mijiaflow)
