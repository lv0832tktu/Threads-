"""Durable multi-container publisher. Uncertain publication is never retried."""
from datetime import datetime, timezone
import json
from .core import SafeError
from .schema import normalize_post, approval_digest


def _save(history, sql, args):
    history.db.execute(sql, args)
    history.db.commit()
    history.persist()


def _part(client, history, account, post, index, payload, resume, publish=True):
    row = history.db.execute('SELECT status,container_id,remote_id FROM post_parts WHERE account=? AND post_id=? AND part_index=?',
                             (account,post['id'],index)).fetchone()
    if row:
        status,container,remote=row
        if status == 'published':
            return remote
        if status == 'ready' and not publish:
            return container
        if not resume or status not in ('created','ready'):
            raise SafeError('Part may be uncertain; reconcile manually instead of retrying')
    else:
        _save(history,'INSERT INTO post_parts(account,post_id,part_index,status,reply_to_id,text) VALUES(?,?,?,?,?,?)',
              (account,post['id'],index,'creating',payload.get('reply_to_id'),payload.get('text')))
        container=client.create(account, **payload)
        _save(history,'UPDATE post_parts SET status=?,container_id=? WHERE account=? AND post_id=? AND part_index=?',
              ('created',container,account,post['id'],index))
    status=client.container_status(container)
    if status != 'FINISHED':
        raise SafeError('Container not FINISHED; explicit resume requires checking known pending state')
    _save(history,'UPDATE post_parts SET status=? WHERE account=? AND post_id=? AND part_index=?',
          ('ready',account,post['id'],index))
    if not publish:
        return container
    _save(history,'UPDATE post_parts SET status=? WHERE account=? AND post_id=? AND part_index=?',
          ('publishing',account,post['id'],index))
    remote=client.publish(account,container)
    _save(history,'UPDATE post_parts SET status=?,remote_id=?,published_at=? WHERE account=? AND post_id=? AND part_index=?',
          ('published',remote,datetime.now(timezone.utc).isoformat(),account,post['id'],index))
    return remote


def publish_content(client, history, post, enabled=False, approved_id='', resume=False):
    post=normalize_post(post)
    if not enabled or approved_id != post['id'] or post.get('approved') is not True:
        raise SafeError('Publication disabled or explicit approval missing')
    digest=approval_digest(post)
    if not post.get('approval_digest') or post['approval_digest'] != digest:
        raise SafeError('Reviewed content digest missing or changed; approve again')
    if post['post_type'] in ('image','carousel'):
        if post.get('rights_confirmed') is not True:
            raise SafeError('Image rights must be confirmed')
        if not post.get('image_sha256'):
            raise SafeError('Verified image SHA256 hashes are required before publication')
        from .media import validate_image_url, MediaError
        try:
            for index,url in enumerate(post['image_urls']):
                metadata=validate_image_url(url,client.media_hosts)
                if post.get('image_sha256') and metadata.get('sha256') != post['image_sha256'][index]:
                    raise SafeError('Image bytes changed since approval; review image and approve again')
        except MediaError:
            raise SafeError('Image preflight failed; verify allowed hosts, rights and image format') from None
    account=client.connect()['id']
    existing=history.db.execute('SELECT status,payload_digest FROM posts WHERE account=? AND post_id=?',(account,post['id'])).fetchone()
    if existing:
        if not resume or existing[0] == 'published' or existing[1] != digest:
            raise SafeError('Already recorded or changed content; refusing duplicate publication')
        unsafe=history.db.execute("SELECT 1 FROM post_parts WHERE account=? AND post_id=? AND status NOT IN ('created','ready','published')",(account,post['id'])).fetchone()
        if unsafe:
            raise SafeError('Uncertain part blocks resume; reconcile manually')
    else:
        if resume:
            raise SafeError('No known publication to resume')
        history.reserve(account,post['id'],post)
    _save(history,'UPDATE posts SET payload_digest=?,job_status=?,post_type=?,category=?,keywords=? WHERE account=? AND post_id=?',
          (digest,'posting',post['post_type'],post.get('category'),json.dumps(post.get('keywords',[]),ensure_ascii=False),account,post['id']))
    remotes=[]
    try:
        kind=post['post_type']
        if kind=='thread':
            parent=None
            for index,item in enumerate(post['thread_items']):
                payload={'media_type':'TEXT','text':item['text']}
                if parent: payload['reply_to_id']=parent
                parent=_part(client,history,account,post,index,payload,resume)
                remotes.append(parent)
        elif kind=='carousel':
            children=[]
            alt=post.get('alt_text','')
            for index,url in enumerate(post['image_urls']):
                payload={'media_type':'IMAGE','image_url':url,'is_carousel_item':True}
                if alt: payload['alt_text']=alt[index] if isinstance(alt,list) else alt
                children.append(_part(client,history,account,post,index,payload,resume,publish=False))
            payload={'media_type':'CAROUSEL','text':post['text'],'children':','.join(children)}
            remotes.append(_part(client,history,account,post,len(children),payload,resume))
        else:
            payload={'media_type':'TEXT' if kind=='text' else 'IMAGE','text':post['text']}
            if kind=='image':
                payload['image_url']=post['image_urls'][0]
                alt=post.get('alt_text','')
                if alt: payload['alt_text']=alt[0] if isinstance(alt,list) else alt
            remotes.append(_part(client,history,account,post,0,payload,resume))
    except SafeError:
        confirmed=history.db.execute("SELECT COUNT(*) FROM post_parts WHERE account=? AND post_id=? AND status='published'",(account,post['id'])).fetchone()[0]
        _save(history,'UPDATE posts SET job_status=?,error_kind=? WHERE account=? AND post_id=?',
              ('partial' if confirmed else 'failed','part_failed_or_pending',account,post['id']))
        raise
    post['remote_ids']=remotes
    history.finish(account,post['id'],remotes[0],post)
    return remotes[0]
