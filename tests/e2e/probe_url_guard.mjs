/** URL mang workflow_id: id lạ và id của NGƯỜI KHÁC không được khôi phục. */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
const PW='Passw0rd!123', BASE='http://127.0.0.1:5274', API='http://127.0.0.1:8080', DB='p118_e2e_db'
const sql=q=>execFileSync('docker',['exec','p118_postgres','psql','-U','p118','-d',DB,'-tAc',q],{encoding:'utf8'}).trim().split('\n').filter(Boolean)
const api=async(p,{method='GET',body}={})=>{const r=await fetch(`${API}${p}`,{method,headers:{'content-type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json().catch(()=>null)}
const b=await chromium.launch()
const mk=async tag=>{
  const p=await (await b.newContext({viewport:{width:1280,height:900}})).newPage()
  const errs=[];p.on('pageerror',e=>errs.push(String(e)))
  const U=tag+Math.floor(Math.random()*1e6)
  await p.goto(`${BASE}/register`);await p.fill('#reg-username',U);await p.fill('#reg-email',`${U}@e.test`)
  await p.fill('#reg-password',PW);await p.fill('#reg-confirm',PW);await p.click('button[type=submit]')
  await p.waitForURL('**/workspace',{timeout:30000});return{p,U,errs}
}
const R=[];const check=(n,ok,d='')=>{R.push([ok,n]);console.log(`${ok?'PASS':'FAIL'} | ${n}${d?`\n       ${d}`:''}`)}

const a=await mk('ug')
const w=sql(`SELECT workflow_id FROM workflows WHERE task_plan::text<>'null' ORDER BY created_at DESC LIMIT 1`)[0]

await a.p.goto(`${BASE}/workspace?w=${w}`); await a.p.waitForTimeout(4000)
check('id của NGƯỜI KHÁC không khôi phục', !/Đặt chỗ đỗ xe · Thanh toán/i.test((await a.p.textContent('body')).replace(/\s+/g,' ')))
check('  và tham số bị gỡ khỏi URL', !a.p.url().includes('w='), a.p.url())

await a.p.goto(`${BASE}/workspace?w=khong-phai-uuid`); await a.p.waitForTimeout(3500)
check('id rác không làm vỡ trang', (await a.p.$('textarea'))!==null)
check('  và tham số bị gỡ', !a.p.url().includes('w='), a.p.url())

await a.p.goto(`${BASE}/workspace`); await a.p.waitForTimeout(2500)
check('vào thẳng /workspace vẫn là màn hình trống', /làm được gì cho bạn/i.test((await a.p.textContent('body'))))
check('không lỗi JS', a.errs.length===0, a.errs.join(' | ').slice(0,200))
console.log(`\n${R.filter(([o])=>o).length}/${R.length} PASS`)
await b.close(); process.exit(R.some(([o])=>!o)?1:0)
