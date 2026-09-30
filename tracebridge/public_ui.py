"""A public report page with receipt capabilities kept out of URLs and HTML."""
import json
import re
import secrets

from fastapi import HTTPException
from fastapi.responses import HTMLResponse


PAGE = r"""<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>문제 제보</title>
<style nonce="NONCE">
body{font:16px/1.65 system-ui,sans-serif;background:#f6f8fa;color:#17212e;margin:0}
main{max-width:650px;margin:5vh auto;padding:28px;background:white;border:1px solid #dce3ea;border-radius:16px}
h1{font-size:26px;margin-top:0}label{display:block;font-weight:600;margin-top:16px}
textarea,input,select,button{font:inherit;box-sizing:border-box}textarea,select,input[type=text]{width:100%;padding:10px;border:1px solid #a8b4c1;border-radius:7px}
textarea{min-height:130px}button{background:#165baa;color:white;padding:10px 18px;border:0;border-radius:7px;cursor:pointer;margin:14px 8px 0 0}
button:disabled{opacity:.5;cursor:wait}small{color:#526373}#status{border-left:4px solid #165baa;padding:12px;margin-top:24px;background:#edf4fb;white-space:pre-wrap}
.consent{font-weight:400}input[type=checkbox]{margin-right:8px}details{margin-top:14px}#error{color:#a32525;white-space:pre-wrap}
@media(max-width:700px){main{margin:12px;padding:20px}}
</style>
<main><h1 id="title">문제 제보</h1><p>어떤 동작에서 문제가 있었나요? 기억나는 내용만 적어 주세요.</p>
<form id="form"><label for="service">문제가 있었던 기능</label><select id="service" required></select>
<label for="text">문제 설명</label><textarea id="text" maxlength="4000" required placeholder="예: 방금 가입 버튼을 눌렀는데 계속 같은 화면이에요."></textarea>
<details><summary>알고 있는 요청 정보 추가</summary><label for="trace">요청 ID</label><input type="text" id="trace" maxlength="64">
<label for="occurred">발생 시각</label><input type="text" id="occurred" placeholder="2026-09-29T12:00:00+09:00" maxlength="80"></details>
<label id="consent-wrap" class="consent" hidden><input type="checkbox" id="consent">제보 내용의 정제된 발췌를 외부 AI 서비스로 보내 조사하는 데 동의합니다.</label>
<button id="send" type="submit">제보 보내기</button></form>
<p id="error" role="alert"></p><section id="receipt" hidden aria-live="polite"><div id="status"></div>
<small>이 브라우저에서 접수 상태와 후속 답변을 확인할 수 있습니다. 접수 정보는 7일 동안 유효합니다.</small>
<button id="refresh" type="button">상태 확인</button><button id="new" type="button">다른 문제 제보</button>
<form id="answer" hidden><label for="answer-text">추가 설명</label><textarea id="answer-text" maxlength="4000" required></textarea>
<label id="answer-consent-wrap" class="consent" hidden><input type="checkbox" id="answer-consent">이번 답변의 정제된 발췌를 외부 AI 서비스로 보내 조사하는 데 동의합니다.</label>
<button type="submit">답변 보내기</button></form></section></main>
<script nonce="NONCE">
const project=PROJECT, base='/v1/public/projects/'+encodeURIComponent(project), storageKey='tracebridge-receipt:'+project;
const el=id=>document.getElementById(id);
let receipt=null,pending=null,answerPending=null;
try{receipt=JSON.parse(sessionStorage.getItem(storageKey)||'null')}catch{}
const random=()=>Array.from(crypto.getRandomValues(new Uint8Array(32)),v=>v.toString(16).padStart(2,'0')).join('');
async function api(path,options={}){
 const response=await fetch(base+path,{...options,headers:{'Content-Type':'application/json',...options.headers},cache:'no-store'});
 const data=await response.json();
 if(!response.ok)throw Error(response.status===429?'접수가 많습니다. 잠시 후 다시 시도해 주세요.':response.status===404?'접수 정보를 찾지 못했거나 접수 기간이 끝났습니다.':response.status===409?'현재 처리 상태가 바뀌었습니다. 상태를 확인한 뒤 다시 시도해 주세요.':'입력과 연결 상태를 확인해 주세요.');
 return data;
}
function show(data){
 el('receipt').hidden=false;el('form').hidden=true;
 el('status').textContent=data.message+(data.questions.length?'\n\n'+data.questions.join('\n'):'');
 el('answer').hidden=!data.can_answer;
}
async function refresh(){if(receipt)show(await api('/reports/'+receipt.report_id,{headers:{Authorization:'Bearer '+receipt.receipt_token}}))}
async function act(task){el('error').textContent='';document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await task()}catch(e){el('error').textContent=e.message}finally{document.querySelectorAll('button').forEach(b=>b.disabled=false)}}
el('form').addEventListener('submit',event=>{event.preventDefault();act(async()=>{
 const context={};if(el('trace').value.trim())context.trace_id=el('trace').value.trim();if(el('occurred').value.trim())context.occurred_at=el('occurred').value.trim();
 const payload={text:el('text').value,service:el('service').value,context,allow_external_analysis:el('consent').checked};
 const serialized=JSON.stringify(payload);if(!pending||pending.body!==serialized)pending={body:serialized,key:random()};
 const data=await api('/reports',{method:'POST',body:pending.body,headers:{'Idempotency-Key':pending.key}});
 receipt={report_id:data.report_id,receipt_token:data.receipt_token,service:payload.service};sessionStorage.setItem(storageKey,JSON.stringify(receipt));el('consent').checked=false;show(data);
})});
el('answer').addEventListener('submit',event=>{event.preventDefault();act(async()=>{
 const body=JSON.stringify({text:el('answer-text').value,service:receipt.service,context:{},allow_external_analysis:el('answer-consent').checked});
 if(!answerPending||answerPending.body!==body)answerPending={body,key:random()};
 const data=await api('/reports/'+receipt.report_id+'/answers',{method:'POST',body:answerPending.body,headers:{Authorization:'Bearer '+receipt.receipt_token,'Idempotency-Key':answerPending.key}});
 show(data);el('answer-text').value='';el('answer-consent').checked=false;answerPending=null;
})});
el('refresh').onclick=()=>act(refresh);
el('new').onclick=()=>{receipt=null;pending=null;answerPending=null;sessionStorage.removeItem(storageKey);el('form').hidden=false;el('receipt').hidden=true;for(const id of ['text','trace','occurred','answer-text'])el(id).value='';el('consent').checked=false;el('answer-consent').checked=false;el('error').textContent=''};
act(async()=>{const info=await api('');el('title').textContent=info.title;document.title=info.title;
 for(const service of info.services){const option=document.createElement('option');option.value=service.id;option.textContent=service.label;el('service').append(option)}
 el('consent-wrap').hidden=!info.external_analysis_available;el('answer-consent-wrap').hidden=!info.external_analysis_available;await refresh();
});
</script></html>"""


def install_public_page(app):
    @app.get("/report/{project_id}", response_class=HTMLResponse)
    def page(project_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
            raise HTTPException(404, "Public project not found")
        with app.state.storage.transaction() as tx:
            app.state.public_service.policy(tx, project_id, public=True)
        nonce = secrets.token_urlsafe(24)
        return HTMLResponse(PAGE.replace("NONCE", nonce).replace("PROJECT", json.dumps(project_id)),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"})
