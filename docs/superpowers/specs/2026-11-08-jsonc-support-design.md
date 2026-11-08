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
| 对象/数组尾逗号 | ✅（空容器里的逗号 `{,}` / `[,]` 仍是语法错误，与 meojson 一致） |
| 无引号键、单引号字符串 | ❌ |
| `NaN` / `Infinity` / `-Infinity` | ❌（stdlib `json` 默认接受，需显式拦截） |
| 重复键 | ❌（meojson 同样报错） |
| 落单的代理字符（`"\ud800"`） | ❌（meojson 同样报错；stdlib 接受但会让后续编码崩溃） |
| 溢出成 `inf` 的数字（`1e999`） | ❌（见下方「有意偏离」） |
| UTF-8 BOM | ✅（自动剥离） |

### 实现

不写递归下降解析器，走「预处理 + 标准库」：

1. **剥注释**：扫描文本，跟踪字符串字面量与转义状态；注释内容替换成**等长空白**
   （块注释内的换行保留），因此字节偏移与行号完全不变。
2. **拦非法字面量**：再扫一遍，识别 `NaN` / `Infinity` / `-Infinity` 并报错。
3. **剥尾逗号**：再扫一遍，把「其后（跳过空白）是 `}` 或 `]`」且「前面已经有过至少
   一个成员」的 `,` 替换成空格——meojson 的解析循环必须先完成一个键值对才会收尾，
   所以 `{,}`、`[,]` 是语法错误，必须同样拒绝。
4. **交给标准库**：`json.loads(cleaned, object_pairs_hook=_make_pairs_hook(doc),
   parse_float=_make_parse_float(doc))`——前者拒绝重复键，后者拒绝溢出数字。
5. **修代理字符**：解析后递归处理字符串，把 `\uXXXX` 代理对还原成真实码点，
   落单的代理字符直接报错。

因为偏移量没变，stdlib `JSONDecodeError` 自带的行列号（`line X column Y`）可以直接
用，不需要自己算位置（BOM 在解析前被剥掉，带 BOM 的文件位置会偏移一个字符）。

### 有意偏离 meojson 的三处

1. **溢出数字**：`1e999` 会被解析成 `inf`，序列化回去是非法的 `Infinity` 字面量。
   meojson 把数字当字面量保存能原样往返，MaaMCP 不能，因此直接拒绝。
2. **顶层值类型**：meojson 只接受对象 / 数组，本模块 `loads` 接受任意顶层值
   （是否合法交给调用方判断——pipeline 文件另行要求顶层为非空对象）。
3. **顶层值之后的尾部残余**：meojson 的 `skip_whitespace` 把「已到输入末尾」与
   「注释起始符非法」混为一谈——顶层值之后出现 `/x` 或未闭合的块注释时，它会静默
   接受并丢弃残余，MaaFramework 继承了这一行为。本模块按标准库语义报错。方向是
   「更严」：MaaMCP 不会接受 MaaFramework 拒绝的文件，只会在这类病态的尾部残余上
   报错。未闭合注释出现在文档**内部**时两边都拒绝，行为一致。

前两条源于 MaaMCP 与 MaaFramework 的调用方式不同——MaaMCP 要把解析结果重新序列化
成严格 JSON 再交给 `Resource.override_pipeline`。

已确认与 meojson **一致**（无需偏离）：注释、尾逗号（含 `{,}` / `[,]` 这类空容器
逗号都拒绝）、重复键、NaN / Infinity、落单代理字符、顶层值之后非 `/` 开头的尾部
垃圾（两者都报错）、顶层值之后的尾部注释（两者都接受）。

### 错误信息

| 情况 | 报错 |
|---|---|
| 块注释未闭合 | `Unterminated comment: line L column C (char N)` |
| 非法 JSON | 复用 stdlib 的 msg，位置用**原文**重新抛出 |
| `NaN` / `Infinity` | `NaN is not valid JSON: line L column C` |
| 溢出数字 | `1e999 is not a finite number`（无位置） |
| 落单代理字符 | `lone surrogate in string`（无位置） |
| 重复键 | `Duplicate key 'X'`（无位置，stdlib hook 无法定位） |

## 接入点

| 位置 | 原实现 → 现实现 | 影响范围 |
|---|---|---|
| `pipeline_tools.py` `_read_and_validate_pipelines` | `json.load(fh)` → `jsonc_loads(fh.read())` | `load_pipeline` / `run_pipeline` / `benchmark_node`（三处共用该函数） |
| `pipeline_tools.py` `save_pipeline` | `json.loads(pipeline_json)` → `jsonc_loads(pipeline_json)` | 校验输入字符串；**写盘仍是 `json.dump(indent=2)` 的标准 JSON**，注释丢弃 |
| `agent_supervisor.py` `load_agent_config` | `json.loads(...)` → `jsonc_loads(...)` | `interface.json` 允许注释——与 MFAAvalonia（Newtonsoft.Json，容忍注释）对齐；MaaFramework 本身不读 `interface.json` |

## 兼容性

- 纯 JSON 是合法 JSONC → 现有行为不变，现有测试全绿。
- MaaMCP 交给 MaaFramework 的永远是**重新序列化出来的严格 JSON**（由 maafw 的
  `override_pipeline` 内部做 `json.dumps`）→ MaaFramework 侧零风险。
- 新增的严格性：重复键报错、落单代理字符报错（这两条与 meojson 一致，能挡掉
  「节点名写重了」「文件里有坏编码」这类真实问题），以及溢出数字报错
  （这一条是上文的有意偏离，防止落盘写出非法的 `Infinity`）。

## 测试

新增 `tests/test_jsonc.py`（按仓库既有惯例整体标 `@pytest.mark.unit`）：

- **解析器**：行/块注释、尾逗号、字符串内含 `//` 与 `/* */`、转义引号、未闭合块注释、
  重复键、NaN/Infinity、溢出数字、代理对与落单代理、BOM、非 str 输入、错误行列号正确。
- **工具层**：带注释的 pipeline 文件能被 `_read_and_validate_pipelines` /
  `load_pipeline` 读出；重复键与非 UTF-8 文件给出明确错误；`save_pipeline` 接受带注释
  的输入且落盘为标准 JSON；`interface.json` 带注释时 agent 配置仍能读到，坏文件返回
  None 而不抛异常。

## 文档

- `CLAUDE.md` / `CLAUDE_CN.md`：同步加一条「pipeline 文件与 interface.json 支持 JSONC」。
- `load_pipeline` / `save_pipeline` / `run_pipeline` / `benchmark_node` 的工具描述里
  点明——工具描述就是 AI 读到的说明书。

## 不做的事

- 不改 `save_pipeline` 的输出格式（统一输出标准 JSON）。
- 不为 `.jsonc` 扩展名做特殊分流（MaaFramework 两种扩展名都收，MaaMCP 不按扩展名分流）。
- 不引入任何新依赖。
