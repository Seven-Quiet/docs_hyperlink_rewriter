"""
Docs 在线表格：批量把指定列的「超链接单元格」显示文字改写为链接中分隔符后的数字，
链接地址本身保持不变。

调用 Docs 官方命令行工具 docs-cli（或 kcli docs-cli），
  1) sheets +info        列出所有 sheet，定位目标 sheet
  2) sheets +read        用 range 前缀（如 "3!A1:W200"）读取指定 sheet 的全量内容
  3) 定位目标列（默认表头名 order_id），提取每个超链接 URL 中分隔符（默认 "="）后的数字
  4) sheets +batch-write 以 HYPERLINK 类型写回（显示文字=数字，跳转地址=原 URL）

依赖：Docs 官方 CLI 工具 docs-cli（Go 二进制，经 kcli 框架分发）

用法示例：
  # 预览（只读取、打印将要做的修改，不写回）
  python3 docs_hyperlink_rewriter.py --spreadsheet-id fcXXX --dry-run

  # 指定 sheet 名称（多 sheet 表格必须指定）
  python3 docs_hyperlink_rewriter.py --spreadsheet-id fcXXX --sheet-name "xxx" --dry-run

  # 指定 sheet 的 0-based 索引（第 1 张=0，第 2 张=1，以此类推）
  python3 docs_hyperlink_rewriter.py --spreadsheet-id fcXXX --sheet-index 3 --dry-run

  # 实际写回（加 --yes 跳过交互确认）
  python3 docs_hyperlink_rewriter.py --spreadsheet-id fcXXX --sheet-name "xxx" --yes

  # 指定列名 / 列字母 / 分隔符 / CLI 前缀
  python3 docs_hyperlink_rewriter.py --spreadsheet-id <ID> \
      --sheet-name "10.4-10.5" \
      --column order_id --separator "=" --cli "kcli docs-cli" --yes
"""

import argparse
import json
import subprocess
import sys


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def run_cli(cli_cmd, args):
    cmd = cli_cmd.split() + args
    return subprocess.run(cmd, capture_output=True, text=True)


def ensure_ok(proc, step_name):
    if proc.returncode != 0:
        print(f"[错误] {step_name} 命令执行失败（退出码 {proc.returncode}）")
        if proc.stderr:
            print(f"  stderr: {proc.stderr.strip()}")
        sys.exit(1)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[错误] {step_name} 返回了非 JSON 内容：")
        print(proc.stdout[:2000])
        sys.exit(1)
    if not data.get("ok"):
        err = data.get("error") or {}
        msg = err.get("message") or err.get("type") or "未知错误"
        hint = err.get("hint") or ""
        print(f"[错误] {step_name} 返回失败：{msg}")
        if hint:
            print(f"  提示：{hint}")
        sys.exit(1)
    return data


def col_letter_to_index(letter):
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def index_to_col_letter(idx):
    s = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


# ---------------------------------------------------------------------------
# Sheet 信息查询
# ---------------------------------------------------------------------------

def get_sheet_info(cli_cmd, spreadsheet_id):
    """返回所有 sheet 的元信息列表。
    +info 的 JSON 路径：data.result.sheetInfos
    """
    proc = run_cli(cli_cmd, ["sheets", "+info", "--spreadsheet-id", spreadsheet_id])
    data = ensure_ok(proc, "获取 sheet 列表")

    # kcli docs-cli 的路径：data.result.sheetInfos
    sheets = (
        data.get("data", {}).get("result", {}).get("sheetInfos")
        or data.get("data", {}).get("sheetInfos")
        or data.get("data", {}).get("sheets")
        or []
    )
    return sheets


def resolve_target_sheet(sheets, sheet_name, sheet_index):
    """定位目标 sheet，返回其元信息字典；找不到则打印可用列表并退出。"""
    if sheet_name is not None:
        for s in sheets:
            if s.get("sheetName") == sheet_name:
                return s
        names = [s.get("sheetName") for s in sheets]
        print(f"[错误] 找不到 sheet「{sheet_name}」，可用列表：{names}")
        sys.exit(1)

    if sheet_index is not None:
        for s in sheets:
            if s.get("sheetIndex") == sheet_index:
                return s
        indices = [(s.get("sheetIndex"), s.get("sheetName")) for s in sheets]
        print(f"[错误] 找不到 sheetIndex={sheet_index}，可用列表：{indices}")
        sys.exit(1)

    # 两者都未指定
    if len(sheets) == 1:
        return sheets[0]

    print("[错误] 该表格包含多个 sheet，请用 --sheet-name 或 --sheet-index 指定目标：")
    for s in sheets:
        print(f"  sheetIndex={s.get('sheetIndex')}  name=\"{s.get('sheetName')}\"")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 读取指定 sheet
# ---------------------------------------------------------------------------

def read_sheet(cli_cmd, spreadsheet_id, sheet_index, max_row, max_col):
    """
    读取指定 sheet 的全量内容。
    +read 不支持 --sheet-index，通过 range 前缀 "<sheetIndex>!A1:Xcol<row>" 来指定。
    默认只返回 100 行，若被截断则用完整 range 补读。
    """
    end_col = index_to_col_letter(max_col)

    # 先用完整 range 直接读（避免两次请求）
    full_range = f"{sheet_index}!A1:{end_col}{max_row}"
    proc = run_cli(cli_cmd, [
        "sheets", "+read",
        "--spreadsheet-id", spreadsheet_id,
        "--range", full_range,
    ])
    data = ensure_ok(proc, "读取 sheet")

    # kcli docs-cli 的路径：data.content
    content = data.get("data", {}).get("content")
    if content is None:
        # 兜底：data.result.content
        content = data.get("data", {}).get("result", {}).get("content", {})
    return content


# ---------------------------------------------------------------------------
# 定位目标列
# ---------------------------------------------------------------------------

def find_target_column(content, column_spec):
    """返回 (columnIndex, 列名)。column_spec 可以是列名或列字母（如 G）。"""
    rows = content.get("rows", [])
    if not rows:
        return None, None
    header = rows[0]
    col_name_map = {cell.get("columnIndex"): cell.get("showValue", "") for cell in header}

    spec = (column_spec or "").strip()

    # 纯列字母（1~3 位）
    if spec and spec.isalpha() and len(spec) <= 3:
        idx = col_letter_to_index(spec)
        return idx, col_name_map.get(idx)

    # 按表头名精确匹配
    for idx, name in col_name_map.items():
        if name == spec:
            return idx, name

    return None, None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="把在线表格某列超链接的显示文字改为「=」后的数字，链接地址不变",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--spreadsheet-id", required=True,
                        help="在线表格 ID（URL /s/home/<ID> 中的 ID）")

    # 目标 sheet 指定（二选一，单 sheet 表格可不填）
    sheet_group = parser.add_mutually_exclusive_group()
    sheet_group.add_argument("--sheet-name",
                             help='目标 sheet 名称，如 "10.4-10.5"（多 sheet 表格必须指定）')
    sheet_group.add_argument("--sheet-index", type=int,
                             help="目标 sheet 的 0-based 索引（第 1 张=0，第 2 张=1，以此类推）")

    parser.add_argument("--column", default="order_id",
                        help="目标列：列名（默认 order_id）或列字母（如 G）")
    parser.add_argument("--separator", default="=",
                        help="从链接中截取数字用的分隔符（默认 =）")
    parser.add_argument("--cli", default="docs-cli",
                        help='CLI 命令前缀，默认 docs-cli；若经 kcli 调用则传 "kcli docs-cli"')
    parser.add_argument("--dry-run", action="store_true",
                        help="只读取并预览改动，不写回")
    parser.add_argument("--yes", action="store_true",
                        help="跳过交互确认，直接写回")
    args = parser.parse_args()

    cli_cmd = args.cli.strip()
    spreadsheet_id = args.spreadsheet_id.strip()

    # 1. 获取 sheet 列表，定位目标 sheet
    print(f"[1/4] 获取 sheet 列表 ({spreadsheet_id}) ...")
    sheets = get_sheet_info(cli_cmd, spreadsheet_id)
    if not sheets:
        print("[错误] 未获取到 sheet 信息，请检查表格 ID 或权限")
        sys.exit(1)

    print("      可用 sheet：")
    for s in sheets:
        print(f"        sheetIndex={s.get('sheetIndex')}  \"{s.get('sheetName')}\"")

    target_sheet = resolve_target_sheet(sheets, args.sheet_name, args.sheet_index)
    actual_idx = target_sheet["sheetIndex"]
    max_row    = int(target_sheet.get("maxRowIndex", 100))
    max_col    = int(target_sheet.get("maxColumnIndex", 25))
    print(f"      目标 sheet：index={actual_idx}  "
          f"name=\"{target_sheet.get('sheetName')}\"  "
          f"maxRow={max_row}  maxCol={max_col}")

    # 2. 读取目标 sheet
    print(f"[2/4] 读取 sheet 内容 ...")
    content = read_sheet(cli_cmd, spreadsheet_id, actual_idx, max_row, max_col)
    rows = content.get("rows", [])
    if not rows:
        print("[错误] sheet 为空或未读取到内容")
        sys.exit(1)
    print(f"      读取到 {len(rows)} 行（含表头）")

    # 3. 定位目标列
    col_idx, col_name = find_target_column(content, args.column)
    if col_idx is None:
        header_names = [c.get("showValue", "") for c in rows[0]]
        print(f"[错误] 未找到目标列「{args.column}」，表头为：{header_names}")
        sys.exit(1)
    print(f"[3/4] 目标列：第 {col_idx + 1} 列  "
          f"表头「{col_name}」  列字母 {index_to_col_letter(col_idx)}")

    # 4. 生成 edits
    edits   = []
    skipped = 0
    already = 0
    for row in rows[1:]:
        cell = None
        for c in row:
            if c.get("columnIndex") == col_idx:
                cell = c
                break
        if cell is None:
            continue
        url = cell.get("hyperlink")
        if not url:
            skipped += 1
            continue
        num = url.rsplit(args.separator, 1)[-1].strip()
        if not num.isdigit():
            print(f"[警告] 单元格 {cell.get('a1Index')} 末尾不是纯数字：{num!r}，已跳过")
            continue
        if cell.get("showValue") == num:
            already += 1
        edits.append({
            "range": f"{actual_idx}!{cell.get('a1Index')}",  # 使用实际 sheetIndex
            "cellType": "HYPERLINK",
            "value": num,
            "hyperlink": url,
        })

    print(f"      共 {len(edits)} 个超链接单元格"
          f"（{already} 个已显示为数字，{skipped} 个无链接跳过）")

    if not edits:
        print("[完成] 没有需要处理的内容")
        return

    print("\n      预览（前 3 条）：")
    for e in edits[:3]:
        print(f"        {e['range']}  显示 -> {e['value']}  （链接保留）")

    if args.dry_run:
        print("\n[dry-run] 仅预览，未写回。去掉 --dry-run 并加 --yes 可实际执行。")
        return

    if not args.yes:
        ans = input(f"\n确认要将 {len(edits)} 个单元格写回为「数字显示 + 原链接」吗？(y/N) ")
        if ans.strip().lower() != "y":
            print("已取消，未做任何修改。")
            return

    edits_file = f"/tmp/docs_edits_{spreadsheet_id}_{actual_idx}.json"
    with open(edits_file, "w", encoding="utf-8") as f:
        json.dump(edits, f, ensure_ascii=False)

    print(f"\n[4/4] 批量写回 {len(edits)} 个单元格 ...")
    proc = run_cli(cli_cmd, [
        "sheets", "+batch-write",
        "--spreadsheet-id", spreadsheet_id,
        "--edits-file", edits_file,
    ])
    data = ensure_ok(proc, "批量写入")
    biz = data.get("data") or {}
    print(f"[完成] 写入成功：{json.dumps(biz, ensure_ascii=False)[:300]}")



"""
execute example:

# 指定 sheet 名称（推荐）
python docs_hyperlink_rewriter.py \
  --spreadsheet-id fcADeqs2LSVXnuauvkn5MaVvd \
  --sheet-name "10.4-10.5" \
  --cli "kcli docs-cli" \
  --dry-run

# 指定 sheet 索引
python docs_hyperlink_rewriter.py \
  --spreadsheet-id fcADeqs2LSVXnuauvkn5MaVvd \
  --sheet-index 3 \
  --cli "kcli docs-cli" \
  --yes

# 如果不指定 sheet，多 sheet 表格会自动列出所有可用 sheet 并提示你选择

"""


if __name__ == "__main__":
    main()
