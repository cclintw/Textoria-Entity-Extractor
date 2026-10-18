#!/usr/bin/env python3
"""Stage local text or a single web page; extraction remains the Agent's job."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen

from entity_pipeline import TEI, RS, source_identity

BLOCKS = {'p', 'div', 'section', 'article', 'main', 'header', 'footer', 'nav',
          'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'blockquote', 'pre', 'br'}
SKIP = {'script', 'style', 'noscript', 'template', 'nav', 'footer'}
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'param', 'source', 'track', 'wbr'}
TAG_TYPES = {v.lower(): k for k, v in TEI.items()}
TAG_TYPES.update({'person': 'person', 'organization': 'organization', 'place': 'place'})


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data, encoding=None):
    # No replacement characters: an incorrect/unknown encoding must be resolved explicitly.
    return data.decode(encoding or 'utf-8-sig', errors='strict')


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.hints = []
        self.headings = []
        self.regions = []
        self.length = 0

    def add(self, text):
        self.parts.append(text)
        self.length += len(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = ('hidden' in attrs or attrs.get('aria-hidden', '').lower() == 'true'
                  or bool(re.search(r'(display\s*:\s*none|visibility\s*:\s*hidden)',
                                    attrs.get('style', ''), re.I)))
        skip = tag in SKIP or hidden or (self.stack and self.stack[-1]['skip'])
        if tag in BLOCKS and not skip:
            self.add('\n\n')
        frame = {'tag': tag, 'attrs': attrs, 'start': self.length, 'skip': bool(skip)}
        if tag not in VOID:
            self.stack.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.stack or not self.stack[-1]['skip']:
            self.add(data)

    def finish_frame(self, frame):
        if frame['skip']:
            return
        tag, attrs = frame['tag'], frame['attrs']
        if tag in {'main', 'article', 'body'}:
            self.regions.append((tag, frame['start'], self.length))
        if tag in {'h1','h2','h3','h4','h5','h6','head'}:
            self.headings.append({'start':frame['start'],'end':self.length,'tag':tag})
        typ = TAG_TYPES.get(tag)
        if tag == 'rs' and attrs.get('type') in RS:
            typ = attrs['type']
        for key in ('data-entity-type', 'data-type', 'type', 'itemprop', 'itemtype', 'class'):
            for token in attrs.get(key, '').split():
                token = token.rstrip('/').rsplit('/', 1)[-1].lower()
                candidate = TAG_TYPES.get(token, token)
                if candidate in TEI or candidate in RS:
                    typ = candidate
                    break
            if typ:
                break
        if typ:
            self.hints.append({'start': frame['start'], 'end': self.length,
                               'suggested_type': typ, 'tag': tag, 'attributes': attrs})

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1)
                      if self.stack[i]['tag'] == tag), None)
        if index is None:
            return
        while len(self.stack) > index:
            self.finish_frame(self.stack.pop())
        if tag in BLOCKS and not (self.stack and self.stack[-1]['skip']):
            self.add('\n\n')

    def result(self, region):
        while self.stack:
            self.finish_frame(self.stack.pop())
        raw = ''.join(self.parts)
        chosen = region
        if region == 'auto':
            chosen = next((tag for tag in ('main', 'article', 'body')
                           if any(r[0] == tag for r in self.regions)), 'all')
        ranges = [(s, e) for tag, s, e in self.regions if tag == chosen]
        if chosen == 'all':
            ranges = [(0, len(raw))]
        if not ranges:
            raise ValueError(f'No <{chosen}> region; choose another --region')
        # Do not duplicate nested article/main regions.
        ranges = [(s, e) for i, (s, e) in enumerate(ranges)
                  if not any(j != i and a <= s and e <= b and (a < s or e < b or j < i)
                             for j, (a, b) in enumerate(ranges))]

        def clean(s):
            s = re.sub(r'[^\S\n]+', ' ', s)
            s = re.sub(r' *\n *', '\n', s)
            return re.sub(r'\n{3,}', '\n\n', s).strip()

        text = '\n\n'.join(clean(raw[s:e]) for s, e in sorted(ranges)).strip()
        hints = []
        for h in self.hints:
            if any(s <= h['start'] and h['end'] <= e for s, e in ranges):
                surface = clean(raw[h['start']:h['end']])
                if surface and surface in text:
                    hints.append({'surface': surface, 'suggested_type': h['suggested_type'],
                                  'tag': h['tag'], 'attributes': h['attributes'],
                                  'review_status': 'unverified_source_annotation'})
        if not text:
            raise ValueError('No readable text; page may require JavaScript or a different region')
        self.heading_hints=[{'surface':clean(raw[h['start']:h['end']]),'tag':h['tag'],'role':'structural_heading'}
                            for h in self.headings if any(s<=h['start'] and h['end']<=e for s,e in ranges)
                            and clean(raw[h['start']:h['end']])]
        return text, hints, chosen


def write_unique(folder, stem, suffix, data):
    stem = re.sub(r'[^\w.\-\u3400-\u9fff]+', '_', stem).strip('._')[:100] or 'source'
    path = folder / (stem + suffix)
    if path.exists() and path.read_bytes() != data:
        path = folder / (stem + '-' + digest(data)[:12] + suffix)
    if path.exists() and path.read_bytes() != data:
        raise ValueError(f'Conflicting source path: {path}')
    if not path.exists():
        with path.open('xb') as f:
            f.write(data)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', help='Local .txt, .md, .html or .htm file')
    source.add_argument('--url', help='Single public HTTP(S) page; no recursive crawling')
    parser.add_argument('--project', required=True, help='User project root, not Skill installation')
    parser.add_argument('--encoding', help='Explicit source encoding when not UTF-8')
    parser.add_argument('--region', choices=['auto', 'main', 'article', 'body', 'all'], default='auto')
    a = parser.parse_args()
    source_url = None
    media = None
    encoding = a.encoding
    if a.url:
        if urlsplit(a.url).scheme not in {'http', 'https'}:
            raise ValueError('Only HTTP(S) URLs are supported')
        request = Request(a.url, headers={'User-Agent': 'Textoria-Entities/1.0'})
        with urlopen(request, timeout=30) as response:
            source_url = response.geturl()
            if urlsplit(source_url).scheme not in {'http', 'https'}:
                raise ValueError('Unsupported redirect scheme')
            media = response.headers.get_content_type()
            encoding = encoding or response.headers.get_content_charset()
            if media not in {'text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown'}:
                raise ValueError(f'Unsupported content type: {media}; convert with a suitable tool')
            data = response.read(20 * 1024 * 1024 + 1)
            if len(data) > 20 * 1024 * 1024:
                raise ValueError('Page exceeds 20 MiB; retrieve the relevant text separately')
        filename = unquote(urlsplit(source_url).path.rsplit('/', 1)[-1]) or urlsplit(source_url).hostname
        is_html = media in {'text/html', 'application/xhtml+xml'}
    else:
        src = Path(a.input).resolve()
        if src.suffix.lower() not in {'.txt', '.md', '.html', '.htm'}:
            raise ValueError('Expected .txt, .md, .html or .htm; convert other formats first')
        filename = src.name
        data = src.read_bytes()
        is_html = src.suffix.lower() in {'.html', '.htm'}
    # Archive the source before decoding/cleaning; retain it even if parsing fails.
    project = Path(a.project).resolve()
    source_dir = project / 'source'
    for directory in (source_dir, project / 'csv', project / '.textoria-entities'):
        directory.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    raw_suffix = '.html' if is_html else ('.md' if Path(filename).suffix.lower() == '.md' else '.txt')
    original = write_unique(source_dir, stem, raw_suffix, data)
    if is_html and not encoding:
        meta = re.search(br'<meta\b[^>]*charset\s*=\s*[\x22\x27]?\s*([\w-]+)', data[:8192], re.I)
        if meta:
            encoding = meta[1].decode('ascii')
    decoded = decode(data, encoding)
    hints, chosen = [], None
    if is_html:
        html = PageText()
        html.feed(decoded)
        html.close()
        text, hints, chosen = html.result(a.region)
    else:
        text = decoded
    if not text.strip():
        raise ValueError('Source contains no readable text')
    # Keep original bytes and make a separate UTF-8 text when conversion is needed.
    prepared = (write_unique(source_dir, original.stem + '.clean', '.txt', text.encode('utf-8'))
                if is_html or encoding or data != text.encode('utf-8') else original)
    manifest = {**source_identity(input_path=a.input,url=a.url,label=stem),
                'pipeline_profile':'entity-reference-v5-source-csv-rotation',
                'original': str(original), 'input': str(prepared),
                'requested_url': a.url, 'final_url': source_url,
                'local_source': str(Path(a.input).resolve()) if a.input else None,
                'raw_sha256': digest(data), 'text_sha256': digest(prepared.read_bytes()),
                'encoding': encoding or 'utf-8-sig', 'region': chosen,
                'annotation_hints': hints,
                'heading_hints':html.heading_hints if is_html else [],
                'warnings': ['HTML selection and whitespace cleanup require Agent inspection; '
                             'existing annotations are unverified hints.'] if is_html else []}
    # Include source identity, selected region and text version; never overwrite other provenance.
    key = digest(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode())[:12]
    work = project / '.textoria-entities' / f'{original.stem}-{key}'
    work.mkdir(exist_ok=True)
    manifest_path = work / 'source.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'input': str(prepared), 'work': str(work), 'manifest': str(manifest_path),
                      'output_dir': str(project / 'csv'),
                      'annotation_hints': len(hints)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
