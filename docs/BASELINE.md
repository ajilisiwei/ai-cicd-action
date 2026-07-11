# ai-cicd-action — 设计基线与实施计划

> 把一套「AI 驱动的 CI/CD 流水线」（PR Review / Auto-Fix / Release Notes / Issue Triage /
> Security Scan / Test Suggestion / Implement Issue）从单个项目（t-cli）中抽取为**可复用、可一键初始化**的中央能力。
>
> 本仓库是该能力的**单一真相源**：一个 model-agnostic 的执行引擎（composite action）+ 一个 bootstrapper Skill。
>
> 状态：**设计基线（未开工）**。原始实现见 t-cli 仓库 `.github/scripts/ai_agent.py`（1075 行，8 个 workflow，已端到端验证并做过一轮安全加固）。

---

## 0. 目标与非目标

**目标**
- 新项目一条命令（Skill `/init-ai-cicd`）即可落地整套 AI CI/CD。
- 逻辑单一真相源：修一次 bug，所有消费项目通过 `@v1` 升级。
- Model-agnostic：默认 DeepSeek，可切 openai/anthropic/openrouter/ollama/custom。
- **可插拔执行基座**：既支持「直接调云端 LLM API」（轻量、确定性强），也支持「编码 agent（Claude Code / Codex / OpenCode）在 runner 内自主改码」（强能力、可探索多文件）。
- 跨语言：Node/Go/Python/TS 通用；语言差异收敛到「language profile」。

**非目标**
- 不做 GitHub 之外的 CI 平台（GitLab/Gitea）——首版只锁 GitHub Actions。
- 不追求 `implement_issue` 深度门禁在所有语言上等价——按 profile 能力分级降级。

---

## 1. 现状分析：可复用内核 vs 项目专属耦合

通读原始 `ai_agent.py` 后，把代码劈成两层。抽取的**全部难度**在于第二层散落于每个 action 的硬编码。

| 层 | 内容 | 处理 |
|---|---|---|
| **可复用内核（项目无关）** | provider 路由表、`INJECTION_GUARD`/`_fenced`、token 计费、`_notice`/`_fail`「无操作 vs 失败」约定、auto-fix 防循环护栏、PR 幂等（REST PATCH）、`@coder` 鉴权白名单、action pin SHA、8 个 action 骨架 | 直接抽走，进 engine |
| **t-cli 专属（散落硬编码）** | 每个 system prompt 的 `"t-cli, a Node.js CLI translator... Ink"`、`npm test`、`node --check`、`.js` 后缀、`src/` 布局、`_scan_exports`/`_validate_imports`（纯 JS 语法）、package.json 解析 | 收敛到 `ai-cicd.yml` + language profile |

`implement_issue` 的深度门禁（import 幻觉检测、丢失导出检测）是 JS-only 且难泛化 → 决定了必须有**语言适配层**（见 §4）。

---

## 2. 分发架构（已定：方案 C — 混合）

评估过三种，选 C。

| 方案 | 形态 | 结论 |
|---|---|---|
| A. 纯拷贝 | Skill 复制 `ai_agent.py`+workflows 进每个项目 | N 份副本漂移，弃为默认；**保留为 `--mode=vendor` 退路** |
| B. 纯引用 | 全部走 reusable workflow (`workflow_call`) | `workflow_run` / private repo 触发器组合受限，弃 |
| **C. 混合（选定）** | **引擎=composite action** + **薄 workflow 留项目** + **config 文件参数化** | 逻辑单一真相源 + 触发器/权限项目自主 |

```
ajilisiwei/ai-cicd-action  (private, 本仓库 = 单一真相源)
├── action.yml                     # composite action 入口: with { action, engine, ... }
├── engine/
│   ├── ai_agent.py                # 去 t-cli 化的执行引擎（读 ai-cicd.yml）
│   ├── executors/                 # 可插拔基座: api / claude-code / codex / opencode
│   └── profiles/                  # 语言 profile: node / python / go / generic
├── skill/                         # bootstrapper skill（可被 ~/.claude/skills 引用或分发）
│   ├── SKILL.md
│   ├── templates/*.yml.tmpl       # 薄 workflow 模板
│   └── references/pitfalls.md     # 运维踩坑总表（从 t-cli 沉淀）
└── docs/BASELINE.md               # 本文件

消费项目/.github/
├── ai-cicd.yml                    # Skill 生成的项目清单（语言/命令/描述/引擎选择）
└── workflows/*.yml                # Skill 生成的薄触发器，每步 uses: ajilisiwei/ai-cicd-action@v1
```

- **Composite action** 优于 reusable workflow：任何触发器下可用（含 auto-fix 的 `workflow_run`），并把 `setup-python`/`pip install`/agent CLI 安装藏进 action。
- **薄 workflow 留项目**：触发器、permissions、secrets 本就是项目策略，允许项目自主。
- **private 仓库跨仓 `uses:` 注意**：消费项目需与本仓同属 `ajilisiwei` 或配 PAT（`repo` scope）访问。个人项目跨账号时，用 `--mode=vendor` 退回自包含拷贝。

---

## 3. 参数化核心：`ai-cicd.yml`

所有硬编码收敛到一个项目清单，引擎从它读上下文，代码里不再有任何 `"t-cli..."` 字面量。

```yaml
# .github/ai-cicd.yml  (Skill 生成，人可改)
project:
  name: t-cli
  description: "Node.js CLI translator using DeepSeek API, Ink (React TUI)"
  conventions_file: CLAUDE.md          # 注入 codegen 的架构约束
language: node                         # → 选 language profile
commands:
  test:  "npm test"
  build: ""
  syntax_check: "node --check {file}"
  audit: "npm audit --json"
layout:
  source_dir: src
  test_dir: test
  file_ext: [".js"]
providers:
  default: deepseek                    # 覆盖用 GitHub Variables LLM_PROVIDER/LLM_MODEL
engine:
  backend: api                         # api | claude-code | codex | opencode （见 §6）
  max_turns: 30                        # agent backend 的预算护栏
  escalate_on_api_failure: true        # api 单发失败 → 升级到 agent backend
actions:                               # 按项目开关
  pr_review: true
  test_suggestion: true
  security_triage: true
  issue_triage: true
  auto_fix: true
  implement_issue: true
  changelog: true
```

`call_llm` 的 system prompt 全部模板化：`f"You are working on {name}, {description}. Tests run via {test_cmd}..."`。

### 3.1 分层定制模型（消费项目直接引用 action 时如何定制）✅

「直接 `uses: @v1`」不锁死项目个性。定制分层落在**消费项目自己的文件**里,90% 不碰中央仓库：

| 定制类型 | 落点（在消费项目内） | 说明 |
|---|---|---|
| 项目身份 / 命令 / 布局 / 功能开关 | `.github/ai-cicd.yml` | name/description/commands/layout/`actions:` |
| 架构约束 / 框架规则（注入 codegen） | `conventions_file`（CLAUDE.md） | 例：「Ink owns stdin」 |
| 触发 / 权限 / 编排 / 前后步骤 | `.github/workflows/*.yml` | action 只是一个 step,周围全是项目策略 |
| **单个 action 的 prompt 措辞** | `ai-cicd.yml` 的 `prompts:` 块 | `extra`（追加）/ `system`（整替）|
| 模型 / 密钥 | GitHub Variables / Secrets | provider/model、API key |
| 引擎版本稳定性 | `uses:` 的 ref | `@v1` / `@vX.Y.Z` / `@<sha>` |
| 项目专属全新 AI 逻辑 | 自定义 step / `--mode=vendor` / fork+`uses: ./local` | 逃生舱 |

**`prompts:` 扩展点（已交付）**：按 AI_ACTION 名 keying,`extra` 追加到默认 system prompt、`system` 整段替换（`implement_issue` 多 prompt,仅支持 `extra`,套到 plan/codegen/diff 三处）。**`INJECTION_GUARD` 恒定最后追加,项目覆盖无法关闭注入防御**（已测试验证）。向后兼容:无 `prompts:` 配置则默认 prompt 原样不变。

---

## 4. 语言适配层（language profiles）

`implement_issue` 门禁**按语言能力分级降级**，不强求全语言等价。

| 门禁 | node | python | go | generic 兜底 |
|---|---|---|---|---|
| 占位符/截断检测 | ✅ | ✅ | ✅ | ✅（语言无关）|
| 语法检查 | `node --check` | `python -m py_compile` | `go build ./...` | 跳过 |
| import 幻觉检测 | ✅ `_validate_imports` | 待写（AST）| 待写（`go vet`）| 跳过 |
| 丢失导出检测 | ✅ | 待写 | 待写 | 跳过 |
| 测试门禁 | `npm test` | `pytest` | `go test ./...` | 必须有 `test` 命令 |

**落地顺序**：先 **node profile（=现有代码，零风险平移）+ generic 兜底（占位符+语法+测试三件套）**。这样 6 个 action（review / changelog / auto_fix / security_triage / issue_triage / test_suggestion）立即全语言可用；`implement_issue` 深度门禁按 profile 逐步补齐，不阻塞首版。

---

## 5. Bootstrapper Skill

`/init-ai-cicd` 触发后：

1. **探测项目**：语言、包管理器、test/build 命令、源码布局（读 package.json / go.mod / pyproject.toml）→ 生成 `ai-cicd.yml` 草稿待确认。
2. **落盘 workflows**：渲染薄触发器（默认 `uses: ajilisiwei/ai-cicd-action@v1`；`--mode=vendor` 则拷贝 engine）。
3. **前置条件清单**（不自动改敏感配置，仅输出待办）：所需 secrets（`DEEPSEEK_API_KEY`、`GH_PAT`，agent 基座另需 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`）、Variables（`LLM_PROVIDER/MODEL`）、`main` 为默认分支约束、PAT scope。
4. **自检**：`actionlint` 校验 YAML + `workflow_dispatch` 干跑一次 `review` 验证接线。
5. **幂等**：可重复运行，已存在文件走 diff 更新。

内置 `references/pitfalls.md`（见附录）——两天踩坑换来的资产，新项目直接绕坑。

---

## 6. 编码 agent 作为修复基座（核心新增分析）

> 需求：CI/CD 的自动修复 / bug 修复，除了「代码调用云端大模型」，还要能用 **Claude Code / Codex CLI / OpenCode** 这类**编码 agent** 作为执行基座。

### 6.1 两种范式对比

| 维度 | **API 单发（现状）** | **Agent 基座（新增）** |
|---|---|---|
| 机制 | 我们手工喂 diff+日志+扫描出的导出 → 模型一次生成 diff/整文件 → `git apply` → 门禁 | agent 在 runner 内自主 read/edit/run test/迭代，直到绿或超预算 |
| 上下文获取 | 手工重建（`_scan_exports`、注入 API 参考、截断到 40k）| agent 自己探索仓库，无需我们喂 |
| 多文件/需探索的 bug | 弱（单发看不全）| 强（原生多轮工具调用）|
| diff 应用 | 脆（自建 3 套 `git apply` 回退）| agent 直接改工作树，无需 apply |
| 自纠错 | 我们外挂「一轮修复」| 原生 loop 内自纠 |
| 成本/确定性 | 低 token、确定性强、快 | 多轮 token 高、非确定、慢 |
| 攻击面 | 小（受限单次调用）| **大**（自主执行 + 仓库写 + 网络 + 密钥）|

结论：**不是二选一，是分层路由**。轻量修复走 API；难 bug / 多文件 / `implement_issue` 走 agent。现有的验证门禁（§4）**保持基座无关**——无论谁改的码，都过同一道门禁，这是信任边界不塌的关键。

### 6.2 Executor 抽象

引擎定义统一协议，基座可插拔；门禁在结果工作树上运行，与基座解耦：

```python
class Executor(Protocol):
    def run(self, task: Task, repo_dir: str) -> ExecResult: ...
    # 约定：run() 结束后，需要的改动已落在工作树；引擎随后跑 language-profile 门禁 + commit/PR

# backends:
#   ApiExecutor        —— 现有逻辑（plan → gen diff/file → git apply → verify）
#   ClaudeCodeExecutor —— headless 调 `claude -p`，让其改仓库
#   CodexExecutor      —— `codex exec`（非交互，带沙箱）
#   OpenCodeExecutor   —— `opencode run`（provider-agnostic，可续用 DeepSeek）
```

`engine.backend` 在 `ai-cicd.yml` 选择；composite action 据此安装对应 CLI。

### 6.3 三个 agent 基座的落地要点

**Claude Code（旗舰质量，官方 Action 现成）**
- headless：`claude -p "<task>" --output-format stream-json`；非交互权限用 `--permission-mode` / 工具 allowlist（**遵守个人规则：不用 `--dangerously-skip-permissions`**，改用 scoped allowlist；runner 为 ephemeral 容器，风险可控但仍最小授权）。
- 认证：CI 用 `ANTHROPIC_API_KEY`，或订阅 `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` 生成存 secret）。
- 快路：官方 `anthropics/claude-code-action` 已封装 `@claude` 触发、PR 创建等，Claude 基座可直接复用，省大量管线。

**Codex CLI（OpenAI，自带沙箱）**
- headless：`codex exec "<task>"`（非交互）；`--sandbox` seatbelt/landlock 限制文件与网络，安全属性最好。
- 认证：`OPENAI_API_KEY` 或 ChatGPT 登录态。

**OpenCode（provider-agnostic，保 DeepSeek 默认）**
- headless：`opencode run "<task>"`；模型任选（含 deepseek），与现有 model-agnostic 定位一致，成本可控。
- 认证：对应 provider key。

> 备选可后续评估：Aider（`aider --yes --message`，git 原生、脚本友好）。

### 6.4 安全模型（agent 基座是最大风险面，必须前置）

自主 agent 在 CI 里拥有仓库写 + 网络 + 密钥，是远大于单次 API 调用的攻击面。issue/PR body 的 prompt injection 可能诱导 agent 外泄 secret 或推恶意码。铁律：

1. **权责分离**：agent 步骤**只生成改动，不持有推送凭证**。push/PR 由后续受控步骤用 `GH_PAT` 执行。agent 步骤只给模型 key。
2. **门禁是唯一信任闸**：无论哪个基座，产出一律过 §4 门禁（语法/import/导出/占位/测试）+ PR 正文如实标注 `verification PASSED/FAILED`。
3. **注入隔离照旧**：不可信输入（issue/PR/日志）仍走 `_fenced` + `INJECTION_GUARD`；给 agent 的任务描述也套围栏。
4. **最小工具面**：Claude Code 用工具 allowlist（禁 `curl`/任意 `Bash` 外联）；优先 Codex 沙箱 / runner 网络出口限制。
5. **预算护栏**：`max_turns` + `timeout-minutes`，防 agent 空转烧钱。
6. **ephemeral runner**：每次全新容器，跑完即毁。

### 6.5 路由策略

```
修复任务
  ├─ auto_fix（CI 失败，多为小改）      → 先 ApiExecutor 单发；失败且 escalate=true → agent 基座
  ├─ implement_issue（多文件/需探索）   → 直接 agent 基座（若配置），否则回退 Api
  └─ engine.backend 显式指定           → 强制该基座
```

---

## 7. 阶段拆分（Roadmap）

```
P0 抽内核    P1 引擎化    P2 Composite   P3 Skill      P4 语言profile  P5 Agent基座   P6 硬化    P7 推广
┌────────┐  ┌────────┐  ┌──────────┐  ┌────────┐  ┌──────────┐  ┌──────────┐ ┌──────┐ ┌──────┐
│去t-cli │→ │ai-cicd │→ │action.yml│→ │/init-  │→ │node平移+ │→ │Executor  │→│注入/ │→│真实新 │
│化+回归 │  │.yml读取│  │+ @v1 tag │  │ai-cicd │  │generic   │  │抽象+CC/  │ │沙箱/ │ │项目端 │
│        │  │        │  │          │  │        │  │兜底      │  │Codex/OC  │ │预算  │ │到端  │
└────────┘  └────────┘  └──────────┘  └────────┘  └──────────┘  └──────────┘ └──────┘ └──────┘
```

- **P0** 去 t-cli 化：`ai_agent.py` 所有硬编码 → 读 `ai-cicd.yml`；在 t-cli 自身回归（t-cli 成为本 skill 首个消费者，dogfooding）。
- **P1** 引擎化：profiles 目录 + generic 兜底，抽出 `call_llm` 模板。
- **P2** Composite action + 私有仓 + `@v1` tag（dependabot 可跟）。
- **P3** Skill：探测 + 模板渲染 + 前置清单 + `actionlint`/dispatch 自检 + 幂等。
- **P4** node profile 平移 + generic 兜底（6 action 全语言可用）。
- **P5** Agent 基座：`Executor` 抽象 → 先 **Claude Code**（质量 + 官方 action）→ **OpenCode**（保 DeepSeek）→ **Codex**（沙箱）。路由 + 权责分离。
- **P6** 安全硬化：注入隔离全覆盖、沙箱/网络出口、预算护栏、密钥最小化。
- **P7** 拿一个真实新项目端到端验证「一键初始化」+ Go/Python 深度门禁增量。

---

## 8. 决策点与待办

| 问题 | 现状/建议 |
|---|---|
| 分发模式 | **C（composite action + 薄 workflow + config）**；`--mode=vendor` 作退路 |
| 中央仓可见性 | private（`ajilisiwei/ai-cicd-action`）；跨账号消费用 vendor |
| 首版语言 | node profile + generic 兜底；Go/Python 深度门禁后置（P7）|
| 默认基座 | `api`（确定性/成本）；agent 基座 opt-in，auto_fix 失败可升级 |
| 首个 agent 基座 | **Claude Code**（官方 action + 质量），次 OpenCode（保 DeepSeek），再 Codex（沙箱）|
| agent 推送权 | **不给** agent，权责分离；push 由受控步骤持 `GH_PAT` |

---

## 附录 A：运维踩坑总表（从 t-cli 沉淀，Skill 内置为 references/pitfalls.md）

| 类别 | 坑 | 结论 |
|---|---|---|
| 分支 | `issue_comment` / `workflow_run` 只用**默认分支**的 workflow 文件 | 这两类改动必须同步到 `main` 才生效 |
| 认证 | `gh auth login --with-token` 与已设 `GH_TOKEN` env 冲突 | 别用 auth step，`gh` 原生读 `GH_TOKEN` |
| 认证 | `gh pr list/edit` 走 GraphQL 需 `read:org` | 只有 `repo` scope 时改用 REST `gh api` |
| 推送 | `github.token` 推送不触发下游 CI（防递归）| 闭环需 `GH_PAT` |
| 触发 | 机器人评论含触发词 → 自触发循环 | 机器人输出**绝不含**触发词，`if` 排除机器人评论 |
| 并发 | `concurrency` 在 job `if` 前求值 | 会被 skipped run 取消合法 run 时，用 `cancel-in-progress: false` |
| 安全 | `${{ }}` 插值进 `run:` = 注入点 | 一律走 `env:` + `"$VAR"` |
| 数据 | `npm audit --json ... 2>&1` 污染 JSON | 用 `2>/dev/null` |
| 盲区 | 单测不覆盖大文件 → CI 假绿 | AI 生成代码必须有独立门禁（语法/import/导出/占位）|
| Agent（新）| 自主 agent 持推送凭证 + 网络 = 大攻击面 | 权责分离（agent 不推送）+ 门禁唯一信任闸 + 沙箱/allowlist |

## 附录 B：原始实现引用

- t-cli `.github/scripts/ai_agent.py`：8 个 action（review/changelog/auto_fix/security_triage/issue_triage/test_suggestion/implement_issue/summary）。
- t-cli `plan-ai-cicd.md`：Phase 1–5 落地记录与端到端验证。
