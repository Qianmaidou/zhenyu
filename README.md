# 针语 · AI 赋能湘绣文化数字化平台（MVP）

> 描述想法 → 得到一张带合规标识的「湘绣成品效果示意」→ 拿到**有规则依据**的针法建议 → 顺着建议追问知识库 → 发布作品赚积分。
> 一句原则：**AI 只做特征识别，不发明针法；专业内容默认标注「待湘绣顾问确认」。**

本地已跑通：<http://127.0.0.1:8130>

---

## 1. 架构与技术选型

```
┌───────────────────────────────────────────────────────────┐
│ 浏览器（移动优先，原生 HTML/CSS/JS，无构建步骤）              │
│  首页 / 生成 / 针法库 / 知识问答 / 社区 / 我的 / 后台          │
└───────────────┬───────────────────────────────────────────┘
                │  /api/*（统一响应体 {code,message,data}）
┌───────────────▼───────────────────────────────────────────┐
│ FastAPI（单进程）                                          │
│   routers/  auth · generate · stitches · knowledge          │
│             community · points · admin · meta               │
│   services/ image_gen · watermark · stitch_rules            │
│             retrieval · points                             │
│   ingest/   fetcher · sources · pipeline（采集与增量）      │
├───────────────────────────────────────────────────────────┤
│ SQLAlchemy 2.0 → SQLite（WAL + FTS5）  可切 Postgres        │
│ 本地文件存储 data/storage/<生成记录号>/result.png(.meta.json)│
└───────────────────────────────────────────────────────────┘
```

| 层 | 选择 | 理由 | 备选与代价 |
|---|---|---|---|
| 后端 | **FastAPI + SQLAlchemy 2.0** | 类型提示即接口文档，异步天然支持图像接口调用；依赖少、启动快 | Django：重、开发慢；Node：与团队技能栈无关 |
| 数据库 | **SQLite（WAL）→ 可切 Postgres** | MVP 数据量小，单文件零运维，克隆即跑 | Postgres 需额外容器与初始化；已把访问层收在 `app/db/`，切换只改 `DB_URL` |
| 检索 | **SQLite FTS5 + 词项重叠打分**，无 FTS 时降级 LIKE | 48→500 条语料用不上向量库，零额外依赖 | pgvector / 向量服务：留到阶段 3「向量+重排+LLM 组织」时替换 `retrieval.py` |
| 图像生成 | **本地程序化渲染（mock）+ OpenAI 兼容接口（api）双 Provider** | 无密钥也能完整演示；接真实模型只改环境变量 | SDXL+LoRA/ControlNet 需数据集与 GPU，是阶段 2.5 的独立课题 |
| 前端 | **原生三件套** | 无 node_modules、无构建、无供应链风险；移动优先 | Next.js 利于组件复用与 SEO，但会多一层构建与联调成本 |
| 部署 | **单容器 Docker Compose** | 一条命令起完整环境；数据卷持久化 | 编排到云主机时需要补反向代理与 HTTPS |

**为什么不做「AI 自由生成针法」**：评委/用户问「凭什么」时，一个编出来的针法名会立刻摧毁可信度。所以针法来自数据表，AI 只负责把描述映射成特征。

---

## 2. 数据方案

### 2.1 来源分层

| 层 | 内容 | 许可与合规 |
|---|---|---|
| 本地种子（离线可用） | 7 种针法、12 条「特征→针法」规则、48 条知识问答、10 件示例作品、3 个账号 | 平台整理稿，全部 `review_status=pending_expert` |
| 采集①：维基百科 API | 湘绣/刺绣/非遗等背景资料 | CC BY-SA 4.0，保留来源链接，串行限速 |
| 采集②：Wikimedia Commons | 公有领域 / CC 授权图片 | 只存直链与许可，**不落本地副本** |
| 采集③：克利夫兰艺术博物馆开放数据 | **CC0** 中国织绣类藏品（当前唯一可达源） | CC0 1.0，保留 creditline 与原件页 |

当前实机采集结果：**32 条返回 → 批内去重 6 条 → 入库 26 条 CC0 素材**；再次运行同一命令返回 `unchanged: 26`（零写入）。

### 2.2 去重、清洗与增量（`backend/app/ingest/`）

1. **礼貌抓取**（`fetcher.py`）：声明 UA；每站点读取并遵守 `robots.txt`；串行限速（0.9–1.5s）；429/503 指数退避；**连续失败 2 次即熔断**该数据源（避免不可达域名拖死任务）。
2. **清洗**（`sources.py`）：HTML 标签剥离与实体反转义、空白折叠、超长段落截断、短于 30 字的段落丢弃、按段落切分为「是什么 / 还有哪些要点」两类问句。
3. **三级去重**（`pipeline.py`）：
   - L1 批内：同 `dedupe_key` / `source_id` 只留信息量最大的一条；
   - L2 跨批：数据库唯一键（`crawled_assets.source_id`、知识按 `question`）；
   - L3 增量：`content_hash` 未变则**不写库**，只记 `unchanged`。
4. **失败即降级**：任一接口不可用时命令不报错退出，明确打印 `aborted_reason`，系统继续以种子数据运行。
5. **可追溯**：知识条目带 `sources` 字段（标题+链接+许可），素材带 `page_url` / `license` / `author`；`/about` 页面集中展示全部来源与许可。

### 2.3 数据模型（12 张表）

`users` · `stitches` · `stitch_rules` · `knowledge_entries`（+FTS5 索引表）· `generations` · `generation_images` · `works` · `interactions`（点赞/收藏去重）· `comments` · `point_ledger`（append-only）· `crawled_assets` · `audit_logs`

三条硬约束在模型层落地：
- 专业内容统一 `review_status` + `reviewer` + `review_note`；
- 生成图必须有 `visible_label` 与 `meta_json`，缺一项保存前断言失败；
- 积分流水只追加，余额与流水同事务更新。

---

## 3. 已实现功能清单

**核心闭环（阶段 2 目标）**
- [x] 文字描述 → 生成「湘绣效果示意」图（本地渲染 0.1s，可选外部接口，失败自动回落）
- [x] 特征抽取 + 规则表匹配 → 1–2 条针法建议，附「因为 A 所以推 B」的理由与确认状态
- [x] 未命中任何规则时**不给建议**，而是引导用户补充描述
- [x] 生成结果带显式角标「AI 效果示意 · 非实物绣品 + 生成编号」与隐式 PNG 元数据 + sidecar JSON；保存前有合规断言
- [x] 成本与耗时台账、每日预算熔断、每小时限流

**知识问答（阶段 3 雏形）**
- [x] FTS5 全文检索 + 词项重叠打分，回答附来源
- [x] 低置信度返回兜底话术「知识库暂无该问题的可靠条目…建议咨询传承人」
- [x] 未过顾问确认的条目在回答中自动加免责说明
- [x] 分类浏览、命中情况可展开查看（可解释性）

**账号与权限**
- [x] 邮箱注册/登录、PBKDF2 密码哈希、HMAC-SHA256 JWT、`user/reviewer/admin` 三角色

**社区与积分（阶段 5 雏形）**
- [x] 作品列表/筛选/搜索、发布、点赞与收藏（游客用设备键，登录用用户键，同一目标只计一次）
- [x] 评论、浏览计数
- [x] 积分：注册 +50、签到 +5、发布 +15、互动 +1~2、生成返还 +2；兑换额度 10 分/次
- [x] 每日上限防刷 + 幂等（同一 ref 只发一次）+ 流水可查、余额一致

**后台最小页**
- [x] 总览（用户数、今日生成/失败/成本/平均耗时、待确认计数）
- [x] 待顾问确认队列（知识/针法/规则/作品/评论五类）+ 一键确认/驳回
- [x] 生成台账、审计留痕

**公开数据**
- [x] 三源采集管线、robots 校验、限速重试、连续失败熔断、三级去重、哈希增量
- [x] 首页展示 CC0 素材并标注作者/许可/原件链接

**工程**
- [x] 统一响应体与 9 个业务错误码、全局异常处理、参数校验
- [x] 端到端冒烟测试 **48 项**（`backend/tests/test_smoke.py`）
- [x] 幂等种子装载、`reset`/`check` 运维命令、Dockerfile + Compose

**明确未做**：支付/交易/供应链、真实 LoRA 训练、小程序、多语言、推荐算法、真实向量检索、完整后台 CRUD。

---

## 4. 启动与运行步骤

### 4.1 本地（推荐，无需 Docker）

```bash
cd zhenyu/backend

# 1) 依赖（Python 3.11+；本项目在 3.13 上验证）
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2) 建表 + 装载种子数据（幂等，可重复执行）
python run.py init

# 3) 可选：采集公开数据（接口不可达会自动跳过，不影响演示）
python run.py crawl --source all --limit 8

# 4) 启动
python run.py serve --port 8130
# 浏览器打开 http://127.0.0.1:8130
```

一条命令搞定：`python run.py all`（init → crawl → serve）。

### 4.2 Docker

```bash
cd zhenyu
docker compose up --build      # http://127.0.0.1:8130
```

### 4.3 常用命令

| 命令 | 作用 |
|---|---|
| `python run.py init` | 建表 + 装载种子（幂等） |
| `python run.py crawl --source cleveland` | 只采集 CC0 素材 |
| `python run.py check` | 数据量与**合规自检**（逐张图核对隐式标识） |
| `python run.py reset` | 清库重建（慎用） |
| `python tests/test_smoke.py` | 端到端冒烟测试（需服务已启动） |

### 4.4 示例账号

| 邮箱 | 密码 | 角色 |
|---|---|---|
| `demo@zhenyu.dev` | `zhenyu123` | 普通用户 |
| `reviewer@zhenyu.dev` | `zhenyu123` | 顾问/审核 |
| `admin@zhenyu.dev` | `zhenyu123` | 管理员 |

### 4.5 切换到真实图像模型

```bash
export IMAGE_PROVIDER=api
export IMAGE_API_URL=https://your-endpoint.example.com     # OpenAI 兼容
export IMAGE_API_KEY=sk-xxx
export IMAGE_API_MODEL=sd-xl
```
上游超时/报错会自动回落到本地渲染并在 `provider` 字段标为 `api-fallback`，不会让用户看到空白。

### 4.6 关键接口

```
POST /api/auth/register|login        GET /api/auth/me
POST /api/generate                   GET  /api/generate/{public_id}
GET  /api/stitches[/{code}]          POST /api/stitches/preview-rules
POST /api/knowledge/ask              GET  /api/knowledge/categories
GET  /api/works                      POST /api/works/{id}/toggle
GET  /api/points/ledger              POST /api/points/checkin
GET  /api/admin/overview|pending     POST /api/admin/review
GET  /api/meta/stats|sources|gallery
```

---

## 5. 后续迭代建议

**P0（下周就能做，直接决定可信度）**
1. **接入湘绣顾问**：把 `review_status` 从占位变成真实流程——顾问登录后台批量确认，前台立即显示「已确认」并署名。这是本项目唯一的护城河。
2. **知识库扩容到 300–500 条**并抽测 50 条，建立「无严重事实错误」的验收口径。
3. **规则表与针法解耦**：规则改为后台可编辑 + 生效版本号，避免每次调整都改代码。

**P1（体验与效果）**
4. 图像生成替换为 SDXL + LoRA（湘绣数据集 ≥200 张、双盲测试 ≥3/5 才上线，否则维持 API 方案）。
5. 检索升级为「向量召回 + 重排 + 大模型组织」，回答做句子级引用高亮。
6. 用户上传图案（含版权声明勾选 + 相似度查重）。

**P2（增长与商业化前置）**
7. 内测埋点与报告：生成成功率、P95 耗时、「像湘绣」占比、针法采纳率。
8. 社区冷启动：本周针法打卡、创作者标识、内容审核队列。
9. 交易与供应链方案（材料包/定制/成品），**只出方案不接支付**。

---

## 6. 合规与边界

- 不自研基座模型；不采购付费云资源（当前零依赖、零外呼即可运行）。
- 生成内容双标识：图面中文角标 + 文件内 `AI-Provider-Code` / `AI-Record-ID` / `AI-Prompt` 等隐式字段，附 `.meta.json` 便于核对。
- 专业内容一律 `pending_expert` 起步，AI 不产出未经规则表匹配的针法结论。
- 采集遵守 robots.txt 与各站 API 条款，只取元数据，图片仅存直链不落副本。
- 未接入支付、交易与供应链。

## 7. 已知限制

| 限制 | 影响 | 说明 |
|---|---|---|
| 默认生成器为程序化渲染 | 效果图是「绣面质感示意」而非可绣的纹样 | 用于打通链路与演示，不宣称艺术效果 |
| 维基/Commons 在当前网络不可达 | 文本类采集返回 0 条 | 已熔断降级，种子数据兜底；换网络后命令即可生效 |
| 所有专业内容未经顾问确认 | 不能作为专业依据 | 前台逐处标注，后台提供确认流程 |
| 积分与社区无行为风控 | 可被脚本刷分 | 仅有每日上限与幂等，生产前需补设备指纹与限流 |
| 后台无 CRUD 与图表 | 仅够演示审核闭环 | 按需再做，避免过度设计 |

---

## 8. 团队协作

### 8.1 仓库与远端

- 远端：<https://github.com/Qianmaidou/zhenyu>（私有仓库，`main` 为默认分支）
- 克隆：`git clone https://github.com/Qianmaidou/zhenyu.git`
- 提交与推送：默认由成员本人执行（`git add` → `git commit` → `git push`）；命令行已装 `gh`，可用 `gh pr create` 直接开 PR。

### 8.2 首次拉取后跑起来

```bash
cd zhenyu/backend
python -m venv .venv && .venv/Scripts/activate      # Windows；macOS/Linux 用 .venv/bin/activate
pip install -r requirements.txt
python run.py init                                   # 建库 + 装载种子（数据不入库，各自生成）
python run.py serve --port 8130
```

Windows 也可以直接双击仓库根目录的 `start.cmd`：默认以局域网模式启动并自动打开浏览器；只想本机可见时执行 `start.cmd 1`。

### 8.3 分支与评审

- `main` 只接受通过评审的改动；日常开发从 `main` 切出 `feat/xxx`、`fix/xxx`，完成后开 PR。
- 改动前端样式或文案后本地刷新验证；接口改动需同步更新 `backend/tests/test_smoke.py`。
- 合并前自检：`python run.py check`（数据与合规自检）、`python -m pytest backend/tests`（冒烟测试）。

### 8.4 数据与代码的边界

| 内容 | 是否入仓库 | 说明 |
|---|---|---|
| `backend/app/data/seed_*.json` | 入库 | 种子数据，队友 `run.py init` 即可得到同一份内容 |
| `data/zhenyu.db` | 不入库 | SQLite 单写者，不能跨机共享；每人一份本地库 |
| `data/storage/` | 不入库 | 生成图与元数据等运行产物 |
| `backend/.env` | 不入库 | 本地配置与密钥，仓库只保留 `.env.example` |

### 8.5 局域网演示

`python run.py serve --host 0.0.0.0 --port 8130` 后，同一 Wi-Fi 下的同事访问 `http://<你的局域网IP>:8130/`。
注意：这样会把体验账号与后台页暴露给同网段成员，仅用于内网演示；对外分享请用发布工具或部署到服务器。

