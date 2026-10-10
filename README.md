# docs_hyperlink_rewriter

批量修改 **Docs 在线表格**中某一列「超链接单元格」的**显示文字**，把它改成链接中分隔符后面的数字；**链接跳转地址保持不变**。

典型场景：表格里是 `HYPERLINK("https://...order_id=123456789", "查看")` 这类单元格，想让它们直接显示单号 `123456789`，但又不想破坏原来的跳转链接 —— 手工一个个改太慢，这个脚本批量处理。

| | 处理前 | 处理后 |
|---|---|---|
| 显示文字 | `查看` | `123456789` |
| 跳转地址 | `https://...order_id=123456789` | `https://...order_id=123456789`（**不变**） |

## 依赖

- **Python 3**（只用标准库 `argparse` / `json` / `subprocess` / `sys`，无需 pip 安装任何包）
- **Docs 官方 CLI `docs-cli`**（Go 二进制，通常由 `kcli` 框架分发）
  - 已在 `PATH` 里：用默认值即可
  - 通过 kcli 调用：加 `--cli "kcli docs-cli"`
  - 其他情况：`--cli "/完整/路径/docs-cli"`

## 快速开始

```bash
# 1) 先预览：只读取、只打印将要做的修改，绝不写回
python3 docs_hyperlink_rewriter.py \
    --spreadsheet-id <SPREADSHEET_ID> \
    --sheet-name "10.4-10.5" \
    --cli "kcli docs-cli" \
    --dry-run

# 2) 确认预览没问题，再实际写回
python3 docs_hyperlink_rewriter.py \
    --spreadsheet-id <SPREADSHEET_ID> \
    --sheet-name "10.4-10.5" \
    --cli "kcli docs-cli" \
    --yes
```

`<SPREADSHEET_ID>` 就是表格 URL `/s/home/<SPREADSHEET_ID>` 里的那一段 ID。

> 建议先跑 `--dry-run`。它会打印每个页签的名称/索引、读取行数、目标列位置，以及前 3 条改动预览。

## 参数

| 参数 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `--spreadsheet-id` | ✅ | — | 在线表格 ID（URL `/s/home/<ID>` 中的 ID） |
| `--sheet-name` | 多 sheet 必填 | — | 目标页签名，精确匹配，如 `"10.4-10.5"` |
| `--sheet-index` | 多 sheet 必填 | — | 目标页签的 **0-based** 索引（第 1 张 = `0`，第 2 张 = `1`） |
| `--column` | | `order_id` | 目标列：表头名，或列字母（如 `G`） |
| `--separator` | | `=` | 从链接里截取数字用的分隔符 |
| `--cli` | | `docs-cli` | CLI 命令前缀；经 kcli 调用传 `"kcli docs-cli"` |
| `--dry-run` | | 关 | 只预览，不写回 |
| `--yes` | | 关 | 跳过交互确认，直接写回 |

`--sheet-name` 与 `--sheet-index` **互斥**，只能给一个。

**单 sheet 表格**可以不指定页签；**多 sheet 表格**不指定时，脚本会列出所有页签及索引后退出，不会瞎猜。

**不知道页签名/索引？** 直接跑一次（哪怕故意不带页签参数），脚本第一步就会把所有页签打印出来：

```
[1/4] 获取 sheet 列表 (fcXXXXXXXX) ...
      可用 sheet：
        sheetIndex=0  "10.4-10.5"
        sheetIndex=1  "10.5-10.6"
```

## 工作原理

```
sheets +info          →  列出所有 sheet（sheetName / sheetIndex / maxRowIndex / maxColumnIndex），定位目标页签
sheets +read          →  用 range 前缀读取目标页签全量内容，如 --range "3!A1:W200"
（本地处理）           →  按表头定位目标列，取每个超链接 URL 中分隔符后的数字
sheets +batch-write   →  以 cellType=HYPERLINK 写回：value=数字，hyperlink=原链接
```

两点实现细节：

- `sheets +read` **不支持**按页签筛选，只能用 range 前缀 `<sheetIndex>!A1:...` 指定页签，所以先 `+info` 拿索引是必需的。
- 写回用 `HYPERLINK` 类型而不是纯文本，这样**显示文字和跳转地址能分别设置**，链接才不会被改掉。

## 处理规则（哪些会被跳过）

**前提：目标页签的第 1 行必须是表头**（脚本用第 1 行认列名，从第 2 行开始处理数据）。

| 情况 | 行为 |
|---|---|
| 目标列里该单元格**没有超链接** | 跳过，并在统计里计数 |
| 链接中分隔符后**不是纯数字**（如 `order_id=abc`） | 打印 `[警告] ... 已跳过`，不写回 |
| 显示文字**本来就是那个数字** | 计入「已显示为数字」，仍会一起写回（所以重复执行是安全的/幂等的） |
| 目标列名/列字母**找不到** | 报错并打印该表头所有列名，退出码 1 |
| 页签名/索引**找不到** | 报错并打印所有可用页签，退出码 1 |

`--column` 既可以是表头名（默认 `order_id`，精确匹配、区分大小写），也可以是列字母（`A` ~ `ZZZ`，最多 3 位）。

## 运行输出示例

```
[1/4] 获取 sheet 列表 (fcXXXXXXXX) ...
      可用 sheet：
        sheetIndex=0  "10.4-10.5"
        sheetIndex=1  "10.5-10.6"
      目标 sheet：index=1  name="10.5-10.6"  maxRow=200  maxCol=22
[2/4] 读取 sheet 内容 ...
      读取到 187 行（含表头）
[3/4] 目标列：第 7 列  表头「order_id」  列字母 G
      共 186 个超链接单元格（12 个已显示为数字，0 个无链接跳过）

      预览（前 3 条）：
        1!G2  显示 -> 123456789  （链接保留）
        1!G3  显示 -> 123456790  （链接保留）
        1!G4  显示 -> 123456791  （链接保留）

[4/4] 批量写回 186 个单元格 ...
[完成] 写入成功：{...}          # 原样输出 docs-cli 返回的 data
```

## 其他说明

- **退出码**：出错（CLI 执行失败 / 返回非 JSON / `ok=false` / 找不到页签 / 找不到列 / 表格为空）都是 `1`；用户回答 `n` 取消不算错误，返回 `0`。
- **不写 `--yes` 时会交互确认**，弹出 `确认要将 N 个单元格写回为「数字显示 + 原链接」吗？(y/N)`。在 CI、定时任务等非交互环境里务必加 `--yes`。
- **临时文件**：写回前会把改动清单落到 `/tmp/docs_edits_<spreadsheet_id>_<sheetIndex>.json`，供 `--edits-file` 使用；脚本**不会自动删除**它，需要的话自己清理。
- 只读取/预览（`--dry-run`）不会产生任何写入，可以放心对生产表格跑。

## 文件

| 文件 | 说明 |
|---|---|
| `docs_hyperlink_rewriter.py` | 主脚本，命令行入口 `main()` |

```bash
python3 docs_hyperlink_rewriter.py --help
```
