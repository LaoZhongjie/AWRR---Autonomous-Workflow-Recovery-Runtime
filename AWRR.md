我会修改，而且修改点很明确：**保留你原来“可靠执行引擎 + benchmark”的骨架**（这是核心护城河），但把它从“工程框架”升级成**Applied Scientist 级别的研究系统**，补上 Gemini / Grok / DeepSeek 提到的三块短板：

1. **Research Question（研究问题）**
2. **AI 参与决策（不是纯 if-else）**
3. **学习与改进闭环（越用越聪明）**
   再加一个特别关键的工业点：**Saga/补偿事务**（很多现实操作不可回滚）。

下面是我给你的**修订版最终方案**（你可以直接按这个做）。

---

## 改完后的项目定位

**新标题**：**Autonomous Workflow Recovery Runtime (AWRR)**
**一句话**：在多步工具工作流中，实现“可恢复 + 可审计 + 可学习”的 agent runtime，并给出系统化 benchmark，对比规则/重试/LLM诊断/学习策略的收益与成本。

---

## 明确的 Research Questions（必须写进 tech report）

至少选 2 个作为主 RQ（你可以全做，但主论文要聚焦）：

* **RQ1：LLM 能否在多大程度上替代人工做故障根因分析（RCA）并提升恢复成功率？**
* **RQ2：在固定预算约束下（token/时间/tool calls），最优的“自动恢复 vs 转人工”边界怎么学出来？**
* **RQ3：系统能否从历史失败轨迹中学习，减少未来相似故障的发生（preventive recovery）？**

这三问直接把你从“工程实现”拉到“Applied Scientist 研究系统”。

---

## 关键架构升级（对照三家AI的建议）

你原骨架不动：状态机 + checkpoint + rollback + eval。
只加 3 个模块 + 1 个事务机制：

### A) Diagnosis Agent（LLM 驱动根因分析）

**输入**：error trace + 当前 state + 最近 N 步工具调用 + 预算剩余
**输出**：`failure_type` + `root_cause_hypothesis` + `verification_steps` + `recommended_action` + `confidence`

> 对比实验：Hard-coded rules vs LLM diagnosis（Gemini 强推的升维点）

### B) Saga / Compensating Actions（补偿事务）

把每个 tool 定义成：

* `do()`：执行
* `compensate()`：补偿（撤销/反向操作）
* `commit_point`：是否形成不可逆副作用

例子（Gemini提到的库存锁死场景）：

* `lock_inventory` 的补偿是 `unlock_inventory`
* `send_email` 不可逆 → 需要“补偿邮件/工单”而不是 rollback

> 这一步会让你的项目在面试里极其亮眼：你在做的是分布式事务级别的可靠性。

### C) Policy Learner（轨迹学习/策略进化）

把每次恢复轨迹记录成数据：

* 成功/失败
* 触发的故障模式
* 采取的动作序列
* 成本与耗时
* 是否转人工

然后做两种学习（任选其一先做，建议从 1 开始）：

1. **检索式策略记忆（Case-based memory）**：相似故障直接检索历史最优恢复路径（最快落地）
2. **轻量 Router/Selector**：学一个小模型/规则+LLM混合，选择恢复策略（retry / rollback / compensate / escalate）

> 这回应 DeepSeek 的核心批评：必须有“学习与改进”循环。

### D) 强化的故障注入：层次化 + 级联 + 语义故障

把 fault taxonomy 从 8 类升级为 4 层（DeepSeek点名要这个）：

* **Transient**：timeout、rate_limit（适合 retry/backoff）
* **Persistent**：auth_denied、deprecated（需要改变策略/权限/参数）
* **Semantic**：API 返回成功但业务错误（最难、最有研究价值）
* **Cascade**：partial_write、orphaned_resource（需要 checkpoint + saga）

---

## 必须补齐的 Baselines（DeepSeek点名缺失）

你的 benchmark 里必须有这 4 条 baseline，对比才有说服力：

* **B0：No recovery**（一错全崩）
* **B1：Naive retry**（固定次数重试）
* **B2：Rule-based recovery**（手写 if-else 策略库）
* **B3：LLM Diagnosis recovery**（诊断 agent 决策）
* **B4：LLM + Learning**（带记忆/选择器的策略进化）

只要你跑出 B2/B3/B4 的清晰差距，你就有“研究结果”。

---

## 评估指标升级（保留你的工业指标 + 加研究指标）

原来的工业指标继续保留（WCR/RR/MTTR/RCO/HIR/RBC/LR），再加 4 个研究指标：

* **RCA Accuracy**：根因分析准确率（对照标注或合成真值）
* **Learning Efficiency**：遇到新故障需要多少次失败才能学会稳定处理
* **Preventive Win Rate**：是否能在故障发生前预判并规避（例如提前校验参数/提前释放资源）
* **TCO Reduction**：把人工成本折算进去的总成本下降（转人工成本可以设常数）

---

## 更新后的里程碑（更符合“先跑通再升维”）

### 第 1–2 周（MVP）

* 原先的 mock_api + 5步workflow + 20任务 + 指标表 ✅
* **新增**：至少 1 个 Saga 场景（库存锁定→支付失败→必须解锁）
* **新增**：Baseline B0/B1/B2 跑通（先别上 LLM 诊断）

### 第 3–4 周（升维1：Diagnosis）

* 上 Diagnosis Agent，跑 B3
* 输出：B2 vs B3 的 RR、MTTR、RCO 对比

### 第 5–8 周（升维2：复杂故障与级联）

* fault injector 支持 cascade + semantic
* 任务集扩到 200 条
* 加 replay 可视化（哪一步失败、为何决策）

### 第 9–12 周（升维3：Learning）

* 上检索式策略记忆或 selector（B4）
* 输出：Learning Efficiency / Preventive Win Rate 的实验结果
* 发布 benchmark + leaderboard + tech report draft

---

## 我会怎么改你之前的 repo 结构（很小改动，但价值巨大）

新增三个目录/文件即可：

* `src/.../engine/diagnosis.py`（Diagnosis Agent）
* `src/.../engine/saga.py`（Compensating actions / undo log）
* `src/.../engine/learning.py`（memory / selector）
* `data/rca_labels.jsonl`（用于 RCA accuracy 的真值或合成标签）
* `eval/baselines.py`（B0–B4 一键跑）

---

## 最重要的取舍建议（避免你被“研究深度”拖死）

DeepSeek 提了 POMDP / MCTS，很酷，但容易把你拖进无底洞。

**我建议的“Applied Scientist 最优折中”是：**

* 用 **Diagnosis Agent + Saga + 轨迹记忆/策略选择器** 来体现研究深度
* POMDP/MCTS 只写在 framing 里（可选做一个小实验），不要作为主要工程目标

这样你 3 个月能做完，并且足够“研究味”。

---

## 你问“要不要修改？”——我的最终回答

**要改，但不是推翻重来。**
改法是：**保留骨架，补齐“诊断 + 补偿事务 + 学习闭环 + baseline 对比”。**

---

如果你点头，我下一条直接给你两样东西（能马上开写/开工）：

1. **一个标准化的 Research Statement（1页以内）**：标题、RQ、方法、baseline、指标、预期结论
2. **MVP 的 Saga 场景任务 JSONL（10条）+ fault injection 表**（你复制就能跑）
