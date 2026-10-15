'use strict';
const t = ToolbeltI18n.text, message = ToolbeltI18n.message;
const $ = id => document.getElementById(id);
const token = location.hash.slice(1);
let state = null, busy = false, form = null, rendered = '', transportFailed = false, reportedStatus = '';
const safeText = (tag, text, className) => {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className; return node;
};
function error(message) { $('error').textContent = ToolbeltI18n.message(message); $('error').hidden = !message; }
async function request(path, payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), payload ? 30000 : 7000);
  try {
    const response = await fetch(path, {
      method: payload ? 'POST' : 'GET', mode:'same-origin', referrerPolicy:'same-origin', credentials: 'omit', cache: 'no-store',
      headers: {Authorization: 'Bearer ' + token, ...(payload ? {'Content-Type': 'application/json'} : {})},
      ...(payload ? {body: JSON.stringify(payload)} : {}), signal: controller.signal
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || t("请求失败，请刷新检查。"));
    return result;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(payload ? t("操作超时，结果可能未知。请刷新确认，勿重复操作。") : t("状态读取超时，未执行任何操作。日志：~/Library/Logs/iterm2-tmux-toolbelt/toolbelt.log"));
    throw e;
  } finally { clearTimeout(timeout); }
}
function controls() {
  const online = state && state.status === 'online';
  $('setup-install').disabled = busy || !state?.context || !state?.setup?.available;
  $('create').disabled = busy || !online;
  $('detach').disabled = busy || !online || (state.target?.mode === 'ssh' && !state.target.attached);
  $('socket-set').disabled = busy || !state?.context || state?.target?.attached;
  $('socket-choice').disabled = busy || state?.target?.attached;
  $('socket-path').disabled = busy || state?.target?.attached;
  $('refresh').disabled = busy;
  $('list').querySelectorAll('button').forEach(button => {button.disabled = busy || !online;});
  $('submit').disabled = busy || !online || !form || form.context !== state.context;
  $('field').disabled = busy;
}
function closeForm() { form = null; $('form').hidden = true; }
function render(next) {
  if (state && next.context !== state.context) $('feedback').textContent = '';
  state = next;
  const mode = next.appearance?.mode;
  if (mode === 'dark' || mode === 'light') document.documentElement.dataset.appearance = mode;
  else delete document.documentElement.dataset.appearance;
  document.documentElement.dataset.contrast = next.appearance?.contrast ? 'high' : 'normal';
  if (form && (next.context !== form.context || next.status !== 'online')) {
    closeForm(); error(t("目标连接或活动 pane 已改变，未提交的操作已取消。"));
  }
  const encoded = JSON.stringify(next);
  if (encoded !== rendered) {
    rendered = encoded;
    $('status').textContent = next.status === 'online' ? t("在线") : next.status === 'unsupported' ? t("待接入") : next.transportFailed ? t("脚本离线") : next.recovering ? t("等待恢复") : t("tmux 离线");
    $('status').className = 'status ' + next.status;
    $('notice').textContent = message(next.message);
    $('setup').hidden = !next.setup || next.setup.kind === 'ready';
    $('setup-message').textContent = message(next.setup?.message);
    $('setup-install').hidden = !next.setup?.available;
    $('setup-install').textContent = message(next.setup?.button) || t("一键配置远端集成");
    $('host').textContent = next.target ? next.target.host : t("没有可操作的目标");
    $('pane').textContent = next.target ? `Tab ${next.target.tab} · Pane ${next.target.pane.slice(0, 8)}` : t("跟随活动 SSH / tmux -CC pane");
    $('socket-control').hidden = next.target?.mode !== 'ssh';
    $('socket-choice').replaceChildren();
    if (next.target?.mode === 'ssh') {
      $('socket-choice').append(new Option(t("选择远端 socket…"),''));
      for (const socket of next.target.sockets) $('socket-choice').append(new Option(socket,socket));
      $('socket-choice').value = next.target.selected_socket || '';
      if (document.activeElement !== $('socket-path')) $('socket-path').value = next.target.selected_socket || '';
    }
    $('details').replaceChildren();
    if (next.target) {
      for (const [label, value] of [['Socket', next.target.socket], [t("服务器 PID"), next.target.server_pid], [t("控制客户端"), next.target.client], [t("连接 ID"), next.target.connection], ['Pane ID', next.target.pane]]) {
        $('details').append(safeText('dt', label), safeText('dd', value));
      }
    }
    $('list').replaceChildren();
    for (const row of next.sessions) {
      const card = safeText('div', '', 'session' + (row.current ? ' current' : ''));
      card.setAttribute('role', 'listitem');
      card.dataset.sessionId = row.id; card.dataset.sessionName = row.name;
      const select = safeText('button', '', 'select');
      select.title = row.name;
      select.setAttribute('aria-label', t('切换到 {name} ({id})', {name:row.name,id:row.id}));
      select.append(safeText('span', row.name + (row.current ? t(" · 当前") : ''), 'name'));
      select.append(safeText('span', t('{id} · {windows} 窗口 · {attached} 连接', {...row, windowsLabel:row.windows===1?'window':'windows', clientsLabel:row.attached===1?'client':'clients'}), 'meta'));
      select.addEventListener('click', () => run({...base(row), action: 'switch'}));
      const actions = safeText('div', '', 'actions');
      const rename = safeText('button', t("改名")); rename.setAttribute('aria-label', t('重命名 {name}', row));
      rename.addEventListener('click', event => {event.stopPropagation(); openForm('rename', row);});
      const remove = safeText('button', t("删除"), 'danger'); remove.setAttribute('aria-label', t('删除 {name}', row));
      remove.title = t('直接删除 {name} ({id})，终止其中所有任务，无法恢复', row);
      remove.addEventListener('click', event => {event.preventDefault(); event.stopPropagation(); run({...base(row), action:'delete'});});
      actions.append(rename, remove); card.append(select, actions); $('list').append(card);
    }
    $('empty').hidden = next.sessions.length > 0;
    $('empty').textContent = next.status === 'online' ? t("此 tmux server 暂无 sessions。点击新建。") : next.status === 'unsupported' ? t("连接普通 SSH 后自动发现；多 socket 请在连接详情中选择。")  : t("连接不可用，操作已禁用。重新连接后自动刷新。");
  }
  controls();
}
function base(row) { return {context: state.context, ...(row ? {id: row.id, created: row.created} : {})}; }
function openForm(action, row) {
  if (!state || state.status !== 'online' || busy) return;
  closeForm();
  form = {...base(row), action, row};
  $('form-title').textContent = action === 'create' ? t("新建 session") : t("重命名 session");
  $('form-description').textContent = action === 'rename' ? t('目标：{name} ({id})', row) : t("在上方显示的主机与 socket 中新建一个 detached session。");
  $('field-label').textContent = t("Session 名称");
  $('field').value = action === 'rename' ? row.name : '';
  $('field').maxLength = 64;
  $('hint').textContent = t("1–64 个字母、数字、中文、下划线或短横线。");
  $('submit').textContent = action === 'create' ? t("创建") : t("保存名称");
  $('submit').className = '';
  $('form').hidden = false; controls(); $('field').focus();
}
async function refresh() {
  try { const next = await request('/api/state'); if (transportFailed) error(''); transportFailed = false; render(next); const styleReady = getComputedStyle(document.documentElement).getPropertyValue('--toolbelt-style').trim() === next.ui_revision; if (!styleReady) error(t("界面样式未刷新，请重新打开本工具。")); if (styleReady && next.ui_revision && next.status !== reportedStatus) {try {await request('/api/ui-status', {status:next.status, revision:next.ui_revision}); reportedStatus = next.status;} catch (e) {error(t("页面已连接，但操作通道校验失败：") + message(e.message));}} }
  catch (e) { transportFailed = true; render({status:'offline', transportFailed:true, message:t("面板无法连接本地脚本。请重新打开本工具，或在 Scripts Console 重启本脚本。"), context:null, target:null, sessions:[]}); error(e.message); }
}
async function run(payload) {
  if (busy) return; busy = true; controls(); error('');
  try { const result = await request('/api/action', payload); if (payload.action.startsWith('setup')) $('feedback').textContent = message(result.message); closeForm(); }
  catch (e) { closeForm(); error(e.message); }
  finally { busy = false; await refresh(); controls(); }
}
$('setup-install').addEventListener('click', () => run({...base(), action:state.setup?.action || 'setup'}));
$('detach').addEventListener('click', () => run({...base(), action:'detach'}));
$('socket-choice').addEventListener('change', () => {if ($('socket-choice').value) $('socket-path').value=$('socket-choice').value;});
$('socket-set').addEventListener('click', () => run({...base(), action:'socket', socket:$('socket-path').value}));
$('create').addEventListener('click', () => openForm('create'));
$('refresh').addEventListener('click', async () => {if (state && state.status === 'offline' && !state.transportFailed) await run({action:'reconnect'}); else await refresh();});
$('cancel').addEventListener('click', closeForm);
$('submit').addEventListener('click', () => {
  if (!form) return;
  const payload = {context:form.context, action:form.action};
  if (form.id) {payload.id = form.id; payload.created = form.created;}
  payload.name = $('field').value;
  run(payload);
});
$('field').addEventListener('keydown', event => {if (event.key === 'Escape') closeForm();});
async function poll() { if (!busy) await refresh(); setTimeout(poll, 1500); }
if (!token) error(t("缺少访问令牌，请从 iTerm2 Toolbelt 打开本面板。"));
poll();
