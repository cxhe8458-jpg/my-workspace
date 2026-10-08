# platforms

## 数据审核归档平台
产品数据人工审核与归档 Web 平台：结构化呈现参数/图片/PDF/网页源文件，原始页面并排对比，快捷键审核，归档台账与备份回滚。
- 入口：`数据审核归档平台/`（含 app/、static/、tools/、deploy/）
- 文档：`使用说明.md`、`内网部署说明.md`、`CLAUDE.md`

## 自动化数据处理平台（产品清洗映射）
映射→分类+清洗→复查→id映射 一站式平台：12 大类/160 子类映射、AI 研判兜底、人工复核闭环、按公司增量处理、统计导出。
- 入口：`数据处理平台/`（backend/ 27 个模块 + config/ 16 份配置 + frontend/ + skills/ + 报告模板/）
- 文档：`README.md`、`DEPLOY.md`、`AUDIT_2026-07-28.md`
- `data/` 仅保留配置类文件（`id_aliases.json`、`task_models.json`），运行产物不入库

## 公司信息采集平台
厂家基础信息批量抓取：官网首页/关于/联系页 → DeepSeek 解析 25 字段+中英互译 → 人工审核 → MySQL 只增写库。
- 入口：`公司信息采集平台/`（script.py + server/）
- 文档：`README_server.md`