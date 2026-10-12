"""e-Stat「個人企業経済調査」の事業経営上の問題点別企業分布（xlsx）を取り込む CLI。

対象：産業分類 × 事業経営上の問題点（主なもの）の表（例：2025年 第14表、statInfId 000040413728）。
公式の Excel ファイルを手動でダウンロードして取り込む。APIキーは使わない。

使い方（backend ディレクトリで実行）:
    .venv/bin/python -m app.estat_import <xlsxファイル> \\
        --table-ref statInfId:000040413728 \\
        --source-url "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040413728&fileKind=0"

方針
- 表の見出し・単位・注記・問題点の項目名が想定と完全に一致しない場合は、推測せずにエラーとする。
- 元データの値（‰）をそのまま保存する。記号（'-' など）は原文を保持し、0 に変換しない。
- 業種内割合などの算出値は保存せず、API で計算して返す（元データと区別するため）。
- 同じファイルの再取り込みは「変更なし」。内容の異なるファイルは、1トランザクションで置き換える。
"""

import argparse
import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import db

SOURCE = "estat"
STAT_CODE = "00200541"  # 個人企業経済調査
PUBLISHER = "総務省統計局"
UNIT = "‰"
SCOPE_NOTE = (
    "個人企業経済調査は、個人企業（個人経営の事業所）を対象とする標本調査（約4万企業）です。"
    "法人企業を含む中小企業全体を代表するものではありません。"
)

# 事業経営上の問題点（主なもの）の項目。表の見出しと完全に一致することを確認する
PROBLEMS: tuple[tuple[str, str], ...] = (
    ("00", "総数"),
    ("01", "大手企業・同業者との競争の激化"),
    ("02", "需要の停滞（売上の停滞・減少）"),
    ("03", "製品・商品ニーズの変化への対応"),
    ("04", "建物・設備の狭小・老朽化"),
    ("05", "資金繰りの悪化"),
    ("06", "従業員の確保難・人材不足"),
    ("07", "人件費の増加"),
    ("08", "後継者難"),
    ("09", "原材料価格・仕入価格の上昇"),
    ("10", "販売価格の低下・値引要請"),
    ("11", "家賃・地代の上昇"),
    ("12", "コストの増加を販売価格に転嫁できない"),
)
PROBLEM_HEADERS = tuple("00_総数" if c == "00" else f"{c}_{n}（主なもの）" for c, n in PROBLEMS)

# 数値以外で許容するセルの記号（原文のまま保存する）
ALLOWED_SYMBOLS = frozenset({"-", "－", "…", "x", "X", "***"})

MAX_XML_BYTES = 20_000_000  # 展開後のサイズ上限（不正なファイル対策）

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")
_SURVEY = re.compile(r"^(\d{4})年（[^）]+）\s*個人企業経済調査$")
_TITLE = re.compile(r"^第([０-９0-9]+)表\s*(.*事業経営上の問題点別企業分布.*)$")
_INDUSTRY = re.compile(r"^(\d+)_(\S.*)$")
_INTEGER = re.compile(r"^\d+(?:\.0+)?$")
_TABLE_REF = re.compile(r"^(?:statInfId|statsDataId):\d+$")


class FormatError(ValueError):
    """Excel の形式が想定と異なる。"""


@dataclass
class Observation:
    industry_code: str
    industry_name: str
    industry_level: int
    parent_code: str | None
    is_subset: bool
    row_order: int
    problem_code: str
    problem_name: str
    value: float | None
    value_symbol: str | None


@dataclass
class ParsedTable:
    period: str
    survey_name: str
    table_no: str
    title: str
    note: str
    answer_type: str
    observations: list[Observation] = field(default_factory=list)

    @property
    def industry_count(self) -> int:
        return len({o.industry_code for o in self.observations})


# --- xlsx の読み取り（標準ライブラリのみ） --------------------------------------

def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        info = z.getinfo(name)
    except KeyError as e:
        raise FormatError(f"xlsx に {name} がありません") from e
    if info.file_size > MAX_XML_BYTES:
        raise FormatError(f"{name} が大きすぎます（{info.file_size} バイト）")
    return ET.fromstring(z.read(name))


def _string_item(si: ET.Element) -> str:
    """共有文字列・インライン文字列の本文。ふりがな（rPh）は含めない。"""
    parts = si.findall("m:t", _NS) + si.findall("m:r/m:t", _NS)
    return "".join(t.text or "" for t in parts)


def read_xlsx_rows(path: Path) -> list[list[str]]:
    """最初のシートを、行ごとの文字列リストとして返す（空セルは ''）。"""
    try:
        return _read_xlsx_rows(path)
    except (ET.ParseError, IndexError, ValueError) as e:
        if isinstance(e, FormatError):
            raise
        raise FormatError(f"xlsx の内容を読み取れません（{e}）") from e


def _read_xlsx_rows(path: Path) -> list[list[str]]:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise FormatError("xlsx（zip）として読み込めません") from e
    with z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in _read_xml(z, "xl/sharedStrings.xml").findall("m:si", _NS):
                shared.append(_string_item(si))
        workbook = _read_xml(z, "xl/workbook.xml")
        sheets = workbook.find("m:sheets", _NS)
        if sheets is None or len(sheets) != 1:
            raise FormatError("シートが1つではありません")
        rel_id = sheets[0].get(f"{{{_NS['r']}}}id")
        rels = _read_xml(z, "xl/_rels/workbook.xml.rels")
        target = next((r.get("Target") for r in rels if r.get("Id") == rel_id), None)
        if not target:
            raise FormatError("シートの参照が見つかりません")
        sheet_path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        sheet = _read_xml(z, sheet_path)

    rows: dict[int, dict[int, str]] = {}
    for c in sheet.iter(f"{{{_NS['m']}}}c"):
        m = _CELL_REF.match(c.get("r", ""))
        if not m:
            raise FormatError(f"セル参照が不正です: {c.get('r')}")
        kind = c.get("t")
        if kind == "inlineStr":
            inline = c.find("m:is", _NS)
            value = "" if inline is None else _string_item(inline)
        else:
            v = c.find("m:v", _NS)
            value = "" if v is None or v.text is None else v.text
            if kind == "s" and value != "":
                value = shared[int(value)]
        rows.setdefault(int(m.group(2)), {})[_col_index(m.group(1))] = value
    if not rows:
        raise FormatError("シートが空です")
    width = max(max(r) for r in rows.values()) + 1
    return [[rows.get(i, {}).get(j, "") for j in range(width)] for i in range(1, max(rows) + 1)]


# --- 表の解析と検証 -----------------------------------------------------------

def _cell(rows: list[list[str]], row: int, col: int) -> str:
    """1始まりの行番号・0始まりの列番号でセルを取得する。"""
    if row > len(rows) or col >= len(rows[row - 1]):
        return ""
    return rows[row - 1][col].strip()


def _to_int(text: str) -> int:
    return int(float(text))


def parse_problem_table(rows: list[list[str]]) -> ParsedTable:
    survey = _cell(rows, 1, 0)
    m = _SURVEY.match(survey)
    if not m:
        raise FormatError(f"1行目が「YYYY年（…） 個人企業経済調査」ではありません: {survey!r}")
    period = m.group(1)

    title_cell = _cell(rows, 2, 0)
    t = _TITLE.match(title_cell)
    if not t:
        raise FormatError(f"2行目が事業経営上の問題点別企業分布の表題ではありません: {title_cell!r}")
    table_no = t.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))

    note = _cell(rows, 3, 0)
    if "最も大きな問題点として一つだけ回答" not in note:
        raise FormatError(f"3行目に単一回答（主なもの）の注記がありません: {note!r}")

    # 問題点の列：6行目が「事業経営上の問題点」の連続した列
    problem_cols = [j for j, v in enumerate(rows[5]) if v.strip() == "事業経営上の問題点"] if len(rows) >= 6 else []
    if len(problem_cols) != len(PROBLEMS) or problem_cols != list(range(problem_cols[0], problem_cols[0] + len(PROBLEMS))):
        raise FormatError(f"事業経営上の問題点の列が {len(PROBLEMS)} 列連続していません（{len(problem_cols)} 列）")
    headers = tuple(_cell(rows, 7, j) for j in problem_cols)
    if headers != PROBLEM_HEADERS:
        diff = [(e, a) for e, a in zip(PROBLEM_HEADERS, headers) if e != a]
        raise FormatError(f"問題点の項目名が想定と異なります: {diff[:3]}")
    for j in problem_cols:
        if _cell(rows, 5, j) != "企業分布（千分率）":
            raise FormatError(f"5行目の表章項目が「企業分布（千分率）」ではありません（列 {j}）")
        if _cell(rows, 8, j) != UNIT:
            raise FormatError(f"8行目の単位が「{UNIT}」ではありません（列 {j}）: {_cell(rows, 8, j)!r}")
    axis = [_cell(rows, 9, j) for j in range(4)]
    if axis[:3] != ["階層レベル", "地域区分", "階層レベル"] or not axis[3].startswith("産業"):
        raise FormatError(f"9行目の軸見出しが想定と異なります: {axis}")

    table = ParsedTable(period, survey, table_no, f"第{table_no}表 {t.group(2).strip()}", note, "single")
    stack: list[tuple[int, str]] = []  # (階層レベル, 業種コード)
    seen_codes: set[str] = set()
    for i in range(10, len(rows) + 1):
        if not any(v.strip() for v in rows[i - 1]):
            continue
        area_level, area, level_text, industry = (_cell(rows, i, j) for j in range(4))
        if area_level != "1" or area != "00_全国":
            raise FormatError(f"{i}行目：全国以外の地域行は対象外です: {area!r}")
        if not level_text.isdigit() or not 1 <= int(level_text) <= 4:
            raise FormatError(f"{i}行目：産業の階層レベルが不正です: {level_text!r}")
        level = int(level_text)
        im = _INDUSTRY.match(industry)
        if not im:
            raise FormatError(f"{i}行目：業種が「コード_名称」の形式ではありません: {industry!r}")
        code, name = im.group(1), im.group(2)
        if code in seen_codes:
            raise FormatError(f"{i}行目：業種コードが重複しています: {code}")
        seen_codes.add(code)

        # 親業種：直前の、より上位の階層の行
        while stack and stack[-1][0] >= level:
            stack.pop()
        if level == 1:
            if stack or table.observations:
                raise FormatError(f"{i}行目：階層レベル1（全産業）は先頭の1行だけのはずです")
            parent = None
        else:
            if not stack or stack[-1][0] != level - 1:
                raise FormatError(f"{i}行目：階層レベル {level} の親業種（レベル {level - 1}）がありません")
            parent = stack[-1][1]
            # 大分類より下の階層では、業種コードが親のコードで始まることを確認する
            if level >= 3 and not code.startswith(parent):
                raise FormatError(f"{i}行目：業種コード {code} が親業種 {parent} と整合しません")
        stack.append((level, code))

        for (p_code, p_name), j in zip(PROBLEMS, problem_cols):
            raw = _cell(rows, i, j)
            if _INTEGER.match(raw):
                value, symbol = float(_to_int(raw)), None
            elif raw in ALLOWED_SYMBOLS:
                value, symbol = None, raw
            else:
                raise FormatError(f"{i}行目：{p_code} の値が数値でも既知の記号でもありません: {raw!r}")
            table.observations.append(Observation(
                code, name, level, parent, name.startswith("うち"), i, p_code, p_name, value, symbol,
            ))
    if not table.observations:
        raise FormatError("データ行がありません")
    if table.observations[0].industry_level != 1:
        raise FormatError("先頭のデータ行が全産業（階層レベル1）ではありません")
    return table


def consistency_warnings(table: ParsedTable) -> list[str]:
    """集計の整合性を確認する（丸めによる差もあり得るため、エラーではなく警告とする）。

    - 12項目の合計が総数を超えないこと
    - 「うち〜」以外の子業種の総数の合計が、親業種の総数と一致すること（中分類以下は大分類が分母のため）
    """
    totals: dict[str, float | None] = {}
    listed: dict[str, float] = {}
    meta: dict[str, Observation] = {}
    for o in table.observations:
        meta.setdefault(o.industry_code, o)
        if o.problem_code == "00":
            totals[o.industry_code] = o.value
        elif o.value is not None:
            listed[o.industry_code] = listed.get(o.industry_code, 0) + o.value
    warnings = []
    for code, total in totals.items():
        if total is not None and listed.get(code, 0) > total:
            warnings.append(f"業種 {code}：12項目の合計 {listed[code]:g} が総数 {total:g} を超えています")
    children: dict[str, list[str]] = {}
    for code, o in meta.items():
        if o.parent_code is not None and o.industry_level >= 3 and not o.is_subset:
            children.setdefault(o.parent_code, []).append(code)
    for parent, codes in children.items():
        values = [totals.get(c) for c in codes]
        if None in values or totals.get(parent) is None:
            continue
        if sum(values) != totals[parent]:
            warnings.append(f"業種 {parent}：内訳の総数の合計 {sum(values):g} が総数 {totals[parent]:g} と一致しません")
    return warnings


# --- 取り込み -----------------------------------------------------------------

@dataclass
class ImportResult:
    status: str  # 'inserted' | 'replaced' | 'unchanged'
    stat_table_id: int
    period: str
    title: str
    industries: int
    observations: int
    symbols: int
    warnings: list[str] = field(default_factory=list)


def import_file(path: Path, table_ref: str, source_url: str) -> ImportResult:
    if not _TABLE_REF.match(table_ref):
        raise ValueError("table_ref は 'statInfId:数字' または 'statsDataId:数字' で指定してください")
    if not source_url.startswith("https://www.e-stat.go.jp/"):
        raise ValueError("source_url は e-Stat（https://www.e-stat.go.jp/）のURLを指定してください")
    content = path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    table = parse_problem_table(read_xlsx_rows(path))

    with db.connect() as conn:  # 例外時は commit されず、ロールバックされる
        db.migrate(conn)  # 未適用のマイグレーションだけ適用する（情報源・記事のテーブルには触れない）
        other = conn.execute(
            "SELECT table_ref, period FROM stat_tables WHERE file_sha256 = ? AND NOT (source = ? AND table_ref = ?)",
            (sha256, SOURCE, table_ref),
        ).fetchone()
        if other:
            raise ValueError(f"同じファイルが別の統計表（{other['table_ref']}、{other['period']}年）として取り込み済みです")
        existing = conn.execute(
            "SELECT id, file_sha256 FROM stat_tables WHERE source = ? AND table_ref = ? AND period = ?",
            (SOURCE, table_ref, table.period),
        ).fetchone()
        if existing and existing["file_sha256"] == sha256:
            status, table_id = "unchanged", existing["id"]
        else:
            values = (SOURCE, STAT_CODE, table_ref, table.table_no, table.title, table.survey_name, table.period,
                      PUBLISHER, source_url, UNIT, table.answer_type, table.note, SCOPE_NOTE, sha256, db.now())
            if existing:
                status, table_id = "replaced", existing["id"]
                conn.execute("DELETE FROM problem_observations WHERE stat_table_id = ?", (table_id,))
                conn.execute(
                    """UPDATE stat_tables SET source = ?, stat_code = ?, table_ref = ?, table_no = ?, title = ?,
                         survey_name = ?, period = ?, publisher = ?, source_url = ?, unit = ?, answer_type = ?,
                         note = ?, scope_note = ?, file_sha256 = ?, imported_at = ? WHERE id = ?""",
                    (*values, table_id),
                )
            else:
                status = "inserted"
                table_id = conn.execute(
                    """INSERT INTO stat_tables (source, stat_code, table_ref, table_no, title, survey_name, period,
                         publisher, source_url, unit, answer_type, note, scope_note, file_sha256, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values,
                ).lastrowid
            _insert_observations(conn, table_id, table.observations)

    return ImportResult(status, table_id, table.period, table.title, table.industry_count,
                        len(table.observations), sum(1 for o in table.observations if o.value_symbol),
                        consistency_warnings(table))


def _insert_observations(conn: sqlite3.Connection, table_id: int, observations: list[Observation]) -> None:
    conn.executemany(
        """INSERT INTO problem_observations
           (stat_table_id, area_code, industry_code, industry_name, industry_level, parent_code, is_subset,
            row_order, problem_code, problem_name, value, value_symbol)
           VALUES (?, '00', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(table_id, o.industry_code, o.industry_name, o.industry_level, o.parent_code, int(o.is_subset),
          o.row_order, o.problem_code, o.problem_name, o.value, o.value_symbol) for o in observations],
    )


STATUS_NAMES = {"inserted": "新規に取り込みました", "replaced": "修正版として置き換えました", "unchanged": "変更なし（同じファイルは取り込み済み）"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="e-Stat 個人企業経済調査（事業経営上の問題点別企業分布）の xlsx を取り込みます。")
    parser.add_argument("path", type=Path, help="e-Stat からダウンロードした xlsx ファイル")
    parser.add_argument("--table-ref", required=True, help="例：statInfId:000040413728")
    parser.add_argument("--source-url", required=True, help="ダウンロード元の e-Stat の URL")
    args = parser.parse_args(argv)
    try:
        r = import_file(args.path, args.table_ref, args.source_url)
    except (FormatError, ValueError, OSError) as e:
        parser.exit(1, f"取り込みを中止しました：{e}\n")
    print(f"{STATUS_NAMES[r.status]}：{r.title}（{r.period}年）")
    print(f"業種：{r.industries}、観測値：{r.observations}（うち記号のセル：{r.symbols}）")
    print("整合性：問題なし" if not r.warnings else "整合性の警告：\n" + "\n".join(f"  - {w}" for w in r.warnings))
    print("※ 統計上の経営課題（最も大きな問題点を1つ回答）であり、ソフトウェア需要や支払意思を示すものではありません。")


if __name__ == "__main__":
    main()
