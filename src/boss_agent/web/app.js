// 同源对话前端；这里只管理界面状态，Agent 调度与持久化均在后端执行。
'use strict';
const $ = (id) => document.getElementById(id);
let session = null;
// 浏览器只持久保存用户标识与会话指针，不能据此认为已经完成身份验证。
let currentUser = localStorage.getItem('boss-agent-user-id') || 'local-user';
let portraitSnapshot = null;
let resetPortraitArmed = false;
// 会话指针按用户分别存储，切换姓名后不复用另一用户的当前会话。
function sessionKey(){return 'boss-agent-session:'+encodeURIComponent(currentUser);}
$('user-id').value=currentUser;
let busy = false;
let currentPlan = null;
let modelConfigured = false;
const sampleJobs = [{id:'demo-web-001',title:'AI Agent 应用开发工程师',company:'虚构示例公司',city:'杭州',salary_min:20000,salary_max:30000,description:'使用 Python 构建 Agent 应用，要求了解工具调用和后端开发。'}];
// 统一用 textContent 渲染用户和模型文本，避免把输入当作 HTML 执行。
function node(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el; }
// 错误提示只改变当前页面显示，不创建伪造的助手消息。
function showError(message='') { $('error').textContent=message; $('error').hidden=!message; }
// 请求统一携带 URL 编码的姓名标识；服务端错误在页面展示。
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-User-ID':encodeURIComponent(currentUser),...options.headers}});
  const body=await response.json().catch(()=>({}));
  if(!response.ok) {
    let message=typeof body.detail==='string'?body.detail:`请求未完成（${response.status}），请检查输入或稍后重试。`;
    if(Array.isArray(body.detail))message='输入格式不正确：'+body.detail.map(item=>item.loc.slice(1).join('.')+' '+item.msg).join('；');
    const error=new Error(message);error.status=response.status;throw error;
  }
  return body;
}
// 请求期间锁定相关控件，减少重复提交与切换身份导致的状态串用。
function setBusy(value){busy=value;['user-id','switch-user','refresh-portrait','save-portrait','reset-portrait','history-toggle','message','send','generate-plan','new-session','mode','resume','jobs','resume-file','jobs-file','load-sample'].forEach(id=>$(id).disabled=value);document.querySelectorAll('.session-item,.starter').forEach(el=>el.disabled=value);$('send').textContent=value?'正在处理…':'发送消息';}
// 将已知角色和正文安全渲染为消息；pending 只表示请求尚未完成。
function addMessage(role, content, pending=false){
  const row=node('article',undefined,'message '+role+(pending?' pending':''));
  row.append(node('div',role==='user'?'你':'b·','avatar'));
  const col=node('div');col.append(node('div',role==='user'?'你':'求职助手','message-label'),node('div',content,'message-body'));row.append(col);$('messages').append(row);
}
// 欢迎建议只是填入输入框，不会自动发送或触发计划。
function welcome(){
  const box=node('div',undefined,'welcome');box.append(node('div','✳','welcome-symbol'));
  box.append(node('h2','下一份工作，从了解你开始。'),node('p','告诉我你的求职期待，或先添加简历。我们一起找到与你的经历和方向更匹配的机会。'));
  const starters=node('div',undefined,'starter-list');
  ['AI Agent 开发需要什么技能？','帮我分析简历适合什么方向','我们聊聊我的求职意向'].forEach(text=>{const button=node('button',text,'starter');button.type='button';button.onclick=()=>{$('message').value=text;$('message').focus();};starters.append(button);});
  box.append(starters);$('messages').append(box);
}
// 根据服务端会话快照重建消息，成功结果以持久化状态为准。
function renderMessages(){ $('messages').replaceChildren();if(session.messages.length<=1)welcome();session.messages.forEach(m=>addMessage(m.role,m.content));$('messages').scrollTop=$('messages').scrollHeight; }
// 只统计当前输入，不把未发送的编辑误报为已经保存。
function updateMeta(){ $('resume-meta').textContent=$('resume').value.length?`${$('resume').value.length.toLocaleString()} 字 · 随下一条消息保存`:'资料会随下一条消息一起保存到本机会话。';try{const jobs=JSON.parse($('jobs').value||'[]');$('jobs-meta').textContent=Array.isArray(jobs)?`${jobs.length} 个导入职位`:'请输入 JSON 数组';}catch{$('jobs-meta').textContent='JSON 格式待检查';} }
// 分别展示长期背景、明确意向和模型建议，避免将推测当作事实。
function renderInsights(){const box=$('insights');box.replaceChildren();box.hidden=false;const portrait=session.user_portrait||{};if(portrait.facts?.length){box.append(node('strong','已记住的职业背景'));portrait.facts.forEach(f=>box.append(node('span',f.value,'tag')));}const prefs=session.preferences||{};const parts=[...(prefs.target_roles||[]),...(prefs.cities||[]),...(prefs.minimum_monthly_salary?[`月薪至少 ${prefs.minimum_monthly_salary}`]:[])];if(parts.length){box.append(node('strong','你已明确的求职意向'));parts.forEach(text=>box.append(node('span',text,'tag')));}if(session.last_execution){box.append(node('strong','本轮协作记录'));box.append(node('p',`${session.last_execution.agents.join(' · ')} · ${session.last_execution.calls} 步`));}if(session.suggested_roles.length){box.append(node('strong','可以探索的方向'));session.suggested_roles.forEach(text=>box.append(node('span',text,'tag')));}if(session.missing_preferences.length){box.append(node('strong','还需要了解'));box.append(node('p',session.missing_preferences.join('、')));} }
// 按 job_id 关联岗位与草稿，明确展示实际投递数量仍为零。
function renderPlan(plan){
  currentPlan=plan;const box=$('plan');box.replaceChildren();box.hidden=!plan;if(!plan)return;
  box.append(node('h3',session.mode==='demo'?'演示求职计划':'本次求职计划'));
  box.append(node('p',`${plan.matches.length} 个岗位 · ${plan.drafts.length} 份草稿 · 实际投递 0`,'plan-summary'));
  box.append(node('p',plan.profile.experience_summary,'plan-summary'));
  plan.matches.forEach(match=>{const job=session.jobs.find(item=>item.id===match.job_id);const card=node('article',undefined,'job-card');card.append(node('h4',job?job.title:match.job_id));card.append(node('span',({recommend:'推荐',consider:'待考虑',skip:'跳过'})[match.decision],'decision'));if(job)card.append(node('p',`${job.company} · ${job.city}`));match.evidence.forEach(text=>card.append(node('p',text)));if(match.gaps.length)card.append(node('p','待确认：'+match.gaps.join('；')));const draft=plan.drafts.find(item=>item.job_id===match.job_id);if(draft)card.append(node('p',draft.message,'draft'));box.append(card);});
}
// 切换或恢复会话时同步资料、模式和摘要；不在浏览器保存完整简历。
function renderSession(){
  $('mode').value=session.mode;
  $('mode-notice').textContent=session.mode==='demo'?'演示模式：使用脚本驱动调度与工具循环，不代表真实模型分析。':'模型模式：基于简历和对话分析；当前只准备计划，不执行真实投递。';
  $('resume').value=session.resume_text;$('jobs').value=session.jobs.length?JSON.stringify(session.jobs,null,2):'';
  renderMessages();renderInsights();updateMeta();
}
// 只读取当前用户分区的会话摘要，事件处理保留对应的具体 ID。
async function refreshSessions(){
  const rows=await api('/api/v1/sessions');const list=$('session-list');list.replaceChildren();
  rows.forEach(item=>{const button=node('button',item.title,'session-item'+(session&&item.id===session.id?' active':''));button.title=item.title;button.disabled=busy;button.onclick=()=>{if(!busy)loadSession(item.id).catch(e=>showError(e.message));};list.append(button);});
}
// 恢复会话及已有计划，并清除上一会话未发送的输入，防止草稿串到其他上下文。
async function loadSession(id){
  $('session-list').classList.remove('open');$('history-toggle').setAttribute('aria-expanded','false');
  showError();setBusy(true);
  try{session=await api('/api/v1/sessions/'+encodeURIComponent(id));sessionStorage.setItem(sessionKey(),session.id);$('message').value='';renderSession();renderPlan(null);if(session.last_plan_id){const record=await api('/api/v1/plans/'+encodeURIComponent(session.last_plan_id));renderPlan(record.plan);}await refreshSessions();await readPortrait();}finally{setBusy(false);}
}
// 新会话由后端载入长期画像；切换模式不会复用另一模式的画像。
async function newSession(){
  if(busy)return;showError();setBusy(true);
  try{session=await api('/api/v1/sessions',{method:'POST',body:JSON.stringify({mode:$('mode').value})});sessionStorage.setItem(sessionKey(),session.id);$('message').value='';renderSession();renderPlan(null);await refreshSessions();await readPortrait();}catch(e){showError(e.message);}finally{setBusy(false);}
}
// 发送普通消息或显式计划；资料不完整不应阻止无关知识咨询。
async function send(action='chat'){
  if(busy||!session)return;
  const typed=$('message').value.trim();const message=typed||(action==='plan'?'请根据当前简历和我已经说明的求职要求，生成求职计划。':'');if(!message){$('message').focus();return;}
  const jobsDraft=$('jobs').value;
  let invalidJobsDraft=false;
// 无效职位草稿在普通聊天中暂不发送；只有生成计划时才要求修正格式。
  let jobs;try{jobs=JSON.parse($('jobs').value||'[]');if(!Array.isArray(jobs)||jobs.some(j=>!j||['id','title','company','city','description'].some(k=>typeof j[k]!=='string'))||new Set(jobs.map(j=>j.id)).size!==jobs.length)throw Error();}catch{if(action==='plan'){showError('职位数据需要是有效的 JSON 数组，请检查右侧输入。');return;}jobs=undefined;invalidJobsDraft=true;}
  if(action==='plan'&&(!$('resume').value.trim()||!jobs.length)){showError('请先添加简历和至少一个职位，再生成计划。');$('context-panel').classList.add('open');$('context-toggle').setAttribute('aria-expanded','true');return;}
  showError();setBusy(true);addMessage('user',message);addMessage('assistant','正在'+(action==='plan'?'整理画像、匹配岗位和准备草稿…':'结合上下文处理你的消息…'),true);$('messages').scrollTop=$('messages').scrollHeight;
  try{
    const result=await api('/api/v1/sessions/'+encodeURIComponent(session.id)+'/messages',{method:'POST',body:JSON.stringify({version:session.version,message,action,resume_text:$('resume').value,jobs})});
    session=result.session;$('message').value='';renderSession();if(invalidJobsDraft){$('jobs').value=jobsDraft;updateMeta();}if(result.plan){renderPlan(result.plan);}else if(!session.last_plan_id){renderPlan(null);}await refreshSessions();await readPortrait();
  }catch(e){renderMessages();showError(e.message);if(e.status===409){showError(e.message+' 正在恢复最新记录；输入框中的消息将保留。');session=await api('/api/v1/sessions/'+encodeURIComponent(session.id));renderMessages();}}
  finally{setBusy(false);$('message').focus();}
}
// 文件先在本地读取和校验，内容随后随消息提交；这里不解析 PDF/DOCX。
async function importFile(input,target,kind){
  const file=input.files[0];if(!file)return;
  try{if(file.size>2*1024*1024)throw Error('文件过大，请使用 2 MB 以内的文本文件。');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(kind==='resume'&&text.length>100000)throw Error('简历最多支持 100000 字。');if(kind==='jobs'){const jobs=JSON.parse(text);if(!Array.isArray(jobs))throw Error('职位文件必须是 JSON 数组。');$(target).value=JSON.stringify(jobs,null,2);}else $(target).value=text;showError();updateMeta();}catch(e){showError('文件导入失败：'+e.message);}finally{input.value='';}
}
// 统一事件入口；中文输入法正在组词时，Enter 不应提前发送消息。
$('chat-form').addEventListener('submit',event=>{event.preventDefault();send();});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();send();}});
$('generate-plan').onclick=()=>send('plan');$('new-session').onclick=newSession;$('mode').onchange=newSession;
$('resume').oninput=updateMeta;$('jobs').oninput=updateMeta;
$('resume-file').onchange=()=>importFile($('resume-file'),'resume','resume');$('jobs-file').onchange=()=>importFile($('jobs-file'),'jobs','jobs');
$('load-sample').onclick=()=>{if(!$('resume').value.trim())$('resume').value='【虚构演示简历】掌握 Python，做过使用大模型 API 的问答应用。本资料仅用于演示。';$('jobs').value=JSON.stringify(sampleJobs,null,2);updateMeta();showError();};
$('history-toggle').onclick=()=>{const open=$('session-list').classList.toggle('open');$('history-toggle').setAttribute('aria-expanded',String(open));};
$('context-toggle').onclick=()=>{const open=$('context-panel').classList.toggle('open');$('context-toggle').setAttribute('aria-expanded',String(open));};

// 读取最新版本与活动事实，编辑器不直接修改来源或 revision。
async function readPortrait(){
  portraitSnapshot=await api('/api/v1/users/me/portrait?mode='+encodeURIComponent(session?.mode||$('mode').value));
  const facts=portraitSnapshot.active_facts||[];
  if(session){session.user_portrait={...portraitSnapshot,facts};renderInsights();}
  const editable={preferences:portraitSnapshot.preferences,skills:facts.filter(f=>f.field==='skills').map(f=>f.value),experience:facts.filter(f=>f.field==='experience').map(f=>f.value),current_focus:facts.find(f=>f.field==='current_focus')?.value||null};
  $('portrait-json').value=JSON.stringify(editable,null,2);
  $('portrait-status').textContent=`${currentUser} · 版本 ${portraitSnapshot.revision} · ${facts.length} 条有效背景记录`;
  $('reset-portrait').textContent='清除长期画像';resetPortraitArmed=false;
}
// 提交当前 revision 以检测并发覆盖；保存后重新读取服务器状态。
async function savePortrait(){
  if(busy||!portraitSnapshot)return;showError();
  try{const data=JSON.parse($('portrait-json').value);setBusy(true);
    await api('/api/v1/users/me/portrait?mode='+encodeURIComponent(session.mode),{method:'PUT',body:JSON.stringify({...data,revision:portraitSnapshot.revision})});
    await readPortrait();$('portrait-status').textContent+=' · 已保存，下次发送会参考最新画像';
  }catch(e){showError(e.message);}finally{setBusy(false);}
}
// 通过再次点击确认清除长期记忆；历史会话和旧简历不会被删除。
async function resetPortrait(){
  if(busy||!portraitSnapshot)return;
  if(!resetPortraitArmed){resetPortraitArmed=true;$('reset-portrait').textContent='再次点击确认清除';return;}
  setBusy(true);showError();
  try{await api('/api/v1/users/me/portrait?mode='+encodeURIComponent(session.mode)+'&revision='+portraitSnapshot.revision,{method:'DELETE'});await readPortrait();$('portrait-status').textContent+=' · 已清除，历史对话保留';}catch(e){showError(e.message);}finally{setBusy(false);}
}
// 先清除上一用户的页面数据，再读取新标识的会话与画像；不是登录认证。
async function switchUser(){
  if(busy)return;const value=$('user-id').value.normalize('NFC').trim();
  if(!value||value.length>64||!/^[\p{L}\p{N}_ -]+$/u.test(value)){showError('请输入 1–64 字的姓名或 user_id，可含字母、数字、空格、下划线和短横线。');return;}
  currentUser=value;session=null;portraitSnapshot=null;$('messages').replaceChildren();renderPlan(null);$('insights').replaceChildren();localStorage.setItem('boss-agent-user-id',value);showError();
  const saved=sessionStorage.getItem(sessionKey());
  if(saved){try{await loadSession(saved);return;}catch(e){if(e.status!==404){showError(e.message);return;}}}
  await newSession();
}
$('switch-user').onclick=switchUser;
$('user-id').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();switchUser();}});
$('refresh-portrait').onclick=()=>readPortrait().catch(e=>showError(e.message));
$('save-portrait').onclick=savePortrait;$('reset-portrait').onclick=resetPortrait;

// 先读取服务配置，再恢复当前用户会话；旧版默认用户指针也可兼容。
(async()=>{
  setBusy(true);
  try{const config=await api('/api/v1/config');modelConfigured=config.model_configured;$('model-status').textContent=modelConfigured?'模型已配置':'未配置模型 · 可用演示模式';$('model-status').classList.toggle('ready',modelConfigured);$('mode').value=modelConfigured?'llm':'demo';setBusy(false);const saved=sessionStorage.getItem(sessionKey()) || (currentUser==='local-user'?sessionStorage.getItem('boss-agent-session'):null);if(saved){try{await loadSession(saved);return;}catch(e){if(e.status!==404)throw e;}}await newSession();}
  catch(e){showError('无法连接服务：'+e.message);$('model-status').textContent='服务连接失败';}
  finally{setBusy(false);}
})();
