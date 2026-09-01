import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
const PW='Passw0rd!123', BASE='http://127.0.0.1:5274', API='http://127.0.0.1:8080', DB='p118_e2e_db'
const sql=q=>execFileSync('docker',['exec','p118_postgres','psql','-U','p118','-d',DB,'-tAc',q],{encoding:'utf8'}).trim().split('\n').filter(Boolean)
const api=async(p,{token,method='GET',body}={})=>{const r=await fetch(`${API}${p}`,{method,headers:{'content-type':'application/json',...(token?{authorization:`Bearer ${token}`}:{})},body:body?JSON.stringify(body):undefined});return{status:r.status,json:await r.json().catch(()=>null)}}
const b=await chromium.launch()
const ctx=await b.newContext({viewport:{width:1512,height:1000}})
const p=await ctx.newPage(); const errs=[]; p.on('pageerror',e=>errs.push(String(e)))
const U='px'+Math.floor(Math.random()*1e6)
await p.goto(`${BASE}/register`)
await p.fill('#reg-username',U); await p.fill('#reg-email',`${U}@e.test`); await p.fill('#reg-password',PW); await p.fill('#reg-confirm',PW)
await p.click('button[type=submit]'); await p.waitForURL('**/workspace',{timeout:30000})
const uid=sql(`SELECT id FROM users WHERE username='${U}'`)[0], rid=sql(`SELECT resident_id FROM residents LIMIT 1`)[0]
sql(`INSERT INTO user_resident_links (user_id,resident_id,verification_status,verified_at) VALUES ('${uid}','${rid}','VERIFIED',now()) ON CONFLICT (user_id) DO UPDATE SET verification_status='VERIFIED',verified_at=now()`)
await p.reload(); await p.waitForTimeout(1500)
const PU='pv'+Math.floor(Math.random()*1e6)
await api('/api/v1/auth/register',{method:'POST',body:{username:PU,password:PW}})
sql(`UPDATE users SET role='provider' WHERE username='${PU}'`)
const ptok=(await api('/api/v1/auth/login',{method:'POST',body:{username:PU,password:PW}})).json.access_token
const ctok=(await api('/api/v1/auth/login',{method:'POST',body:{username:U,password:PW}})).json.access_token

await p.fill('textarea','Giữ chỗ đỗ xe Khu B ngày 2028-09-25 cho xe máy biển số 51K-99123')
await p.keyboard.press('Enter')
const wfOf=()=>sql(`SELECT w.workflow_id FROM workflows w JOIN users u ON u.id=w.owner_user_id WHERE u.username='${U}' AND w.task_plan::text<>'null' ORDER BY w.created_at DESC LIMIT 1`)[0]
const wait=async(f,ms=150000)=>{const t=Date.now();while(Date.now()-t<ms){if(await f())return true;await new Promise(r=>setTimeout(r,2000))}return false}
await wait(async()=>{const w=wfOf();return w&&sql(`SELECT count(*) FROM service_approvals WHERE workflow_id='${w}' AND status='AWAITING'`)[0]!=='0'})
const w=wfOf(); console.log('workflow',w.slice(0,8))
const LY='Khu B đã kín chỗ ngày 25/09/2028. Bạn chọn khu khác giúp mình nhé.'
for(const t of sql(`SELECT task_id FROM service_approvals WHERE workflow_id='${w}' AND status='AWAITING'`)){
  const tool=sql(`SELECT tool FROM service_approvals WHERE workflow_id='${w}' AND task_id='${t}'`)[0]
  const r=await api(`/api/v1/service-approvals/${w}/${t}/decide`,{token:ptok,method:'POST',body:tool==='book_parking'?{decision:'reject',reject_code:'NO_AVAILABILITY',reject_reason:LY}:{decision:'approve'}})
  console.log('  decide',t,tool,r.status)
}
console.log('\n--- theo dõi UI 60s ---')
for(let i=0;i<12;i++){
  await new Promise(r=>setTimeout(r,5000))
  const a=(await api(`/api/v1/workflows/demo/${w}`,{token:ctok})).json
  const body=(await p.textContent('body')).replace(/\s+/g,' ')
  const ids=await p.$$eval('input,select,textarea',e=>e.map(x=>x.id).filter(Boolean))
  const conv=await p.$$eval('[aria-label="Trao đổi với P-118"] li',e=>e.map(x=>x.textContent.replace(/\s+/g,' ').trim()))
  console.log(`t+${(i+1)*5}s  API=${a.status} missing=${JSON.stringify(a.missing_fields)}  |  UI ô=[${ids.join(',')}]`)
  console.log(`        UI câu cuối: ${(conv.slice(-1)[0]??'(chưa có)').slice(0,110)}`)
  if(/kín chỗ/i.test(body)&&ids.some(x=>x.includes('parking'))){console.log('  → UI đã bắt kịp');break}
}
console.log('\nlỗi JS:',errs.length?errs.join(' | '):'không')
await b.close()
