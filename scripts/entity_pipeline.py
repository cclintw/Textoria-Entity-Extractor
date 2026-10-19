#!/usr/bin/env python3
"""Textoria incremental entity reference inventory; standard-library only."""
import argparse,csv,json,re,hashlib,unicodedata,os,tempfile,io
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

def export_text_id(source,sources):
    """Use the source filename stem; disambiguate same-name sources in this project."""
    label=source['source_label']
    reserved={unicodedata.normalize('NFC',s.get('text_id',s['source_id'])).casefold()
              for s in sources.values() if s['source_id']!=source['source_id']}
    candidate=label
    if unicodedata.normalize('NFC',candidate).casefold() in reserved:
        candidate=f"{label}-{source['source_id']}"
    while unicodedata.normalize('NFC',candidate).casefold() in reserved:
        candidate+='_'+source['source_id']
    return candidate

def commit_versions(index_path,index,folder,cleanup):
    # Remember cleanup until it succeeds, so interruptions cannot orphan expired versions.
    paths=list(dict.fromkeys([*index.get('pending_deletions',[]),*(str(p) for p in cleanup)]))
    if any(Path(p).resolve().parent!=folder for p in paths):raise ValueError('Refusing to delete a version outside csv/')
    index['pending_deletions']=paths
    atomic_bytes(index_path,(json.dumps(index,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    for value in paths:
        path=Path(value)
        if path.exists():path.unlink()
    index.pop('pending_deletions')
    atomic_bytes(index_path,(json.dumps(index,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))

def migrate_slot_state(folder,state):
    """Rename legacy slot files by their actual export times; never by slot number."""
    if 'slots' not in state:return []
    versions=[];cleanup=[]
    for entry in sorted(state['slots'].values(),key=lambda e:e['generation']):
        old=Path(entry['output']).resolve()
        if old.parent!=folder:raise ValueError('Legacy CSV outside expected directory')
        if not old.exists():continue
        data=old.read_bytes();stamp=datetime.fromisoformat(entry['exported_at'])
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        output=timestamped_path(folder,state['source_id'],stamp)
        while output.exists() and output.read_bytes()!=data:
            stamp+=timedelta(microseconds=1);output=timestamped_path(folder,state['source_id'],stamp)
        if not output.exists():atomic_bytes(output,data)
        updated={k:v for k,v in entry.items() if k!='slot'}
        updated.update(output=str(output),filename_at=stamp.isoformat(),previous_slot_output=str(old))
        versions.append(updated);cleanup.append(old)
    state['versions']=versions
    state.pop('slots')
    if versions:state['last_filename_at']=max(versions,key=lambda e:datetime.fromisoformat(e['filename_at']))['filename_at']
    return cleanup

def migrate_csv_versions(folder):
    folder=Path(folder).resolve();root=folder.parent/'.textoria-entities'
    with csv_version_lock(root):
        index_path=root/'csv-versions.json';index=load(index_path,{'version':2,'sources':{}})
        cleanup=[]
        for state in index['sources'].values():cleanup.extend(migrate_slot_state(folder,state))
        index['version']=2
        commit_versions(index_path,index,folder,cleanup)

def rotate_csv(folder,source,data,exported_at,work,columns):
    """Create a dated CSV, then remove the oldest export beyond three per source."""
    folder=Path(folder).resolve()
    if folder.name!='csv':raise ValueError('Export directory must be the project csv/ folder')
    folder.mkdir(parents=True,exist_ok=True)
    root=folder.parent/'.textoria-entities'
    with csv_version_lock(root):
        index_path=root/'csv-versions.json'
        index=load(index_path,{'version':2,'sources':{}})
        state=index['sources'].setdefault(source['source_id'],{
            **source,'total_exports':0,'versions':[]})
        if state['source_key']!=source['source_key']:raise ValueError('Source identifier collision')
        cleanup=migrate_slot_state(folder,state)
        # Keep retention keyed by source_id, including exports with legacy hash filenames.
        if not state.get('text_id'):state['text_id']=export_text_id(source,index['sources'])
        generation=state['total_exports']+1
        stamp=datetime.fromisoformat(exported_at)
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        if state.get('last_filename_at'):
            last=datetime.fromisoformat(state['last_filename_at'])
            stamp=stamp.astimezone(last.tzinfo)
            if stamp<=last:stamp=last+timedelta(microseconds=1)
        output=timestamped_path(folder,state['text_id'],stamp)
        while output.exists():
            stamp+=timedelta(microseconds=1);output=timestamped_path(folder,state['text_id'],stamp)
        atomic_bytes(output,data)
        entry={'output':str(output),'exported_at':exported_at,'work':str(Path(work).resolve()),
               'generation':generation,'filename_at':stamp.isoformat(),'columns':columns,'source_id':source['source_id'],'text_id':state['text_id'],
               'alias_semantics':'source_context_not_global_identity'}
        state['total_exports']=generation
        state['last_filename_at']=stamp.isoformat()
        versions=sorted([*state['versions'],entry],key=lambda e:e['generation'])
        removed=versions[:-3];state['versions']=versions[-3:]
        index['version']=2
        commit_versions(index_path,index,folder,[*cleanup,*(Path(e['output']).resolve() for e in removed)])
        return entry

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
      'output_contract':{'paragraphs':[{'id':'p00001','entities':[{'name':'原文實體','type':'place','aliases':[],'evidence':'原文中出現的字串'}]}]},
      'instructions':['Read every paragraph in full; extract ALL distinct entities, including known ones only if a new alias or ambiguity is found.',
       'Known matches are hints, not definitive annotations; find ALL new entities and names even inside known matches.',
       'Extract every meaningful name form as its own name/type row: full names, short names, aliases and independently meaningful nested organization names. An alias-only known match does not replace a standalone row. Do not discard shorter names just because a longer name contains them. Do not enumerate arbitrary substrings or infer an alias relationship from containment alone.',
       'Aliases record source-context usage, never a global same-as relation. A short name may refer to different organizations in different contexts. Never replace it with one full name, transfer aliases transitively or suppress its standalone row. known_matches.registry_ids identify exact name rows only; alias_contexts are contextual hints, not identity decisions.',
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
            # No automatic merge across aliases: same surface+type is a candidate only.
            existing=next((r for r in reg if r['name']==name and r['type']==typ),None)
            if existing:
                for alias in aliases:
                    if alias not in existing.setdefault('aliases',[]):existing['aliases'].append(alias)
                existing['source_paragraphs']=list(dict.fromkeys(existing['source_paragraphs']+[p['id']]))
                events.append({'paragraph':p['id'],'entity_id':existing['id'],'action':'seen','name':name})
            else:
                eid='e'+hashlib.sha256((typ+'\x00'+name).encode()).hexdigest()[:14]
                obj={'id':eid,'name':name,'tei':tei,'type':typ,'aliases':aliases,'source_paragraphs':[p['id']],'review_status':'candidate'}
                reg.append(obj);new.append(name)
                events.append({'paragraph':p['id'],'entity_id':eid,'action':'new','name':name})
            target=existing if existing else obj
            for relation in relations:
                if relation not in target.setdefault('alias_relations',[]):target['alias_relations'].append(relation)
                # An observed alias is a name row in its own right, without inheriting the
                # full name's ID, aliases, or identity. No reverse/transitive alias expansion.
                other=next((r for r in reg if r['name']==relation['alias'] and r['type']==typ),None)
                if other is None:
                    alias_id='e'+hashlib.sha256((typ+'\x00'+relation['alias']).encode()).hexdigest()[:14]
                    other={'id':alias_id,'name':relation['alias'],'tei':tei,'type':typ,'aliases':[],
                           'source_paragraphs':[p['id']],'review_status':'candidate'}
                    reg.append(other);new.append(relation['alias'])
                    events.append({'paragraph':p['id'],'entity_id':alias_id,'action':'alias_name_added','name':relation['alias']})
                else:
                    other['source_paragraphs']=list(dict.fromkeys(other['source_paragraphs']+[p['id']]))
            if typ=='title':
                target['mention_role']='work_reference'
                target['reference_evidence']=list(dict.fromkeys(target.get('reference_evidence',[])+[evidence]))
        p['status']='done'
    save(root/'paragraphs.json',ps);save(root/'registry.json',reg);save(root/'audit.json',audit+events)
    print(f'Merged {len(entries)} paragraphs, {len(new)} new candidates, total={len(reg)}')

def reference_rows(reg,reviewed_only=False):
    """Serialize reviewed name forms; aliases supplement, never replace, name rows."""
    blocked={(r['name'],r['type']) for r in reg if r.get('reference_excluded')}
    eligible=[]
    for r in reg:
        if r['type'] in EXCLUDED_TYPES or r.get('reference_excluded'):continue
        if r['type']=='currency' and re.fullmatch(r'[零〇一二三四五六七八九十百千萬億兩壹貳參肆伍陸柒捌玖拾佰仟數餘有近\d.]+(?:金|兩|錢|文|元)',r['name']):continue
        if reviewed_only and r['review_status']!='verified':continue
        eligible.append(r)
    result={(r['name'],r['type']):[r['name'],r['type'],'|'.join(r.get('aliases',[]))] for r in eligible}
    for r in eligible:
        for alias in r.get('aliases',[]):
            if '|' in alias:raise ValueError('Alias contains reserved separator |')
            key=(alias,r['type'])
            if alias and key not in blocked:
                result.setdefault(key,[alias,r['type'],''])
    return sorted(result.values(),key=lambda r:(r[1],r[0]))

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
    history=load(root/'exports.json',[])
    history.append({**entry,'timezone':a.timezone or str(now.tzinfo),
                    'rows':len(rows),'reviewed_only':a.reviewed_only,
                    'alias_separator':None if a.no_alias else '|','alias_semantics':'source_context_not_global_identity',
                    'deduplication_key':['name','type']})
    save(root/'exports.json',history)
    output=Path(entry['output'])
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
    q=sub.add_parser('export');q.add_argument('--work',required=True);q.add_argument('--output-dir',required=True);q.add_argument('--timezone',help='User IANA time zone, e.g. Asia/Taipei; defaults to system local time');q.add_argument('--no-alias',action='store_true',help='Export only name,type');q.add_argument('--reviewed-only',action='store_true');q.set_defaults(func=cmd_export)
    q=sub.add_parser('status');q.add_argument('--work',required=True);q.set_defaults(func=cmd_status)
    q=sub.add_parser('verify');q.add_argument('--work',required=True);q.add_argument('--ids',required=True);q.set_defaults(func=cmd_verify)
    a=p.parse_args();a.func(a)
if __name__=='__main__':main()
