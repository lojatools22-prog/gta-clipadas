"""Gera a versão de demonstração estática (sem servidor) em ../demo."""
import json, re, shutil, sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT.parent / "demo"
sys.path.insert(0, str(ROOT))
from app import templates  # noqa: E402

(OUT / "static").mkdir(parents=True, exist_ok=True)
(OUT / "previews").mkdir(exist_ok=True)
(OUT / "demo").mkdir(exist_ok=True)
for f in ("logo.jpg", "hero-scene.jpg", "BowlbyOne_400Regular.ttf", "termos.html", "privacidade.html"):
    shutil.copy(ROOT / "app/static" / f, OUT / "static" / f)
for p in (ROOT / "app/previews").glob("*.jpg"):
    shutil.copy(p, OUT / "previews" / p.name)
for name, y in (("noite", 820), ("praia", 1000), ("marina", 800)):
    Image.open(ROOT / f"app/scenes/{name}.jpg").convert("RGB").crop((0, y, 1152, y + 648)).resize((640, 360)).save(OUT / f"demo/{name}.jpg", quality=80)

live = [{"login": "streamer_um", "name": "streamer_um", "title": "GTA 6 DIA 1", "viewers": 98213, "game": "Grand Theft Auto VI", "thumb": "demo/noite.jpg"},
        {"login": "streamerdois", "name": "StreamerDois", "title": "PRIMEIRA VEZ NO GTA 6", "viewers": 64110, "game": "Grand Theft Auto VI", "thumb": "demo/praia.jpg"},
        {"login": "streamertres", "name": "StreamerTres", "title": "GTA 6 COM A FAMÍLIA", "viewers": 31877, "game": "Grand Theft Auto VI", "thumb": "demo/marina.jpg"}]
clips = [{"id": f"c{i}", "url": "https://clips.twitch.tv/exemplo", "title": t, "views": v, "duration": 28, "creator": c, "thumb": th,
          "created_at": "2026-11-19T04:10:00Z", "vod_id": "1", "vod_offset": 4200}
         for i, (t, v, c, th) in enumerate([("ele caiu da ponte KKKKK", 45122, "streamer_um", "demo/praia.jpg"),
                                            ("perseguição de 5 estrelas", 38110, "StreamerDois", "demo/noite.jpg"),
                                            ("roubou a lancha errada", 22004, "StreamerTres", "demo/marina.jpg")])]
data = {"status": {"demo": True, "demo_text": "Demonstração: o código de SMS é 123456, o código Pro é GTA6, e as contas somem ao recarregar.",
                   "captions": True, "max_len": 180, "free_clips": 3, "max_upload_mb": 3072, "checkout": False, "sms": False, "checkout_pro": "", "checkout_passe": ""},
        "catalog": templates.public_catalog(), "live": live, "clips": clips,
        "vods": [{"id": "1", "title": "GTA 6 DIA 1 (gravada)", "url": "https://www.twitch.tv/videos/1", "duration": "6h12m",
                  "created_at": "2026-11-19T03:00:00Z", "views": 120000, "thumb": "demo/noite.jpg"}]}

MOCK = """<script>
/* Demonstração: simula o servidor (contas, SMS, planos e cortes) dentro da página. */
(function(){const D=%s;const jobs={},users={},toks={},otps={};
 const PL={gratis:{n:'Grátis',q:3,t:0,mb:1024,b:false,w:false},iniciante:{n:'Iniciante',q:30,t:1,mb:1024,b:false,w:true},clipador:{n:'Clipador',q:150,t:2,mb:1024,b:false,w:true},pro:{n:'Pro',q:300,t:2,mb:3072,b:true,w:true},passe:{n:'Passe GTA 6',q:30,t:2,mb:1024,b:false,w:true}};
 const ok=b=>Promise.resolve(new Response(JSON.stringify(b),{status:200,headers:{'Content-Type':'application/json'}}));
 const bad=(c,m)=>Promise.resolve(new Response(JSON.stringify({detail:m}),{status:c,headers:{'Content-Type':'application/json'}}));
 const plan=u=>u.plan!=='gratis'&&u.until>Date.now()/1000?u.plan:'gratis';
 const pub=u=>{const p=plan(u),P=PL[p];return {name:u.name,email:u.email,phone_hint:'+55•••••••'+u.phone.slice(-4),plan:p,plan_name:P.n,plan_until:p==='gratis'?null:u.until,left:p==='gratis'?u.credits:u.quota,tier:P.t,max_mb:P.mb,batch:P.b,watermark_off:P.w,admin:false,credits:p==='gratis'?u.credits:u.quota,pro:p!=='gratis'}};
 const who=o=>{const h=(o&&o.headers&&(o.headers.Authorization||o.headers.authorization))||'';return toks[h.replace('Bearer ','')]};
 const body=o=>{try{return JSON.parse(o.body)}catch{return {}}};
 const tok=u=>{const t=Math.random().toString(36).slice(2);toks[t]=u;return {step:'done',token:t,user:pub(u)}};
 const otp=(u,purpose)=>{const c=Math.random().toString(36).slice(2);otps[c]={u,purpose};return {challenge:c,phone_hint:'+55•••••••'+u.phone.slice(-4),dev_code:'123456'}};
 const TT=Object.fromEntries(D.catalog.templates.map(t=>[t.id,t.tier])),CT=Object.fromEntries(D.catalog.captions.map(c=>[c.id,c.tier]));
 const real=window.fetch.bind(window);
 window.fetch=function(u,o){u=String(u);o=o||{};if(!u.startsWith('/api/'))return real(u,o);const b=body(o);
  if(u==='/api/status')return ok(D.status);if(u==='/api/catalog')return ok(D.catalog);if(u.startsWith('/api/live'))return ok(D.live);
  if(u.startsWith('/api/clips'))return ok(D.clips);if(u.startsWith('/api/vods/'))return ok(D.vods);if(u.startsWith('/api/schedule/'))return ok([]);
  if(u==='/api/waitlist')return ok({ok:true});
  if(u==='/api/auth/signup'){const e=(b.email||'').trim().toLowerCase(),ph=(b.phone||'').replace(/\\D/g,'');if((b.name||'').trim().length<2)return bad(400,'Coloque seu nome');if(!/^[^@ ]+@[^@ ]+[.][^@ ]+$/.test(e))return bad(400,'E-mail inválido');if(ph.length<10)return bad(400,'Celular inválido. Use DDD + número, ex.: (11) 99999-9999');if((b.password||'').length<6)return bad(400,'A senha precisa ter pelo menos 6 caracteres');if(users[e])return bad(400,'Esse e-mail já tem conta. Entre com sua senha.');
    users[e]={name:b.name,email:e,phone:ph,pw:b.password,credits:0,plan:'gratis',until:0,quota:0,jobs:[],ok:false};return ok({step:'phone',...otp(users[e],'phone')})}
  if(u==='/api/auth/verify-phone'||u==='/api/auth/verify-login'){const x=otps[b.challenge];if(!x)return bad(400,'O código expirou. Peça um novo.');if(b.code!=='123456')return bad(400,'Código errado');delete otps[b.challenge];if(!x.u.ok){x.u.ok=true;x.u.credits=3}return ok(tok(x.u))}
  if(u==='/api/auth/resend'){const x=otps[b.challenge];return x?ok(otp(x.u,x.purpose)):bad(400,'Comece de novo')}
  if(u==='/api/auth/login'){const x=users[(b.email||'').trim().toLowerCase()];if(!x||x.pw!==b.password)return bad(400,'E-mail ou senha errados');return ok(x.ok?tok(x):{step:'phone',...otp(x,'phone')})}
  if(u==='/api/auth/forgot'){const x=users[(b.email||'').trim().toLowerCase()];return ok(x?{via:'sms',...otp(x,'reset')}:{via:'sms',challenge:'x'})}
  if(u==='/api/auth/reset'){const x=otps[b.challenge];if(!x||b.code!=='123456')return bad(400,'Código errado');x.u.pw=b.password;return ok(tok(x.u))}
  if(u==='/api/auth/logout')return ok({ok:true});
  const me=who(o);
  if(u==='/api/me')return me?ok(pub(me)):bad(401,'Entre na sua conta');
  if(u==='/api/pro/redeem'){if(!me)return bad(401,'Entre na sua conta');if((b.code||'').trim().toUpperCase()!=='GTA6')return bad(400,'Código inválido');me.plan='pro';me.until=Date.now()/1000+30*86400;me.quota=300;return ok(pub(me))}
  if(u==='/api/pay/checkout')return bad(400,'Na demo o pagamento não abre. Use o código GTA6 em "Tenho um código" pra testar o Pro.');
  if(u==='/api/my/jobs'){if(!me)return bad(401,'Entre na sua conta');return ok(me.jobs.map(j=>({...j,...st(j)})))}
  if(u.startsWith('/api/jobs/')&&(!o.method||o.method==='GET')){const j=jobs[u.split('/').pop()];return j?ok({id:j.id,...st(j)}):bad(404,'Corte não encontrado')}
  if(u.startsWith('/api/jobs')){if(!me)return bad(401,'Faça seu cadastro grátis pra gerar cortes (você ganha 3 cortes grátis).');
    const g=k=>o.body instanceof FormData?o.body.get(k):b[k];const P=PL[plan(me)];
    if((TT[g('template')]||0)>P.t||(CT[g('caption')]||0)>P.t)return bad(402,'Esse visual ou legenda não está no seu plano. Suba de plano pra usar.');
    let segs=g('segments');try{segs=typeof segs==='string'?JSON.parse(segs||'[]'):(segs||[])}catch{segs=[]}const n=Math.max(1,segs.length);
    if(n>1&&!P.b)return bad(402,'Corte em lote é do plano Pro.');const col=plan(me)==='gratis'?'credits':'quota';
    if(me[col]<=0)return bad(402,col==='credits'?'Seus cortes grátis acabaram. Escolha um plano pra continuar.':'Você usou todos os cortes do seu plano.');
    const ids=[];for(let i=0;i<n&&me[col]>0;i++){me[col]--;const id=Math.random().toString(16).slice(2,14);jobs[id]={id,t:Date.now(),title:(g('title')||'Corte GTA')+(n>1?' ('+(i+1)+')':'')};me.jobs.unshift(jobs[id]);ids.push(id)}
    return ok({id:ids[0],jobs:ids,status:'na fila',progress:5,user:pub(me)})}
  return bad(404,'?')};
 function st(j){const t=(Date.now()-j.t)/1000;const s=t<1.5?['baixando',25]:t<3?['legendando',55]:t<4.5?['renderizando',85]:['pronto',100];return {status:s[0],progress:s[1],video:'demo/exemplo.mp4',cover:'demo/capa.jpg',thumb:'demo/capa.jpg'}}
})();
</script>
"""
h = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
h = h.replace('href="/termos"', 'href="static/termos.html"').replace('href="/privacidade"', 'href="static/privacidade.html"').replace("url(/static/", "url(static/").replace('src="/static/', 'src="static/').replace('href="/static/', 'href="static/').replace("/previews/", "previews/")
h = h.replace("<script>\nconst $=s=>", MOCK % json.dumps(data, ensure_ascii=False) + "<script>\nconst $=s=>", 1)
assert "window.fetch=function" in h and not re.search(r'["(]/(static|previews)/', h)
(OUT / "index.html").write_text(h, encoding="utf-8")
print("demo ok:", sum(1 for _ in OUT.rglob("*") if _.is_file()), "arquivos")
