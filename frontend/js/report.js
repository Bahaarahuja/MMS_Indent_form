let currentReport=null;
let originalDraft=null;
let undoStack=[];
let redoStack=[];
let saveTimer=null;
let invalidPending=null;
let dragIndex=null;
let dragRowId=null;
let bookSearchCache=[];
let bookSearchMarket="";
let searchLoadPromise=null;
let duplicatePending=null;
let quickNewReleaseSearchTimer=null;
let quickNewReleaseSearchRequest=0;

const monthNames=["January","February","March","April","May","June","July","August","September","October","November","December"];

function newId(){
  if(window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "row-"+Date.now()+"-"+Math.random().toString(16).slice(2);
}

function initReportPage(){
  const month=document.getElementById("month");
  month.innerHTML=monthNames.map((m,i)=>`<option value="${i+1}">${m}</option>`).join("");
  const now=new Date();
  month.value=now.getMonth()+1;
  document.getElementById("year").value=now.getFullYear();
  if(!loadPreferredFileName())refreshDefaultFileName(true);
  month.addEventListener("change",()=>{
    refreshDefaultFileName(false);
    updateReportSettings();
  });
  document.getElementById("year").addEventListener("change",()=>{
    refreshDefaultFileName(false);
    updateReportSettings();
  });
  document.getElementById("market").addEventListener("change",updateReportSettings);
  document.getElementById("pdfFileName").addEventListener("input",e=>{
    e.target.dataset.userEdited="1";
    savePreferredFileName(e.target.value);
  });
  document.getElementById("pdfFileName").addEventListener("change",e=>{
    e.target.dataset.userEdited="1";
    savePreferredFileName(e.target.value);
  });
  setHelpText();
  renderEditor();
  updateReportBreadcrumb();
  loadReportFromUrl();
}
document.addEventListener("DOMContentLoaded",initReportPage);

function pdfFileNameStorageKey(){
  return `mms.status-circular.pdf-file-name.${REPORT_TYPE}`;
}

function loadPreferredFileName(){
  const input=document.getElementById("pdfFileName");
  const saved=localStorage.getItem(pdfFileNameStorageKey());
  if(!input || !saved)return false;
  input.value=saved;
  input.dataset.userEdited="1";
  return true;
}

function savePreferredFileName(value){
  const name=String(value||"").trim();
  if(name)localStorage.setItem(pdfFileNameStorageKey(),name);
  else localStorage.removeItem(pdfFileNameStorageKey());
}

function refreshDefaultFileName(force=false){
  const input=document.getElementById("pdfFileName");
  if(!input || (!force && input.dataset.userEdited==="1")) return;
  const m=Number(document.getElementById("month").value||1);
  const y=document.getElementById("year").value;
  input.value=`${REPORT_LABEL} - ${monthNames[m-1]} ${y}`;
}

function updateReportSettings(){
  if(!currentReport)return;
  const market=document.getElementById("market").value;
  const month=Number(document.getElementById("month").value);
  const year=Number(document.getElementById("year").value);
  const marketChanged=currentReport.market!==market;
  currentReport.market=market;
  currentReport.month=month;
  currentReport.year=year;
  if(marketChanged){
    bookSearchCache=[];
    bookSearchMarket="";
  }
  if(REPORT_TYPE==="out-of-stock" && currentReport.draft?.meta){
    currentReport.draft.meta.validation=null;
    document.getElementById("validationArea")?.classList.add("hidden");
  }
  updateReportBreadcrumb();
  scheduleSave();
}

async function ensureReportStarted(){
  if(currentReport)return true;
  const message=document.getElementById("reportMessage");
  if(message)message.innerHTML="";
  if(window.REPORT_READY===false){
    const missing=(window.REPORT_MISSING||[]).join(", ");
    showMessage(`This report is not ready yet. Set up or accept these source files first: ${missing}.`,"error");
    return false;
  }
  setAutosaveStatus("Creating draft...");
  try{
    const data=await jsonFetch("/api/report/generate",{
      method:"POST",
      body:JSON.stringify({
        report_type:REPORT_TYPE,
        market:document.getElementById("market").value,
        month:Number(document.getElementById("month").value),
        year:Number(document.getElementById("year").value)
      })
    });
    currentReport=data.report;
    originalDraft=structuredClone(currentReport.draft);
    await ensureBookSearchCache(true);
    undoStack=[];
    redoStack=[];
    updateUndoButton();
    setHelpText();
    setAutosaveStatus("Draft ready");
    updateChanges();
    updateCatalogWorkspaceStatus();
    updateReportBreadcrumb();
    return true;
  }catch(e){
    setAutosaveStatus("Draft not ready");
    if(message)message.innerHTML=`<div class="alert error">${escapeHtml(e.message)}</div>`;
    return false;
  }
}

async function loadReportFromUrl(){
  const reportId=new URLSearchParams(location.search).get("report_id");
  if(!reportId)return;
  const message=document.getElementById("reportMessage");
  try{
    const data=await jsonFetch(`/api/report/${encodeURIComponent(reportId)}`);
    if(data.report.report_type!==REPORT_TYPE){
      throw new Error("This draft belongs to a different report type.");
    }
    currentReport=data.report;
    originalDraft=structuredClone(currentReport.original||currentReport.draft);
    document.getElementById("market").value=currentReport.market||"INDIAN";
    document.getElementById("month").value=currentReport.month;
    document.getElementById("year").value=currentReport.year;
    if(currentReport.final_filename){
      const fileName=currentReport.final_filename.replace(/\.pdf$/i,"");
      document.getElementById("pdfFileName").value=fileName;
      document.getElementById("pdfFileName").dataset.userEdited="1";
      savePreferredFileName(fileName);
    }else{
      if(!loadPreferredFileName())refreshDefaultFileName(true);
    }
    await ensureBookSearchCache(true);
    undoStack=[];
    redoStack=[];
    updateUndoButton();
    document.getElementById("editorShell").classList.remove("hidden");
    if(isCatalogReport() && typeof openCatalogWorkspace==="function")openCatalogWorkspace();
    if(!isCatalogReport() && typeof openReportWorkspace==="function")openReportWorkspace("Draft");
    setHelpText();
    renderEditor();
    updateChanges();
    setAutosaveStatus("Draft loaded");
    updateReportBreadcrumb();
    if(message)message.innerHTML=`<div class="alert success">Draft loaded. Changes save automatically.</div>`;
  }catch(e){
    if(message)message.innerHTML=`<div class="alert error">${escapeHtml(e.message)}</div>`;
  }
}

function setHelpText(){
  const el=document.getElementById("editorHelp");
  if(!el)return;
  if(REPORT_TYPE==="new-releases"){
    el.textContent="Add a row, type a BAV Code and choose a matching book. Title, author and price auto-fill. Type and edition/impression years remain yours to enter.";
  }else if(REPORT_TYPE==="book-catalog"){
    el.textContent="Add books in the order they should appear in the PDF.";
  }else{
    el.textContent="Add the books you believe are out of stock. Validate at the end against the current MMS grey-row stock logic.";
  }
}

function snapshot(){
  if(!currentReport) return;
  undoStack.push(structuredClone(currentReport.draft));
  if(undoStack.length>500) undoStack.shift();
  redoStack=[];
  updateUndoButton();
}
function updateUndoButton(){
  const b=document.getElementById("undoBtn");
  if(b) b.disabled=undoStack.length===0;
  const redo=document.getElementById("redoBtn");
  if(redo) redo.disabled=redoStack.length===0;
  updateCatalogWorkspaceStatus();
  updateNewReleaseWorkspaceStatus();
  updateOutOfStockWorkspaceStatus();
}
async function undoAction(){
  if(!undoStack.length || !currentReport) return;
  redoStack.push(structuredClone(currentReport.draft));
  currentReport.draft=undoStack.pop();
  updateUndoButton();
  renderEditor();
  await saveDraft();
}
async function redoAction(){
  if(!redoStack.length || !currentReport) return;
  undoStack.push(structuredClone(currentReport.draft));
  currentReport.draft=redoStack.pop();
  updateUndoButton();
  renderEditor();
  await saveDraft();
}
function markMutation(){
  if(!currentReport) return;
  snapshot();
  if(currentReport.draft.meta) currentReport.draft.meta.validation=null;
  document.getElementById("validationArea")?.classList.add("hidden");
}

function blankRow(){
  const base={
    id:newId(), code:"", mms_code:"", title:"", author:"", language:"",
    language_category:"", category:"", price:"", included:true, selected:false,
    source_match:false, manual_code_override:false
  };
  if(REPORT_TYPE==="new-releases"){
    return {...base,type:"",edition_number:"",edition_year:"",impression_number:"",impression_year:""};
  }
  if(REPORT_TYPE==="book-catalog"){
    return {...base,description_html:"",description_font_size:10.5,image_data:""};
  }
  return {...base,dl_stock:null,pb_stock:null,combined_stock:null,status:""};
}

async function addBlankRow(){
  if(!(await ensureReportStarted())) return;
  markMutation();
  const row=blankRow();
  currentReport.draft.rows.push(row);
  if(isCatalogReport())catalogEditingRowId=row.id;
  addAudit("row_added","Added a row.");
  renderEditor();
  scheduleSave();
  setTimeout(()=>{
    const rows=document.querySelectorAll(".code-input");
    rows[rows.length-1]?.focus();
  },0);
}

async function addCatalogBookFromQuickEntry(){
  if(!isCatalogReport())return;
  const input=document.getElementById("catalogQuickCode");
  const raw=(input?.value||"").trim();
  if(!raw){
    showToast("Select a book from the BAV code list first.","info");
    return;
  }
  let exact=null;
  try{
    await ensureBookSearchCache(false);
    const compact=normalizeSearchCode(raw);
    exact=bookSearchCache.find(book=>normalizeSearchCode(book.code)===compact);
  }catch(_){}
  if(!exact){
    showToast("Choose a matching book from the BAV code list.","info");
    searchQuickCatalogCode(raw);
    return;
  }
  if(!(await ensureReportStarted()))return;

  markMutation();
  const row=blankRow();
  currentReport.draft.rows.push(row);
  const rowIndex=currentReport.draft.rows.length-1;
  catalogEditingRowId=null;
  requestBookApply(rowIndex,exact,{removeOnReject:true});
  if(input)input.value="";
  hideQuickCatalogSuggestions();
  showToast("Book added from Book Master.","success");
  setTimeout(()=>scrollCatalogRowIntoView(row.id),0);
}

async function addNewReleaseBookFromQuickEntry(){
  if(REPORT_TYPE!=="new-releases")return;
  const input=document.getElementById("newReleaseQuickCode");
  const raw=(input?.value||"").trim();
  if(!raw){
    showToast("Select a book from the BAV code list first.","info");
    return;
  }
  let exact=null;
  try{
    const market=document.getElementById("market").value;
    const data=await jsonFetch(`/api/books/resolve?code=${encodeURIComponent(raw)}&market=${encodeURIComponent(market)}`);
    exact=data.found ? data.book : null;
  }catch(_){}
  if(!exact){
    showToast("Choose a matching book from the BAV code list.","info");
    searchQuickNewReleaseCode(raw);
    return;
  }
  if(!(await ensureReportStarted()))return;

  markMutation();
  currentReport.draft.rows.push(blankRow());
  requestBookApply(currentReport.draft.rows.length-1,exact,{removeOnReject:true});
  if(input)input.value="";
  hideQuickNewReleaseSuggestions();
  showToast("Book added from Book Master.","success");
}

async function addOutOfStockBookFromQuickEntry(){
  if(REPORT_TYPE!=="out-of-stock")return;
  const input=document.getElementById("outOfStockQuickCode");
  const raw=(input?.value||"").trim();
  if(!raw){
    showToast("Select a book from the BAV code list first.","info");
    return;
  }
  let exact=null;
  try{
    await ensureBookSearchCache(false);
    const compact=normalizeSearchCode(raw);
    exact=bookSearchCache.find(book=>normalizeSearchCode(book.code)===compact);
  }catch(_){}
  if(!exact){
    showToast("Choose a matching book from the BAV code list.","info");
    searchQuickOutOfStockCode(raw);
    return;
  }
  if(!(await ensureReportStarted()))return;

  markMutation();
  currentReport.draft.rows.push(blankRow());
  requestBookApply(currentReport.draft.rows.length-1,exact,{removeOnReject:true});
  if(input)input.value="";
  hideQuickOutOfStockSuggestions();
  showToast("Book added from Book Master.","success");
}

async function searchQuickCatalogCode(value){
  const box=document.getElementById("catalogQuickSuggestions");
  const q=normalizeSearchCode(value);
  if(!box || !q){
    hideQuickCatalogSuggestions();
    return;
  }
  try{
    await ensureBookSearchCache(false);
    const books=matchingBooks(value);
    if(!books.length){
      box.innerHTML=`<span class="quick-code-empty">No matching BAV code</span>`;
    }else{
      box.innerHTML=books.map(book=>`<button type="button" onclick='chooseQuickCatalogBook(${JSON.stringify(book.code)})'><strong>${escapeHtml(book.code||"")}</strong><span>${escapeHtml(book.title||"Untitled Book")}</span></button>`).join("");
    }
    box.classList.remove("hidden");
  }catch(_){
    hideQuickCatalogSuggestions();
  }
}

function hideQuickCatalogSuggestions(){
  const box=document.getElementById("catalogQuickSuggestions");
  if(box){
    box.classList.add("hidden");
    box.innerHTML="";
  }
}

function searchQuickNewReleaseCode(value){
  const box=document.getElementById("newReleaseQuickSuggestions");
  const q=normalizeSearchCode(value);
  clearTimeout(quickNewReleaseSearchTimer);
  const requestId=++quickNewReleaseSearchRequest;
  if(!box || !q){
    hideQuickNewReleaseSuggestions();
    return;
  }
  quickNewReleaseSearchTimer=setTimeout(async()=>{
    try{
      const market=document.getElementById("market").value;
      const data=await jsonFetch(`/api/books/search?q=${encodeURIComponent(value)}&market=${encodeURIComponent(market)}`);
      if(requestId!==quickNewReleaseSearchRequest)return;
      const books=data.books||[];
      if(!books.length){
        box.innerHTML=`<span class="quick-code-empty">No matching BAV code</span>`;
      }else{
        box.innerHTML=books.map(book=>`<button type="button" onclick='chooseQuickNewReleaseBook(${JSON.stringify(book.code)})'><strong>${escapeHtml(book.code||"")}</strong><span>${escapeHtml(book.title||"Untitled Book")}</span></button>`).join("");
      }
      box.classList.remove("hidden");
    }catch(_){
      if(requestId===quickNewReleaseSearchRequest)hideQuickNewReleaseSuggestions();
    }
  },120);
}

function hideQuickNewReleaseSuggestions(){
  quickNewReleaseSearchRequest++;
  const box=document.getElementById("newReleaseQuickSuggestions");
  if(box){
    box.classList.add("hidden");
    box.innerHTML="";
  }
}

async function searchQuickOutOfStockCode(value){
  const box=document.getElementById("outOfStockQuickSuggestions");
  const q=normalizeSearchCode(value);
  if(!box || !q){
    hideQuickOutOfStockSuggestions();
    return;
  }
  try{
    await ensureBookSearchCache(false);
    const books=matchingBooks(value).slice(0,6);
    if(!books.length){
      box.innerHTML=`<span class="quick-code-empty">No matching BAV code</span>`;
    }else{
      box.innerHTML=books.map(book=>`<button type="button" onclick='chooseQuickOutOfStockBook(${JSON.stringify(book.code)})'><strong>${escapeHtml(book.code||"")}</strong><span>${escapeHtml(book.title||"Untitled Book")}</span></button>`).join("");
    }
    box.classList.remove("hidden");
  }catch(_){
    hideQuickOutOfStockSuggestions();
  }
}

function hideQuickOutOfStockSuggestions(){
  const box=document.getElementById("outOfStockQuickSuggestions");
  if(box){
    box.classList.add("hidden");
    box.innerHTML="";
  }
}

function chooseQuickCatalogBook(code){
  const input=document.getElementById("catalogQuickCode");
  if(input)input.value=code||"";
  hideQuickCatalogSuggestions();
  addCatalogBookFromQuickEntry();
}

function chooseQuickNewReleaseBook(code){
  const input=document.getElementById("newReleaseQuickCode");
  if(input)input.value=code||"";
  hideQuickNewReleaseSuggestions();
  addNewReleaseBookFromQuickEntry();
}

function chooseQuickOutOfStockBook(code){
  const input=document.getElementById("outOfStockQuickCode");
  if(input)input.value=code||"";
  hideQuickOutOfStockSuggestions();
  addOutOfStockBookFromQuickEntry();
}

function selectedIndexes(){
  const out=[];
  (currentReport?.draft?.rows||[]).forEach((r,i)=>{if(r.selected)out.push(i)});
  return out;
}
function toggleSelect(i,checked){
  currentReport.draft.rows[i].selected=checked;
  updateCatalogWorkspaceStatus();
  updateNewReleaseWorkspaceStatus();
  updateOutOfStockWorkspaceStatus();
}
function toggleSelectAll(checked){
  currentReport.draft.rows.forEach(r=>r.selected=checked);
  renderEditor();
}
async function deleteSelected(){
  if(!currentReport) return;
  const indexes=selectedIndexes();
  if(!indexes.length) return;
  const removedIds=currentReport.draft.rows.filter(r=>r.selected).map(r=>r.id);
  markMutation();
  currentReport.draft.rows=currentReport.draft.rows.filter(r=>!r.selected);
  if(removedIds.includes(catalogEditingRowId))catalogEditingRowId=null;
  addAudit("bulk_remove",`${indexes.length} selected row(s) removed.`);
  renderEditor();
  if(await discardEmptyReport())return;
  scheduleSave();
}
async function removeRow(i){
  markMutation();
  const row=currentReport.draft.rows[i];
  currentReport.draft.rows.splice(i,1);
  if(row?.id===catalogEditingRowId)catalogEditingRowId=null;
  addAudit("remove",`Removed ${row.code||row.title||"blank row"}.`);
  renderEditor();
  if(await discardEmptyReport())return;
  scheduleSave();
}
function moveRow(i,delta){
  const j=i+delta;
  if(j<0||j>=currentReport.draft.rows.length)return;
  markMutation();
  const rows=currentReport.draft.rows;
  [rows[i],rows[j]]=[rows[j],rows[i]];
  renderEditor(); scheduleSave();
}

function dragStart(i,e){
  const row=currentReport?.draft?.rows?.[i];
  if(!row)return;

  dragIndex=i;
  dragRowId=row.id;

  if(e.dataTransfer){
    e.dataTransfer.effectAllowed="move";
    // Safari is more reliable when some data is attached.
    try{e.dataTransfer.setData("text/plain",row.id||String(i));}catch(_){}
  }
  e.currentTarget.classList.add("dragging");
}

function dragEnd(e){
  dragIndex=null;
  dragRowId=null;
  e.currentTarget?.classList.remove("dragging");
  document.querySelectorAll(".drop-target").forEach(x=>x.classList.remove("drop-target"));
}

function dragOver(i,e){
  e.preventDefault();
  if(e.dataTransfer)e.dataTransfer.dropEffect="move";

  const target=currentReport?.draft?.rows?.[i];
  if(!target || !dragRowId || target.id===dragRowId)return;

  e.currentTarget.classList.add("drop-target");
}

function dragLeave(e){
  e.currentTarget.classList.remove("drop-target");
}

function dropRow(i,e){
  e.preventDefault();
  e.currentTarget.classList.remove("drop-target");

  if(!currentReport || !dragRowId)return;

  const rows=currentReport.draft.rows;
  const from=rows.findIndex(r=>r.id===dragRowId);
  if(from<0)return;

  // Resolve target against the CURRENT row array, not the index captured
  // several renders ago.
  const targetRow=rows[i];
  const targetId=targetRow?.id;
  let to=targetId ? rows.findIndex(r=>r.id===targetId) : i;

  if(to<0 || from===to){
    dragIndex=null;
    dragRowId=null;
    return;
  }

  snapshot();

  const [moved]=rows.splice(from,1);

  // If moving downward, removal shifts the target left by one.
  if(from<to)to-=1;
  to=Math.max(0,Math.min(to,rows.length));

  rows.splice(to,0,moved);

  addAudit("reorder","Rows reordered by drag and drop.");

  dragIndex=null;
  dragRowId=null;

  renderEditor();
  scheduleSave();
}
function addAudit(type,message,extra={}){
  if(!currentReport.draft.meta)currentReport.draft.meta={};
  if(!Array.isArray(currentReport.draft.meta.audit_events))currentReport.draft.meta.audit_events=[];
  currentReport.draft.meta.audit_events.push({
    type,message,time:new Date().toISOString(),...extra
  });
}

let searchTimers={};
let catalogEditingRowId=null;

function isCatalogReport(){
  return REPORT_TYPE==="book-catalog";
}

function reportPeriodLabel(report=currentReport){
  if(!report)return "";
  const month=monthNames[Number(report.month||0)-1]||report.month||"";
  return `${month} ${report.year||""}`.trim();
}

function catalogDomId(rowId){
  return `catalog-row-${String(rowId||"").replace(/[^a-zA-Z0-9_-]/g,"_")}`;
}

function catalogPlainText(html){
  return String(html||"")
    .replace(/<[^>]*>/g," ")
    .replace(/&nbsp;/gi," ")
    .replace(/\s+/g," ")
    .trim();
}

function catalogRowState(row){
  const missingCode=!String(row?.code||"").trim();
  const missingTitle=!String(row?.title||"").trim();
  const missingImage=!row?.image_data;
  const missingDescription=!catalogPlainText(row?.description_html);
  const manualCode=!!row?.manual_code_override;
  return {
    missingCode,
    missingTitle,
    missingImage,
    missingDescription,
    manualCode,
    ready:!(missingCode||missingTitle||missingImage||missingDescription||manualCode)
  };
}

function catalogStats(rows){
  return (rows||[]).reduce((acc,row)=>{
    const state=catalogRowState(row);
    acc.total+=1;
    if(state.ready)acc.ready+=1;
    if(state.missingCode)acc.missingCode+=1;
    if(state.missingImage)acc.missingImage+=1;
    if(state.missingDescription)acc.missingDescription+=1;
    if(state.manualCode)acc.manualCode+=1;
    if(state.missingTitle)acc.missingTitle+=1;
    return acc;
  },{
    total:0,
    ready:0,
    missingCode:0,
    missingImage:0,
    missingDescription:0,
    manualCode:0,
    missingTitle:0
  });
}

function renderCatalogWorkspaceOverview(){
  if(!isCatalogReport())return;
  const rows=currentReport?.draft?.rows||[];
  const stats=catalogStats(rows);
  const pill=document.getElementById("catalogCompletionPill");
  if(pill){
    pill.textContent=!rows.length ? "0 books" : `${stats.total} books`;
  }

  const note=document.getElementById("catalogFinalizeNote");
  if(!note)return;
  if(!rows.length){
    note.textContent="Add books in final PDF order.";
    return;
  }
  const issues=[];
  if(stats.missingCode || stats.manualCode || stats.missingTitle)issues.push("codes to review");
  if(stats.missingImage)issues.push(`${stats.missingImage} without cover`);
  if(stats.missingDescription)issues.push(`${stats.missingDescription} without description`);
  note.textContent=issues.length ? issues.join(" · ") : "Ready to finalize.";
}

function updateCatalogWorkspaceStatus(){
  if(!isCatalogReport())return;
  const rows=currentReport?.draft?.rows||[];
  const stats=catalogStats(rows);
  const count=document.getElementById("catalogTotalBooks");
  if(count)count.textContent=String(rows.length);
  const finalize=document.querySelector(".download-btn");
  if(finalize)finalize.disabled=rows.length===0;
  const removeSelected=document.getElementById("removeSelectedBtn");
  if(removeSelected){
    removeSelected.classList.toggle("hidden", rows.length===0);
    removeSelected.disabled=!rows.some(row=>row.selected);
  }
  renderCatalogWorkspaceOverview();
  syncCurrentCatalogDraft();
}

function updateNewReleaseWorkspaceStatus(){
  if(REPORT_TYPE!=="new-releases")return;
  const rows=currentReport?.draft?.rows||[];
  const count=document.getElementById("newReleaseTotalBooks");
  if(count)count.textContent=String(rows.length);
  const removeSelected=document.getElementById("newReleaseRemoveSelectedBtn");
  if(removeSelected)removeSelected.disabled=!rows.some(row=>row.selected);
}

function updateOutOfStockWorkspaceStatus(){
  if(REPORT_TYPE!=="out-of-stock")return;
  const rows=currentReport?.draft?.rows||[];
  const count=document.getElementById("outOfStockTotalBooks");
  if(count)count.textContent=String(rows.length);
  const removeSelected=document.getElementById("outOfStockRemoveSelectedBtn");
  if(removeSelected)removeSelected.disabled=!rows.some(row=>row.selected);
}

function updateReportBreadcrumb(){
  if(isCatalogReport())return;
  if(typeof setReportBreadcrumb==="function")setReportBreadcrumb(currentReport ? "Draft" : "");
}

function setCatalogEditing(rowId){
  catalogEditingRowId=rowId;
  renderEditor();
}

function scrollCatalogRowIntoView(rowId){
  const card=document.getElementById(catalogDomId(rowId));
  card?.scrollIntoView({behavior:"smooth",block:"center"});
}

function focusCatalogRow(rowId,target="code"){
  if(!rowId)return;
  catalogEditingRowId=rowId;
  renderEditor();
  setTimeout(()=>{
    scrollCatalogRowIntoView(rowId);
    const card=document.getElementById(catalogDomId(rowId));
    if(!card)return;
    const selector=target==="description"
      ? ".rich-editor"
      : target==="image"
        ? ".image-file-input"
        : ".code-input";
    card.querySelector(selector)?.focus();
  },0);
}

function focusCatalogIssue(kind){
  if(!isCatalogReport())return;
  const rows=currentReport?.draft?.rows||[];
  if(!rows.length){
    showToast("Add a book first.","info");
    return;
  }
  const index=rows.findIndex(row=>{
    const state=catalogRowState(row);
    if(kind==="image")return state.missingImage;
    if(kind==="description")return state.missingDescription;
    if(kind==="manual")return state.manualCode;
    return state.missingCode;
  });
  if(index<0){
    const doneMessage={
      image:"Every book already has a cover.",
      description:"Every book already has a description.",
      manual:"No manual code overrides need review."
    }[kind]||"No matching issue found.";
    showToast(doneMessage,"success");
    return;
  }
  focusCatalogRow(rows[index].id,kind==="description"?"description":kind==="image"?"image":"code");
}

async function saveCatalogDraft(){
  if(!isCatalogReport())return;
  return saveCurrentDraft();
}

async function saveCurrentDraft(){
  if(!currentReport || !(currentReport.draft?.rows||[]).length){
    showToast("Add a book before saving a draft.","info");
    return;
  }
  if(await saveDraft()){
    updateCatalogWorkspaceStatus();
    showToast("Draft saved.","success");
  }
}

function syncCurrentCatalogDraft(){
  if(!isCatalogReport() || !currentReport || !Array.isArray(window.RECENT_REPORT_DRAFTS))return;
  const rows=currentReport?.draft?.rows||[];
  const record={
    id:currentReport.id,
    report_type:currentReport.report_type,
    market:currentReport.market,
    month:currentReport.month,
    year:currentReport.year,
    title:currentReport.title,
    label:REPORT_LABEL,
    status:currentReport.status,
    updated_at:currentReport.updated_at,
    created_at:currentReport.created_at,
    row_count:rows.length
  };
  if(typeof markRecentDraftListChanged==="function")markRecentDraftListChanged();
  window.RECENT_REPORT_DRAFTS=[
    record,
    ...window.RECENT_REPORT_DRAFTS.filter(item=>item.id!==currentReport.id)
  ];
  if(typeof renderCatalogDraftList==="function")renderCatalogDraftList();
}

function normalizeSearchCode(value){
  return (value||"").toString().toUpperCase().replace(/[^A-Z0-9]/g,"");
}

function looksLikeFullBav(code){
  // Accept both hyphenated and unhyphenated input.
  const compact=normalizeSearchCode(code);
  return compact.length>=7;
}

async function ensureBookSearchCache(force=false){
  const market=document.getElementById("market").value;
  if(!force && bookSearchCache.length && bookSearchMarket===market) return bookSearchCache;
  if(searchLoadPromise && bookSearchMarket===market) return searchLoadPromise;

  bookSearchMarket=market;
  searchLoadPromise=(async()=>{
    const data=await jsonFetch(`/api/books/options?market=${encodeURIComponent(market)}`);
    bookSearchCache=data.books||[];
    return bookSearchCache;
  })();

  try{
    return await searchLoadPromise;
  }finally{
    searchLoadPromise=null;
  }
}

function matchingBooks(value){
  const q=normalizeSearchCode(value);
  if(!q) return [];
  return bookSearchCache.filter(b=>normalizeSearchCode(b.code).startsWith(q));
}

function codeInput(i,value){
  const row=currentReport.draft.rows[i];
  row.code=value;
  row.source_match=false;
  row.manual_code_override=false;

  // Typing or deleting always restarts search immediately.
  if(invalidPending && invalidPending.index===i){
    invalidPending=null;
    document.getElementById("confirmModal")?.classList.add("hidden");
  }

  clearTimeout(searchTimers[i]);
  searchTimers[i]=setTimeout(()=>loadSuggestions(i,value),20);
}


function setSuggestionOpenState(i,isOpen){
  const wrap=document.getElementById(`suggest-${i}`)?.closest(".editor-table-wrap");
  if(!wrap)return;
  wrap.classList.toggle("suggestions-open",!!isOpen);
}

async function loadSuggestions(i,value){
  const box=document.getElementById(`suggest-${i}`);
  if(!box) return;

  const q=normalizeSearchCode(value);
  if(!q){
    box.innerHTML="";
    box.classList.add("hidden");
    setSuggestionOpenState(i,false);
    return;
  }

  try{
    await ensureBookSearchCache(false);
    const books=matchingBooks(value);
    window[`suggestions_${i}`]=books;

    if(!books.length){
      box.innerHTML=`<div class="suggest-empty">
        <strong>No matching BAV Code in Book Master</strong>
        <span>${looksLikeFullBav(value) ? "Press Enter or leave the field to review a manual override." : "Keep typing, delete, or change any part of the code."}</span>
      </div>`;
      box.classList.remove("hidden");
      setSuggestionOpenState(i,true);
      return;
    }

    box.innerHTML=books.map((b,idx)=>`
      <button type="button" class="suggest-item"
        onmousedown="event.preventDefault();chooseSuggestion(${i},${idx})">
        <strong>${escapeHtml(b.code)}</strong>
        <span>${escapeHtml(b.title)}${b.language ? " · "+escapeHtml(b.language) : ""}</span>
      </button>`).join("");
    box.classList.remove("hidden");
    setSuggestionOpenState(i,true);
  }catch(e){
    box.innerHTML=`<div class="suggest-empty"><strong>Search temporarily unavailable</strong><span>Keep typing to retry.</span></div>`;
    box.classList.remove("hidden");
    setSuggestionOpenState(i,true);
  }
}

function chooseSuggestion(i,idx){
  const list=window[`suggestions_${i}`]||[];
  const b=list[idx];
  if(!b) return;
  document.getElementById(`suggest-${i}`)?.classList.add("hidden");
  setSuggestionOpenState(i,false);
  requestBookApply(i,b);
}

async function verifyCode(i){
  const row=currentReport.draft.rows[i];
  const raw=(row.code||"").trim();
  if(!raw) return;

  await ensureBookSearchCache(false);
  const compact=normalizeSearchCode(raw);

  // Exact match ignores capitalization and hyphens.
  const exact=bookSearchCache.find(b=>normalizeSearchCode(b.code)===compact);
  if(exact){
    document.getElementById(`suggest-${i}`)?.classList.add("hidden");
    setSuggestionOpenState(i,false);
    requestBookApply(i,exact);
    return;
  }

  // Never interrupt obviously partial typing.
  if(!looksLikeFullBav(raw)){
    loadSuggestions(i,raw);
    return;
  }

  invalidPending={index:i,code:raw.toUpperCase()};
  document.getElementById("confirmMessage").innerHTML=
    `<strong>${escapeHtml(raw.toUpperCase())}</strong> is not in the current Book Master for this market.<br><br>`+
    `Are you sure you want to keep this code anyway? Your Yes/No decision will be documented.`;
  document.getElementById("confirmModal").classList.remove("hidden");
}

function answerInvalidCode(keep){
  if(!invalidPending) return;
  const {index,code}=invalidPending;
  const row=currentReport.draft.rows[index];
  snapshot();

  if(keep){
    row.code=code;
    row.source_match=false;
    row.manual_code_override=true;
    addAudit("invalid_code_override",
      `Kept ${code} even though it was not found in Book Master.`,
      {code,decision:"YES"});
  }else{
    row.code="";
    row.source_match=false;
    row.manual_code_override=false;
    addAudit("invalid_code_rejected",
      `Did not keep ${code} because it was not found in Book Master.`,
      {code,decision:"NO"});
  }

  invalidPending=null;
  document.getElementById("confirmModal").classList.add("hidden");
  renderEditor();
  scheduleSave();

  setTimeout(()=>{
    const inputs=document.querySelectorAll(".code-input");
    inputs[index]?.focus();
    if(inputs[index]?.value) loadSuggestions(index,inputs[index].value);
  },0);
}


function findDuplicateRowIndex(code,currentIndex){
  const target=normalizeSearchCode(code);
  if(!target)return -1;
  return (currentReport?.draft?.rows||[]).findIndex((r,idx)=>
    idx!==currentIndex && normalizeSearchCode(r.code)===target
  );
}

function requestBookApply(i,b,options={}){
  const duplicateIndex=findDuplicateRowIndex(b.code,i);
  if(duplicateIndex<0){
    applyBook(i,b);
    return;
  }
  duplicatePending={index:i,book:b,duplicateIndex,removeOnReject:!!options.removeOnReject};
  document.getElementById("duplicateMessage").innerHTML=
    `<strong>${escapeHtml(b.code)}</strong> — ${escapeHtml(b.title||"")} is already in this report.`+
    `<br><br>Do you still want to add the same book again? Your decision will be documented.`;
  const modal=document.getElementById("duplicateModal");
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden","false");
}

function closeDuplicateModal(){
  duplicatePending=null;
  const modal=document.getElementById("duplicateModal");
  if(modal){
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden","true");
  }
}

function answerDuplicate(allow){
  if(!duplicatePending)return;

  // Copy pending values BEFORE clearing state.
  const pending=duplicatePending;
  duplicatePending=null;

  // Close immediately and remove any lingering visual state.
  const modal=document.getElementById("duplicateModal");
  if(modal){
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden","true");
  }

  const {index,book}=pending;

  if(allow){
    addAudit(
      "duplicate_book_allowed",
      `Added ${book.code} again after duplicate warning.`,
      {code:book.code,decision:"YES"}
    );

    // Apply from one complete book object so code/title/author/language/etc.
    // cannot become partially populated.
    applyBook(index,{...book});
  }else{
    addAudit(
      "duplicate_book_rejected",
      `Did not add ${book.code} again because it was already in this report.`,
      {code:book.code,decision:"NO"}
    );

    if(pending.removeOnReject){
      currentReport.draft.rows.splice(index,1);
      renderEditor();
      discardEmptyReport().then(discarded=>{if(!discarded)scheduleSave();});
      return;
    }

    const row=currentReport.draft.rows[index];
    row.code="";
    row.mms_code="";
    row.title="";
    row.author="";
    row.language="";
    row.language_category="";
    row.category="";
    row.price="";
    row.source_match=false;
    row.manual_code_override=false;

    if(REPORT_TYPE==="new-releases"){
      row.edition_number="";
      row.impression_number="";
      row.edition_year="";
      row.impression_year="";
    }

    renderEditor();
    scheduleSave();

    setTimeout(()=>{
      const inputs=document.querySelectorAll(".code-input");
      inputs[index]?.focus();
    },0);
  }
}

function applyBook(i,b){
  const row=currentReport.draft.rows[i];
  const sameAlready=row.source_match && normalizeSearchCode(row.code)===normalizeSearchCode(b.code) && row.title===(b.title||"");
  if(sameAlready){
    document.getElementById(`suggest-${i}`)?.classList.add("hidden");
    setSuggestionOpenState(i,false);
    return;
  }
  snapshot();
  row.code=b.code||"";
  row.mms_code=b.mms_code||"";
  row.title=b.title||"";
  row.author=b.author||"";
  row.language=b.language||"";
  row.language_category=b.language_category||"";
  row.category=b.category||"";
  row.price=b.price??"";
  row.source_match=true;
  row.manual_code_override=false;
  if(REPORT_TYPE==="new-releases"){
    row.edition_number=b.edition_number??"";
    row.impression_number=b.impression_number??"";
    row.edition_year="";
    row.impression_year="";
    if(row.type===undefined)row.type="";
  }
  addAudit("book_match",`Matched ${row.code} to Book Master and auto-filled report fields.`,{code:row.code});
  renderEditor(); scheduleSave();
}

function updateField(i,key,value){
  if(key==="edition_year" || key==="impression_year"){
    value=String(value??"").replace(/\D/g,"").slice(0,4);
  }
  snapshot();
  currentReport.draft.rows[i][key]=value;
  if(isCatalogReport())updateCatalogWorkspaceStatus();
  scheduleSave();
}
function updateRich(i,html){
  snapshot();
  currentReport.draft.rows[i].description_html=html;
  if(isCatalogReport())updateCatalogWorkspaceStatus();
  scheduleSave();
}

function bindCatalogDescriptionEditors(){
  document.querySelectorAll(".rich-editor[data-row-index]").forEach(editor=>{
    const index=Number(editor.dataset.rowIndex);
    editor.addEventListener("input",()=>{
      const row=currentReport?.draft?.rows?.[index];
      if(!row)return;
      row.description_html=editor.innerHTML;
      updateCatalogWorkspaceStatus();
      scheduleSave();
    });
    editor.addEventListener("focus",()=>{editor.dataset.before=editor.innerHTML;});
    editor.addEventListener("blur",()=>{
      if(editor.dataset.before!==editor.innerHTML)updateRich(index,editor.innerHTML);
    });
    editor.addEventListener("paste",event=>pasteDescription(index,event));
    editor.addEventListener("keydown",event=>event.stopPropagation());
    editor.addEventListener("mousedown",event=>event.stopPropagation());
  });
}

function formatDescription(i,command){
  const editor=document.getElementById(`desc-${i}`);
  if(!editor)return;
  editor.focus();
  snapshot();
  document.execCommand(command,false,null);
  currentReport.draft.rows[i].description_html=editor.innerHTML;
  if(isCatalogReport())updateCatalogWorkspaceStatus();
  scheduleSave();
}

function pasteDescription(i,event){
  event.preventDefault();
  const text=event.clipboardData?.getData("text/plain")||"";
  const editor=document.getElementById(`desc-${i}`);
  if(!editor)return;
  editor.focus();
  const selection=window.getSelection();
  let range=null;
  if(selection?.rangeCount && editor.contains(selection.anchorNode)){
    range=selection.getRangeAt(0);
  }else{
    range=document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
  }
  range.deleteContents();
  const fragment=document.createDocumentFragment();
  let lastNode=null;
  text.split(/\r?\n/).forEach((line,index)=>{
    if(index){
      lastNode=document.createElement("br");
      fragment.append(lastNode);
    }
    lastNode=document.createTextNode(line);
    fragment.append(lastNode);
  });
  range.insertNode(fragment);
  if(lastNode && selection){
    range.setStartAfter(lastNode);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
  }
  currentReport.draft.rows[i].description_html=editor.innerHTML;
  scheduleSave();
}

function adjustDescriptionFontSize(i,amount){
  const row=currentReport?.draft?.rows?.[i];
  if(!row)return;
  snapshot();
  const current=Number(row.description_font_size)||10.5;
  row.description_font_size=Math.max(8,Math.min(14,Math.round((current+amount)*10)/10));
  renderEditor();
  scheduleSave();
}
function setImage(i,file){
  if(!file)return;
  const allowed=["image/jpeg","image/png","image/webp"];
  const maxBytes=5*1024*1024;
  if(!allowed.includes(file.type)){
    showMessage("Book images must be JPG, PNG, or WebP.","error");
    return;
  }
  if(file.size>maxBytes){
    showMessage("Book image is too large. Maximum image size is 5 MB.","error");
    return;
  }
  snapshot();
  const reader=new FileReader();
  reader.onload=()=>{
    currentReport.draft.rows[i].image_data=reader.result;
    currentReport.draft.rows[i].image_name=file.name;
    renderEditor();
    scheduleSave();
  };
  reader.readAsDataURL(file);
}

function codeCell(r,i){
  return `<div class="code-search-wrap">
    <input class="code-input" value="${escapeHtml(r.code||"")}"
      placeholder="Start typing BAV Code"
      autocomplete="off"
      onfocus="loadSuggestions(${i},this.value)"
      oninput="codeInput(${i},this.value)"
      onkeydown="if(event.key==='Enter'){event.preventDefault();verifyCode(${i})}"
      onblur="setTimeout(()=>verifyCode(${i}),160)">
    <div id="suggest-${i}" class="code-suggestions hidden"></div>
    ${r.manual_code_override?'<span class="warning-tag">Manual override</span>':""}
  </div>`;
}
function rowStart(r,i){
  return `<tr ondragover="dragOver(${i},event)" ondragleave="dragLeave(event)" ondrop="dropRow(${i},event)">
      <td class="select-cell"><input type="checkbox" ${r.selected?"checked":""} onchange="toggleSelect(${i},this.checked)"></td>
      <td class="drag-cell" draggable="true" ondragstart="dragStart(${i},event)" ondragend="dragEnd(event)" title="Drag to reorder" aria-label="Drag to reorder">⋮⋮</td>`;
}
function actions(i){
  return `<div class="row-actions">
    <button class="icon-btn" title="Move up" onclick="moveRow(${i},-1)">↑</button>
    <button class="icon-btn" title="Move down" onclick="moveRow(${i},1)">↓</button>
    <button class="icon-btn danger" title="Remove" onclick="removeRow(${i})">×</button>
  </div>`;
}
function textInput(i,key,value,placeholder=""){
  return `<input value="${escapeHtml(value??"")}" placeholder="${escapeHtml(placeholder)}"
    onchange="updateField(${i},'${key}',this.value)">`;
}
function yearInput(i,key,value){
  const year=String(value??"").replace(/\D/g,"").slice(0,4);
  return `<input type="number" inputmode="numeric" min="1000" max="9999" step="1" maxlength="4" value="${escapeHtml(year)}" placeholder="Year"
    oninput="if(this.value.length>4)this.value=this.value.slice(0,4)"
    onchange="updateField(${i},'${key}',this.value)">`;
}

function renderEditor(){
  if(!currentReport){
    const editor=document.getElementById("editor");
    if(editor)editor.innerHTML=emptyState();
    updateCatalogWorkspaceStatus();
    updateNewReleaseWorkspaceStatus();
    updateOutOfStockWorkspaceStatus();
    return;
  }
  if(REPORT_TYPE==="new-releases")renderNewRelease();
  else if(REPORT_TYPE==="book-catalog")renderCatalog();
  else renderOutOfStock();
  updateNewReleaseWorkspaceStatus();
  updateOutOfStockWorkspaceStatus();
}

function setTypeChoice(i,value){
  snapshot();
  if(value==="__OTHER__"){
    currentReport.draft.rows[i].type="Other";
  }else{
    currentReport.draft.rows[i].type=value;
  }
  renderEditor();
  scheduleSave();
}

function renderNewRelease(){
  const rows=currentReport.draft.rows||[];
  let html=`<div class="editor-table-wrap"><table class="editor-table manual-table new-releases-table"><thead><tr>
    <th class="select-cell"><input type="checkbox" onchange="toggleSelectAll(this.checked)"></th>
    <th class="drag-cell"></th>
    <th>CODE</th><th>TITLE</th><th>TYPE</th><th>AUTHOR</th><th>EDN. YR<br>IMP. YR</th><th>PRICE</th><th></th>
  </tr></thead><tbody>`;
  rows.forEach((r,i)=>{
    html+=rowStart(r,i)+`
      <td>${codeCell(r,i)}</td>
      <td>${textInput(i,"title",r.title)}</td>
      <td class="type-cell">
        <select onchange="setTypeChoice(${i},this.value)">
          <option value="" ${!r.type?"selected":""}>—</option>
          <option value="New" ${r.type==="New"?"selected":""}>New</option>
          <option value="Reprint" ${r.type==="Reprint"?"selected":""}>Reprint</option>
          <option value="__OTHER__" ${r.type && !["New","Reprint"].includes(r.type)?"selected":""}>Other</option>
        </select>
        ${r.type && !["New","Reprint"].includes(r.type)
          ? `<input class="other-type-input" value="${r.type==="Other"?"":escapeHtml(r.type)}"
              placeholder="Other option"
              onchange="updateField(${i},'type',this.value)">`
          : ""}
      </td>
      <td>${textInput(i,"author",r.author)}</td>
      <td class="edition-stack">
        <div><b>${ordinalJs(r.edition_number)} Ed.</b> ${yearInput(i,"edition_year",r.edition_year)}</div>
        <div><b>${ordinalJs(r.impression_number)} Imp.</b> ${yearInput(i,"impression_year",r.impression_year)}</div>
      </td>
      <td><div class="price-field"><span>Rs.</span>${textInput(i,"price",r.price)}</div></td>
      <td>${actions(i)}</td>
    </tr>`;
  });
  html+=`</tbody></table></div>${rows.length?"":emptyState()}`;
  document.getElementById("editor").innerHTML=html;
}

function catalogMetaLine(row){
  return [row.language, row.author, row.category].filter(Boolean).join(" · ") || "Book details not filled yet";
}

function catalogStatusPills(row){
  const state=catalogRowState(row);
  const pills=[];
  if(row.source_match)pills.push({label:"Book Master match",tone:"success"});
  if(state.ready){
    pills.push({label:"Ready",tone:"success"});
  }else{
    if(state.missingCode)pills.push({label:"Code needed",tone:"warning"});
    if(state.missingTitle)pills.push({label:"Title needed",tone:"warning"});
    if(state.missingImage)pills.push({label:"Cover needed",tone:"warning"});
    if(state.missingDescription)pills.push({label:"Description needed",tone:"warning"});
  }
  if(state.manualCode)pills.push({label:"Manual code",tone:"warning"});
  return pills.length
    ? `<div class="catalog-status-pills">${pills.map(pill=>`<span class="catalog-status-pill ${pill.tone}">${escapeHtml(pill.label)}</span>`).join("")}</div>`
    : "";
}

function renderCatalog(){
  const rows=currentReport.draft.rows||[];
  let html="";

  if(rows.length){
    html+=`<div class="catalog-book-list">`;
  }

  rows.forEach((r,i)=>{
    const editing=catalogEditingRowId===r.id;
    const img=r.image_data
      ? `<img class="catalog-row-image" src="${r.image_data}" alt="">`
      : `<div class="catalog-row-image image-empty">No image</div>`;
    const selected=r.selected?"checked":"";
    const rowId=JSON.stringify(r.id);
    const domId=catalogDomId(r.id);

    html+=`<article id="${domId}" class="catalog-book-row ${editing?"editing":""}"
      ondragover="dragOver(${i},event)" ondragleave="dragLeave(event)" ondrop="dropRow(${i},event)">
      <label class="catalog-select"><input type="checkbox" ${selected} onchange="toggleSelect(${i},this.checked)"><span>Select</span></label>
      <button class="drag-cell catalog-drag" type="button" draggable="true" ondragstart="dragStart(${i},event)" ondragend="dragEnd(event)" title="Drag to reorder" aria-label="Drag to reorder">⋮⋮</button>
      ${img}
      <div class="catalog-row-summary">
        <span class="catalog-code">${escapeHtml(r.code||"BAV code needed")}${r.manual_code_override?' <b class="warning-tag">Manual override</b>':""}</span>
        <strong>${escapeHtml(r.title||"Untitled Book")}</strong>
        <small>${escapeHtml(catalogMetaLine(r))}</small>
      </div>
      <div class="catalog-row-buttons">
        <button class="btn secondary" type="button" onclick='setCatalogEditing(${rowId})'>${editing?"Editing":"Edit"}</button>
        <button class="btn secondary danger-outline" type="button" onclick="removeRow(${i})">Delete</button>
      </div>
      ${editing?`
      <div class="catalog-edit-panel">
        <label class="catalog-code-field"><span>BAV Code</span>${codeCell(r,i)}</label>
        <div class="catalog-fields-grid">
          <label><span>Book Name</span>${textInput(i,"title",r.title)}</label>
          <label><span>Language</span>${textInput(i,"language",r.language)}</label>
          <label><span>Author</span>${textInput(i,"author",r.author)}</label>
          <label><span>Category</span>${textInput(i,"category",r.category)}</label>
        </div>
        <div class="catalog-media-grid">
          <label class="catalog-image-control"><span>Cover image</span>
            <span class="catalog-upload-box ${r.image_data?"has-image":""}">
              ${r.image_data ? `<img src="${r.image_data}" alt="">` : `<b>Upload cover</b><small>JPG, PNG or WebP</small>`}
              <input class="image-file-input" type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" onchange="setImage(${i},this.files[0])">
            </span>
            <small class="image-help">Maximum 5 MB</small>
          </label>
          <div class="catalog-description-control"><span>Description</span>
            <div class="rich-toolbar">
              <button type="button" title="Bold" onmousedown="event.preventDefault();formatDescription(${i},'bold')"><b>B</b></button>
              <button type="button" title="Italic" onmousedown="event.preventDefault();formatDescription(${i},'italic')"><i>I</i></button>
              <button type="button" title="Underline" onmousedown="event.preventDefault();formatDescription(${i},'underline')"><u>U</u></button>
              <span class="description-size-controls" title="Description font size">
                <button type="button" title="Decrease description size" onmousedown="event.preventDefault();adjustDescriptionFontSize(${i},-0.5)">A-</button>
                <span>${Number(r.description_font_size)||10.5}</span>
                <button type="button" title="Increase description size" onmousedown="event.preventDefault();adjustDescriptionFontSize(${i},0.5)">A+</button>
              </span>
            </div>
        <div id="desc-${i}" class="rich-editor" data-row-index="${i}" contenteditable="true" draggable="false" spellcheck="true" tabindex="0" role="textbox" aria-multiline="true"
          style="font-size:${Number(r.description_font_size)||10.5}px">${r.description_html||""}</div>
          </div>
        </div>
        <div class="catalog-edit-actions">
          <button class="btn secondary" type="button" onclick="setCatalogEditing(null)">Done</button>
        </div>
      </div>`:""}
    </article>`;
  });

  if(rows.length){
    html+=`</div>`;
  }else{
    html+=emptyState();
  }
  document.getElementById("editor").innerHTML=html;
  bindCatalogDescriptionEditors();
  updateCatalogWorkspaceStatus();
}
function renderOutOfStock(){
  const rows=currentReport.draft.rows||[];
  const validated=currentReport.draft.meta?.validation||null;

  let html=`<div class="editor-table-wrap"><table class="editor-table manual-table oos-editor-table"><thead><tr>
    <th class="select-cell"><input type="checkbox" onchange="toggleSelectAll(this.checked)"></th>
    <th class="drag-cell"></th>
    <th>CODE</th><th>TITLE</th><th>LANGUAGE</th><th class="actions-heading">ACTIONS</th>
  </tr></thead><tbody>`;

  rows.forEach((r,i)=>{
    html+=rowStart(r,i)+`
      <td>${codeCell(r,i)}</td>
      <td>${textInput(i,"title",r.title)}</td>
      <td>${textInput(i,"language",r.language)}</td>
      <td>${actions(i)}</td>
    </tr>`;
  });

  html+=`</tbody></table></div>${rows.length?"":emptyState()}
    <div class="validation-footer">
      <button class="btn validation-btn" onclick="validateOutOfStock()">Validate Against Current Stock Data</button>
      <p class="muted">
        Uses current Book Master + Books Stock Position:
        <b>Total HQ DL Stock (V) + PB HQ + Dera Stalls (AM) &lt; 10</b>.
        Previous Indent is not used.
      </p>
    </div>`;

  document.getElementById("editor").innerHTML=html;
  if(validated)renderValidation(validated);
}
function emptyState(){
  if(isCatalogReport()){
    return `<button type="button" class="manual-empty manual-empty-button catalog-empty" onclick="addBlankRow()">
      <div class="plus-circle">+</div><h3>No books added yet</h3>
      <p>Add a book and start by entering its BAV code.</p>
      <span>Add first book</span>
    </button>`;
  }
  return `<button type="button" class="manual-empty manual-empty-button" onclick="addBlankRow()">
    <div class="plus-circle">+</div><h3>No books added yet</h3>
    <p>Click here or <b>+ Add Row</b> to begin.</p>
  </button>`;
}
function ordinalJs(v){
  const n=parseInt(v,10);
  if(!Number.isFinite(n))return "_";
  const mod100=n%100;
  const suffix=(mod100>=11&&mod100<=13)?"th":({1:"st",2:"nd",3:"rd"}[n%10]||"th");
  return `${n}${suffix}`;
}

async function validateOutOfStock(){
  if(!currentReport){showMessage("Add at least one book before validating.","error");return;}
  const btn=document.querySelector(".validation-btn");
  const oldText=btn?.textContent||"Validate Against Current Stock Data";
  if(btn){btn.disabled=true;btn.textContent="Validating...";}
  try{
    if(!(await saveDraft()))return;
    const data=await jsonFetch(`/api/report/${currentReport.id}/validate-out-of-stock`,{method:"POST",body:"{}"});
    currentReport=data.report;
    renderEditor();
    updateChanges();
  }catch(e){showMessage(e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent=oldText;}}
}

function validationGroups(items){
  return (items||[]).reduce((groups,item)=>{
    const language=String(item.language||"Other").trim()||"Other";
    (groups[language]||=[]).push(item);
    return groups;
  },{});
}

function validationLanguageAccordions(title,items){
  const groups=validationGroups(items);
  const languages=Object.keys(groups);
  if(!languages.length)return "";
  return `<section class="validation-books-section"><div class="validation-books-heading"><h3>${title}</h3><div><button class="text-link" type="button" onclick="setValidationGroups(true)">Open all</button><button class="text-link" type="button" onclick="setValidationGroups(false)">Close all</button></div></div>${languages.map(language=>`<details class="validation-language-group"><summary><span>${escapeHtml(language)}</span><b>${groups[language].length}</b></summary><div class="validation-language-books">${groups[language].map(item=>`<article class="validation-book"><strong>${escapeHtml(item.code||"")} - ${escapeHtml(item.title||"Unknown title")}</strong><span>${item.dl_stock==null?"Stock data needs review":`DL: ${escapeHtml(item.dl_stock)} | PB HQ + Dera: ${escapeHtml(item.pb_stock)} | Combined: ${escapeHtml(item.combined_stock)}`}</span><small>${escapeHtml(item.reason||"")}</small></article>`).join("")}</div></details>`).join("")}</section>`;
}

function setValidationGroups(open){
  document.querySelectorAll(".validation-language-group").forEach(group=>{group.open=open;});
}

function renderValidation(v){
  const box=document.getElementById("validationArea");
  if(!box)return;
  box.classList.remove("hidden");

  const missing=v.missing||[];
  const extra=v.extra||[];
  const reviewed=v.reviewed||[];

  let html=`<div class="validation-summary">
    <div class="validation-stat"><b>${v.user_count}</b><span>Your list</span></div>
    <div class="validation-stat"><b>${v.expected_count}</b><span>MMS logic</span></div>
    <div class="validation-stat ${missing.length?"warn":""}"><b>${missing.length}</b><span>Missing</span></div>
    <div class="validation-stat ${extra.length?"warn":""}"><b>${extra.length}</b><span>Extra / review</span></div>
  </div>
  <div class="logic-card">
    <strong>Validation source</strong><br>
    Book Master + Books Stock Position only. <b>Previous Indent is not used.</b><br>
    Rule: <b>Total HQ DL Stock (Excel V) + PB HQ + Dera Stalls (Excel AM) &lt; 10</b>.
  </div>`;

  html+=validationLanguageAccordions("Your entered books - stock review",reviewed);

  if(!missing.length&&!extra.length){
    html+=`<div class="alert success"><strong>Matched.</strong> Your list matches the current stock-data calculation.</div>`;
  }

  html+=validationLanguageAccordions("Books missing from your list",missing);
  html+=validationLanguageAccordions("Books you added that need review",extra);

  html+=`<div class="source-check"><strong>Database cross-check</strong>`+
    (v.sources||[]).map(s=>`<div>${escapeHtml(s.label)}: ${escapeHtml(s.status||"—")} · Drive updated ${escapeHtml(s.drive_modified_time||"—")} · Synced ${escapeHtml(s.last_successful_sync||"—")}</div>`).join("")+
    `</div>`;

  box.innerHTML=html;
}
function scheduleSave(){
  clearTimeout(saveTimer);
  setAutosaveStatus("Saving...");
  saveTimer=setTimeout(saveDraft,280);
}
async function saveDraft(){
  if(!currentReport)return true;
  clearTimeout(saveTimer);
  saveTimer=null;
  try{
    await jsonFetch(`/api/report/${currentReport.id}`,{
      method:"PUT",
      body:JSON.stringify({
        draft:currentReport.draft,
        market:currentReport.market,
        month:currentReport.month,
        year:currentReport.year
      })
    });
    updateChanges();
    if(!isCatalogReport() && typeof syncCurrentReportDraft==="function")syncCurrentReportDraft();
    setAutosaveStatus("Saved");
    return true;
  }catch(e){
    setAutosaveStatus("Save failed");
    showMessage(e.message,"error");
    return false;
  }
}

async function discardEmptyReport(){
  if(!currentReport || (currentReport.draft?.rows || []).length)return false;
  const reportId=currentReport.id;
  try{
    await jsonFetch(`/api/report/${reportId}/delete`,{method:"POST",body:"{}"});
  }catch(e){
    showMessage(e.message,"error");
    return false;
  }
  clearTimeout(saveTimer);
  currentReport=null;
  originalDraft=null;
  undoStack=[];
  redoStack=[];
  updateUndoButton();
  window.RECENT_REPORT_DRAFTS=(window.RECENT_REPORT_DRAFTS||[]).filter(report=>report.id!==reportId);
  if(isCatalogReport() && typeof showCatalogChooser==="function")showCatalogChooser();
  if(!isCatalogReport() && typeof showReportChooser==="function")showReportChooser();
  showToast("Empty draft discarded.","info");
  return true;
}
function setAutosaveStatus(text){
  const el=document.getElementById("autosaveStatus");
  if(el)el.textContent=text;
}
function updateChanges(){
  const box=document.getElementById("changesList");
  if(!box||!currentReport) return;
  const audit=currentReport.draft?.meta?.audit_events||[];
  if(!audit.length){
    box.textContent="No manual changes yet.";
    return;
  }
  box.innerHTML=audit.slice().reverse().map(a=>`
    <div class="change-item">${escapeHtml(a.message||"Change recorded.")}</div>
  `).join("");
}

async function downloadFinal(){
  if(!currentReport){showMessage("Add at least one book before downloading.","error");return;}
  if(!(currentReport.draft.rows||[]).length){showMessage("Add at least one book before downloading.","error");return;}
  if(REPORT_TYPE==="out-of-stock"){
    const validation=currentReport.draft.meta?.validation;
    if(!validation){
      showMessage("Run stock validation before downloading this report.","error");
      return;
    }
    const missing=(validation.missing||[]).length;
    const extra=(validation.extra||[]).length;
    if(missing||extra){
      const ok=await askConfirm({
        title:"Download with validation issues?",
        message:`Validation found ${missing} missing and ${extra} review item(s). Download only if you have reviewed these results.`,
        confirmText:"Download anyway",
        cancelText:"Review first"
      });
      if(!ok)return;
    }
  }

  const btn=document.querySelector(".download-btn");
  const oldText=btn?.textContent||"Download PDF";
  if(btn){btn.disabled=true;btn.textContent="Preparing PDF…";}

  try{
    if(!(await saveDraft()))return;
    const requestedName=(document.getElementById("pdfFileName")?.value||"").trim();
    savePreferredFileName(requestedName);
    const data=await jsonFetch(`/api/report/${currentReport.id}/finalize`,{
      method:"POST",
      body:JSON.stringify({filename:requestedName})
    });
    triggerReportDownload(data.download_url,data.filename);
  }catch(e){
    showMessage(e.message,"error");
  }finally{
    if(btn){btn.disabled=false;btn.textContent=oldText;}
  }
}

function triggerReportDownload(downloadUrl,filename){
  const link=document.createElement("a");
  link.href=fileUrl(downloadUrl);
  link.download=filename||"report.pdf";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function previewPdf(){
  if(!currentReport || !(currentReport.draft.rows||[]).length){
    showMessage("Add at least one book before previewing.","error");
    return;
  }
  const previewWindow=window.open("","_blank");
  const btn=document.querySelector(".preview-btn");
  const oldText=btn?.textContent||"Preview PDF";
  if(btn){btn.disabled=true;btn.textContent="Preparing preview...";}
  try{
    if(!(await saveDraft()))throw new Error("Could not save the draft.");
    const requestedName=(document.getElementById("pdfFileName")?.value||"").trim();
    savePreferredFileName(requestedName);
    const data=await jsonFetch(`/api/report/${currentReport.id}/preview`,{
      method:"POST",
      body:JSON.stringify({filename:requestedName})
    });
    if(previewWindow)previewWindow.location.href=fileUrl(data.preview_url);
    else window.open(fileUrl(data.preview_url),"_blank");
  }catch(e){
    previewWindow?.close();
    showMessage(e.message,"error");
  }finally{
    if(btn){btn.disabled=false;btn.textContent=oldText;}
  }
}

function showMessage(message,type="info"){
  const box=document.getElementById("reportMessage");
  box.innerHTML=`<div class="alert ${type}">${escapeHtml(message)}</div>`;
}
