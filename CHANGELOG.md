# CHANGELOG

本文件记录数据建设资产库的每次版本迭代。版本号格式 v主版本.次版本，双周更新时递增次版本号。

---

## v1.1 — 2026-10-15

**双周更新（10 月 15 日）**

### 优化
- `prompts/README.md`：补充完整的产品抓取通用 prompt 模板，明确抓取规范与参数解析要求
- `platforms/数据处理平台/DEPLOY.md`：完善部署文档（+393B）
- `platforms/数据处理平台/报告模板/README.md`：更新报告模板说明（+66B）
- `platforms/数据处理平台/frontend/css/stats2.css`：样式微调（+44B）
- `platforms/数据处理平台/frontend/js/ovid.js`：前端逻辑修正（+8B）
- `platforms/数据审核归档平台/static/js/compare.js`：对比功能优化（+9B）
- `platforms/数据审核归档平台/tools/migrate.py`：迁移脚本微调（+2B）

### 修复
- `platforms/数据处理平台/backend/overview.py`：修正 overview 接口（-9B）
- `platforms/数据处理平台/frontend/js/taxonomy.js`：修正分类树渲染（-5B）
- `platforms/数据处理平台/deploy/deploy_check.bat`：部署检查脚本修正
- `platforms/数据审核归档平台/deploy/deploy_check.bat`：部署检查脚本修正
- `platforms/数据审核归档平台/CLAUDE.md`：更新项目说明

### 新增
- 无新增文件

---

## v1.0 — 2026-10-09

**初始版本：全部工作资产首次入库**

包含：
- 3 个平台：公司信息采集平台、数据处理平台、数据审核归档平台
- 2 个 Skill：company-product-scraper、html-param-extractor
- 论文抓取脚本插件
- 各类 prompt、config 映射表、部署脚本、文档
- 共 168 个文件
