"""Read-only, narrow-window iFinD HTTP probe. Credentials stay in memory.

Official endpoints from the iFinD API manual. Does not print/write request
headers or credentials and refuses redirects to avoid forwarding credentials.
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/ifind_auction_probe_20260923'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post(endpoint, body, auth):
    request = urllib.request.Request('https://quantapi.51ifind.com/api/v1/'+endpoint,
        data=json.dumps(body).encode(), headers={'Content-Type':'application/json', 'access_token':auth, 'ifindlang':'cn'})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=25) as response:
            status, raw = response.status, response.read(8_000_000).decode('utf-8')
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(100_000).decode('utf-8',errors='replace')
    except (urllib.error.URLError, TimeoutError) as exc:
        return {'http_status':None, 'transport_error':type(exc).__name__}
    for value in [auth, os.environ.get('IFIND_AUTH_TOKEN'), os.environ.get('IFIND_REFRESH_TOKEN')]:
        if value:
            raw = raw.replace(value,'[REDACTED]')
    try:
        content = json.loads(raw)
    except ValueError:
        content = {'unparsed_response':raw[:500]}
    return {'http_status':status, 'response':content}


def refresh_access():
    refresh = os.environ.get('IFIND_REFRESH_TOKEN','')
    if not refresh:
        return None, {'status':'missing_refresh_token'}
    request = urllib.request.Request('https://quantapi.51ifind.com/api/v1/get_access_token',
        data=b'',headers={'Content-Type':'application/json','refresh_token':refresh})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=25) as response:
            body=json.loads(response.read(100_000).decode('utf-8'))
    except urllib.error.HTTPError as exc:
        return None, {'status':'http_error','http_status':exc.code}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return None, {'status':type(exc).__name__}
    token=(body.get('data') or {}).get('access_token')
    # Never return the authentication response or token in diagnostics.
    return token, {'status':'ok' if token else 'no_access_token','errorcode':body.get('errorcode')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--endpoint', choices=['snap_shot','high_frequency'],default='snap_shot')
    parser.add_argument('--start',default='09:24:55')
    parser.add_argument('--end',default='09:25:05')
    parser.add_argument('--indicators',default='latest,volume,amount')
    args = parser.parse_args()
    auth = os.environ.get('IFIND_AUTH_TOKEN','')
    if not auth:
        raise RuntimeError('IFIND_AUTH_TOKEN unavailable')
    body={'codes':args.code,'indicators':args.indicators,'starttime':f'{args.date} {args.start}','endtime':f'{args.date} {args.end}'}
    result=post(args.endpoint,body,auth)
    refresh_status = None
    if result.get('http_status') == 401:
        token, refresh_status = refresh_access()
        if token:
            result=post(args.endpoint,body,token)
    payload={'retrieved_at':datetime.now().isoformat(timespec='seconds'),'endpoint':args.endpoint,'request':body,'refresh_status':refresh_status,**result}
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/f"{args.code}_{args.date}_{args.endpoint}_{args.start.replace(':','')}_{args.end.replace(':','')}.json"
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),'utf-8')
    response=result.get('response',{})
    print(json.dumps({'path':str(path),'refresh_status':refresh_status,'http_status':result.get('http_status'),'transport_error':result.get('transport_error'),'body':response},ensure_ascii=False)[:5500])


if __name__=='__main__':
    main()
