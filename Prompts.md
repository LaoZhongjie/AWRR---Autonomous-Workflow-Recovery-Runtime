好，那我直接给你 **V2：增强版分阶段 Prompt 套件**（已把 Gemini/Grok/DeepSeek 的关键约束融进去，但仍然保持“可复制就开干”的长度）。你按阶段执行：**完成一个阶段的 DoD，再拿下一个 Prompt 给 AI。**

---

# Prompt Pack V2（AWRR：Autonomous Workflow Recovery Runtime）

## 全局硬约束（每个阶段都要贴在开头）

把下面这段**原封不动**贴给任何 AI（每阶段 prompt 开头都包含了）：

**GLOBAL CONSTRAINTS**

1. Python 3.12；禁止“只给伪代码”。必须可运行。
2. 必须用 `@dataclass` 定义核心数据结构（见 Phase0）。禁止随意 dict/print 充当日志。
3. 所有运行过程必须通过 `TraceLogger` 输出 JSONL 轨迹，字段必须一致，后续阶段要复用。
4. 所有 baseline / 策略对比必须 **同一批任务 + 同一故障序列**（可复现：seed=42）。
5. `mock_api` 必须 **无状态**（stateless）；所有系统状态只能在 `WorldState` 中。
6. 必须有预算与死循环防护：token/tool_calls/time 任一超限→ `escalate_human`。
7. 输出必须包含：如何运行（命令）、预期输出示例、关键文件列表。

---

## ✅ Phase 0：骨架 + 数据结构 + 任务生成器 + 结构化日志

### 🎯DoD

* `runner.py` 能跑 20 个任务（由生成器生成）
* `mock_api` 8类故障注入、stateless
* 产生 `traces.jsonl`（结构化、字段固定）
* 输出基础指标（WCR/RR/MTTR）+ budget/loop 防护生效

### Prompt 0（复制给AI）

你是资深 Applied Scientist + Senior Backend Engineer。请实现一个最小可运行的“可恢复工作流执行引擎”AWRR（Python 3.12），并严格遵守 GLOBAL CONSTRAINTS。

## 目标

实现 5-step workflow：

1. get_record
2. policy_check
3. update_record
4. send_message
5. commit
   失败时可 recovery 或 escalate_human。

---

## 必须定义的数据结构（dataclasses）

请创建 `state.py` 并严格按以下字段实现（字段名不可改）：

```python
@dataclass
class WorldState:
    records: dict
    inventory: dict
    audit_log: list

@dataclass
class Budget:
    max_tokens: int
    max_tool_calls: int
    max_time_s: float
    used_tokens: int = 0
    used_tool_calls: int = 0
    start_time: float = 0.0

@dataclass
class StepContext:
    task_id: str
    step_idx: int
    step_name: str
    tool_name: str
    params: dict
    state_hash: str
    budget_remaining: dict  # tokens/tool_calls/time

@dataclass
class StepResult:
    status: str  # "ok" | "error"
    output: dict | None
    error_type: str | None
    error_msg: str | None
    error_trace: str | None
    latency_ms: int
    injected_fault: dict | None

@dataclass
class TraceEvent:
    task_id: str
    step_idx: int
    step_name: str
    tool_name: str
    params: dict
    status: str
    latency_ms: int
    error_type: str | None
    injected_fault: dict | None
    state_hash: str
    budget: dict
    recovery_action: str | None
```

要求：`state_hash` 用 `hashlib.sha256(json.dumps(...,sort_keys=True).encode())` 生成。

---

## 必须实现文件

1. `mock_api.py`（stateless）

* 工具函数（必须同名）：

  * get_record(world_state, record_id)
  * policy_check(world_state, action, context)
  * update_record(world_state, record_id, patch)
  * send_message(world_state, user_id, text)
  * create_ticket(world_state, summary, severity)
  * commit(world_state)
  * rollback(world_state, checkpoint)  # Phase0可做简单快照恢复

* 支持 8 类故障注入（错误类型字符串必须同名）：
  `Timeout, HTTP_500, BadRequest, AuthDenied, NotFound, Conflict, PolicyRejected, StateCorruption`

* 故障注入配置 schema（由 task 提供）：

```json
{"step_idx": 2, "fault_type": "Conflict", "prob": 1.0, "fault_id": "F17"}
```

* 每次注入必须把 `fault_id` 写到 StepResult.injected_fault。

2. `trace_logger.py`

* `TraceLogger.append(event: TraceEvent)`
* `TraceLogger.flush_jsonl(path="traces.jsonl")`

3. `task_generator.py`

* `generate_tasks(n=20, seed=42) -> list[dict]`
* 输出 JSONL：`tasks.jsonl`
* 每个 task 包含：

  * task_id
  * initial_world_state（可序列化）
  * steps（5步固定，params可变）
  * fault_injections（覆盖8类，分布尽量均匀）
  * success_condition（字符串或lambda描述均可，但必须可被 oracle_checker 执行）

4. `oracle_checker.py`

* `check_success(world_state, task) -> bool`

5. `runner.py`

* 执行 steps，生成 checkpoint（简单 deep copy）
* recovery 最简策略：

  * Timeout/HTTP_500 → retry <=3（指数退避 100/200/400ms）
  * Conflict → rollback checkpoint → retry
  * PolicyRejected/AuthDenied → create_ticket + stop（escalate）
* 预算与死循环：

  * BudgetTracker：token估算可用 `len(json.dumps(prompt))/4` 或固定每步 200 token 估算（MVP允许估算）
  * loop detection：连续 3 次失败且 `state_hash` 不变 → escalate

6. `metrics.py`
   输出：WCR、RR、MTTR（从 traces 计算），打印为 pandas DataFrame。

---

## 输出要求

* 给出完整可运行代码（所有文件）
* 给出运行命令：

  * `python task_generator.py`
  * `python runner.py --tasks tasks.jsonl --out traces.jsonl`
  * `python metrics.py --traces traces.jsonl`
* 给出一次运行的示例输出（表格样例）

完成后我会进入 Phase 1。

---

---

## ✅ Phase 1：Baseline（B0–B2）+ 公平对比 + 结果落盘

### 🎯DoD

* 同一批 tasks + 同一 fault schedule
* 跑 B0/B1/B2
* 输出 leaderboard（WCR/RR/MTTR/RCO/HIR/UAR）

### Prompt 1（复制给AI）

你是 LLM Agent Reliability 研究员。基于我 Phase0 的代码（保持数据结构与 TraceEvent schema 不变），实现 baseline 对比并确保完全可复现（seed=42，任务与故障序列一致），严格遵守 GLOBAL CONSTRAINTS。

## 需要实现

1. `baselines.py`

* `run(tasks_path, mode="B0|B1|B2", seed=42) -> traces_path`
* B0 No-Recovery：任何 error 立即失败
* B1 Naive-Retry：任何 error retry<=3（不区分类型）
* B2 Rule-Based：

  * Timeout/HTTP_500 → retry+backoff
  * Conflict → rollback_then_retry
  * PolicyRejected/AuthDenied → escalate_human(create_ticket)

2. `metrics.py` 扩展指标（从 traces.jsonl 计算）

* RCO：恢复额外成本（额外 tool_calls 或 token 之比）
* HIR：触发 create_ticket 的比例
* UAR：Unauthorized Action Rate（policy_check/AuthDenied 触发次数 / 总任务）

3. `leaderboard.py`
   输出表（pandas/markdown 都可）：
   | Baseline | WCR | RR | MTTR | RCO | HIR | UAR |

## 实验要求

* 至少 50 个任务（你可以扩展 task_generator：n=50）
* 运行三次 baseline，产出三份 traces（或一个文件里加 baseline 字段也行，但字段要清晰）
* 输出简短对比分析（B2 相比 B1 提升多少 & 代价多少）

完成后进入 Phase 2。

---

---

## ✅ Phase 2：Diagnosis Agent（prompts.py + strict JSON）+ RCA评估

### 🎯DoD

* `prompts.py` 定义系统提示词（含2个few-shot）
* DiagnosisAgent 支持 Mock 模式和 LLM 模式
* B3 跑通，输出 RCA Accuracy + 混淆矩阵

### Prompt 2（复制给AI）

你是顶级 Applied Scientist。请在 Phase1 的基础上加入 Diagnosis Agent，使“故障分类与恢复动作”由模型参与决策，并可评估 RCA Accuracy。严格遵守 GLOBAL CONSTRAINTS。

## 必须新增文件

1. `prompts.py`

* 定义 Diagnosis Agent System Prompt，角色=Senior SRE
* 任务：根据 error_trace + last_actions + state_hash 判断 fault layer：
  transient/persistent/semantic/cascade
* 输出：Strict JSON（必须能被 json.loads）
* 内置 2 个 one-shot 示例：

  * 一个 transient→retry
  * 一个 persistent/semantic→escalate

2. `diagnosis.py`

* `DiagnosisAgent(mode="mock"|"llm")`
* `diagnose(step_context, step_result, history_events) -> dict`
  返回 JSON：

```json
{"layer":"transient|persistent|semantic|cascade","action":"retry|rollback|compensate|escalate","confidence":0.0-1.0}
```

* Mock 模式：关键词规则（确保流程跑通）
* LLM 模式：预留接口（不要求真实key，但结构要完整）

3. Ground truth 记录方式（必须实现其一）

* 方式A（推荐）：每次 fault 注入时，StepResult.injected_fault 里包含 `fault_type` & `layer_gt`（你定义映射）
* 方式B：oracle 文件维护 mapping

4. `baselines.py` 增加 B3

* B3 Diagnosis-driven：

  * 先诊断，再按 action 执行
  * 如果 confidence < 0.7 → escalate（保守安全）

5. `rca_eval.py`

* 输出 RCA Accuracy（layer 分类准确率）
* 输出混淆矩阵（pandas crosstab）

## 实验要求

* 用同一批任务对比：B2 vs B3
* 输出表：
  | Strategy | RR | MTTR | RCO | RCA_Acc |
* 给出 5 条失败案例的诊断输出（从 traces 中抽样）

完成后进入 Phase 3。

---

---

## ✅ Phase 3：Saga Pattern（Undo Log + compensate failure handling）+ SRR

### 🎯DoD

* `saga.py` 实现 undo log
* 经典库存锁定案例跑通
* SRR（Safe Rollback Rate）> 95%（模拟任务上）

### Prompt 3（复制给AI）

你是分布式系统专家。请在现有 engine 中实现 Saga Pattern（补偿事务）并给出可评估的 SRR。严格遵守 GLOBAL CONSTRAINTS。

## 必须新增/修改

1. `saga.py`

* `TransactionStack.push(compensate_fn, args)`
* `SagaManager.rollback_saga(world_state)`：逆序执行补偿
* compensate 失败处理：记录 critical event → create_ticket → 停止

2. `mock_api.py` 增加工具（必须）

* lock_inventory(world_state, item_id, qty)
* unlock_inventory(world_state, item_id, qty)  # compensate
* process_payment(world_state, order_id, amount)
* refund_payment(world_state, order_id, amount)  # compensate（可模拟）

3. 在 runner 中引入 ToolSpec

* 每个 tool 有 do/compensate/irreversible 标记
* rollback 时：优先 saga 补偿，再恢复 checkpoint（或相反，但要说明）

4. 新指标 `SRR`

* SRR = 需要补偿的任务中，最终 world_state 通过一致性检查的比例
* 一致性检查：inventory 数量恢复 + records 未出现 orphaned 状态

## 实验要求

* 至少 50 个含库存/支付故障的任务
* 输出：
  | Strategy | SRR | RR | MTTR | RCO |
  对比：无 Saga vs 有 Saga

完成后进入 Phase 4。

---

---

## ✅ Phase 4：Learning Loop（Memory Bank / kNN）+ 省钱与收敛

### 🎯DoD

* Memory Bank 命中能 bypass LLM 诊断
* 输出 Learning Efficiency + Preventive Win Rate
* B4（Diagnosis+Learning）跑通

### Prompt 4（复制给AI）

你是 Agent Learning 研究员。请实现一个轻量“从历史恢复轨迹中学习”的 Memory Bank（先 kNN/规则相似度，不要上复杂RAG），并展示成本降低与收敛效果。严格遵守 GLOBAL CONSTRAINTS。

## 必须新增

1. `learning.py`

* `FaultSignature`：从 TraceEvent 抽取特征：

  * tool_name + error_type + step_name + topK_error_keywords + state_hash_prefix
* `MemoryBank.upsert(signature, best_action)`
* `MemoryBank.query(signature) -> (action, confidence)`
* 流程：在调用 DiagnosisAgent 之前先查 MemoryBank：

  * 若 confidence>=0.8 → bypass LLM（直接用历史最佳 action）
  * 否则走 DiagnosisAgent

2. `baselines.py` 增加 B4

* B4 = Diagnosis + MemoryBank

3. 量化指标（必须）

* Learning Efficiency：按 episode 分批（例如 10批×10任务），统计 RR 随批次提升，报告达到 RR>=0.8 的最早批次
* Preventive Win Rate：实现 `predict_potential_failure()`（可简单：若历史表明某参数组合必错，则提前校验并修正），统计避免故障比例
* LLM Call Reduction：B4 相比 B3 的 LLM diagnose 调用次数下降百分比

## 实验输出

* 表：
  | Strategy | RR | MTTR | RCO | LLM_Calls | LLM_Reduction |
* 学习曲线（matplotlib）：episode vs RR
* 5条“bypass LLM”的案例轨迹片段（从 traces 抽）

完成后进入 Phase 5。

---

---

## ✅ Phase 5：WS-Bench + Leaderboard + Tech Report + 可视化 + 开源就绪

### 🎯DoD

* WS-Bench 200–500 任务（多域：电商/医疗/内容）
* 完整 B0–B4 Leaderboard
* 两张图（matplotlib）
* Tech Report 初稿（MLSys风格）
* requirements + LICENSE

### Prompt 5（复制给AI）

你是 MLSys 论文作者 + 开源维护者。请把项目固化为可发布的 benchmark + report。严格遵守 GLOBAL CONSTRAINTS。

## 必须产出

1. `benchmark.md`

* WS-Bench 任务格式规范（tasks.jsonl schema）
* 多域比例建议：40%电商、30%医疗、30%内容
* 故障分类（4层 + 8类）与注入规范

2. `leaderboard.md`

* B0–B4 指标汇总表 + 简短结论（回答 RQ1–RQ3）

3. `plot_results.py`（matplotlib，禁止 seaborn）

* 图1：WCR vs RCO 散点（B0–B4）
* 图2：Learning curve（episode vs RR 或 cumulative failures）

4. `tech_report.md`（4–6页结构化草稿）

* Introduction（问题 + RQ）
* System Design（状态机/诊断/saga/学习）
* WS-Bench（数据集）
* Experiments（B0–B4 + ablation）
* Limitations（成本、模拟差距）
* Conclusion

5. 开源就绪

* `requirements.txt`
* `LICENSE`（MIT）

6. 简历 bullet（基于你跑出来的真实数字，留占位符 xx/yy 也可以）

---

# 你接下来怎么用（最省心的方式）

* 你现在把 **Prompt 0** 丢给任意一个强模型，让它直接产出 repo 代码骨架
* 你跑通后，把 repo 贴回同一个模型，再给 **Prompt 1**
* 以此类推
