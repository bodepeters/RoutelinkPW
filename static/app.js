const state = { partners: [], partnerId: null, scan: null };
const $ = (id) => document.getElementById(id);

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('pricewatch-theme', theme);
  $('themeToggle').textContent = theme === 'light' ? '☾ Dark' : '☀ Light';
  $('themeToggle').setAttribute('aria-label', theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode');
  const logo = $('brandLogo');
  if (logo) logo.src = theme === 'dark' ? logo.dataset.darkLogo : logo.dataset.lightLogo;
}
function initTheme() {
  const saved = localStorage.getItem('pricewatch-theme');
  applyTheme(saved || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
}

function toast(message) { const el = $('toast'); el.textContent = message; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 3200); }
function initials(name) { return name.split(/\s+/).map(x => x[0]).join('').slice(0,2).toUpperCase(); }
function esc(value) { return String(value ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#039;'}[c])); }
function money(value) { return value == null ? '—' : '$' + Number(value).toFixed(2); }
function time(value) { return value ? new Date(value).toLocaleString([], {dateStyle:'medium', timeStyle:'short'}) : '—'; }

async function api(url, options) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Request failed'); return data; }

async function loadPartners() {
  const data = await api('/api/partners'); state.partners = data.partners;
  if (!state.partnerId && state.partners.length) state.partnerId = state.partners[0].id;
  renderPartners();
  if (state.partnerId) await selectPartner(state.partnerId);
}

function renderPartners() {
  $('partnerList').innerHTML = state.partners.map(p => {
    const count = p.latest_scan?.summary?.total_changes;
    return `<div class="partner ${p.id === state.partnerId ? 'active' : ''}" data-id="${p.id}"><div class="avatar">${esc(initials(p.name))}</div><div class="partner-info"><div class="partner-name">${esc(p.name)}</div><div class="partner-email">${esc(p.email || 'No source configured')}</div></div>${count != null ? `<div class="change-count">${count} change${count === 1 ? '' : 's'}</div>` : ''}</div>`;
  }).join('') || '<div class="empty">No partners yet.</div>';
  document.querySelectorAll('.partner').forEach(el => el.addEventListener('click', () => selectPartner(Number(el.dataset.id))));
}

async function selectPartner(id) {
  state.partnerId = id; state.scan = null; renderPartners();
  const p = state.partners.find(x => x.id === id); if (!p) return;
  $('partnerTitle').textContent = p.name;
  $('partnerMeta').textContent = p.email ? `Source: ${p.email} · Upload two files to compare pricing` : 'No source configured · Upload two files to compare pricing';
  $('notifyBtn').disabled = true; $('deletePartnerBtn').disabled = false; $('baselineFile').value = ''; $('currentFile').value = '';
  $('baselineName').textContent = 'Choose CSV file'; $('currentName').textContent = 'Choose CSV file'; $('runBtn').disabled = true;
  if (p.latest_scan) { try { await loadScan(p.latest_scan.id); } catch (e) { toast(e.message); } } else { renderEmpty(); }
  await loadActivity(id);
}

function renderStats(summary) {
  if (!summary) { $('stats').innerHTML = ''; return; }
  const items = [['Price increases', summary.price_increases, 'red'], ['Price decreases', summary.price_decreases, 'green'], ['Billing changes', summary.billing_changes, 'yellow'], ['Rows current', summary.rows_current, ''], ['Total changes', summary.total_changes, summary.total_changes ? 'red' : 'green']];
  $('stats').innerHTML = items.map(([label, value, cls]) => `<div class="stat"><label>${label}</label><strong class="${cls}">${value}</strong><small>latest comparison</small></div>`).join('');
}
function renderEmpty() { state.scan = null; renderStats(null); $('resultEmpty').classList.remove('hidden'); $('changesWrap').classList.add('hidden'); $('scanTime').textContent = 'No scan yet'; }
function fileLabel(input, target) { const file = input.files[0]; $(target).textContent = file ? file.name : 'Choose CSV file'; $('runBtn').disabled = !($('baselineFile').files[0] && $('currentFile').files[0]) || !state.partnerId; }

function typePill(change) {
  const type = change.type;
  if (type === 'added') return '<span class="pill added">ADDED</span>';
  if (type === 'removed') return '<span class="pill removed">REMOVED</span>';
  const bits = []; if (type.includes('price_up')) bits.push('<span class="pill up">PRICE ↑</span>'); if (type.includes('price_down')) bits.push('<span class="pill down">PRICE ↓</span>'); if (type.includes('billing')) bits.push('<span class="pill billing">BILLING</span>'); if (type.includes('product')) bits.push('<span class="pill billing">PRODUCT</span>'); return bits.join(' ');
}
function renderScan(data) {
  state.scan = data; renderStats(data.summary); $('resultEmpty').classList.toggle('hidden', data.changes.length > 0); $('changesWrap').classList.toggle('hidden', data.changes.length === 0); $('scanTime').textContent = `Scanned ${time(data.created_at)}`; $('notifyBtn').disabled = false;
  $('changesBody').innerHTML = data.changes.map(c => `<tr><td>${typePill(c)}</td><td>${esc(c.sku)}</td><td>${esc(c.product)}</td><td>${c.old ? money(c.old.price) + (c.old.billing_increment ? ' · ' + esc(c.old.billing_increment) : '') : '—'}</td><td>${c.new ? money(c.new.price) + (c.new.billing_increment ? ' · ' + esc(c.new.billing_increment) : '') : '—'}</td><td>${c.percent != null ? (c.delta >= 0 ? '+' : '') + c.percent + '%' : '—'}</td></tr>`).join('');
}
async function loadScan(id) { renderScan(await api('/api/scans/' + id)); }
async function loadActivity(id) { const data = await api(`/api/partners/${id}/activity`); $('activity').innerHTML = data.activity.length ? data.activity.map(a => `<div class="activity-row"><span class="activity-time">${time(a.created_at)}</span><span class="activity-level ${a.level === 'warn' ? 'warn' : ''}">${a.level === 'warn' ? '⚠' : '✓'}</span><span>${esc(a.message)}</span></div>`).join('') : '<div class="empty">No activity yet.</div>'; }

async function runScan() {
  const form = new FormData(); form.append('baseline', $('baselineFile').files[0]); form.append('current', $('currentFile').files[0]);
  $('runBtn').disabled = true; $('runStatus').textContent = 'Parsing and comparing…';
  try { const data = await api(`/api/partners/${state.partnerId}/scan`, {method:'POST', body:form}); renderScan(data); await loadActivity(state.partnerId); await loadPartners(); toast(`${data.summary.total_changes} change(s) detected`); } catch (e) { toast(e.message); } finally { $('runBtn').disabled = false; $('runStatus').textContent = ''; }
}
async function notifyPreview() { if (!state.scan) return; try { const data = await api(`/api/scans/${state.scan.id}/notify-preview`, {method:'POST'}); $('notifyContent').textContent = `TO: ${data.to || '(no recipient configured)'}\nSUBJECT: ${data.subject}\n\n${data.body}`; $('notifyDialog').showModal(); await loadActivity(state.partnerId); } catch(e) { toast(e.message); } }
async function sendMail() {
  if (!state.scan) return;
  try {
    const data = await api(`/api/scans/${state.scan.id}/send-mail`, {method:'POST'});
    const params = new URLSearchParams({subject: data.subject, body: data.body});
    window.location.href = `mailto:${encodeURIComponent(data.to)}?${params.toString()}`;
    $('notifyDialog').close();
    await loadActivity(state.partnerId);
    toast('Mail draft opened');
  } catch (e) { toast(e.message); }
}
function openDeleteDialog() {
  const partner = state.partners.find(p => p.id === state.partnerId);
  if (!partner) return;
  $('deleteMessage').textContent = `Delete ${partner.name}? This will permanently remove the partner, scan history, and uploaded files.`;
  $('deleteDialog').showModal();
}
async function deletePartner() {
  const partner = state.partners.find(p => p.id === state.partnerId);
  if (!partner) return;
  $('confirmDelete').disabled = true;
  try {
    await api(`/api/partners/${partner.id}`, {method:'DELETE'});
    $('deleteDialog').close();
    state.partnerId = null; state.scan = null;
    $('deletePartnerBtn').disabled = true; $('notifyBtn').disabled = true;
    await loadPartners();
    if (!state.partnerId) {
      $('partnerTitle').textContent = 'Select a partner';
      $('partnerMeta').textContent = 'Create a partner or select one from the list.';
      renderEmpty(); $('activity').innerHTML = '<div class="empty">Select a partner to load activity.</div>';
    }
    toast('Partner deleted');
  } catch (e) { toast(e.message); }
  finally { $('confirmDelete').disabled = false; }
}

$('baselineFile').addEventListener('change', e => fileLabel(e.target, 'baselineName'));
$('currentFile').addEventListener('change', e => fileLabel(e.target, 'currentName'));
$('runBtn').addEventListener('click', runScan); $('notifyBtn').addEventListener('click', notifyPreview); $('sendMailBtn').addEventListener('click', sendMail); $('deletePartnerBtn').addEventListener('click', openDeleteDialog); $('confirmDelete').addEventListener('click', deletePartner);
$('closeNotify').addEventListener('click', () => $('notifyDialog').close()); $('cancelNotify').addEventListener('click', () => $('notifyDialog').close());
$('themeToggle').addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'));
$('newPartnerBtn').addEventListener('click', () => $('partnerDialog').showModal());
$('closePartner').addEventListener('click', () => $('partnerDialog').close()); $('cancelPartner').addEventListener('click', () => $('partnerDialog').close()); $('cancelDelete').addEventListener('click', () => $('deleteDialog').close());
$('partnerForm').addEventListener('submit', async e => { e.preventDefault(); const form = new FormData(e.target); try { const p = await api('/api/partners', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:form.get('name'), email:form.get('email')})}); $('partnerDialog').close(); e.target.reset(); state.partnerId = p.id; await loadPartners(); toast('Partner created'); } catch(err) { toast(err.message); } });
initTheme();
loadPartners().catch(e => toast(e.message));
