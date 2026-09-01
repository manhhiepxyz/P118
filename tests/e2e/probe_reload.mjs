import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
const PW='Passw0rd!123', BASE='http://127.0.0.1:5274', API='http://127.0.0.1:8080', DB='p118_e2e_db'
const sql=q=>execFileSync('docker',['exec','p118_postgres','psql','-U','p118','-d',DB,'-tAc',q],{encoding:'utf8'}).trim().split('\n').filter(Boolean)
const api=async(p,{token,method='GET',body}={})=>{const r=await fetch(`${API}${p}`,{method,headers:{'content-type':'application/json',...(token?{authorization:`Bearer ${token}`}:{})},body:body?JSON.stringify(body):undefined});return{status:r.status,json:await r.json().catch(()=>null)}}
const b=await chromium.launch(); const p=await (await b.newContext({viewport:{width:1512,height:1000}})).newPage()
const errs=[]; p.on('pageerror',e=>errs.push(String(e)))
const U='rl'+Math.floor(Math.random()*1e6)
await p.goto(`${BASE}/register`); await p.fill('#reg-username',U); await p.fill('#reg-email',`${U}@e.test`)
await p.fill('#reg-password',PW); await p.fill('#reg-confirm',PW); await p.click('button[type=submit]')
await p.waitForURL('**/workspace',{timeout:30000})
const uid=sql(`SELECT id FROM users WHERE username='${U}'`)[0], rid=sql(`SELECT resident_id FROM residents LIMIT 1`)[0]
sql(`INSERT INTO user_resident_links (user_id,resident_id,verification_status,verified_at) VALUES ('${uid}','${rid}','VERIFIED',now()) ON CONFLICT (user_id) DO UPDATE SET verification_status='VERIFIED',verified_at=now()`)
await p.reload(); await p.waitForTimeout(1500)
const ctok=(await api('/api/v1/auth/login',{method:'POST',body:{username:U,password:PW}})).json.access_token
await p.fill('textarea','Giữ chỗ đỗ xe Khu B ngày 2028-09-27 cho xe máy biển số 51K-'+Math.floor(Math.random()*90000+10000)+''); await p.keyboard.press('Enter')
const wfOf=()=>sql(`SELECT w.workflow_id FROM workflows w JOIN users u ON u.id=w.owner_user_id WHERE u.username='${U}' AND w.task_plan::text<>'null' ORDER BY w.created_at DESC LIMIT 1`)[0]
const wait=async(f,ms=150000)=>{const t=Date.now();while(Date.now()-t<ms){if(await f())return true;await new Promise(r=>setTimeout(r,2000))}return false}
const ok=await wait(async()=>{const w=wfOf();return w&&sql(`SELECT count(*) FROM service_approvals WHERE workflow_id='${w}' AND status='AWAITING'`)[0]!=='0'})
const w=wfOf()
if(!w){console.log('KHÔNG tạo được workflow. UI:',(await p.textContent('body')).replace(/\s+/g,' ').slice(0,300));await b.close();process.exit(1)}
void ok
await new Promise(r=>setTimeout(r,6000))
const url1=p.url()
const before=(await p.textContent('body')).replace(/\s+/g,' ')
console.log('workflow      :',w.slice(0,8))
console.log('URL trước F5  :',url1)
console.log('API trước F5  :',(await api(`/api/v1/workflows/demo/${w}`,{token:ctok})).json.status)
console.log('UI  trước F5  :',before.slice(0,150))
await p.reload(); await p.waitForTimeout(6000)
const after=(await p.textContent('body')).replace(/\s+/g,' ')
console.log('\nURL sau F5    :',p.url())
console.log('API sau F5    :',(await api(`/api/v1/workflows/demo/${w}`,{token:ctok})).json.status)
console.log('UI  sau F5    :',after.slice(0,180))
console.log('\ncòn thấy yêu cầu đang chạy?', /chờ đơn vị|Đặt chỗ|Đăng ký phương tiện/i.test(after) ? 'CÓ' : 'KHÔNG — mất hẳn')
console.log('lỗi JS:',errs.length?errs.join(' | '):'không')
await b.close()
