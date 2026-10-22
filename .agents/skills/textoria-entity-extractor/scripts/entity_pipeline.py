#!/usr/bin/env python3
"""Textoria incremental entity reference inventory; standard-library only."""
import argparse,csv,json,re,hashlib,unicodedata,os,tempfile,io,shutil
from pathlib import Path
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from contextlib import contextmanager
from urllib.parse import urlsplit,urlunsplit

COLUMNS=['name','type','alias']
TEI={'person':'persName','place':'placeName','organization':'orgName','title':'title','date':'date','time':'time','object':'objectName','material':'material','measure':'measure'}
RS={'office','official_title','plant','animal','food','artifact','currency','clothing','textile','product','medicine','disease','symptom','contract_term','ethnic_group','era_name'}
EXCLUDED_TYPES={'date','time','measure'}
ALL=(set(TEI)|RS)-EXCLUDED_TYPES

def source_identity(input_path=None,url=None,label=None):
    """Stable source identity independent of contents, extraction work or export date."""
    if url:
        parsed=urlsplit(url)
        key='url:'+urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path,parsed.query,''))
        label=label or Path(parsed.path).stem or parsed.hostname or 'source'
    elif input_path:
        source=Path(input_path).resolve()
        key='file:'+str(source)
        label=label or source.stem
    else:raise ValueError('Cannot rotate CSV without an identifiable source')
    label=re.sub(r'[^\w.\-\u3400-\u9fff]+','_',label).strip('._')[:60] or 'source'
    return {'source_key':key,'source_id':hashlib.sha256(key.encode()).hexdigest()[:12],'source_label':label}

def work_source(work):
    manifest=load(Path(work)/'source.json',{})
    identity=load(Path(work)/'input.json',{})
    return source_identity(input_path=manifest.get('local_source') or identity.get('path') or manifest.get('original'),
                           url=manifest.get('requested_url') or manifest.get('final_url'),
                           label=manifest.get('source_label'))

@contextmanager
def csv_version_lock(root):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'csv-versions.lock').open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0,2)
            if not stream.tell():stream.write(b'0');stream.flush()
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:
            if os.name=='nt':stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

def atomic_bytes(path,data):
    path=Path(path)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.'+path.name+'-',suffix='.tmp',delete=False) as f:
            temporary=Path(f.name);f.write(data)
        os.replace(temporary,path)
    finally:
        if temporary and temporary.exists():temporary.unlink()

def timestamped_path(folder,source_id,stamp):
    return folder/f"{source_id}-{stamp.strftime('%Y%m%d-%H%M%S-%f')}.csv"

def rotate_csv(folder,source,data,exported_at,work,columns):
    """Retain three exports per source using filenames, without a project index."""
    folder=Path(folder).resolve()
    if folder.name!='csv':raise ValueError('Export directory must be the project csv/ folder')
    folder.mkdir(parents=True,exist_ok=True)
    lock_id=hashlib.sha256(str(folder).encode()).hexdigest()
    lock_root=Path(tempfile.gettempdir())/('textoria-csv-lock-'+lock_id)
    with csv_version_lock(lock_root):
        stamp=datetime.fromisoformat(exported_at)
        text_id=source['source_label']+'-'+source['source_id']
        output=timestamped_path(folder,text_id,stamp)
        while output.exists():
            stamp+=timedelta(microseconds=1);output=timestamped_path(folder,text_id,stamp)
        atomic_bytes(output,data)
        # Verify the durable result before deleting any old export or temporary work.
        if output.read_bytes()!=data:raise ValueError('CSV verification failed; work retained')
        pattern=re.compile(r'(?:.+-)?'+re.escape(source['source_id'])+r'-\d{8}-\d{6}-\d{6}\.csv')
        versions=[p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                  and pattern.fullmatch(p.name) and p!=output]
        versions.sort(key=lambda p:(p.stat().st_mtime_ns,p.name))
        for expired in versions[:-2]:expired.unlink()
        return {'output':str(output),'exported_at':exported_at,'columns':columns,
                'source_id':source['source_id'],'text_id':text_id}

def cleanup_work(work):
    """Only remove a work directory created by prepare_source in system temp."""
    work=Path(work).resolve()
    manifest=load(work/'source.json',{})
    if not manifest.get('temporary_work'):return
    if (work.parent!=Path(tempfile.gettempdir()).resolve()
            or not work.name.startswith('textoria-entity-extractor-work-')):
        raise ValueError('Refusing to remove work outside the managed temporary directory')
    shutil.rmtree(work)

def chunks(text,max_chars=300,overlap=40):
    """Yield exact source slices, with punctuation-aware boundaries and overlap."""
    if max_chars<20 or not 0<=overlap<max_chars//2:
        raise ValueError('chunk size must be >=20; overlap must be less than half the size')
    start=0
    while start<len(text):
        end=min(start+max_chars,len(text))
        if end<len(text):
            # Prefer sentence boundaries, then list boundaries. Never drop any characters.
            for punctuation in ('。！？!?；;\n','，、,:：'):
                candidates=[i+1 for i in range(start+max_chars//2,end) if text[i] in punctuation]
                if candidates:
                    end=candidates[-1]
                    while end<min(start+max_chars,len(text)) and text[end] in '』」”’）)':end+=1
                    break
        yield start,end,text[start:end]
        if end==len(text):break
        start=end-overlap

def norm(s):return unicodedata.normalize('NFC',str(s or '').strip())
def load(p,default):return json.loads(Path(p).read_text(encoding='utf-8')) if Path(p).exists() else default
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def paragraphs(src):
    text=Path(src).read_text(encoding='utf-8-sig').replace('\r\n','\n')
    # Blank lines are paragraph separators. Never infer paragraph boundaries from line wrapping.
    return [re.sub(r'\n+','\n',s).strip() for s in re.split(r'\n\s*\n',text) if s.strip()]
def divisions(ps):
    # Keep sections configurable: don't assume every heading is a division.
    heading=re.compile(r'^(?:裨海紀遊)?卷[上下中一二三四五六七八九十]+$|^(?:弁言|序|跋|附錄|鄭氏逸事(?:（.*）)?|番境補遺|海上紀略|宇內形勢)$')
    cur='前置文本'; out=[]
    for p in ps:
        if '\n' not in p and heading.fullmatch(p):cur=p
        out.append((cur,p))
    return out

def cmd_init(a):
    root=Path(a.out);root.mkdir(parents=True,exist_ok=True)
    identity={'path':str(Path(a.input).resolve()),'sha256':hashlib.sha256(Path(a.input).read_bytes()).hexdigest(),
              'segmentation':{'max_chars':a.chunk_chars,'overlap':a.overlap,'version':3}}
    if (root/'paragraphs.json').exists():
        if load(root/'input.json',{})!=identity:
            raise ValueError('Source or segmentation differs, or legacy work has no matching identity; use a new work directory')
        print(f'Resuming existing work in {root}');return
    ps=divisions(paragraphs(a.input)); rows=[]
    heading_hints=load(root/'source.json',{}).get('heading_hints',[])
    heading_texts={h['surface'] for h in heading_hints}
    # Validate settings even for an empty source.
    list(chunks('',a.chunk_chars,a.overlap))
    for i,(div,p) in enumerate(ps,1):
        for start,end,text in chunks(p,a.chunk_chars,a.overlap):
            rows.append({'id':f'p{len(rows)+1:05d}','division':div,'text':text,'status':'pending',
                         'content_role':'heading' if p==div or p in heading_texts or re.match(r'^#{1,6}\s',p) else 'unclassified',
                         'source_paragraph_id':f's{i:05d}','start':start,'end':end,
                         'context_before':p[max(0,start-80):start],'context_after':p[end:end+80]})
    save(root/'paragraphs.json',rows);save(root/'registry.json',[]);save(root/'audit.json',[])
    save(root/'input.json',identity)
    (root/'responses').mkdir(exist_ok=True)
    print(f'Initialized {len(rows)} paragraphs in {root}')

def matched(text,registry):
    # Surface-name hints only: retain nested spans, never resolve an alias to an identity.
    names={}
    for e in registry:
        names.setdefault(e['name'],{'registry_ids':[],'alias_contexts':[]})['registry_ids'].append(e['id'])
        for alias in e.get('aliases',[]):
            if not alias:continue
            hint=names.setdefault(alias,{'registry_ids':[],'alias_contexts':[]})
            relations=[r for r in e.get('alias_relations',[]) if r['alias']==alias]
            hint['alias_contexts'].append({'name':e['name'],'type':e['type'],
                                          'scope':'source_context' if relations else 'legacy_unscoped_hint',
                                          'evidence':relations})
    spans=[]
    for name in names:
        for m in re.finditer(re.escape(name),text):
            spans.append({'start':m.start(),'end':m.end(),'surface':name,**names[name]})
    return sorted(spans,key=lambda x:(x['start'],-x['end'],x['surface']))

def cmd_next(a):
    root=Path(a.work);ps=load(root/'paragraphs.json',[]);registry=load(root/'registry.json',[])
    identity=load(root/'input.json',{})
    if identity and hashlib.sha256(Path(identity['path']).read_bytes()).hexdigest()!=identity['sha256']:
        raise ValueError('Source changed since init; prepare a new version before continuing')
    hints=load(root/'source.json',{}).get('annotation_hints',[])
    pending=[p for p in ps if p['status']=='pending']
    if not pending:print('DONE: no pending paragraphs');return
    batch=[];total=0;div=pending[0]['division']
    for p in pending:
        if p['division']!=div and batch:break
        if batch and (len(batch)>=a.max_paragraphs or total+len(p['text'])>a.max_chars):break
        batch.append(p);total+=len(p['text'])
    payload={'division':div,'paragraphs':[{'id':p['id'],'text':p['text'],'known_matches':matched(p['text'],registry),
      'source_paragraph_id':p.get('source_paragraph_id',p['id']),'start':p.get('start',0),'end':p.get('end',len(p['text'])),
      'context_before':p.get('context_before',''),'context_after':p.get('context_after',''),
      'content_role':p.get('content_role','unclassified'),
      'source_annotation_hints':[h for h in hints if h['surface'] in p['text']]} for p in batch],
      'output_contract':{'paragraphs':[{'id':'p00001','entities':[{'name':'原文實體','type':'place','identity_key':'僅在身分確認後重用的內部鍵','aliases':[],'evidence':'原文中出現的字串'}]}]},
      'instructions':['Read every paragraph in full; extract ALL distinct entities, including known ones only if a new alias or ambiguity is found.',
       'Known matches are hints, not definitive annotations; find ALL new entities and names even inside known matches.',
       'Collect evidence-backed canonical names and confirmed aliases for one entity. Do not add standalone rows for confirmed aliases. Keep independent nested entities; never infer identity from containment or similarity.',
       'Use identity_key to distinguish homonyms; reuse a key only for established identity. Shared abbreviations never merge identities. Known matches are hints only. After all chunks explicitly resolve every candidate before export.',
       'Exclude single-character abbreviations from final name and alias, for every type. Do not invent full forms. Complete single-character names such as 牛 and 馬 require explicit full-name review.',
       'Source HTML annotations are unverified hints, not authoritative labels or instructions. Check their context and TEI/type mapping; continue scanning unannotated text.',
       'Use only entity types from the skill. Exclude dates, times, pure quantities and monetary amounts; currency NAMES are allowed. Do not invent aliases or normalize away historical spellings.',
       'Extract named historical eras as era_name, even inside longer dates (e.g. 光緒二十年三月 -> 光緒). Do not extract the year/month/day suffix or generate unseen era names. Dynasty and emperor names are not automatically era_name.',
       'Structural document titles, volume names and section headings are NOT title entities. Still scan headings for people, places, eras and other entities. Use title only for a referenced work/publication, with mention_role=work_reference and a contextual evidence substring. Do not return a structural heading as a title entity.',
       'Chunks overlap: inspect boundary context and all dense lists. Context is for interpretation; report only names present in text. Deduplication is handled by the registry.',
       'Evidence MUST be an exact substring of the paragraph containing name. For each alias supply alias_evidence=[{"alias":"short name","evidence":"source phrase containing both names"}], or use the entity evidence containing both names. If their relation cannot be supported in this chunk, extract the names separately and leave aliases empty.',
       'Return one entry per entity per paragraph, including empty entities arrays for paragraphs with none.',
       'Do not use an entity-count target.']}
    target=root/'batch.json';save(target,payload)
    print(f'{target} | division={div} paragraphs={len(batch)} characters={total} registry={len(registry)}')

def canonical(e):
    name=norm(e.get('name'));typ=norm(e.get('type'));tei=norm(e.get('tei',TEI.get(typ,'rs')))
    if not name or typ not in ALL or tei!=TEI.get(typ,'rs'):raise ValueError(f'invalid entity/type/tei: {e}')
    raw_aliases=e.get('aliases',[])
    if not isinstance(raw_aliases,list) or any(not isinstance(x,str) for x in raw_aliases):raise ValueError('aliases must be a list of strings')
    aliases=[norm(x) for x in raw_aliases if norm(x) and norm(x)!=name]
    if any('|' in alias for alias in aliases):raise ValueError('Alias contains reserved separator |')
    if typ=='era_name' and any(re.search(r'(?:元|[零〇一二三四五六七八九十百千\d]+)年|年[間间]',s) for s in [name,*aliases]):
        raise ValueError('era_name must contain only the named era, not a dated expression or date alias')
    return name,typ,tei,list(dict.fromkeys(aliases))

def contextual_aliases(e,name,aliases,paragraph,identity):
    """Validate textual support and record usage without creating a global identity link."""
    if not aliases:return []
    proofs=e.get('alias_evidence',[])
    if not isinstance(proofs,list) or any(not isinstance(x,dict) for x in proofs):raise ValueError('alias_evidence must be a list of evidence objects')
    result=[]
    for alias in aliases:
        evidence=next((norm(x.get('evidence')) for x in proofs if x.get('alias')==alias),norm(e.get('evidence')))
        if not evidence or evidence not in paragraph['text'] or name not in evidence or alias not in evidence:
            raise ValueError(f'Alias needs source-context evidence containing both names: {name} / {alias}')
        result.append({'alias':alias,'scope':'source_context','paragraph':paragraph['id'],
                       'source_path':identity.get('path'),'source_sha256':identity.get('sha256'),
                       'evidence':evidence})
    return result

def cmd_merge(a):
    root=Path(a.work);ps=load(root/'paragraphs.json',[]);reg=load(root/'registry.json',[]);audit=load(root/'audit.json',[])
    batch=load(root/'batch.json',{});res=load(a.response,{})
    identity=load(root/'input.json',{})
    requested={p['id'] for p in batch.get('paragraphs',[])}
    entries=res.get('paragraphs',[])
    if len(entries)!=len(requested) or {x['id'] for x in entries}!=requested:raise ValueError('Response must contain exactly one entry per requested paragraph')
    lookup={p['id']:p for p in ps};new=[];events=[]
    for r in reg:r.pop('reference_decision',None)
    for item in entries:
        p=lookup[item['id']]
        if p['status']!='pending':raise ValueError(f'already processed: {p["id"]}')
        for e in item.get('entities',[]):
            name,typ,tei,aliases=canonical(e)
            if name not in p['text']:raise ValueError(f'name not in paragraph {p["id"]}: {name}')
            evidence=norm(e.get('evidence',''))
            if not evidence or evidence not in p['text'] or name not in evidence:raise ValueError(f'evidence must contain name in paragraph {p["id"]}: {name}')
            relations=contextual_aliases(e,name,aliases,p,identity)
            if typ=='title' and e.get('mention_role')!='work_reference':
                raise ValueError(f'title requires an actual work reference, not a structural heading: {name}')
            identity_key=norm(e.get('identity_key')) or p['id']
            # Default to separate context candidates; only explicit keys link contexts.
            existing=next((r for r in reg if r['name']==name and r['type']==typ
                           and r.get('identity_key')==identity_key),None)
            if existing:
                for alias in aliases:
                    if alias not in existing.setdefault('aliases',[]):existing['aliases'].append(alias)
                existing['source_paragraphs']=list(dict.fromkeys(existing['source_paragraphs']+[p['id']]))
                events.append({'paragraph':p['id'],'entity_id':existing['id'],'action':'seen','name':name})
            else:
                eid='e'+hashlib.sha256((typ+'\x00'+name+'\x00'+identity_key).encode()).hexdigest()[:14]
                obj={'id':eid,'name':name,'tei':tei,'type':typ,'aliases':aliases,'source_paragraphs':[p['id']],'review_status':'candidate','identity_key':identity_key}
                reg.append(obj);new.append(name)
                events.append({'paragraph':p['id'],'entity_id':eid,'action':'new','name':name})
            target=existing if existing else obj
            proof={'paragraph':p['id'],'evidence':evidence}
            if proof not in target.setdefault('evidence',[]):target['evidence'].append(proof)
            for relation in relations:
                if relation not in target.setdefault('alias_relations',[]):target['alias_relations'].append(relation)
            if typ=='title':
                target['mention_role']='work_reference'
                target['reference_evidence']=list(dict.fromkeys(target.get('reference_evidence',[])+[evidence]))
        p['status']='done'
    save(root/'paragraphs.json',ps);save(root/'registry.json',reg);save(root/'audit.json',audit+events)
    print(f'Merged {len(entries)} paragraphs, {len(new)} new candidates, total={len(reg)}')

def resolve_registry(reg, decisions, paragraphs):
    """Apply explicit semantic review; never infer groups from names or alias graphs."""
    import copy
    result=copy.deepcopy(reg)
    by_id={r['id']:r for r in result}
    if len(by_id)!=len(result):raise ValueError('Duplicate registry IDs')
    texts={p['id']:p['text'] for p in paragraphs}
    used=set()
    for group in decisions.get('entities',[]):
        if not norm(group.get('reason')):raise ValueError('Resolution requires a semantic reason')
        members=group.get('members',[])
        canonical_id=group.get('canonical_id')
        if not members or canonical_id not in members:raise ValueError('Canonical ID must be a group member')
        for member in members:
            if member not in by_id or member in used:raise ValueError('Unknown or repeated member ID')
            used.add(member)
        target=by_id[canonical_id]
        if any(by_id[m]['type']!=target['type'] for m in members):raise ValueError('Cannot merge different types')
        proofs=group.get('evidence',[])
        if not isinstance(proofs,list):raise ValueError('evidence must be a list')
        for proof in proofs:
            text=texts.get(proof.get('paragraph'),'')
            if not proof.get('evidence') or proof['evidence'] not in text:
                raise ValueError('Resolution evidence must be an exact paragraph substring')
        def supported(form):
            return any(target['name'] in p['evidence'] and form in p['evidence'] for p in proofs)
        if not supported(target['name']):raise ValueError('Canonical name needs source evidence')
        for member in members:
            if member!=canonical_id and not supported(by_id[member]['name']):
                raise ValueError('Identity merge needs evidence containing both names')
        if len(target['name'])==1 and group.get('name_kind')!='full':
            raise ValueError('Single-character name requires name_kind=full; exclude abbreviations')
        aliases=group.get('aliases',[])
        if not isinstance(aliases,list) or any(not isinstance(a,str) or not a.strip() for a in aliases):
            raise ValueError('aliases must be a list of nonempty strings')
        aliases=list(dict.fromkeys(norm(a) for a in aliases if norm(a)!=target['name']))
        if any(len(a)<2 or '|' in a for a in aliases):raise ValueError('Aliases must have at least two characters and no |')
        if any(not supported(a) for a in aliases):raise ValueError('Alias needs evidence containing both names')
        canonical({'name':target['name'],'type':target['type'],'aliases':aliases})
        for member in members:
            by_id[member]['reference_decision']={'include':False,'canonical_id':canonical_id}
        target['reference_decision']={'include':True,'aliases':aliases,'evidence':proofs,
                                      'name_kind':group.get('name_kind','full'),'reason':group['reason']}
    for exclusion in decisions.get('excluded',[]):
        member=exclusion.get('id')
        if member not in by_id or member in used or not norm(exclusion.get('reason')):
            raise ValueError('Exclusion requires an unused ID and a reason')
        used.add(member)
        by_id[member]['reference_decision']={'include':False,'reason':exclusion['reason']}
    if used!=set(by_id):raise ValueError('Resolve every registry ID, including exclusions')
    return result


def cmd_resolve(a):
    root=Path(a.work)
    ps=load(root/'paragraphs.json',[])
    if any(p['status']!='done' for p in ps):raise ValueError('Complete all paragraphs before resolution')
    reg=resolve_registry(load(root/'registry.json',[]),load(a.response,{}),ps)
    save(root/'registry.json',reg)
    print('Resolved reference identities:',len(reg))


def reference_rows(reg,reviewed_only=False):
    """Serialize canonical decisions; never expand or transitively merge aliases."""
    if any('reference_decision' not in r for r in reg):
        raise ValueError('Canonical review required: run resolve; legacy work is not automatically normalized')
    rows=[]
    for r in reg:
        decision=r['reference_decision']
        if not decision['include'] or r.get('reference_excluded') or r['type'] in EXCLUDED_TYPES:continue
        if r['type']=='currency' and re.fullmatch(r'[零〇一二三四五六七八九十百千萬億兩壹貳參肆伍陸柒捌玖拾佰仟數餘有近\d.]+(?:金|兩|錢|文|元)',r['name']):continue
        if reviewed_only and r['review_status']!='verified':continue
        aliases=decision['aliases']
        if len(r['name'])==1 and decision.get('name_kind')!='full':raise ValueError('Unreviewed single-character name')
        if any(len(a)<2 or '|' in a for a in aliases):raise ValueError('Invalid reference alias')
        rows.append([r['name'],r['type'],'|'.join(aliases)])
    # Identical surface names may represent different identities. Preserve their rows.
    return sorted(rows,key=lambda r:(r[1],r[0],r[2]))

def cmd_export(a):
    root=Path(a.work);reg=load(root/'registry.json',[])
    ps=load(root/'paragraphs.json',None)
    if ps is None or any(p['status']!='done' for p in ps):raise ValueError('Complete all paragraphs before export')
    rows=reference_rows(reg,a.reviewed_only)
    now=datetime.now(ZoneInfo(a.timezone)) if a.timezone else datetime.now().astimezone()
    columns=COLUMNS[:2] if a.no_alias else COLUMNS
    buffer=io.StringIO(newline='')
    writer=csv.writer(buffer);writer.writerow(columns)
    writer.writerows(row[:2] if a.no_alias else row for row in rows)
    entry=rotate_csv(a.output_dir,work_source(root),buffer.getvalue().encode('utf-8-sig'),now.isoformat(),root,columns)
    output=Path(entry['output'])
    cleanup_work(root)
    label=output.name.replace('\\','\\\\').replace('[','\\[').replace(']','\\]')
    print(f'[{label}](<{output}>)')

def cmd_status(a):
    root=Path(a.work);ps=load(root/'paragraphs.json',[]);reg=load(root/'registry.json',[])
    from collections import Counter
    print('paragraphs:',len(ps),'done:',sum(x['status']=='done' for x in ps),'pending:',sum(x['status']=='pending' for x in ps))
    print('entities:',len(reg),'by_type:',dict(sorted(Counter(x['type'] for x in reg).items())))

def cmd_verify(a):
    root=Path(a.work);reg=load(root/'registry.json',[]);ids=set(a.ids.split(','))
    n=0
    for r in reg:
        if r['id'] in ids or r['name'] in ids:r['review_status']='verified';n+=1
    save(root/'registry.json',reg);print('Verified',n)

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('init');q.add_argument('--input',required=True);q.add_argument('--out',required=True);q.add_argument('--chunk-chars',type=int,default=300);q.add_argument('--overlap',type=int,default=40);q.set_defaults(func=cmd_init)
    q=sub.add_parser('next');q.add_argument('--work',required=True);q.add_argument('--max-paragraphs',type=int,default=4);q.add_argument('--max-chars',type=int,default=1200);q.set_defaults(func=cmd_next)
    q=sub.add_parser('merge');q.add_argument('--work',required=True);q.add_argument('--response',required=True);q.set_defaults(func=cmd_merge)
    q=sub.add_parser('resolve');q.add_argument('--work',required=True);q.add_argument('--response',required=True);q.set_defaults(func=cmd_resolve)
    q=sub.add_parser('export');q.add_argument('--work',required=True);q.add_argument('--output-dir',required=True);q.add_argument('--timezone',help='User IANA time zone, e.g. Asia/Taipei; defaults to system local time');q.add_argument('--no-alias',action='store_true',help='Export only name,type');q.add_argument('--reviewed-only',action='store_true');q.set_defaults(func=cmd_export)
    q=sub.add_parser('status');q.add_argument('--work',required=True);q.set_defaults(func=cmd_status)
    q=sub.add_parser('verify');q.add_argument('--work',required=True);q.add_argument('--ids',required=True);q.set_defaults(func=cmd_verify)
    a=p.parse_args();a.func(a)
if __name__=='__main__':main()
