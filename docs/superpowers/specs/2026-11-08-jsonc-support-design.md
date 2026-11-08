# JSONC 支持设计（issue #38）

> 状态：已实现
> 关联 issue：https://github.com/MAA-AI/MaaMCP/issues/38
> 日期：2026-11-08

## 背景

MaaMCP 用标准库 `json` 读取 pipeline 文件与 `interface.json`。但 MaaFramework 自带的
pipeline 解析器（内部使用 [meojson](https://github.com/MistEO/meojson) 的 `json::parsec`）
是 JSONC 语法——允许注释与尾逗号。于是同一份文件 MaaFramework 能读、MaaMCP 报
`Expecting value`，`load_pipeline` / `run_pipeline` / `benchmark_node` / `save_pipeline`
全部受影响。

实测 MaaFramework 5.14.3（`Resource.post_pipeline`）的接受集合：

| 语法 | MaaFramework |
|---|---|
| `//` 行注释 | ✅ |
| `/* */` 块注释 | ✅ |
| 尾逗号（对象/数组） | ✅ |
| 无引号键 | ❌ |
| 单引号字符串 | ❌ |

MaaMCP 侧必须与之一致：比 MaaFramework 更宽松会让「MaaMCP 能跑、bundle 加载失败」，
更严格则会让 MaaFramework 能跑的文件在 MaaMCP 报错。

## 方案选型

### 为什么不是 meojson

meojson 是 header-only C++17 库，无 PyPI 包、无 Python binding（GitHub 全库搜索仅
C++ 仓库本身）。MaaMCP 是纯 Python pip 包，调用它需要：

1. 把头文件 vendor 进仓库；
2. 写 CPython 扩展；
3. 构建系统从 hatchling 换成 scikit-build-core；
4. 要么要求用户本机有 C++17 工具链，要么 CI 为 win/mac/linux 交叉编译 wheel。

为一个 JSON 解析功能付出这个分发代价不划算。

被否决的两条「借道 MaaFramework」的路：

- `Resource.post_pipeline()` 确实能原生解析 JSONC，但 `get_node_data()` 返回的是
  MaaFramework **归一化后**的节点结构（未知字段被丢弃、默认值被填充、`next` 被改写成
  对象数组），有损，不能当解析器用。
- `Resource.override_pipeline()` 走的是 meojson 的严格 `json::parse`（见 MaaFramework
  仓库 `.agents/skills/meojson/SKILL.md`），所以 MaaMCP 交给它的必须是严格 JSON。

### 选定方案

在 Python 侧复刻 meojson `json::parsec` 的语义，零新依赖。

## 解析层：`maa_mcp/jsonc.py`

### 公开 API

```python
def loads(text: str) -> Any
```

- 入参：JSONC 文本（可含 BOM）。
- 返回：解析后的 Python 对象。
- 失败：抛 `json.JSONDecodeError` 子类（兼容现有 `except json.JSONDecodeError`）。

### 语义

| 语法 | 支持 |
|---|---|
| `//` 行注释、`/* */` 块注释 | ✅ |
| 对象/数组尾逗号 | ✅ |
| 无引号键、单引号字符串 | ❌ |
| `NaN` / `Infinity` / `-Infinity` | ❌（stdlib `json` 默认接受，需显式拦截） |
| 重复键 | ❌（meojson 同样报错） |
| UTF-8 BOM | ✅（自动剥离） |

### 实现

不写递归下降解析器，走「预处理 + 标准库」：

1. **剥注释**：扫描文本，跟踪字符串字面量与转义状态；注释内容替换成**等长空白**
   （块注释内的换行保留），因此字节偏移与行号完全不变。
2. **剥尾逗号**：在剥完注释的文本上再扫一遍，跟踪字符串状态，把「其后（跳过空白）
   是 `}` 或 `]`」的 `,` 替换成空格。
3. **交给标准库**：`json.loads(cleaned, object_pairs_hook=_reject_duplicates,
   parse_constant=_reject_constant)`。

因为偏移量没变，stdlib `JSONDecodeError` 自带的行列号（`line X column Y`）可以直接
用，不需要自己算位置。

### 错误信息

| 情况 | 报错 |
|---|---|
| 块注释未闭合 | `Unterminated comment: line L column C (char N)` |
| 非法 JSON | 复用 stdlib 的 msg，位置用**原文**重新抛出 |
| `NaN` / `Infinity` | `NaN/Infinity is not valid JSON: line L column C` |
| 重复键 | `Duplicate key 'X'`（无位置，stdlib hook 无法定位） |

## 接入点

| 位置 | 现状 | 影响范围 |
|---|---|---|
| `pipeline_tools.py` `_read_and_validate_pipelines` | `json.load(fh)` | `load_pipeline` / `run_pipeline` / `benchmark_node`（三处共用该函数） |
| `pipeline_tools.py` `save_pipeline` | `json.loads(pipeline_json)` | 校验输入字符串；**写盘仍是 `json.dump(indent=2)` 的标准 JSON**，注释丢弃 |
| `agent_supervisor.py` `load_agent_config` | `json.loads(...)` | `interface.json` 允许注释，与 MaaFramework 读 interface.json 的行为对齐 |

## 兼容性

- 纯 JSON 是合法 JSONC → 现有行为不变，现有测试应全绿。
- MaaMCP 交给 MaaFramework 的永远是 `json.dumps` 产出的严格 JSON → MaaFramework 侧零风险。
- 唯一新增的严格性是重复键报错，有意为之：meojson 也报错，且能挡掉「pipeline 文件里
  节点名写重了」这类真实错误。

## 测试

新增 `tests/test_jsonc.py`：

- **单元**：行/块注释、尾逗号、字符串内含 `//` 与 `/* */`、转义引号、未闭合块注释、
  重复键、NaN/Infinity、BOM、错误行列号正确。
- **集成**：带注释的 pipeline 文件能被 `load_pipeline` 读出；`save_pipeline` 接受带注释
  的输入且落盘为标准 JSON；`interface.json` 带注释时 agent 配置仍能读到。

## 文档

- `CLAUDE.md` / `CLAUDE_CN.md`：同步加一条「pipeline 文件与 interface.json 支持 JSONC」。
- `load_pipeline` / `save_pipeline` / `run_pipeline` 的工具描述里点明——工具描述就是
  AI 读到的说明书。

## 不做的事

- 不改 `save_pipeline` 的输出格式（统一输出标准 JSON）。
- 不为 `.jsonc` 扩展名做特殊分流（MaaFramework 两种扩展名都收，MaaMCP 不按扩展名分流）。
- 不引入任何新依赖。
