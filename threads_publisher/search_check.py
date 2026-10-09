"""Manually dispatched GET-only keyword permission test with encrypted results."""
import argparse
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from .core import SafeError
from .market import MarketClient,features,public_url,POST_METRICS,metric
from .storage import read_json
from .private_state import PrivateState
from .weekly import private_path


def select_keywords(catalog,group='ai_tools',offset=0,count=3,keyword=''):
    groups=catalog['groups']
    all_words=list(dict.fromkeys(word for g in groups for word in g['keywords']))
    if keyword:
        if keyword not in all_words:raise SafeError('Keyword must be present in the configured catalog')
        return [keyword]
    matches=[g for g in groups if g['id']==group]
    if len(matches)!=1 or not 0<=offset<len(matches[0]['keywords']) or not 1<=count<=10:
        raise SafeError('Unknown keyword group or invalid offset/count (maximum 10)')
    return matches[0]['keywords'][offset:offset+count]


def run_check(token,key,catalog,words,path='private/search-check.enc',transport=None,search_type='RECENT',limit=10):
    if search_type not in ('TOP','RECENT') or not 1<=limit<=25 or not 1<=len(words)<=10:
        raise SafeError('Invalid read-only search limits')
    output=private_path(path)
    cipher=PrivateState(key=key).cipher # Validate encryption BEFORE any network request.
    client=MarketClient(token,limit=len(words),transport=transport)
    result={'collected_at':datetime.now(timezone.utc).isoformat(),'operation':'GET keyword_search only',
            'search_type':search_type,'queries':[],'posts':[],'errors':[]}
    seen={}
    try:
        for word in words:
            try:
                payload=client.get('keyword_search',{'q':word,'search_type':search_type,'limit':limit,'fields':'id,username,text,timestamp,permalink'})
                if not isinstance(payload.get('data'),list):raise SafeError('Unexpected keyword_search response')
                result['queries'].append({'keyword':word,'success':True,'returned':min(len(payload['data']),limit)})
                for row in payload['data'][:limit]:
                    if not isinstance(row,dict) or not isinstance(row.get('id'),str):continue
                    url=public_url(row.get('permalink'))
                    if not url or not isinstance(row.get('text'),str):continue
                    matching=[g['theme'] for g in catalog['groups'] if any(term.casefold() in row['text'].casefold() for term in g['keywords'])]
                    data=features(row['text'],[])
                    post=seen.setdefault(row['id'],{'url':url,'themes':matching or ['未分類'],'characters':data['characters'],
                                                   'hook':data['hook'],'metrics':{name:metric(row.get(field)) for name,field in POST_METRICS.items()},'search_keywords':[]})
                    if word not in post['search_keywords']:post['search_keywords'].append(word)
            except SafeError as error:
                result['queries'].append({'keyword':word,'success':False})
                result['errors'].append({'reason':str(error)})
                # This is a connection test: stop on permission, authentication or rate failure.
                break
    finally:
        result['posts']=list(seen.values())
        PrivateState._write(output,cipher.encrypt(json.dumps(result,ensure_ascii=False).encode()))
    return {'successful_queries':sum(q['success'] for q in result['queries']), 'posts':len(result['posts']), 'errors':len(result['errors'])}


def decrypt_report(source,key,output='private/search-check.md'):
    source,output=private_path(source),private_path(output)
    cipher=PrivateState(key=key).cipher
    try:result=json.loads(cipher.decrypt(source.read_bytes()))
    except Exception:raise SafeError('Cannot authenticate/decrypt search test artifact') from None
    lines=['# keyword_search 接続テスト結果',f"取得日時：{result['collected_at']}",
           'テーマはキーワード一致による分類です。本文は保存していません。反応数が返らない場合は取得不可です。',
           '|テーマ|文字数|いいね|返信|リポスト|引用|投稿URL|','|---|---:|---:|---:|---:|---:|---|']
    for post in result['posts']:
        numbers=[str(post['metrics'][name]) if post['metrics'][name] is not None else '取得不可' for name in POST_METRICS]
        lines.append('|'+ '/'.join(post['themes'])+'|'+str(post['characters'])+'|'+'|'.join(numbers)+'|'+post['url']+'|')
    if not result['posts']:lines.append('\n有効な公開投稿が0件でした。検索の成功と0件の結果は両立します。')
    lines.append('\n## 認証・権限・接続の結果')
    for query in result['queries']:lines.append(f"- {query['keyword']}：{'成功' if query['success'] else '失敗'}")
    for error in result['errors']:lines.append('- '+error['reason'])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'posts':len(result['posts']),'errors':len(result['errors'])}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--catalog',default='config/search-keywords.json')
    parser.add_argument('--group',default='ai_tools')
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--count',type=int,default=3)
    parser.add_argument('--keyword',default='')
    parser.add_argument('--search-type',choices=['RECENT','TOP'],default='RECENT')
    parser.add_argument('--limit',type=int,default=10)
    parser.add_argument('--decrypt',action='store_true')
    parser.add_argument('--source',default='private/search-check.enc')
    args=parser.parse_args()
    try:
        key=os.environ.get('THREADS_STATE_KEY','')
        if args.decrypt:
            result=decrypt_report(args.source,key)
        else:
            catalog=read_json(args.catalog)
            words=select_keywords(catalog,args.group,args.offset,args.count,args.keyword)
            result=run_check(os.environ.get('THREADS_ACCESS_TOKEN'),key,catalog,words,search_type=args.search_type,limit=args.limit)
        # Counts only: no token, raw errors, response bodies, handles or URLs in Actions logs.
        print(json.dumps(result))
        return 1 if result.get('errors') else 0
    except (SafeError,OSError,ValueError,KeyError,TypeError) as error:
        print(str(error) if isinstance(error,SafeError) else 'Read-only test failed; inspect configuration and encrypted results')
        return 1


if __name__=='__main__':raise SystemExit(main())
