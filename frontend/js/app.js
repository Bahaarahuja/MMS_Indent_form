async function jsonFetch(url, options={}){
  const res=await fetch(apiUrl(url),{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  const data=await res.json().catch(()=>({ok:false,message:"Invalid server response"}));
  if(!res.ok||data.ok===false)throw new Error(data.message||"Request failed");
  return data;
}
function escapeHtml(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function toastRoot(){
  let root=document.getElementById('toastRoot');
  if(!root){
    root=document.createElement('div');
    root.id='toastRoot';
    root.className='toast-root';
    document.body.appendChild(root);
  }
  return root;
}
function showToast(message,type='info'){
  const item=document.createElement('div');
  item.className=`toast ${type}`;
  item.innerHTML=`<span>${escapeHtml(message)}</span><button type="button" aria-label="Dismiss">×</button>`;
  item.querySelector('button').addEventListener('click',()=>item.remove());
  toastRoot().appendChild(item);
  setTimeout(()=>item.remove(),5200);
}
function askConfirm({title='Are you sure?',message='',confirmText='Continue',cancelText='Cancel',danger=false}={}){
  return new Promise(resolve=>{
    const modal=document.createElement('div');
    modal.className='modal';
    modal.innerHTML=`<div class="modal-card confirm-card" role="dialog" aria-modal="true">
      <div class="modal-header"><h3>${escapeHtml(title)}</h3><button class="icon-btn" type="button" data-cancel>×</button></div>
      <p>${escapeHtml(message)}</p>
      <div class="button-row"><button class="btn secondary" type="button" data-cancel>${escapeHtml(cancelText)}</button><button class="btn ${danger?'danger-button':'primary'}" type="button" data-confirm>${escapeHtml(confirmText)}</button></div>
    </div>`;
    let settled=false;
    const onKey=event=>{if(event.key==='Escape')done(false);};
    const done=value=>{
      if(settled)return;
      settled=true;
      document.removeEventListener('keydown',onKey);
      modal.remove();
      resolve(value);
    };
    modal.querySelectorAll('[data-cancel]').forEach(btn=>btn.addEventListener('click',()=>done(false)));
    modal.querySelector('[data-confirm]').addEventListener('click',()=>done(true));
    modal.addEventListener('click',event=>{if(event.target===modal)done(false);});
    document.addEventListener('keydown',onKey);
    document.body.appendChild(modal);
  });
}
async function syncAll(ev){const btn=ev?.target;const oldText=btn?.textContent;if(btn){btn.disabled=true;btn.textContent="Checking…";}try{const data=await jsonFetch('/api/source/sync-all',{method:'POST',body:'{}'});const pending=(data.results||[]).filter(x=>x.pending_review).length;const failed=(data.results||[]).filter(x=>!x.ok).length;showToast(failed?`Finished with ${failed} issue(s).`:(pending?`${pending} source(s) need review.`:'All configured sources are up to date.'),failed?'error':(pending?'info':'success'));setTimeout(()=>location.reload(),900);}catch(e){showToast(e.message,'error');}finally{if(btn){btn.disabled=false;btn.textContent=oldText||'Check Sources';}}}
async function saveSourceConfig(sourceType){const fileId=document.getElementById('driveFileId').value.trim();const box=document.getElementById('sourceActionMessage');try{await jsonFetch('/api/source/configure',{method:'POST',body:JSON.stringify({source_type:sourceType,drive_file_id:fileId})});box.innerHTML='<div class="alert success">Source saved. Click Sync & Verify when you want to check for a new version.</div>';}catch(e){box.innerHTML=`<div class="alert error">${escapeHtml(e.message)}</div>`;}}
async function syncSource(sourceType){const box=document.getElementById('sourceActionMessage');box.innerHTML='<div class="alert info">Downloading a read-only copy and comparing it with the active version…</div>';try{const data=await jsonFetch(`/api/source/sync/${sourceType}`,{method:'POST',body:'{}'});box.innerHTML=`<div class="alert success">${escapeHtml(data.message)}</div>`;setTimeout(()=>location.reload(),350);}catch(e){box.innerHTML=`<div class="alert error">${escapeHtml(e.message)}<br>The active version was not changed.</div>`;}}
async function uploadLocalTestSource(sourceType){const input=document.getElementById('localTestFile');const box=document.getElementById('sourceActionMessage');if(!input.files[0]){box.innerHTML='<div class="alert error">Choose a workbook first.</div>';return;}const form=new FormData();form.append('file',input.files[0]);box.innerHTML='<div class="alert info">Uploading, validating and comparing with the active version…</div>';try{const res=await fetch(apiUrl(`/api/source/upload/${sourceType}`),{method:'POST',body:form});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.message||'Import failed');box.innerHTML=`<div class="alert success">${escapeHtml(data.message)}</div>`;setTimeout(()=>location.reload(),350);}catch(e){box.innerHTML=`<div class="alert error">${escapeHtml(e.message)}<br>The active version was not changed.</div>`;}}
async function reviewSourceVersion(versionId,decision){const verb=decision==='accept'?'accept':'reject';const ok=await askConfirm({title:decision==='accept'?'Accept new source version?':'Reject source version?',message:decision==='accept'?'This will replace the current active source data with the staged version.':'This will keep the current active source data unchanged.',confirmText:decision==='accept'?'Accept version':'Reject version',danger:decision!=='accept'});if(!ok)return;try{const data=await jsonFetch(`/api/source/version/${versionId}/${decision}`,{method:'POST',body:'{}'});showToast(data.message||`Source version ${verb}ed.`,'success');setTimeout(()=>location.reload(),700);}catch(e){showToast(e.message,'error');}}
let deleteReportId=null;function confirmDeleteReport(id,name){deleteReportId=id;document.getElementById('deleteReportMessage').textContent=`${name}: this permanently deletes the report record and any saved final file from this system.`;document.getElementById('deleteReportModal').classList.remove('hidden');}function closeDeleteReportModal(){deleteReportId=null;document.getElementById('deleteReportModal')?.classList.add('hidden');}async function deleteReportConfirmed(){if(!deleteReportId)return;try{await jsonFetch(`/api/report/${deleteReportId}/delete`,{method:'POST',body:'{}'});closeDeleteReportModal();showToast('Report deleted.','success');setTimeout(()=>location.reload(),600);}catch(e){showToast(e.message,'error');}}
function relativeTime(date){const diff=(date.getTime()-Date.now())/1000;const abs=Math.abs(diff);const rtf=new Intl.RelativeTimeFormat(undefined,{numeric:'auto'});if(abs<60)return rtf.format(Math.round(diff),'second');if(abs<3600)return rtf.format(Math.round(diff/60),'minute');if(abs<86400)return rtf.format(Math.round(diff/3600),'hour');if(abs<2592000)return rtf.format(Math.round(diff/86400),'day');return rtf.format(Math.round(diff/2592000),'month');}
function localizeTimes(){const tz=Intl.DateTimeFormat().resolvedOptions().timeZone||'local time';document.querySelectorAll('.local-time').forEach(el=>{const raw=el.dataset.iso;if(!raw)return;const d=new Date(raw);if(Number.isNaN(d.getTime()))return;el.textContent=new Intl.DateTimeFormat(undefined,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(d);el.title=`${relativeTime(d)} · ${tz}`;});}
function setSidebarCollapsed(collapsed){
  document.body.classList.toggle('sidebar-collapsed',collapsed);
  const toggle=document.getElementById('sidebarToggle');
  if(toggle)toggle.setAttribute('aria-expanded',String(!collapsed));
}
document.addEventListener('DOMContentLoaded',()=>{
  const smallScreen=window.matchMedia('(max-width: 800px)').matches;
  setSidebarCollapsed(smallScreen);
  localizeTimes();
  const toggle=document.getElementById('sidebarToggle');
  if(toggle)toggle.addEventListener('click',()=>setSidebarCollapsed(!document.body.classList.contains('sidebar-collapsed')));
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&window.matchMedia('(max-width: 800px)').matches)setSidebarCollapsed(true);
  });
});
