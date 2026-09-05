let currentReportId = null;
let csrfToken = '';
let platformStatus = {};
let installedModels = [];
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = {...(options.headers || {})};
  if (options.body) headers['Content-Type'] = 'application/json';
  if (!['GET', 'HEAD'].includes(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const response = await fetch(path, {...options, method, headers, credentials:'same-origin'});
  let data;
  try { data = await response.json(); } catch (_) { data = {error: '服务返回了无法识别的响应'}; }
  if (!response.ok) {
    const error = new Error(data.error || '请求失败');
    error.code = data.code || '';
    error.status = response.status;
    throw error;
  }
  return data;
}

function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.add('show');
  setTimeout(() => node.classList.remove('show'), 3000);
}

function unlockApp(session) {
  csrfToken = session.csrf_token || csrfToken;
  document.body.classList.remove('app-locked');
  $('#auth-gate').classList.add('hidden');
  $('#app-main').setAttribute('aria-hidden', 'false');
  $('#logout-btn').classList.remove('hidden');
  if (session.user && session.user.username) {
    $('#reviewer').value = session.user.username;
    $('#reviewer').readOnly = true;
  }
  Promise.all([loadDocuments(), loadPlatform()]).catch(handleError);
}

function showAuth(configured) {
  document.body.classList.add('app-locked');
  $('#auth-gate').classList.remove('hidden');
  $('#bootstrap-card').classList.toggle('hidden', configured);
  $('#login-card').classList.toggle('hidden', !configured);
  $('#app-main').setAttribute('aria-hidden', 'true');
}

async function initializeAuth() {
  const status = await api('/api/auth/status');
  const configured = Boolean(status.configured ?? status.admin_configured);
  if (!configured) return showAuth(false);
  try {
    const session = await api('/api/auth/session');
    if (session.authenticated) return unlockApp(session);
  } catch (_) {}
  showAuth(true);
}

async function submitBootstrap() {
  const username = $('#bootstrap-username').value.trim();
  const password = $('#bootstrap-password').value;
  if (password !== $('#bootstrap-confirm').value) return toast('两次输入的密码不一致');
  if (password.length < 12) return toast('密码至少需要 12 位');
  const session = await api('/api/auth/bootstrap', {method:'POST', body:JSON.stringify({username, password})});
  $('#bootstrap-password').value = '';
  $('#bootstrap-confirm').value = '';
  unlockApp(session);
  toast('本机管理员已创建');
}

async function submitLogin() {
  const session = await api('/api/auth/login', {
    method:'POST',
    body:JSON.stringify({username:$('#login-username').value.trim(), password:$('#login-password').value})
  });
  $('#login-password').value = '';
  unlockApp(session);
  toast('登录成功');
}

function handleError(error) {
  if (error.status === 401 || error.status === 428 || ['auth_required','admin_setup_required'].includes(error.code)) {
    initializeAuth().catch(inner => toast(inner.message));
    return;
  }
  toast(error.message || '操作失败');
}

function showStep(number) {
  document.querySelectorAll('.panel,.step').forEach(node => node.classList.remove('active'));
  $('#panel-' + number).classList.add('active');
  document.querySelector('.step[data-step="' + number + '"]').classList.add('active');
  window.scrollTo({top: 360, behavior: 'smooth'});
  if (number === '4') loadAudits().catch(handleError);
}

async function loadDocuments() {
  const data = await api('/api/documents');
  $('#documents').innerHTML = data.items.map(item => '<div class="doc"><strong>' + escapeHtml(item.name) + '</strong><br><small>' + item.characters + ' 字符 · ' + escapeHtml(item.created_at) + '</small></div>').join('') || '尚无材料';
}

async function loadAudits() {
  const data = await api('/api/audits');
  $('#audits').innerHTML = data.items.map(item => '<div class="audit"><strong>' + escapeHtml(item.action) + '</strong><br>' + escapeHtml(item.detail) + '<br><small>' + escapeHtml(item.created_at) + '</small></div>').join('');
}

function normalizeModels(data) {
  const source = data.items || data.profiles || data.models || data.allowed_models || [];
  return source.map(item => typeof item === 'string'
    ? {id:item, name:item}
    : {id:item.id || item.model || item.name, name:item.title || item.label || item.name || item.model || item.id, ...item}
  ).filter(item => item.id && item.kind !== 'embedding');
}

async function loadPlatform() {
  const publicStatus = await api('/api/status');
  platformStatus = publicStatus;
  let modelData = {items: publicStatus.allowed_models || publicStatus.models || []};
  let doctor = {};
  try { modelData = await api('/api/models'); } catch (error) {
    if (error.status !== 404) throw error;
  }
  try { doctor = await api('/api/doctor'); } catch (error) {
    if (error.status !== 404) throw error;
  }
  const models = normalizeModels(modelData);
  const select = $('#model-select');
  select.innerHTML = models.map(item => {
    const size = item.size_gb ? ' · 约 ' + item.size_gb + 'GB' : '';
    return '<option value="' + escapeHtml(item.id) + '">' + escapeHtml(item.name) + size + '</option>';
  }).join('') || '<option value="">暂无可安装模型</option>';

  const runtime = modelData.ollama || publicStatus.ollama || publicStatus.runtime ||
    (typeof publicStatus.model === 'object' ? publicStatus.model : {});
  const connected = Boolean(runtime.connected ?? runtime.available ?? publicStatus.ollama_available);
  const installed = runtime.installed ?? runtime.model_installed ?? publicStatus.model_installed;
  const activeModel = runtime.model || (typeof publicStatus.model === 'string' ? publicStatus.model : '') || publicStatus.active_model || '';
  installedModels = runtime.installed_models || [];
  const hardware = doctor.system || publicStatus.hardware || {};
  const memory = hardware.memory_gb || hardware.total_memory_gb;
  const hardwareText = [hardware.os || hardware.system, hardware.architecture || hardware.arch, memory ? memory + 'GB 内存' : ''].filter(Boolean).join(' · ');

  $('#header-status').textContent = connected ? '本地模型服务已连接' : '证据抽取模式';
  $('#header-status').classList.toggle('ok', connected);
  $('#runtime-title').textContent = connected
    ? (installed ? '本地模型可用：' + activeModel : '运行时已连接，等待安装模型')
    : '当前使用零模型证据抽取';
  $('#runtime-detail').textContent = (hardwareText || '本机环境') + '。即使没有模型，证据检索、报告和审批仍可运行。';
  if (activeModel && [...select.options].some(option => option.value === activeModel)) select.value = activeModel;
  $('#model-pull-btn').disabled = !connected || !select.value;
  $('#model-pull-btn').textContent = installedModels.includes(select.value) ? '设为当前模型' : '下载并部署模型';
}

async function pullModel() {
  const model = $('#model-select').value;
  if (!model) return toast('请选择模型档位');
  if (installedModels.includes(model)) {
    try {
      await api('/api/models/select', {method:'POST', body:JSON.stringify({model})});
      toast('当前模型已切换');
      return loadPlatform();
    } catch (error) { return handleError(error); }
  }
  if (!window.confirm('将从模型仓库下载并部署 ' + model + '。文件可能占用数 GB 磁盘，是否继续？')) return;
  $('#model-pull-btn').disabled = true;
  $('#model-progress').classList.remove('hidden');
  $('#model-note').textContent = '正在部署 ' + model + '，请保持本机运行时在线……';
  try {
    const job = await api('/api/models/pull', {method:'POST', body:JSON.stringify({model})});
    await pollModelJob(job.job_id || job.id);
  } catch (error) {
    $('#model-pull-btn').disabled = false;
    handleError(error);
  }
}

async function pollModelJob(jobId) {
  const job = await api('/api/models/pull/' + encodeURIComponent(jobId));
  const percent = Number(job.percent ?? job.progress ?? 0);
  $('#model-progress-bar').style.width = Math.max(2, Math.min(100, percent)) + '%';
  $('#model-note').textContent = job.message || '模型部署中：' + Math.round(percent) + '%';
  if (['failed','error'].includes(job.status)) {
    $('#model-pull-btn').disabled = false;
    throw new Error(job.error || '模型部署失败');
  }
  if (['completed','complete','success','succeeded'].includes(job.status)) {
    $('#model-progress-bar').style.width = '100%';
    toast('本地模型部署完成');
    return loadPlatform();
  }
  setTimeout(() => pollModelJob(jobId).catch(handleError), 1000);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
}

document.querySelectorAll('.step').forEach(button => button.addEventListener('click', () => showStep(button.dataset.step)));
document.querySelectorAll('.next').forEach(button => button.addEventListener('click', () => showStep(button.dataset.next)));
$('#bootstrap-btn').addEventListener('click', () => submitBootstrap().catch(handleError));
$('#login-btn').addEventListener('click', () => submitLogin().catch(handleError));
$('#bootstrap-confirm').addEventListener('keydown', event => { if (event.key === 'Enter') submitBootstrap().catch(handleError); });
$('#login-password').addEventListener('keydown', event => { if (event.key === 'Enter') submitLogin().catch(handleError); });
$('#logout-btn').addEventListener('click', async () => {
  try { await api('/api/auth/logout', {method:'POST', body:'{}'}); } finally { csrfToken = ''; showAuth(true); }
});
$('#model-pull-btn').addEventListener('click', pullModel);
$('#model-select').addEventListener('change', () => {
  const runtime = platformStatus.ollama || platformStatus.runtime || {};
  const publicModel = typeof platformStatus.model === 'object' ? platformStatus.model : {};
  $('#model-pull-btn').disabled = !(runtime.connected ?? runtime.available ?? publicModel.available ?? platformStatus.ollama_available);
  $('#model-pull-btn').textContent = installedModels.includes($('#model-select').value) ? '设为当前模型' : '下载并部署模型';
});

$('#import-btn').addEventListener('click', async () => {
  try {
    await api('/api/documents', {method:'POST', body:JSON.stringify({name:$('#doc-name').value, content:$('#doc-content').value})});
    $('#doc-content').value = '';
    await loadDocuments();
    toast('材料已写入本机并完成切分');
  } catch (error) { handleError(error); }
});

$('#answer-btn').addEventListener('click', async () => {
  try {
    const data = await api('/api/answer', {method:'POST', body:JSON.stringify({question:$('#question').value})});
    const citations = data.citations.map((item, index) => '<div class="citation"><small>来源 ' + (index + 1) + ' · ' + escapeHtml(item.document_name) + ' · 第 ' + (item.position + 1) + ' 段</small><br>' + escapeHtml(item.content) + '</div>').join('');
    const mode = ['model','ollama'].includes(data.mode) ? '<span class="answer-mode">本地模型 · ' + escapeHtml(data.model || '') + '</span>' : '<span class="answer-mode">证据抽取</span>';
    $('#answer-result').innerHTML = mode + '<p>' + escapeHtml(data.answer).replaceAll('\n','<br>') + '</p>' + citations;
    if (data.warning) toast(data.warning);
    $('#to-report').classList.remove('hidden');
  } catch (error) { handleError(error); }
});

$('#report-btn').addEventListener('click', async () => {
  try {
    const data = await api('/api/reports', {method:'POST', body:JSON.stringify({title:$('#report-title').value, question:$('#question').value})});
    currentReportId = data.id;
    $('#report-result').textContent = data.content;
    $('#to-approve').classList.remove('hidden');
  } catch (error) { handleError(error); }
});

$('#confirmed').addEventListener('change', event => { $('#approve-btn').disabled = !event.target.checked; });
$('#approve-btn').addEventListener('click', async () => {
  if (!currentReportId) return toast('请先生成待审简报');
  try {
    const data = await api('/api/reports/' + currentReportId + '/approve', {method:'POST', body:JSON.stringify({reviewer:$('#reviewer').value})});
    $('#approve-result').innerHTML = '<strong>审批完成</strong><br>审批人：' + escapeHtml(data.reviewer) + '<br>时间：' + escapeHtml(data.approved_at);
    $('#approve-btn').disabled = true;
    await loadAudits();
    toast('审批记录已写入审计日志');
  } catch (error) { handleError(error); }
});

initializeAuth().catch(handleError);
