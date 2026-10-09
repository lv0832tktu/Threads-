"""Manually dispatched GET-only keyword permission test with encrypted results."""
import argparse
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from .core import SafeError
from .market import MarketClient,PublicAPIError,API_VERSION,features,public_url,POST_METRICS,metric
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


SEARCH_FIELDS={'standard':'id,username,text,timestamp,permalink','minimal':'id,text,timestamp,permalink'}


def request_diagnostic(api_version,search_type,limit,fields,query_count):
    return {'method':'GET','endpoint':'keyword_search','api_version':api_version,'search_type':search_type,
            'limit':limit,'fields':SEARCH_FIELDS[fields],'query_count':query_count,
            'required_permissions':['threads_basic','threads_keyword_search'],'granted_permissions':'not_verified'}


def run_check(token,key,catalog,words,path='private/search-check.enc',transport=None,search_type='RECENT',limit=10,api_version=API_VERSION,fields='standard'):
    if search_type not in ('TOP','RECENT') or not 1<=limit<=25 or not 1<=len(words)<=10:
        raise SafeError('Invalid read-only search limits')
    output=private_path(path)
    cipher=PrivateState(key=key).cipher # Validate encryption BEFORE any network request.
    if fields not in SEARCH_FIELDS:raise SafeError('Unknown search fields preset')
    client=MarketClient(token,limit=len(words),transport=transport,api_version=api_version)
    result={'collected_at':datetime.now(timezone.utc).isoformat(),'operation':'GET keyword_search only',
            'search_type':search_type,'queries':[],'posts':[],'errors':[],
            'request':request_diagnostic(api_version,search_type,limit,fields,len(words))}
    seen={}
    try:
        for word in words:
            try:
                payload=client.get('keyword_search',{'q':word,'search_type':search_type,'limit':limit,'fields':SEARCH_FIELDS[fields]})
                if not isinstance(payload.get('data'),list):raise PublicAPIError('Unexpected keyword_search response','response_format',200)
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
                result['errors'].append({'reason':str(error),'diagnostic':error.diagnostic if isinstance(error,PublicAPIError) else {'kind':'local_validation','http_status':None,'api_code':None,'api_subcode':None}})
                # This is a connection test: stop on permission, authentication or rate failure.
                break
    finally:
        result['posts']=list(seen.values())
        PrivateState._write(output,cipher.encrypt(json.dumps(result,ensure_ascii=False).encode()))
    summary={'successful_queries':sum(q['success'] for q in result['queries']), 'posts':len(result['posts']), 'errors':len(result['errors'])}
    if result['errors']:
        summary['diagnostics']=[error['diagnostic'] for error in result['errors']]
        summary['request']=result['request']
    return summary


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
    if result.get('request'):lines.append('安全なリクエスト設定：'+json.dumps(result['request'],ensure_ascii=False))
    for query in result['queries']:lines.append(f"- {query['keyword']}：{'成功' if query['success'] else '失敗'}")
    for error in result['errors']:
        lines.append('- '+error['reason'])
        if error.get('diagnostic'):lines.append('  '+json.dumps(error['diagnostic'],ensure_ascii=False))
        else:lines.append('  旧履歴：HTTPステータス・APIコードは記録されていません。推測できません。')
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
    parser.add_argument('--validate-key',action='store_true',help='Validate local encryption prerequisites without API requests')
    parser.add_argument('--diagnose-config',action='store_true',help='Inspect safe configuration only; no API requests')
    parser.add_argument('--api-version',default=API_VERSION)
    parser.add_argument('--fields',choices=tuple(SEARCH_FIELDS),default='standard')
    parser.add_argument('--source',default='private/search-check.enc')
    args=parser.parse_args()
    try:
        key=os.environ.get('THREADS_STATE_KEY','')
        if args.validate_key:
            PrivateState(key=key)
            print('Encryption prerequisites passed; no API request made')
            return 0
        if args.decrypt:
            result=decrypt_report(args.source,key)
        else:
            catalog=read_json(args.catalog)
            words=select_keywords(catalog,args.group,args.offset,args.count,args.keyword)
            if args.diagnose_config:
                PrivateState(key=key)
                MarketClient(os.environ.get('THREADS_ACCESS_TOKEN'),api_version=args.api_version)
                if not 1<=args.limit<=25:raise SafeError('Invalid read-only search limits')
                result={**request_diagnostic(args.api_version,args.search_type,args.limit,args.fields,len(words)), 'token_present':True,'network_requests':0}
            else:
                result=run_check(os.environ.get('THREADS_ACCESS_TOKEN'),key,catalog,words,search_type=args.search_type,limit=args.limit,api_version=args.api_version,fields=args.fields)
        # Counts only: no token, raw errors, response bodies, handles or URLs in Actions logs.
        print(json.dumps(result))
        return 1 if result.get('errors') else 0
    except (SafeError,OSError,ValueError,KeyError,TypeError) as error:
        print(str(error) if isinstance(error,SafeError) else 'Read-only test failed; inspect configuration and encrypted results')
        return 1


if __name__=='__main__':raise SystemExit(main())
