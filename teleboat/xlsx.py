"""Excel (.xlsx) を標準ライブラリだけで書く (収支の記録の出力用。Google スプレッドシートにもそのまま読み込める)。

book([("シート名", rows), ...]) -> bytes
  rows は [[値...], ...]。1行目は見出しとして色を付けて固定する (前の版と同じ使い方)。
book([Sheet(...), ...]) で、色・大きな文字・数の形式・セルの結合・グラフも使える。
  値は 数・文字・None (空欄)、または Cell(値, "スタイル名")。スタイル名は STYLES のキー。
"""
import io
import zipfile
from xml.sax.saxutils import escape

# ---- 色 (やわらかい色で、印刷してもつぶれない濃さ) ----
NAVY, BLUE, GREEN, RED, GRAY = "1F3B5C", "2E6DA4", "1E7B3C", "C0392B", "6B7280"
FILL_HEAD, FILL_CARD, FILL_HIT, FILL_WAIT, FILL_PLUS, FILL_MINUS = "1F3B5C", "EEF3F9", "E3F4E8", "F1F2F4", "E3F4E8", "FBE5E2"

# 数の形式 (id 164〜)
NUMFMT = {"yen": '#,##0"円"', "syen": '+#,##0"円";-#,##0"円";0"円"', "pct": "0.0%", "f1": "0.0", "f2": "0.00", "int": "#,##0"}

# スタイル: (font, fill, numfmt, align)  font = (大きさ, 太字, 色)
STYLES = {
    "": ((11, False, None), None, None, None),
    "head": ((11, True, "FFFFFF"), FILL_HEAD, None, "center"),
    "title": ((18, True, NAVY), None, None, None),
    "sub": ((10, False, GRAY), None, None, None),
    "h2": ((13, True, NAVY), None, None, None),
    "label": ((11, True, GRAY), FILL_CARD, None, "center"),
    "big_pct": ((26, True, BLUE), FILL_CARD, "pct", "center"),
    "big_yen": ((22, True, NAVY), FILL_CARD, "yen", "center"),
    "big_plus": ((22, True, GREEN), FILL_PLUS, "syen", "center"),
    "big_minus": ((22, True, RED), FILL_MINUS, "syen", "center"),
    "big_text": ((16, True, NAVY), FILL_CARD, None, "center"),
    "card": ((11, False, GRAY), FILL_CARD, None, "center"),
    "yen": ((11, False, None), None, "yen", None),
    "int": ((11, False, None), None, "int", None),
    "pct": ((11, False, None), None, "pct", None),
    "f1": ((11, False, None), None, "f1", None),
    "f2": ((11, False, None), None, "f2", None),
    "plus": ((11, True, GREEN), None, "syen", None),
    "minus": ((11, True, RED), None, "syen", None),
    "pct_good": ((11, True, GREEN), None, "pct", None),
    "pct_bad": ((11, True, RED), None, "pct", None),
    "f2_good": ((11, True, GREEN), None, "f2", None),
    "hit": ((11, True, GREEN), FILL_HIT, None, None),
    "wait": ((11, False, GRAY), FILL_WAIT, None, None),
    "total": ((11, True, NAVY), FILL_CARD, None, None),
    "total_yen": ((11, True, NAVY), FILL_CARD, "yen", None),
    "total_pct": ((11, True, NAVY), FILL_CARD, "pct", None),
    "total_syen": ((11, True, NAVY), FILL_CARD, "syen", None),
}
# 行ごとの色: 「名前@hit」(当たり・薄い緑) 「名前@wait」(結果待ち・薄い灰色)
for _base in ("", "yen", "int", "pct", "f1", "f2", "f2_good", "plus", "minus"):
    _font, _fill, _fmt, _al = STYLES[_base]
    STYLES[_base + "@hit"] = (_font, FILL_HIT, _fmt, _al)
    STYLES[_base + "@wait"] = (_font, FILL_WAIT, _fmt, _al)
_NAMES = list(STYLES)


class Cell:
    __slots__ = ("v", "s")

    def __init__(self, v, s=""):
        self.v, self.s = v, s


class Sheet:
    """name: シート名。rows: 行のリスト。head: 1行目を見出しにする。freeze: 固定する行数。
    widths: {列番号: 幅}。merges: ["A1:D1", ...]。heights: {行番号(1から): 高さ}。
    charts: [Chart, ...]"""
    def __init__(self, name, rows, head=True, freeze=None, widths=None, merges=None, heights=None, charts=None):
        self.name, self.rows, self.head = name, rows, head
        self.freeze = (1 if head else 0) if freeze is None else freeze
        self.widths, self.merges, self.heights, self.charts = widths or {}, merges or [], heights or {}, charts or []


class Chart:
    """kind: "line" か "bar"。title: 見出し。sheet: データのシート名。cats: 項目の範囲 ("A2:A30")。
    series: [(名前, 値の範囲, 色), ...]。anchor: (左上の列, 行, 右下の列, 行) (0 から)。pct: 縦軸を % にする"""
    def __init__(self, kind, title, sheet, cats, series, anchor, pct=False):
        self.kind, self.title, self.sheet, self.cats, self.series, self.anchor, self.pct = kind, title, sheet, cats, series, anchor, pct


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, v, style=0):
    st = f' s="{style}"' if style else ""
    if v is None or v == "":
        return f'<c r="{ref}"{st}/>' if style else ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"{st}><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"{st}><v>{v}</v></c>'
    text = escape(str(v)).replace("\n", "&#10;")
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{text}</t></is></c>'


def _width(v):
    if v is None:
        return 0
    if isinstance(v, float):
        v = f"{v:,.2f}"
    elif isinstance(v, int):
        v = f"{v:,}円"
    return sum(2 if ord(ch) > 255 else 1 for ch in str(v))


def _sheet_xml(sh, drawing):
    widths = {}
    for i, row in enumerate(sh.rows):
        for j, v in enumerate(row):
            val = v.v if isinstance(v, Cell) else v
            big = isinstance(v, Cell) and v.s.startswith("big")
            n = _width(val) * (2 if big else 1)
            widths[j] = max(widths.get(j, 6), min(n + 2, 40))
    widths.update(sh.widths)
    cols = "".join(f'<col min="{j + 1}" max="{j + 1}" width="{w}" customWidth="1"/>' for j, w in sorted(widths.items()))
    body = []
    for i, row in enumerate(sh.rows):
        cells = []
        for j, v in enumerate(row):
            if isinstance(v, Cell):
                style = _NAMES.index(v.s) if v.s in STYLES else 0
                v = v.v
            else:
                style = _NAMES.index("head") if (sh.head and i == 0) else 0
            cells.append(_cell(f"{_col(j)}{i + 1}", v, style))
        ht = sh.heights.get(i + 1)
        hta = f' ht="{ht}" customHeight="1"' if ht else ""
        body.append(f'<row r="{i + 1}"{hta}>{"".join(cells)}</row>')
    if sh.freeze:
        top = f"A{sh.freeze + 1}"
        view = (f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{sh.freeze}" topLeftCell="{top}" '
                'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    else:
        view = '<sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>'
    merges = (f'<mergeCells count="{len(sh.merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in sh.merges) + "</mergeCells>"
              if sh.merges else "")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
            f'{view}<sheetFormatPr defaultRowHeight="18"/>'
            f'{"<cols>" + cols + "</cols>" if cols else ""}<sheetData>{"".join(body)}</sheetData>{merges}'
            '<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.3" footer="0.3"/>'
            '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
            + ('<drawing r:id="rId1"/>' if drawing else "") + '</worksheet>')


def _styles_xml():
    fonts, fills, fmts, xfs = [], ["none", "gray125"], [], []
    font_ix, fill_ix = {}, {}
    for name in _NAMES:
        font, fill, fmt, align = STYLES[name]
        if font not in font_ix:
            font_ix[font] = len(fonts)
            fonts.append(font)
        if fill and fill not in fill_ix:
            fill_ix[fill] = len(fills)
            fills.append(fill)
    fmt_ids = {k: 164 + i for i, k in enumerate(NUMFMT)}
    for name in _NAMES:
        font, fill, fmt, align = STYLES[name]
        a = f'<alignment horizontal="{align}" vertical="center"/>' if align else '<alignment vertical="center"/>'
        xfs.append(f'<xf numFmtId="{fmt_ids[fmt] if fmt else 0}" fontId="{font_ix[font]}" fillId="{fill_ix[fill] if fill else 0}" '
                   f'borderId="0" xfId="0" applyFont="1" applyFill="1" applyNumberFormat="1" applyAlignment="1">{a}</xf>')
    font_xml = "".join("<font>" + ("<b/>" if b else "") + f'<sz val="{sz}"/>' + (f'<color rgb="FF{c}"/>' if c else "")
                       + '<name val="Yu Gothic"/></font>' for sz, b, c in fonts)
    fill_xml = "".join(f'<fill><patternFill patternType="{f}"/></fill>' if f in ("none", "gray125") else
                       f'<fill><patternFill patternType="solid"><fgColor rgb="FF{f}"/><bgColor indexed="64"/></patternFill></fill>'
                       for f in fills)
    fmt_xml = "".join(f'<numFmt numFmtId="{fmt_ids[k]}" formatCode="{escape(v, {chr(34): "&quot;"})}"/>' for k, v in NUMFMT.items())
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<numFmts count="{len(NUMFMT)}">{fmt_xml}</numFmts>'
            f'<fonts count="{len(fonts)}">{font_xml}</fonts>'
            f'<fills count="{len(fills)}">{fill_xml}</fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>')


def _ref(sheet, rng):
    a, b = rng.split(":")
    abs_ = lambda c: "$" + "".join(ch for ch in c if ch.isalpha()) + "$" + "".join(ch for ch in c if ch.isdigit())
    return f"'{sheet.replace(chr(39), chr(39) * 2)}'!{abs_(a)}:{abs_(b)}"


def _chart_xml(ch):
    C = 'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'

    def rich(text, sz=1200):
        return (f'<c:tx><c:rich><a:bodyPr/><a:p><a:pPr><a:defRPr sz="{sz}" b="1"/></a:pPr>'
                f'<a:r><a:rPr lang="ja-JP" sz="{sz}" b="1"/><a:t>{escape(text)}</a:t></a:r></a:p></c:rich></c:tx>')
    ser = []
    for i, (name, rng, color) in enumerate(ch.series):
        line = (f'<c:spPr><a:ln w="28575"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></c:spPr><c:marker><c:symbol val="none"/></c:marker>'
                if ch.kind == "line" else f'<c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></c:spPr><c:invertIfNegative val="0"/>')
        ser.append(f'<c:ser><c:idx val="{i}"/><c:order val="{i}"/><c:tx><c:v>{escape(name)}</c:v></c:tx>{line}'
                   f'<c:cat><c:strRef><c:f>{escape(_ref(ch.sheet, ch.cats))}</c:f></c:strRef></c:cat>'
                   f'<c:val><c:numRef><c:f>{escape(_ref(ch.sheet, rng))}</c:f></c:numRef></c:val>'
                   + ('<c:smooth val="0"/>' if ch.kind == "line" else "") + '</c:ser>')
    if ch.kind == "line":
        plot = f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>{"".join(ser)}<c:marker val="1"/>'
    else:
        plot = f'<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/><c:varyColors val="0"/>{"".join(ser)}<c:gapWidth val="60"/>'
    plot += '<c:axId val="1001"/><c:axId val="1002"/>' + ("</c:lineChart>" if ch.kind == "line" else "</c:barChart>")
    fmt = "0%" if ch.pct else '#,##0"円"'
    grid = '<c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="E5E7EB"/></a:solidFill></a:ln></c:spPr></c:majorGridlines>'
    axes = ('<c:catAx><c:axId val="1001"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="b"/>'
            '<c:numFmt formatCode="General" sourceLinked="0"/><c:tickLblPos val="low"/><c:crossAx val="1002"/><c:crosses val="autoZero"/>'
            '<c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>'
            f'<c:valAx><c:axId val="1002"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="l"/>{grid}'
            f'<c:numFmt formatCode="{escape(fmt, {chr(34): "&quot;"})}" sourceLinked="0"/><c:tickLblPos val="nextTo"/>'
            '<c:crossAx val="1001"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>')
    legend = '<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>' if len(ch.series) > 1 else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<c:chartSpace {C}><c:roundedCorners val="0"/><c:chart><c:title>{rich(ch.title)}<c:overlay val="0"/></c:title>'
            f'<c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>{plot}{axes}</c:plotArea>{legend}<c:plotVisOnly val="1"/>'
            '<c:dispBlanksAs val="gap"/></c:chart><c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="1000"><a:latin typeface="Yu Gothic"/>'
            '<a:ea typeface="Yu Gothic"/></a:defRPr></a:pPr><a:endParaRPr lang="ja-JP"/></a:p></c:txPr></c:chartSpace>')


def _drawing_xml(charts, first_id):
    X = ('xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
         'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"')
    parts = []
    for k, ch in enumerate(charts):
        c1, r1, c2, r2 = ch.anchor
        parts.append(f'<xdr:twoCellAnchor><xdr:from><xdr:col>{c1}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{r1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                     f'<xdr:to><xdr:col>{c2}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{r2}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
                     f'<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="{k + 2}" name="グラフ {k + 1}"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>'
                     '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
                     f'<c:chart r:id="rId{k + 1}"/></a:graphicData></a:graphic></xdr:graphicFrame><xdr:clientData/></xdr:twoCellAnchor>')
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr {X}>{"".join(parts)}</xdr:wsDr>'


def _rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{t}" Target="{g}"/>'
                      for i, t, g in items) + '</Relationships>')


def book(sheets):
    sheets = [s if isinstance(s, Sheet) else Sheet(s[0], s[1]) for s in sheets]
    buf = io.BytesIO()
    n = len(sheets)
    over = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        chart_no, drawing_no = 0, 0
        for i, sh in enumerate(sheets):
            drawing = bool(sh.charts)
            if drawing:
                drawing_no += 1
                d = drawing_no
                z.writestr(f"xl/worksheets/_rels/sheet{i + 1}.xml.rels", _rels([("rId1", "drawing", f"../drawings/drawing{d}.xml")]))
                z.writestr(f"xl/drawings/drawing{d}.xml", _drawing_xml(sh.charts, chart_no))
                items = []
                for k, ch in enumerate(sh.charts):
                    chart_no += 1
                    z.writestr(f"xl/charts/chart{chart_no}.xml", _chart_xml(ch))
                    items.append((f"rId{k + 1}", "chart", f"../charts/chart{chart_no}.xml"))
                    over.append((f"/xl/charts/chart{chart_no}.xml", "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"))
                z.writestr(f"xl/drawings/_rels/drawing{d}.xml.rels", _rels(items))
                over.append((f"/xl/drawings/drawing{d}.xml", "application/vnd.openxmlformats-officedocument.drawing+xml"))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sh, drawing))
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(n))
                   + "".join(f'<Override PartName="{p}" ContentType="{t}"/>' for p, t in over)
                   + '</Types>')
        z.writestr("_rels/.rels", _rels([("rId1", "officeDocument", "xl/workbook.xml")]))
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(sh.name[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, sh in enumerate(sheets))
                   + '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   _rels([(f"rId{i + 1}", "worksheet", f"worksheets/sheet{i + 1}.xml") for i in range(n)]
                         + [(f"rId{n + 1}", "styles", "styles.xml")]))
        z.writestr("xl/styles.xml", _styles_xml())
    return buf.getvalue()
