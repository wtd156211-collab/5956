# 日志查询引擎（只读查询语言）

我们有个日志分析的小工具。之前是让运营同事写 Python 片段丢进来跑，出过两次事故，有一次差点把生产数据删了。现在想收口：他们只写查询语句，我们这边解析执行。

语法就是下面这些：过滤条件、字段选择、分组聚合、排序、取前几条，聚合支持计数、求和、最小、最大、平均。写错了得能说清楚——解析不了的时候要报出是第几个字符、什么原因，同事看到报错知道改哪儿；类型对不上也得在跑之前就报出来，不能跑到一半崩掉。执行结果必须确定：同一条查询跑两遍要一模一样，排序并列的时候顺序不能飘，分组的输出顺序也按下面定的规则来，别依赖字典的遍历顺序。性能上，samples 里那份二十万行的数据，一条带过滤加聚合的查询要在几秒内出结果，过滤条件要能短路。实现只用标准库，测试用 unittest。

仓库里只有这份说明和 samples/ 里的样例数据与查询，代码从零写。

## 数据与字段类型

samples/logs.csv，第一行是表头，二十万行数据，列与类型固定：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ts` | string | UTC 时间戳 `YYYY-MM-DDTHH:MM:SS.mmmZ`，格式固定，所以按字符串比较等于按时间比较 |
| `level` | string | `INFO` / `WARN` / `ERROR` |
| `service` | string | 服务名 |
| `region` | string | 机房；**字段为空表示 null** |
| `status` | int | HTTP 状态码 |
| `latency_ms` | int | 耗时毫秒 |

## 语法

```text
SELECT <选择列表> FROM logs [WHERE <条件>] [GROUP BY <字段列表>] [ORDER BY <排序项列表>] [LIMIT <n>]
```

- 关键字与字段名都**不区分大小写**，输出表头统一用小写。
- **选择列表**：字段（`service`）或聚合（`count(*)`、`sum(latency_ms)`），可以用 `AS` 起别名；没写别名时，字段列用字段名、聚合列用函数名（`count`、`sum`、`min`、`max`、`avg`）作为表头。允许出现同名列，表头按书写顺序列出。
- **聚合函数**：`count(*)`、`count(field)`、`sum(field)`、`min(field)`、`max(field)`、`avg(field)`，就这六个，不额外扩展。
- **WHERE 条件**：`字段 运算符 字面量`，运算符是 `=`、`!=`、`<`、`<=`、`>`、`>=` 之一；字面量是**非负整数**或**单引号字符串**（字符串里的单引号写成两个连续单引号）。
- 条件之间用 `AND`、`OR`、`NOT` 组合，支持括号。优先级：`NOT` > `AND` > `OR`。
- **GROUP BY**：一个或多个字段。
- **ORDER BY**：一个或多个排序项，每项是「别名或字段名 + 可选 `ASC`/`DESC`」（默认 `ASC`）；先按别名匹配，再按字段名匹配。
- **LIMIT n**：`n` 是非负整数，在排序之后取前 n 行。

不许扩展语法：`JOIN`、子查询、`IN`、`LIKE`、函数嵌套、负数与小数都不支持，遇到就报错。

## 类型规则

- 比较必须**同类型**：整数列只和整数字面量比，字符串列只和字符串字面量比（`ts` 是字符串列，所以和字符串比）。
- `sum` 与 `avg` 只接受**整数列**；`count`、`min`、`max` 两种类型都接受。
- 类型不对在**执行之前**就报 `TYPE_MISMATCH`，不允许跑一半才发现（这条要求解析完成后先做一遍全量检查再读数据）。

## 空值语义

- CSV 里的空字段就是 `null`（`region` 有空值）。
- `null` 与任何值的比较结果都是**未知**：`WHERE` 里未知按不成立处理，`NOT 未知` 仍然是未知，同样不成立（三值逻辑）。
- 聚合里 `count(*)` 统计行数（含 null 行）；`count(field)` 只数非 null；`sum`、`min`、`max`、`avg` 都忽略 null。
- 一组里全是 null 时：`count` 输出 `0`，`sum` 输出 `0`，`min`、`max`、`avg` 输出 `null`。
- `GROUP BY` 里 null 单独成一组。

## 结果确定性

- `GROUP BY` 的输出顺序：按分组字段的值升序（null 排最前；整数按数值、字符串按 UTF-8 字节序）；多个分组字段按书写顺序依次比较。**不许依赖字典遍历顺序。**
- `ORDER BY` 是**稳定排序**：排序键完全相同的行保持排序前的相对顺序——分组查询里就是分组键升序那一套，非分组查询里就是输入行的顺序。
- 排序与比较时 null 视为最小。
- 因此同一条查询在同一天数据上跑两遍，输出必须逐字节一样。

## 输出格式

CSV，第一行表头，逗号分隔，行尾 LF：

- 整数字段原样输出十进制；
- 字符串原样输出；
- `null` 输出成 `null` 这四个字母；
- `avg` 输出保留**三位小数**，四舍五入（`sum` 与 `count` 都是整数，结果确定）。

出错时只输出一行：

```text
error,<错误码>,<位置>,<说明>
```

`位置` 是出错字符在查询语句里的下标，**从 1 开始**（按 Unicode 码点计），指向出错内容的第一字符；`说明` 是人看的文字，内容不限。`samples/expected/bad-*.txt` 里只固定了错误码与位置两列，验收时也只比这两项。

## 错误码

| 错误码 | 什么时候报 |
| --- | --- |
| `SYNTAX_ERROR` | 语法不成立：缺少关键字、括号没闭合、字符串没闭合、多余内容、不认识的字符、缺字面量等 |
| `UNKNOWN_FIELD` | 引用了不存在的字段（WHERE、GROUP BY、ORDER BY、选择列表里都算） |
| `UNKNOWN_FUNCTION` | 用了六个聚合之外的函数名 |
| `TYPE_MISMATCH` | 比较两边类型不同，或 `sum`/`avg` 用在了字符串列上 |
| `MIXED_WITHOUT_GROUP_BY` | 选择列表里既有字段又有聚合，却没有 `GROUP BY`（位置指向第一个字段列） |
| `BAD_LIMIT` | `LIMIT` 后面不是非负整数 |

## 性能要求

二十万行的 `samples/logs.csv`：

- 带过滤 + 聚合的查询（例如 `q-2.sql`）在**3 秒**内出结果（单线程、只算执行时间）；
- 过滤条件要**短路**：`AND` 左边为假就不再算右边，`OR` 左边为真就不再算右边，别把整行每个条件都算一遍；
- 类型检查在读取数据之前完成，别等扫完数据才报类型错。

## 实现说明

### 模块划分

- `logquery/lexer.py`：词法分析，把查询切分为关键字、字段名、整数、字符串、运算符和括号；每个 token 保留从 1 开始的 Unicode 码点位置。
- `logquery/parser.py`：递归下降语法分析，生成 AST；表达式按 `NOT`、`AND`、`OR` 的优先级组织。
- `logquery/schema.py`：固定字段名与字段类型。
- `logquery/semantic.py`：执行前校验字段、聚合函数、比较类型、聚合/分组组合和 `ORDER BY` 可引用对象。
- `logquery/executor.py`：读取 CSV、执行三值逻辑过滤、聚合、确定性排序并生成 CSV。
- `logquery/__main__.py`：命令行入口。
- `tests/test_logquery.py`：基于 `unittest` 的回归测试，覆盖样例错误位置、空值、聚合、确定性和短路行为。

### 执行前校验

`executor.execute_query()` 的顺序固定为：

1. 调用 `parse_query()` 完成词法和语法分析；
2. 调用 `validate_query()` 遍历 AST，只依据固定 schema 检查字段、类型和聚合规则；
3. 全部通过后才打开并读取数据文件。

因此，`TYPE_MISMATCH`、`UNKNOWN_FIELD`、`UNKNOWN_FUNCTION`、`MIXED_WITHOUT_GROUP_BY` 和 `BAD_LIMIT` 都不会在扫描部分数据后才抛出。

### 三值逻辑与短路

条件值为真、假或未知。`evaluate_condition()` 递归执行 AST：

- `AND` 的左侧为假时立即返回假，不计算右侧；
- `OR` 的左侧为真时立即返回真，不计算右侧；
- `NOT 未知` 仍为未知；
- 只有最终结果为真的行才进入投影或聚合。

字段值在首次访问时才按 schema 转换并缓存；被短路跳过的右侧条件不会触发对应字段读取或转换。

### 命令行

```bash
python3 -m logquery samples/logs.csv samples/queries/q-1.sql
python3 -m logquery samples/logs.csv --sql "SELECT count(*) FROM logs"
```

成功时把 CSV 写到标准输出；查询错误时把 `error,<错误码>,<位置>,<说明>` 写到标准输出并以非零状态退出。

## 样例

```text
samples/logs.csv                 二十万行数据
samples/queries/q-N.sql          正确的查询
samples/expected/q-N.csv         对应的期望结果
samples/queries/bad-N.sql        故意写错的查询
samples/expected/bad-N.txt       期望的错误码与位置（error,<码>,<位置>）
```

六条正确查询分别覆盖：单表聚合 + 多条件过滤（`q-1`）、分组聚合 + 别名 + 排序 + `LIMIT`（`q-2`）、多字段分组与分组排序（`q-3`）、纯投影 + `LIMIT`（`q-4`）、多聚合的极值（`q-5`）、null 分组与 `count(field)` 的空值语义（`q-6`）。七条错误查询覆盖缺表名、字段名拼错、类型不匹配、缺 `GROUP BY`、`avg` 用错类型、括号没闭合、`GROUP BY` 引用不存在的字段。
