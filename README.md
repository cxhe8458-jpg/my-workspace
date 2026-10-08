# my-workspace

个人工作资产库：集中整理工作中搭建的平台、编写的脚本、skill 与 prompt，每份资产都附有功能介绍与操作方法。

## 目录结构

| 目录 | 内容 | 说明 |
| --- | --- | --- |
| `platforms/` | 工作平台 | 每个平台一个子目录，含 README 与代码 |
| `scripts/` | 脚本 | 自动化脚本，按脚本单独说明 |
| `skills/` | Skill 定义 | 可复用的技能包，含 SKILL.md |
| `prompts/` | Prompt 模板 | 常用提示词模板 |
| `docs/` | 通用文档 | 规范、经验笔记、模板 |

## 资产清单

| 名称 | 类型 | 一句话简介 | 入口 |
| --- | --- | --- | --- |
| （待补充） | 平台 / 脚本 / skill / prompt | … | `platforms/xxx/README.md` |

## 维护规范

- 新增资产：放入对应目录，并编写 README（参考 `docs/README-TEMPLATE.md`）。
- 密钥与敏感信息一律不入库，一律使用环境变量。
- 代码更新时同步更新对应 README。
