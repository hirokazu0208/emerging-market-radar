"""e-Stat 個人企業経済調査（事業経営上の問題点）の取り込み・API のテスト。

実データ（tests/data/estat_kojinke_2025_t14.xlsx：e-Stat の公式ファイルを無加工で保存）を使って、
件数・階層・集計の整合性を確認する。形式エラーのテストは、実データの行を一部だけ書き換えて行う。
"""

import copy
import sqlite3
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

from app import db, estat_import, main
from app.estat_import import FormatError, import_file, parse_problem_table, read_xlsx_rows
from app.problems import get_problems
from app.sources import SOURCES

REAL_XLSX = Path(__file__).parent / "data" / "estat_kojinke_2025_t14.xlsx"
TABLE_REF = "statInfId:000040413728"
SOURCE_URL = "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040413728&fileKind=0"


@pytest.fixture(scope="module")
def real_rows():
    return read_xlsx_rows(REAL_XLSX)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))
    db.init_db(SOURCES)
    return tmp_path


def write_xlsx(path: Path, rows: list[list[str]]) -> Path:
    """行のリストから、1シートの最小限の xlsx を作る（テスト用）。"""
    def col(j):
        s = ""
        j += 1
        while j:
            j, r = divmod(j - 1, 26)
            s = chr(65 + r) + s
        return s
    cells = "".join(
        f'<row r="{i}">' + "".join(
            f'<c r="{col(j)}{i}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for j, v in enumerate(row) if v != ""
        ) + "</row>"
        for i, row in enumerate(rows, start=1)
    )
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{m}" xmlns:r="{r}"><sheets><sheet name="14" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{m}"><sheetData>{cells}</sheetData></worksheet>')
    return path


def find_row(rows, industry_label):
    return next(i for i, r in enumerate(rows) if len(r) > 3 and r[3] == industry_label)


P = 15  # 実データで「事業経営上の問題点」が始まる列（P列、0始まり）


# --- 実データの解析 -----------------------------------------------------------

def test_real_table_counts_and_metadata(real_rows):
    t = parse_problem_table(real_rows)
    assert (t.period, t.table_no, t.answer_type) == ("2025", "14", "single")
    assert t.survey_name == "2025年（令和7年） 個人企業経済調査"
    assert "最も大きな問題点として一つだけ回答" in t.note
    assert t.industry_count == 35
    assert len(t.observations) == 35 * 13
    symbols = [o for o in t.observations if o.value_symbol]
    assert len(symbols) == 8 and {o.value_symbol for o in symbols} == {"-"}
    assert all(o.value is None for o in symbols)  # 記号は 0 に変換しない


def test_real_table_hierarchy_and_subsets(real_rows):
    t = parse_problem_table(real_rows)
    meta = {o.industry_code: o for o in t.observations}
    assert len(meta) == 35  # 業種コードは一意
    assert (meta["0"].industry_level, meta["0"].parent_code) == (1, None)
    assert {c for c, o in meta.items() if o.industry_level == 2} == {"1", "2", "3", "4", "5", "6"}
    assert all(meta[c].parent_code == "0" for c in "123456")
    assert (meta["11"].parent_code, meta["11"].is_subset) == ("1", False)
    assert (meta["51"].parent_code, meta["51"].is_subset) == ("5", True)  # うち生活関連サービス業
    assert (meta["621"].industry_level, meta["621"].parent_code, meta["621"].is_subset) == (4, "62", True)
    for o in meta.values():  # 中分類以下は親のコードで始まる
        if o.industry_level >= 3:
            assert o.industry_code.startswith(o.parent_code)
    assert sum(1 for o in meta.values() if o.is_subset) == 13


def test_real_table_aggregates_are_consistent(real_rows):
    t = parse_problem_table(real_rows)
    assert estat_import.consistency_warnings(t) == []
    v = {(o.industry_code, o.problem_code): o.value for o in t.observations}
    assert v[("0", "00")] == 1000 and v[("0", "02")] == 256 and v[("0", "09")] == 179
    assert sum(v[("0", f"{i:02d}")] for i in range(1, 13)) == 840  # 残り 160 は表に掲載のない回答
    # 中分類以下は大分類が分母：網羅的な内訳の総数の合計は大分類の 1000 に一致する
    assert v[("11", "00")] + v[("12", "00")] + v[("13", "00")] == 1000
    assert sum(v[(c, "00")] for c in ("21", "22", "23", "24", "25", "26", "27")) == 1000
    assert v[("621", "00")] == 278 and v[("62", "00")] == 282


# --- 形式エラー（推測して読み進めない） -------------------------------------------

def mutate(rows, row, col, value):
    rows = copy.deepcopy(rows)
    while len(rows[row - 1]) <= col:
        rows[row - 1].append("")
    rows[row - 1][col] = value
    return rows


@pytest.mark.parametrize(
    "row, col, value, message",
    [
        (1, 0, "2025年 家計調査", "1行目"),
        (2, 0, "第１４表　産業中分類別売上高", "2行目"),
        (3, 0, "1) 複数回答", "単一回答"),
        (7, P + 2, "02_需要の停滞（主なもの）", "項目名"),  # 項目名の変更
        (8, P + 3, "%", "単位"),
        (5, P + 1, "企業数", "表章項目"),
        (6, P + 12, "今後の事業展開", "13 列連続"),
        (9, 3, "地域", "9行目"),
        (11, 3, "建設業", "コード_名称"),
        (12, 3, "1_総合工事業", "重複"),  # 業種コードの重複
        (12, 2, "4", "親業種"),  # 階層の飛び
        (12, P + 1, "", "数値でも既知の記号でもありません"),  # 空セル
        (12, P + 1, "12.5", "数値でも既知の記号でもありません"),
        (12, P + 1, "△", "数値でも既知の記号でもありません"),
        (10, 1, "13_東京都", "全国以外"),
    ],
)
def test_unexpected_format_raises(real_rows, row, col, value, message):
    with pytest.raises(FormatError, match=message):
        parse_problem_table(mutate(real_rows, row, col, value))


def test_child_code_must_match_parent(real_rows):
    i = find_row(real_rows, "12_職別工事業(設備工事業を除く)")
    rows = mutate(real_rows, i + 1, 3, "92_職別工事業(設備工事業を除く)")
    with pytest.raises(FormatError, match="整合しません"):
        parse_problem_table(rows)


def test_non_xlsx_file_raises(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_text("not a zip")
    with pytest.raises(FormatError, match="xlsx"):
        read_xlsx_rows(bad)


# --- 取り込み -----------------------------------------------------------------

def counts():
    with db.connect() as conn:
        return (conn.execute("SELECT COUNT(*) FROM stat_tables").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM problem_observations").fetchone()[0])


def test_import_real_file_then_reimport_is_unchanged(tmp_db):
    first = import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    assert (first.status, first.industries, first.observations, first.symbols) == ("inserted", 35, 455, 8)
    assert first.warnings == []
    second = import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    assert (second.status, second.stat_table_id) == ("unchanged", first.stat_table_id)
    assert counts() == (1, 455)
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM problem_observations WHERE industry_code = '12' AND problem_code = '11'").fetchone()
        assert (row["value"], row["value_symbol"]) == (None, "-")
        t = conn.execute("SELECT * FROM stat_tables").fetchone()
        assert (t["unit"], t["answer_type"], t["publisher"], t["source_url"]) == ("‰", "single", "総務省統計局", SOURCE_URL)
        assert "個人企業" in t["scope_note"] and "代表するものではありません" in t["scope_note"]


def test_revised_file_replaces_in_one_transaction(tmp_db, tmp_path, real_rows):
    import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    i = find_row(real_rows, "0_全産業")
    revised = write_xlsx(tmp_path / "revised.xlsx", mutate(real_rows, i + 1, P + 2, "250"))
    result = import_file(revised, TABLE_REF, SOURCE_URL)
    assert result.status == "replaced"
    assert counts() == (1, 455)
    with db.connect() as conn:
        assert conn.execute(
            "SELECT value FROM problem_observations WHERE industry_code = '0' AND problem_code = '02'"
        ).fetchone()[0] == 250


def test_failed_import_rolls_back(tmp_db, tmp_path, real_rows, monkeypatch):
    import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    with db.connect() as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM problem_observations ORDER BY industry_code, problem_code")]
        sha_before = conn.execute("SELECT file_sha256 FROM stat_tables").fetchone()[0]

    original = estat_import._insert_observations

    def failing(conn, table_id, observations):
        original(conn, table_id, observations[:10])
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(estat_import, "_insert_observations", failing)
    i = find_row(real_rows, "0_全産業")
    revised = write_xlsx(tmp_path / "revised.xlsx", mutate(real_rows, i + 1, P + 2, "250"))
    with pytest.raises(sqlite3.OperationalError):
        import_file(revised, TABLE_REF, SOURCE_URL)

    with db.connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM problem_observations ORDER BY industry_code, problem_code")] == before
        assert conn.execute("SELECT file_sha256 FROM stat_tables").fetchone()[0] == sha_before


def test_invalid_format_does_not_touch_db(tmp_db, tmp_path, real_rows):
    bad = write_xlsx(tmp_path / "bad.xlsx", mutate(real_rows, 8, P, "%"))
    with pytest.raises(FormatError):
        import_file(bad, TABLE_REF, SOURCE_URL)
    assert counts() == (0, 0)


@pytest.mark.parametrize(
    "table_ref, source_url",
    [("000040413728", SOURCE_URL), (TABLE_REF, "https://example.com/14.xlsx")],
)
def test_invalid_reference_is_rejected(tmp_db, table_ref, source_url):
    with pytest.raises(ValueError):
        import_file(REAL_XLSX, table_ref, source_url)


def test_import_does_not_modify_news_data(tmp_db):
    with db.connect() as conn:
        conn.execute("INSERT INTO articles (source, external_id, title, url, published_at, category, fetched_at, language) "
                     "VALUES ('hackernews', '1', 't', 'u', '2026-10-12T00:00:00+00:00', 'Other', 'f', 'en')")
        snapshot = lambda: [tuple(r) for t in ("articles", "article_markets", "article_labels")
                            for r in conn.execute(f"SELECT * FROM {t}")]
    with db.connect() as conn:
        before = snapshot()
    import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    with db.connect() as conn:
        assert snapshot() == before


def test_cli_reports_counts(tmp_db, capsys):
    estat_import.main([str(REAL_XLSX), "--table-ref", TABLE_REF, "--source-url", SOURCE_URL])
    out = capsys.readouterr().out
    assert "新規に取り込みました" in out and "業種：35、観測値：455（うち記号のセル：8）" in out
    assert "整合性：問題なし" in out and "ソフトウェア需要や支払意思を示すものではありません" in out
    estat_import.main([str(REAL_XLSX), "--table-ref", TABLE_REF, "--source-url", SOURCE_URL])
    assert "変更なし" in capsys.readouterr().out


def test_cli_exits_with_error_on_bad_file(tmp_db, tmp_path, capsys):
    bad = tmp_path / "bad.xlsx"
    bad.write_text("x")
    with pytest.raises(SystemExit) as e:
        estat_import.main([str(bad), "--table-ref", TABLE_REF, "--source-url", SOURCE_URL])
    assert e.value.code == 1
    assert "取り込みを中止しました" in capsys.readouterr().err


# --- 算出値と API ---------------------------------------------------------------

@pytest.fixture
def imported(tmp_db):
    return import_file(REAL_XLSX, TABLE_REF, SOURCE_URL).stat_table_id


def test_computed_values_are_separate_from_source_values(imported):
    data = get_problems(imported)
    ind = {i["code"]: i for i in data["industries"]}
    food = ind["42"]  # 飲食サービス業：中分類は大分類の企業全体が分母
    assert food["total"] == {"value": 911, "symbol": None}
    item = next(c for c in food["items"] if c["code"] == "09")
    assert item["value"] == 429  # 元データ（‰、宿泊業・飲食サービス業=1000）
    assert item["computed_share_pct"] == round(429 / 911 * 100, 2)  # 本アプリの算出（業種内割合）
    assert food["base_industry"] == {"code": "4", "name": "宿泊業，飲食サービス業"}
    assert ind["621"]["base_industry"]["code"] == "6"
    assert ind["0"]["unlisted"]["computed_value"] == 160
    assert ind["0"]["unlisted"]["computed_share_pct"] == 16.0
    assert ind["61"]["low_precision"] is True and ind["1"]["low_precision"] is False
    symbol = next(c for c in ind["12"]["items"] if c["code"] == "11")
    assert (symbol["value"], symbol["symbol"], symbol["computed_share_pct"]) == (None, "-", None)
    assert ind["12"]["unlisted"]["includes_symbol_cells"] is True


@pytest.fixture
def client(imported):
    with TestClient(main.app) as c:
        yield c


def test_problem_tables_api(client):
    t = client.get("/api/problem-tables").json()["tables"][0]
    assert t["attribution"] == (
        "出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）「2025年（令和7年） 個人企業経済調査」"
        "第14表（総務省統計局）を加工して作成"
    )
    assert "業種内割合" in t["processing_note"]
    assert "支払意思" in t["interpretation_note"]
    assert "代表するものではありません" in t["scope_note"]


def test_problems_api_filters(client):
    data = client.get("/api/problems").json()  # 省略時は最新の表
    assert len(data["industries"]) == 35 and len(data["problems"]) == 12
    sub = client.get("/api/problems", params={"industry": "6"}).json()["industries"]
    assert [i["code"] for i in sub] == ["6", "61", "62", "621", "63", "631", "632", "64", "641", "65", "651", "66", "661"]
    one = client.get("/api/problems", params={"problem": "09"}).json()["industries"]
    assert all([c["code"] for c in i["items"]] == ["09"] for i in one)


@pytest.mark.parametrize("params", [{"industry": "999"}, {"problem": "13"}])
def test_problems_api_rejects_unknown_codes(client, params):
    assert client.get("/api/problems", params=params).status_code == 422


def test_problems_api_unknown_table(client):
    assert client.get("/api/problems", params={"table_id": 999}).status_code == 404


def test_problems_api_without_data(tmp_db):
    with TestClient(main.app) as c:
        assert c.get("/api/problems").json() == {"table": None, "problems": [], "industries": []}


# --- マイグレーション v4 → v5 ---------------------------------------------------

def test_v4_db_migrates_to_v5_preserving_news_data(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.BASE_SCHEMA)
    for v in (2, 3, 4):
        conn.executescript(f"BEGIN;\n{db.MIGRATIONS[v]}\nPRAGMA user_version = {v};\nCOMMIT;")
    conn.executescript("""
        INSERT INTO articles (id, source, external_id, title, url, published_at, category, fetched_at, language)
          VALUES (1, 'egov_pcm', '1', 't', 'u', '2026-10-12T00:00:00+00:00', 'Policy & Regulation', 'f', 'ja');
        INSERT INTO article_markets VALUES (1, 'JP', 'source_default', 'c');
        INSERT INTO article_labels (article_id, taxonomy, label, method, evidence, model, created_at, strength, rule_version)
          VALUES (1, 'signal_type', 'regulation', 'keyword', 'e', NULL, 'c', 'explicit', 'kw-2026.10.1');
    """)
    tables = ("articles", "article_markets", "article_labels")
    before = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    conn.close()
    monkeypatch.setenv("RADAR_DB_PATH", str(path))

    db.init_db(SOURCES)

    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables} == before
    assert conn.execute("SELECT COUNT(*) FROM stat_tables").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):  # 値と記号はどちらか一方だけ
        conn.execute("INSERT INTO stat_tables (id, source, stat_code, table_ref, table_no, title, survey_name, period, publisher,"
                     " source_url, unit, answer_type, scope_note, file_sha256, imported_at)"
                     " VALUES (1, 's', 'c', 'r', '1', 't', 'n', '2025', 'p', 'u', '‰', 'single', 'x', 'h', 'i')")
        conn.execute("INSERT INTO problem_observations VALUES (1, '00', '0', 'n', 1, NULL, 0, 1, '00', 'n', NULL, NULL)")
    conn.close()


# --- 最終レビューで追加したテスト -------------------------------------------------

def test_phonetic_runs_are_excluded_from_text(tmp_path):
    # 日本語の Excel では、共有文字列にふりがな（rPh）が含まれることがある
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    path = tmp_path / "ruby.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{m}" xmlns:r="{r}"><sheets><sheet name="1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{m}"><si><t>建設業</t><rPh sb="0" eb="3"><t>ケンセツギョウ</t></rPh></si>'
                   '<si><r><t>総合</t></r><r><t>工事業</t></r></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{m}"><sheetData><row r="1">'
                   '<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row></sheetData></worksheet>')
    assert read_xlsx_rows(path) == [["建設業", "総合工事業"]]


@pytest.mark.parametrize(
    "sheet",
    ["<worksheet><broken", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
     '<sheetData><row r="1"><c r="A1" t="s"><v>5</v></c></row></sheetData></worksheet>'],
)
def test_malformed_xlsx_content_raises_format_error(tmp_path, sheet):
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    path = tmp_path / "broken.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{m}" xmlns:r="{r}"><sheets><sheet name="1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)  # 不正な XML／存在しない共有文字列の参照
    with pytest.raises(FormatError):
        read_xlsx_rows(path)


def test_same_file_under_another_table_ref_is_rejected(tmp_db):
    import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    with pytest.raises(ValueError, match="別の統計表"):
        import_file(REAL_XLSX, "statsDataId:0003421686", SOURCE_URL)
    assert counts() == (1, 455)


def test_import_does_not_touch_sources_table(tmp_db):
    with db.connect() as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM sources ORDER BY id")]
    import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    with db.connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM sources ORDER BY id")] == before  # updated_at も変わらない


def test_import_into_fresh_db_applies_migrations(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "fresh.db"))
    assert import_file(REAL_XLSX, TABLE_REF, SOURCE_URL).status == "inserted"
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_problems_api_treats_empty_params_as_unspecified(client):
    data = client.get("/api/problems", params={"industry": "", "problem": ""}).json()
    assert len(data["industries"]) == 35 and all(len(i["items"]) == 12 for i in data["industries"])


@pytest.mark.parametrize("params", [{"table_id": "abc"}, {"table_id": "1.5"}])
def test_problems_api_rejects_invalid_table_id(client, params):
    assert client.get("/api/problems", params=params).status_code == 422
