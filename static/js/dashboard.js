const state = {
    uiModel: null,
    panels: new Map(),
    timers: new Map(),
    lastUpdated: new Map(),
};

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
        const text = await response.text();
        throw new Error(`${response.status} ${response.statusText}: ${text}`);
    }
    return response.json();
}

function setStatus(message, variant = 'info') {
    const banner = document.querySelector('#status-banner');
    if (!banner) return;
    banner.textContent = message;
    banner.dataset.variant = variant;
    banner.className = `status-banner ${variant}`;
    if (message) {
        banner.removeAttribute('hidden');
    } else {
        banner.setAttribute('hidden', 'hidden');
    }
}

function renderLogicProgress(logic) {
    const container = document.querySelector('.logic-steps');
    if (!container) return;
    container.innerHTML = '';

    const order = logic?.order ?? [];
    const rules = logic?.rules ?? {};

    order.forEach((key, index) => {
        const rule = rules[key] || {};
        const step = document.createElement('div');
        step.className = 'logic-step';
        step.dataset.order = index + 1;
        step.innerHTML = `
            <h3>${key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</h3>
            <p>${rule.description || 'No description provided.'}</p>
            ${rule.depends_on ? `<p><strong>Depends:</strong> ${rule.depends_on.join(', ')}</p>` : ''}
        `;
        container.appendChild(step);
    });
}

function buildNavigation(uiModel) {
    const nav = document.querySelector('.tab-navigation');
    const panelsContainer = document.querySelector('#sections');
    nav.innerHTML = '';
    panelsContainer.innerHTML = '';

    const sectionsById = new Map(uiModel.sections.map(section => [section.id, section]));
    const orderedIds = uiModel.logic?.order?.length ? uiModel.logic.order : uiModel.sections.map(s => s.id);

    orderedIds.forEach((sectionId, index) => {
        const section = sectionsById.get(sectionId);
        if (!section) return;

        const button = document.createElement('button');
        button.textContent = section.label;
        button.dataset.section = section.id;
        if (index === 0) {
            button.classList.add('active');
        }
        button.addEventListener('click', () => activateSection(section.id));
        nav.appendChild(button);

        const panel = document.createElement('section');
        panel.className = `section-panel${index === 0 ? ' active' : ''}`;
        panel.dataset.section = section.id;
        panel.innerHTML = `
            <div class="section-header">
                <div>
                    <h2>${section.label}</h2>
                    <p>${section.description || ''}</p>
                </div>
                <div class="controls">
                    <button type="button" data-action="refresh" data-section="${section.id}">Refresh</button>
                    <span class="last-updated" data-section="${section.id}"></span>
                </div>
            </div>
            <div class="section-body" data-role="body"></div>
        `;
        panelsContainer.appendChild(panel);
        state.panels.set(section.id, panel);
    });

    panelsContainer.addEventListener('click', (event) => {
        const { target } = event;
        if (target.matches('button[data-action="refresh"]')) {
            const sectionId = target.dataset.section;
            loadSection(sectionId, { force: true });
        }
    });
}

function activateSection(sectionId) {
    document.querySelectorAll('.tab-navigation button').forEach(button => {
        button.classList.toggle('active', button.dataset.section === sectionId);
    });
    document.querySelectorAll('.section-panel').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.section === sectionId);
    });
    loadSection(sectionId, { immediate: true });
}

function scheduleSectionRefresh(section, refreshSeconds) {
    if (state.timers.has(section.id)) {
        clearInterval(state.timers.get(section.id));
    }
    if (!refreshSeconds) {
        return;
    }
    const timer = setInterval(() => loadSection(section.id), refreshSeconds * 1000);
    state.timers.set(section.id, timer);
}

async function loadSection(sectionId, options = {}) {
    const section = state.uiModel.sections.find(item => item.id === sectionId);
    if (!section) return;

    const now = Date.now();
    const last = state.lastUpdated.get(sectionId) || 0;
    if (!options.force && !options.immediate && now - last < 15000) {
        return;
    }

    const panel = state.panels.get(sectionId);
    if (!panel) return;

    const body = panel.querySelector('.section-body');
    body.innerHTML = '<p class="empty-state">Loading…</p>';

    try {
        let payload;
        switch (section.id) {
            case 'overview': {
                const [stats, feed, trends] = await Promise.all([
                    fetchJson(`${section.endpoint}`),
                    fetchJson('/api/feed'),
                    fetchJson('/api/trends'),
                ]);
                payload = { stats, feed, trends };
                renderOverview(payload, body);
                break;
            }
            case 'documents': {
                const documents = await fetchJson(`${section.endpoint}?limit=25`);
                payload = documents;
                renderDocuments(payload, body);
                break;
            }
            case 'alerts': {
                const alerts = await fetchJson(`${section.endpoint}?sent=false`);
                payload = alerts;
                renderAlerts(payload, body);
                break;
            }
            case 'members': {
                const members = await fetchJson(section.endpoint);
                payload = members;
                renderMembers(payload, body);
                break;
            }
            case 'committees': {
                const committees = await fetchJson(section.endpoint);
                payload = committees;
                renderCommittees(payload, body);
                break;
            }
            case 'watchlist': {
                const keywords = await fetchJson(section.endpoint);
                payload = keywords;
                renderWatchlist(payload, body);
                break;
            }
            case 'reports': {
                const trends = await fetchJson('/api/trends');
                payload = trends;
                renderReports(payload, body);
                break;
            }
            default: {
                const generic = await fetchJson(section.endpoint);
                payload = generic;
                renderGeneric(payload, body);
            }
        }
        state.lastUpdated.set(sectionId, now);
        const stamp = panel.querySelector(`.last-updated[data-section="${sectionId}"]`);
        if (stamp) {
            const formatted = new Date().toLocaleTimeString();
            stamp.textContent = `Updated ${formatted}`;
        }
    } catch (error) {
        console.error(`Failed to load section ${sectionId}`, error);
        body.innerHTML = `<div class="error-banner">${error.message}</div>`;
    }
}

function renderOverview({ stats, feed, trends }, container) {
    const totals = stats?.watching || {};
    const activeAlerts = stats?.active_alerts || {};

    container.innerHTML = `
        <div class="stat-grid">
            <div class="stat-card">
                <h3>New Documents Today</h3>
                <strong>${stats?.new_today ?? 0}</strong>
                <span>Total alerts: ${stats?.total_alerts ?? 0}</span>
            </div>
            <div class="stat-card">
                <h3>Active Critical Alerts</h3>
                <strong>${activeAlerts.critical ?? 0}</strong>
                <span>High priority: ${activeAlerts.high ?? 0}</span>
            </div>
            <div class="stat-card">
                <h3>Keywords Tracked</h3>
                <strong>${totals.keywords ?? 0}</strong>
                <span>Members: ${totals.members ?? 0} · Committees: ${totals.committees ?? 0}</span>
            </div>
        </div>
        <div class="stat-grid" style="margin-top:24px;">
            <div class="stat-card" style="grid-column: span 2;">
                <h3>Document Trend (7 days)</h3>
                <div id="trend-sparkline" style="height:80px;"></div>
            </div>
            <div class="stat-card">
                <h3>Top Keywords</h3>
                <div id="top-keywords"></div>
            </div>
        </div>
        <h3 style="margin:32px 0 16px;">Recent Activity</h3>
        <div class="activity-feed"></div>
    `;

    renderTrendSparkline(trends?.daily || {}, container.querySelector('#trend-sparkline'));
    renderTopKeywords(trends?.top_keywords || [], container.querySelector('#top-keywords'));
    renderActivityFeed(feed || [], container.querySelector('.activity-feed'));
}

function renderTrendSparkline(daily, container) {
    if (!container) return;
    const entries = Object.entries(daily);
    if (!entries.length) {
        container.innerHTML = '<div class="empty-state">No trend data available.</div>';
        return;
    }
    const max = Math.max(...entries.map(([, value]) => value.total || 0));
    const bars = entries
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([date, value]) => {
            const height = max ? Math.max(6, (value.total / max) * 80) : 6;
            return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;">
                <span style="font-size:11px;color:var(--muted);">${new Date(date).toLocaleDateString(undefined, { day: '2-digit', month: 'short' })}</span>
                <div style="width:100%;background:rgba(0,77,61,0.12);border-radius:8px;height:${height}px"></div>
            </div>`;
        })
        .join('');
    container.innerHTML = `<div style="display:flex;align-items:flex-end;gap:10px;height:100%;">${bars}</div>`;
}

function renderTopKeywords(keywords, container) {
    if (!container) return;
    if (!keywords.length) {
        container.innerHTML = '<div class="empty-state">No keyword hits recorded.</div>';
        return;
    }
    container.innerHTML = keywords
        .map(([keyword, count]) => `<div class="badge">${keyword} (${count})</div>`)
        .join('');
}

function renderActivityFeed(items, container) {
    if (!container) return;
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No recent activity.</div>';
        return;
    }
    container.innerHTML = items
        .map(item => `
            <div class="activity-item">
                <strong>${item.title}</strong>
                <small>${item.chamber} · ${item.type}</small>
                <span>${item.description}</span>
                <small>${item.time}</small>
            </div>
        `)
        .join('');
}

function renderDocuments(data, container) {
    const documents = data?.documents || [];
    if (!documents.length) {
        container.innerHTML = '<div class="empty-state">No documents captured in this window.</div>';
        return;
    }
    container.innerHTML = `
        <div class="list">
            ${documents
                .map(doc => `
                    <article class="list-item">
                        <h3>${doc.title}</h3>
                        <div class="meta">
                            <span class="badge">${doc.type}</span>
                            <span>${doc.chamber || 'N/A'}</span>
                            <span>${doc.date_published || doc.date_discovered || ''}</span>
                        </div>
                        <p>${doc.description || 'No description available.'}</p>
                        ${doc.keywords?.length ? `<div>${doc.keywords.map(kw => `<span class="badge">${kw}</span>`).join('')}</div>` : ''}
                        <a href="${doc.url}" target="_blank" rel="noopener">Open document →</a>
                    </article>
                `)
                .join('')}
        </div>
    `;
}

function renderAlerts(data, container) {
    const alerts = data?.alerts || [];
    if (!alerts.length) {
        container.innerHTML = '<div class="empty-state">No active alerts.</div>';
        return;
    }
    container.innerHTML = `
        <div class="list">
            ${alerts
                .map(alert => `
                    <article class="list-item">
                        <div class="meta">
                            <span class="badge ${alert.level}">${alert.level}</span>
                            <span>${alert.chamber || 'N/A'}</span>
                            <span>${alert.date_created}</span>
                        </div>
                        <h3>${alert.title}</h3>
                        <p>${alert.description || 'No description provided.'}</p>
                        <p><strong>Keywords:</strong> ${alert.keywords_matched || 'None'}</p>
                        <a href="${alert.document_url}" target="_blank" rel="noopener">View document →</a>
                    </article>
                `)
                .join('')}
        </div>
    `;
}

function renderMembers(data, container) {
    const members = data?.members || [];
    if (!members.length) {
        container.innerHTML = '<div class="empty-state">No member records available.</div>';
        return;
    }
    container.innerHTML = `
        <div class="list">
            ${members
                .map(member => `
                    <article class="list-item">
                        <h3>${member.name}</h3>
                        <div class="meta">
                            <span>${member.chamber}</span>
                            <span>${member.party || 'Independent'}</span>
                            <span>${member.role || ''}</span>
                        </div>
                        ${member.portfolios?.length ? `<p><strong>Portfolios:</strong> ${member.portfolios.join(', ')}</p>` : ''}
                        ${member.committees?.length ? `<p><strong>Committees:</strong> ${member.committees.join(', ')}</p>` : ''}
                    </article>
                `)
                .join('')}
        </div>
    `;
}

function renderCommittees(data, container) {
    const committees = data?.committees || [];
    if (!committees.length) {
        container.innerHTML = '<div class="empty-state">No committee updates available.</div>';
        return;
    }
    container.innerHTML = `
        <div class="list">
            ${committees
                .map(committee => `
                    <article class="list-item">
                        <h3>${committee.name}</h3>
                        <div class="meta">
                            <span>${committee.chamber}</span>
                            <span>${committee.status}</span>
                            <span>${committee.type || ''}</span>
                        </div>
                        <p>${committee.description || 'No summary provided.'}</p>
                        ${committee.inquiries?.length ? `<p><strong>Current inquiries:</strong> ${committee.inquiries.join(', ')}</p>` : ''}
                    </article>
                `)
                .join('')}
        </div>
    `;
}

function renderWatchlist(data, container) {
    const keywords = data?.keywords || [];
    const categories = new Map();
    keywords.forEach(item => {
        if (!categories.has(item.category)) {
            categories.set(item.category, []);
        }
        categories.get(item.category).push(item.keyword);
    });

    const form = document.createElement('form');
    form.className = 'watchlist-form';
    form.innerHTML = `
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px;">
            <input type="text" name="keyword" placeholder="Add keyword" required style="flex:2;min-width:180px;padding:10px;border-radius:10px;border:1px solid var(--border);">
            <input type="text" name="category" placeholder="Category" style="flex:1;min-width:140px;padding:10px;border-radius:10px;border:1px solid var(--border);">
            <button type="submit" style="background:var(--secondary);color:white;border:none;border-radius:10px;padding:10px 16px;cursor:pointer;">Add</button>
        </div>
    `;

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(form);
        const keyword = formData.get('keyword');
        const category = formData.get('category') || 'custom';
        try {
            await fetchJson('/api/keywords', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keyword, category }),
            });
            setStatus(`Keyword "${keyword}" added to ${category}.`, 'success');
            form.reset();
            loadSection('watchlist', { force: true });
        } catch (error) {
            setStatus(error.message, 'error');
        }
    });

    const grid = document.createElement('div');
    grid.className = 'keywords-grid';

    Array.from(categories.entries()).forEach(([category, words]) => {
        const card = document.createElement('article');
        card.className = 'keyword-card';
        card.innerHTML = `
            <h3>${category.replace(/_/g, ' ')}</h3>
            <ul>
                ${words
                    .map(word => `<li>${word}<button type="button" data-keyword="${word}" data-category="${category}">×</button></li>`)
                    .join('')}
            </ul>
        `;
        grid.appendChild(card);
    });

    grid.addEventListener('click', async (event) => {
        const button = event.target.closest('button[data-keyword]');
        if (!button) return;
        const keyword = button.dataset.keyword;
        const category = button.dataset.category;
        try {
            await fetchJson('/api/keywords', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keyword, category }),
            });
            setStatus(`Removed keyword "${keyword}" from ${category}.`, 'success');
            loadSection('watchlist', { force: true });
        } catch (error) {
            setStatus(error.message, 'error');
        }
    });

    container.innerHTML = '';
    container.appendChild(form);
    container.appendChild(grid);
}

function renderReports(data, container) {
    const keywords = Object.entries(data?.keywords || {});
    container.innerHTML = `
        <div class="reports-grid">
            <article class="report-card">
                <h3>Keyword Momentum</h3>
                ${keywords.length ? `<p>${keywords.slice(0, 10).map(([kw, count]) => `<span class="badge">${kw} (${count})</span>`).join(' ')}</p>` : '<p class="empty-state">No keyword activity in the last 30 days.</p>'}
            </article>
            <article class="report-card">
                <h3>On-demand Export</h3>
                <p>Generate a JSON or CSV export for sharing with business stakeholders.</p>
                <div style="display:flex;gap:12px;flex-wrap:wrap;">
                    <button type="button" data-export="json">Download JSON</button>
                    <button type="button" data-export="csv">Download CSV</button>
                </div>
            </article>
        </div>
    `;

    container.querySelectorAll('button[data-export]').forEach(button => {
        button.addEventListener('click', () => {
            const format = button.dataset.export;
            window.open(`/api/export?format=${format}`, '_blank');
        });
    });
}

function renderGeneric(data, container) {
    container.innerHTML = `<pre style="overflow:auto;background:rgba(15,23,42,0.05);padding:16px;border-radius:12px;">${JSON.stringify(data, null, 2)}</pre>`;
}

function setupSyncButton() {
    const syncButton = document.querySelector('#sync-now');
    if (!syncButton) return;
    syncButton.addEventListener('click', async () => {
        try {
            syncButton.disabled = true;
            syncButton.textContent = 'Syncing…';
            const response = await fetchJson('/api/sync', { method: 'POST' });
            setStatus(`Sync complete. ${response.new_documents} new documents detected.`, 'success');
            state.panels.forEach((_, sectionId) => loadSection(sectionId, { force: true }));
        } catch (error) {
            setStatus(`Sync failed: ${error.message}`, 'error');
        } finally {
            syncButton.disabled = false;
            syncButton.textContent = 'Run Sync';
        }
    });
}

async function initialiseDashboard() {
    try {
        setStatus('Loading dashboard model…', 'info');
        const uiModel = await fetchJson('/api/ui-model');
        state.uiModel = uiModel;
        renderLogicProgress(uiModel.logic);
        buildNavigation(uiModel);
        setupSyncButton();
        state.timers.forEach(timer => clearInterval(timer));
        state.timers.clear();
        uiModel.sections.forEach(section => scheduleSectionRefresh(section, section.refresh_seconds || uiModel.refresh_seconds));
        const initialSection = uiModel.logic?.order?.[0] || uiModel.sections?.[0]?.id;
        if (initialSection) {
            activateSection(initialSection);
        }
        setStatus('Dashboard ready.', 'success');
    } catch (error) {
        console.error(error);
        setStatus(`Failed to load dashboard model: ${error.message}`, 'error');
    }
}

document.addEventListener('DOMContentLoaded', initialiseDashboard);
