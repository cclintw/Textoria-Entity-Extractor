#!/usr/bin/env python3
"""Convert a completed Textoria CSV to optional platform files; no LLM or dependencies."""
import argparse
import csv
import io
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from entity_pipeline import ALL

# Use MARKUS's built-in categories where available, otherwise custom tag names.
MARKUS_TYPES = {'person': 'fullName', 'place': 'placeName', 'office': 'officialTitle',
                'official_title': 'officialTitle', 'era_name': 'nianhao'}
DOCUSKY_TYPES = {'person': 'PersonName', 'place': 'LocName'}
SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def read_names(source):
    """Preserve independent name forms, expanding aliases without identity merging."""
    names = set()
    with Path(source).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames not in (['name', 'type', 'alias'], ['name', 'type']):
            raise ValueError('Expected Textoria CSV columns name,type,alias or name,type')
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Invalid column count on CSV row {number}')
            typ = row['type']
            if typ not in ALL or not row['name'].strip():
                raise ValueError(f'Invalid name/type on CSV row {number}')
            forms = [row['name']] + [a for a in row.get('alias', '').split('|') if a]
            for name in forms:
                # Plain TSV consumers cannot represent embedded line breaks or tabs.
                if any(c in name for c in '\t\r\n'):
                    raise ValueError(f'Name contains a tab or line break on CSV row {number}')
                if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', name):
                    raise ValueError(f'Name contains an unsupported XML character on CSV row {number}')
                if len(name.encode('utf-16-le')) // 2 > 32767:
                    raise ValueError(f'Name exceeds Excel cell limit on CSV row {number}')
                names.add((name, typ))
    return sorted(names, key=lambda item: (item[1], item[0]))


def markus_bytes(names):
    # Keep the requested three-column heading; paste only data rows into Batch Markup.
    # No invented IDs: fullName IDs are interpreted as CBDB identifiers by MARKUS.
    rows = {(MARKUS_TYPES.get(typ, typ), name, '') for name, typ in names}
    ordered = [('tagtype', 'tag', 'id')] + sorted(rows, key=lambda r: (-len(r[1]), r))
    return '\n'.join('\t'.join(row) for row in ordered).encode('utf-8')


def docusky_bytes(names):
    """A minimal, string-only OOXML workbook for import, using Python's standard library."""
    rows = [('tagName', 'tagVal', '@Term')]
    rows += sorted({(DOCUSKY_TYPES.get(typ, 'Udef_' + typ), name, '')
                    for name, typ in names}, key=lambda r: (-len(r[1]), r))
    if len(rows) > 1048576:
        raise ValueError('Too many rows for one Excel worksheet')
    ET.register_namespace('', SHEET_NS)
    sheet = ET.Element(f'{{{SHEET_NS}}}worksheet')
    data = ET.SubElement(sheet, f'{{{SHEET_NS}}}sheetData')
    for index, values in enumerate(rows, 1):
        row = ET.SubElement(data, f'{{{SHEET_NS}}}row', r=str(index))
        for column, value in zip('ABC', values):
            cell = ET.SubElement(row, f'{{{SHEET_NS}}}c', r=f'{column}{index}', t='inlineStr')
            inline = ET.SubElement(cell, f'{{{SHEET_NS}}}is')
            text = ET.SubElement(inline, f'{{{SHEET_NS}}}t', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'})
            text.text = value
    parts = {
        '[Content_Types].xml': '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''',
        '_rels/.rels': '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        'xl/workbook.xml': '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="entities" sheetId="1" r:id="rId1"/></sheets></workbook>''',
        'xl/_rels/workbook.xml.rels': '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''',
        'xl/worksheets/sheet1.xml': ET.tostring(sheet, encoding='utf-8', xml_declaration=True),
    }
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w', compression=ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            # Fixed package timestamps make retries byte-for-byte reproducible.
            archive.writestr(ZipInfo(name), content, compress_type=ZIP_DEFLATED)
    return buffer.getvalue()


def save_export(path, data):
    """Never replace an existing file; reuse identical exports and clean up failed writes."""
    try:
        stream = path.open('xb')
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError(f'Output already exists with different contents: {path}')
        return
    try:
        with stream:
            stream.write(data)
        if path.read_bytes() != data:
            raise ValueError(f'Export verification failed: {path}')
    except Exception:
        path.unlink()
        raise


def convert(source, formats):
    source = Path(source).resolve()
    names = read_names(source)
    outputs = []
    for fmt in dict.fromkeys(formats):
        if fmt == 'markus':
            if not names:
                raise ValueError('Cannot create MARKUS Batch Markup input from an empty vocabulary')
            data, suffix = markus_bytes(names), '.txt'
        elif fmt == 'docusky':
            data, suffix = docusky_bytes(names), '.xlsx'
        else:
            raise ValueError(f'Unknown export format: {fmt}')
        path = source.with_name(fmt + '-' + source.stem + suffix)
        save_export(path, data)
        outputs.append(str(path))
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Completed Textoria CSV; never modified')
    parser.add_argument('--formats', nargs='+', choices=['markus', 'docusky'], required=True)
    args = parser.parse_args()
    print(json.dumps({'csv': str(Path(args.input).resolve()),
                      'outputs': convert(args.input, args.formats)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
