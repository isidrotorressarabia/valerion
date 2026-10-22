"""Small bounded read-only load test. Run only against your own local/staging service."""
import argparse
import concurrent.futures
import json
import math
import time
import httpx

def measure(url,requests=100,concurrency=5):
    elapsed=[];failures=0
    with httpx.Client(base_url=url,timeout=30) as client:
        # Exclude warm-up; measure cold starts separately.
        client.get('/health').raise_for_status()
        def one(i):
            start=time.perf_counter()
            try:
                r=client.get(['/','/api/mezcales?page=1','/api/mezcales?sort=precio_asc','/api/mezcales?q=Mezcal'][i%4]);ok=r.status_code==200
            except httpx.HTTPError:ok=False
            return (time.perf_counter()-start)*1000,ok
        start=time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            for ms,ok in executor.map(one,range(requests)):elapsed.append(ms);failures+=not ok
        duration=time.perf_counter()-start
    elapsed.sort()
    return {'requests':requests,'concurrency':concurrency,'errors':failures,'seconds':round(duration,3),'requests_per_second':round(requests/duration,2),'p50_ms':round(elapsed[math.ceil(len(elapsed)*.5)-1],2),'p95_ms':round(elapsed[math.ceil(len(elapsed)*.95)-1],2)}
if __name__=='__main__':
    p=argparse.ArgumentParser(description='Carga de lectura sobre tu servidor local o de pruebas; no ejecuta escrituras.')
    p.add_argument('--url',default='http://127.0.0.1:8000');p.add_argument('--requests',type=int,default=100);p.add_argument('--concurrency',type=int,default=5)
    args=p.parse_args()
    if not 1<=args.requests<=10000 or not 1<=args.concurrency<=30:p.error('Usa 1–10000 solicitudes y concurrencia 1–30.')
    result=measure(args.url,args.requests,args.concurrency);print(json.dumps(result,indent=2));raise SystemExit(1 if result['errors'] else 0)
