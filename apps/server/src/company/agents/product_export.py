"""Read public product exports without executing spreadsheet formulas."""

import csv
from io import BytesIO, StringIO
from itertools import islice
from pathlib import PurePath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAX_EXPORT_BYTES = 2 * 1024 * 1024
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_rows(data: bytes):
    with ZipFile(BytesIO(data)) as book:
        if sum(entry.file_size for entry in book.infolist()) > 16 * 1024 * 1024:
            raise ValueError("Workbook expands beyond the import limit")

        def xml(name: str):
            raw = book.read(name)
            if b"\x00" in raw or b"<!" in raw:
                raise ValueError("Unsupported workbook XML")
            return ET.fromstring(raw)

        def cell_text(element):
            return "".join(node.text or "" for node in element.iter(f"{_NS}t"))

        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = [cell_text(item) for item in xml("xl/sharedStrings.xml")]
        for name in book.namelist():
            if not name.startswith("xl/worksheets/") or "/" in name[14:]:
                continue
            if not name.endswith(".xml"):
                continue
            for row in xml(name).iter(f"{_NS}row"):
                values = []
                size = 0
                for cell in row.findall(f"{_NS}c"):
                    column = 0
                    for letter in (cell.get("r") or ""):
                        if not "A" <= letter <= "Z":
                            break
                        column = column * 26 + ord(letter) - 64
                    if column > 16384:
                        raise ValueError("Invalid workbook column")
                    values.extend([""] * max(0, column - 1 - len(values)))
                    value = cell.findtext(f"{_NS}v") or ""
                    kind = cell.get("t")
                    if kind == "s":
                        value = shared[int(value)]
                    elif kind == "inlineStr":
                        value = cell_text(cell)
                    size += len(value) + 1
                    if size > 24000:
                        raise ValueError("Workbook row contains too much data")
                    values.append(value)
                yield values


def read_product_export(filename: str, data: bytes) -> str:
    """Return bounded CSV text suitable for the intake assistant."""
    if not data or len(data) > MAX_EXPORT_BYTES:
        raise ValueError("Invalid product export size")
    suffix = PurePath(filename).suffix.lower()
    output = StringIO()
    writer = csv.writer(output)
    try:
        if suffix == ".csv":
            try:
                decoded = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                decoded = data.decode("gb18030")
            if "\x00" in decoded:
                raise ValueError("Invalid CSV")
            rows = csv.reader(StringIO(decoded, newline=""), strict=True)
        elif suffix == ".xlsx":
            rows = _xlsx_rows(data)
        else:
            raise ValueError("Unsupported product export")
        with_rows = (row for row in rows if any(str(value).strip() for value in row))
        for index, row in enumerate(islice(with_rows, 101)):
            if index == 100:
                raise ValueError("Product export contains too many rows")
            writer.writerow(row)
            if output.tell() > 24000:
                raise ValueError("Product export contains too much data")
    except (BadZipFile, ET.ParseError, csv.Error, IndexError, KeyError, RuntimeError) as exc:
        raise ValueError("Invalid product export") from exc
    finally:
        if suffix == ".xlsx":
            rows.close()
    result = output.getvalue()
    if not result.strip():
        raise ValueError("Empty product export")
    return result
