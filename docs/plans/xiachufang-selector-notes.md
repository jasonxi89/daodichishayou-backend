# 下厨房步骤解析调查结论（2026-07-18，用户授权后实测）

> 前版记录"合规阻断未调查"；用户 2026-07-18 明确决策恢复：真实抓取（低频+熔断）
> 与 LLM 补写并行，真实步骤优先级高于 LLM 补写，反向覆盖禁止。

- 实测 3 个生产库 URL：第 1 个返回真实页面（40KB），第 2、3 个连续请求即触发
  CAPTCHA（~4.4KB 拦截页）→ **风控极敏感，10s 间隔也只容忍首个请求**。
  补爬脚本必须：长间隔（≥30s）+ CAPTCHA 即熔断 + 可断点续跑。
- 真实页面结构（fixture: `tests/fixtures/xiachufang_detail_2026.html`）：
  - JSON-LD **存在且含 `recipeInstructions`**，但值是**一整个字符串**（`"1.xxx\n2.xxx"`），
    不是数组 → 旧代码 `isinstance(steps, list)` 判断落空，steps 保持 None
  - DOM `div.steps` 仍存在（li > p 结构，旧 DOM 选择器**本可以工作**）
- **步骤全空的根因是两个代码 bug，不是选择器过时**：
  1. JSON-LD 的 `recipeInstructions` 字符串形态未处理
  2. JSON-LD 分支匹配到 `@type: Recipe` 后无条件提前 `return` → DOM fallback 永远不执行
- 修复：字符串形态拆分为步骤列表 + 取消提前 return（steps 缺失时继续走 DOM fallback）。
