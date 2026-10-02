/**
 * NEC Protocol Workbench — Unified HTTP & Modbus TCP SCADA Dashboard
 */

// Application State
const App = {
    ws: null,
    wsConnected: false,
    reconnectTimer: null,
    reconnectInterval: 1000,

    state: {
        coils: [],
        registers: [],
        stats: {},
        master_config: {},
        server_info: {}
    },

    logs: [],
    activeTab: 'tab-http',
    activeLogFilter: 'ALL',
    searchQuery: '',
    autoScroll: true,

    // HTTP Poller client-side state
    http: {
        active: false,
        timer: null,
        interval: 1.0,
        method: 'GET',
        serverSize: 'small',
        clientSize: 'small',
        keepAlive: true,
        txCount: 0,
        txBytes: 0,
        rxCount: 0,
        rxBytes: 0,
        lastRtt: 0
    },

    // SCADA Client Poller client-side state
    scada: {
        active: false,
        timer: null,
        interval: 1.0,
        fc: 3
    },

    focusedInputId: null
};

// ==========================================================================
// Utility Functions
// ==========================================================================

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.remove('hidden');
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => {
        toast.classList.add('hidden');
    }, 2400);
}

function copyToClipboard(text, label) {
    navigator.clipboard.writeText(text).then(() => {
        showToast(`Copied ${label || 'filter'}: ${text}`);
    }).catch(() => {
        showToast(`Filter: ${text}`);
    });
}

function generatePadding(bytes) {
    return 'Z'.repeat(Math.max(0, bytes));
}

// ==========================================================================
// WebSocket Real-time Feed
// ==========================================================================

function initWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws/events`;

    App.ws = new WebSocket(wsUrl);

    App.ws.onopen = () => {
        App.wsConnected = true;
        App.reconnectInterval = 1000;
        document.getElementById('ws-indicator').classList.remove('disconnected');
        document.getElementById('ws-status-text').textContent = 'Live Feed Connected';
    };

    App.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'init') {
                App.state = data.state;
                App.logs = data.logs || [];
                renderFullState();
                renderAllLogs();
            } else if (data.type === 'state') {
                App.state = data.payload;
                renderFullState();
            } else if (data.type === 'log') {
                handleNewLogEntry(data.entry);
            }
        } catch (err) {
            console.error('Error parsing WS message:', err);
        }
    };

    App.ws.onclose = () => {
        App.wsConnected = false;
        document.getElementById('ws-indicator').classList.add('disconnected');
        document.getElementById('ws-status-text').textContent = 'Feed Reconnecting...';

        clearTimeout(App.reconnectTimer);
        App.reconnectTimer = setTimeout(() => {
            App.reconnectInterval = Math.min(App.reconnectInterval * 1.5, 5000);
            initWebSocket();
        }, App.reconnectInterval);
    };

    App.ws.onerror = () => {
        App.ws && App.ws.close();
    };
}

// ==========================================================================
// Rendering: Full State & UI
// ==========================================================================

function renderFullState() {
    const s = App.state;

    // Header Info
    if (s.server_info) {
        const httpPortEl = document.getElementById('hdr-http-port');
        const modbusPortEl = document.getElementById('hdr-modbus-port');
        const lanIpEl = document.getElementById('hdr-lan-ip');

        if (httpPortEl) httpPortEl.textContent = s.server_info.http_port || 8050;
        if (modbusPortEl) modbusPortEl.textContent = s.server_info.modbus_port || 1502;
        if (lanIpEl) lanIpEl.textContent = s.server_info.primary_ip || '127.0.0.1';
    }

    // Modbus KPIs
    if (s.stats) {
        document.getElementById('mb-stat-transactions').textContent = s.stats.modbus_transactions || 0;
        document.getElementById('mb-stat-active-conn').textContent = s.stats.modbus_active_connections || 0;
        const totalMbBytes = (s.stats.modbus_bytes_tx || 0) + (s.stats.modbus_bytes_rx || 0);
        document.getElementById('mb-stat-bytes').textContent = formatBytes(totalMbBytes);
        document.getElementById('mb-stat-opened').textContent = s.stats.modbus_connections_opened || 0;

        const clientList = s.stats.modbus_connected_clients || [];
        document.getElementById('mb-stat-connected-clients').textContent = `${clientList.length} connected client(s)`;

        // Connected clients list box (only update DOM if changed)
        const clientsBox = document.getElementById('mb-connected-clients-list');
        if (clientsBox) {
            const newHtml = clientList.length === 0
                ? '<span class="tag-empty">No external LAN clients currently connected</span>'
                : clientList.map(c => `<span class="client-tag">${c}</span>`).join('');
            if (clientsBox.innerHTML !== newHtml) {
                clientsBox.innerHTML = newHtml;
            }
        }
    }

    // Coils Table (in-place update)
    renderCoilsTable(s.coils || []);

    // Registers Table (in-place update)
    renderRegistersTable(s.registers || []);

    // Modbus Poller Controls
    if (s.master_config) {
        const cfg = s.master_config;
        const pollerActive = !!cfg.polling_active;
        const btnToggle = document.getElementById('mb-btn-toggle-poller');
        const badgePoller = document.getElementById('mb-badge-poller');

        if (btnToggle && badgePoller) {
            if (pollerActive) {
                btnToggle.textContent = 'Pause Master Poller';
                btnToggle.className = 'btn btn-secondary btn-block';
                badgePoller.textContent = 'Poller Active';
                badgePoller.className = 'badge badge-active';
            } else {
                btnToggle.textContent = 'Resume Master Poller';
                btnToggle.className = 'btn btn-primary btn-block';
                badgePoller.textContent = 'Poller Paused';
                badgePoller.className = 'badge badge-inactive';
            }
        }

        // Never overwrite inputs if the user is currently focused on them or editing them
        const txtTarget = document.getElementById('mb-txt-target');
        if (txtTarget && document.activeElement !== txtTarget && !txtTarget.dataset.userEdited) {
            txtTarget.value = cfg.target_host || '127.0.0.1';
        }

        const numPort = document.getElementById('mb-num-port');
        if (numPort && document.activeElement !== numPort && !numPort.dataset.userEdited) {
            numPort.value = cfg.target_port || 1502;
        }

        const rngInterval = document.getElementById('mb-rng-interval');
        const lblInterval = document.getElementById('mb-lbl-interval');
        if (rngInterval && document.activeElement !== rngInterval) {
            rngInterval.value = cfg.interval || 1.0;
            if (lblInterval) lblInterval.textContent = `${(cfg.interval || 1.0).toFixed(1)} s`;
        }

        const selFc = document.getElementById('mb-sel-fc');
        if (selFc && document.activeElement !== selFc) {
            selFc.value = cfg.function_code || 3;
        }

        const selConnMode = document.getElementById('mb-sel-conn-mode');
        if (selConnMode && document.activeElement !== selConnMode) {
            selConnMode.value = cfg.connection_mode || 'keep-alive';
        }
    }
}

function renderCoilsTable(coils) {
    const tbody = document.getElementById('tbl-coils');
    if (!tbody) return;

    // 1. Initial build: only if table is empty
    if (tbody.children.length === 0) {
        tbody.innerHTML = coils.map((coil, idx) => {
            const addrDisplay = `0000${idx + 1}`.slice(-5);
            const isOn = coil.value;
            const btnId = `coil-btn-${idx}`;
            return `
                <tr>
                    <td style="font-family: var(--font-mono); color: var(--text-muted);">${addrDisplay}</td>
                    <td>
                        <div style="font-weight: 500;">${coil.name}</div>
                        <div style="font-size: 0.7rem; color: var(--text-muted);">${coil.desc}</div>
                    </td>
                    <td style="text-align: center;">
                        <button id="${btnId}" class="coil-switch ${isOn ? 'on' : 'off'}" onclick="toggleCoil(${idx})">
                            ${isOn ? '1 / ON' : '0 / OFF'}
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
        return;
    }

    // 2. Incremental in-place update (never recreate the DOM)
    coils.forEach((coil, idx) => {
        const btn = document.getElementById(`coil-btn-${idx}`);
        if (btn) {
            const isOn = coil.value;
            const targetClass = `coil-switch ${isOn ? 'on' : 'off'}`;
            const targetText = isOn ? '1 / ON' : '0 / OFF';
            if (btn.className !== targetClass) btn.className = targetClass;
            if (btn.textContent.trim() !== targetText) btn.textContent = targetText;
        }
    });
}

function renderRegistersTable(registers) {
    const tbody = document.getElementById('tbl-registers');
    if (!tbody) return;

    // 1. Initial build: only if table is empty
    if (tbody.children.length === 0) {
        tbody.innerHTML = registers.map((reg, idx) => {
            const inputId = `reg-input-${idx}`;
            const valId = `reg-val-${idx}`;
            const currentValue = reg.value;

            return `
                <tr>
                    <td style="font-family: var(--font-mono); color: var(--text-muted);">
                        <div>${reg.display_addr}</div>
                        <div style="font-size: 0.68rem; color: var(--text-muted);">${reg.hex_addr}</div>
                    </td>
                    <td>
                        <div style="font-weight: 500;">${reg.name}</div>
                        <div style="font-size: 0.7rem; color: var(--text-muted);">${reg.desc}</div>
                    </td>
                    <td id="${valId}" style="text-align: right; font-family: var(--font-mono); font-weight: 600; color: var(--status-green);">
                        ${currentValue} <span style="font-size: 0.7rem; color: var(--text-muted); font-weight: normal;">${reg.unit}</span>
                    </td>
                    <td>
                        <div class="reg-write-group">
                            <input type="number" id="${inputId}" class="reg-input" 
                                   value="${currentValue}"
                                   min="0" max="65535"
                                   oninput="this.dataset.userEdited='true'"
                                   onkeydown="if(event.key==='Enter') writeRegister(${idx}, this.value)">
                            <button class="reg-btn-set" onclick="writeRegister(${idx}, document.getElementById('${inputId}').value)">Set</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
        return;
    }

    // 2. Incremental in-place update (never recreate the DOM rows or inputs!)
    registers.forEach((reg, idx) => {
        const valElem = document.getElementById(`reg-val-${idx}`);
        if (valElem) {
            valElem.innerHTML = `${reg.value} <span style="font-size: 0.7rem; color: var(--text-muted); font-weight: normal;">${reg.unit}</span>`;
        }

        const inputElem = document.getElementById(`reg-input-${idx}`);
        // ONLY update input.value if the user is NOT actively typing or focused on it
        if (inputElem && document.activeElement !== inputElem && !inputElem.dataset.userEdited) {
            inputElem.value = reg.value;
        }
    });
}

// ==========================================================================
// Log & Packet Stream Analyzer
// ==========================================================================

function handleNewLogEntry(entry) {
    App.logs.push(entry);
    if (App.logs.length > 250) {
        App.logs.shift();
    }

    updateFilterCounts();

    if (entryMatchesFilter(entry)) {
        appendLogRow(entry);
    }
}

function entryMatchesFilter(entry) {
    if (App.activeLogFilter !== 'ALL' && entry.proto !== App.activeLogFilter) {
        return false;
    }
    if (App.searchQuery) {
        const q = App.searchQuery.toLowerCase();
        const text = `${entry.summary} ${entry.peer} ${entry.raw_hex} ${entry.proto}`.toLowerCase();
        if (!text.includes(q)) return false;
    }
    return true;
}

function updateFilterCounts() {
    const cntAll = App.logs.length;
    const cntHttp = App.logs.filter(l => l.proto === 'HTTP').length;
    const cntModbus = App.logs.filter(l => l.proto === 'MODBUS').length;

    document.getElementById('cnt-all').textContent = cntAll;
    document.getElementById('cnt-http').textContent = cntHttp;
    document.getElementById('cnt-modbus').textContent = cntModbus;
}

function renderAllLogs() {
    updateFilterCounts();
    const tbody = document.getElementById('tbl-log-stream');
    tbody.innerHTML = '';

    const filtered = App.logs.filter(entryMatchesFilter);
    const fragment = document.createDocumentFragment();

    filtered.forEach(entry => {
        const tr = createLogRowElement(entry);
        fragment.appendChild(tr);
    });

    tbody.appendChild(fragment);
    scrollLogsToBottom();
}

function appendLogRow(entry) {
    const tbody = document.getElementById('tbl-log-stream');
    const tr = createLogRowElement(entry);
    tbody.appendChild(tr);

    // Keep DOM limited to last 200 rows
    while (tbody.children.length > 200) {
        tbody.removeChild(tbody.firstChild);
    }

    scrollLogsToBottom();
}

function createLogRowElement(entry) {
    const tr = document.createElement('tr');
    tr.className = 'analyzer-row';
    tr.onclick = () => openPacketModal(entry);

    const protoClass = entry.proto === 'HTTP' ? 'proto-http' : 'proto-modbus';
    const dirClass = entry.dir === 'TX' ? 'dir-tx' : 'dir-rx';

    tr.innerHTML = `
        <td style="color: var(--text-muted);">${entry.time}</td>
        <td><span class="proto-badge ${protoClass}">${entry.proto}</span></td>
        <td class="${dirClass}">${entry.dir}</td>
        <td style="color: var(--text-secondary);">${entry.peer}</td>
        <td style="color: var(--text-primary); font-weight: 500;">${entry.summary}</td>
        <td class="log-hex" title="${entry.raw_hex}">${entry.raw_hex || '—'}</td>
    `;
    return tr;
}

function scrollLogsToBottom() {
    if (App.autoScroll) {
        const box = document.querySelector('.analyzer-table-box');
        if (box) box.scrollTop = box.scrollHeight;
    }
}

function openPacketModal(entry) {
    document.getElementById('modal-time').textContent = entry.time;
    document.getElementById('modal-proto').textContent = entry.proto;
    document.getElementById('modal-dir').textContent = entry.dir;
    document.getElementById('modal-peer').textContent = entry.peer;
    document.getElementById('modal-desc').textContent = JSON.stringify(entry.details, null, 2) || entry.summary;
    document.getElementById('modal-hex').textContent = entry.raw_hex || '(No binary frame attached)';

    document.getElementById('packet-modal').classList.remove('hidden');
}

// ==========================================================================
// HTTP Traffic Generator Logic
// ==========================================================================

function toggleHttpPolling() {
    App.http.active = !App.http.active;
    const btn = document.getElementById('http-btn-toggle');
    const badge = document.getElementById('http-badge-status');
    const stateVal = document.getElementById('http-stat-conn-state');
    const stateSub = document.getElementById('http-stat-conn-sub');

    if (App.http.active) {
        btn.textContent = 'Stop HTTP Polling';
        btn.className = 'btn btn-danger btn-block';
        badge.textContent = 'Polling Active';
        badge.className = 'badge badge-active';
        stateVal.textContent = 'Active';
        stateVal.style.color = 'var(--status-green)';
        stateSub.textContent = `Running every ${App.http.interval.toFixed(1)}s`;

        executeHttpPoll();
    } else {
        clearTimeout(App.http.timer);
        btn.textContent = 'Start HTTP Polling';
        btn.className = 'btn btn-primary btn-block';
        badge.textContent = 'Inactive';
        badge.className = 'badge badge-inactive';
        stateVal.textContent = 'Idle';
        stateVal.style.color = 'var(--text-muted)';
        stateSub.textContent = 'Poller suspended';
    }
}

async function executeHttpPoll() {
    if (!App.http.active) return;

    const method = App.http.method;
    const serverSize = App.http.serverSize;
    const clientSize = App.http.clientSize;
    const keepAlive = App.http.keepAlive;

    const url = `/api/lab/traffic?size=${serverSize}&keep_alive=${keepAlive}`;
    const startTime = performance.now();

    try {
        let options = {
            method: method,
            headers: {
                'Connection': keepAlive ? 'keep-alive' : 'close'
            }
        };

        if (method === 'POST') {
            const padSize = clientSize === 'medium' ? 1024 : clientSize === 'large' ? 10240 : clientSize === 'jumbo' ? 65536 : 60;
            const bodyStr = JSON.stringify({
                type: 'client_telemetry',
                padding: generatePadding(padSize)
            });
            options.headers['Content-Type'] = 'application/json';
            options.body = bodyStr;

            App.http.txCount++;
            App.http.txBytes += new Blob([bodyStr]).size;
        } else {
            App.http.txCount++;
            App.http.txBytes += 120; // Estimated HTTP GET header length
        }

        const res = await fetch(url, options);
        const rtt = Math.round(performance.now() - startTime);
        App.http.lastRtt = rtt;

        if (res.ok) {
            const text = await res.text();
            App.http.rxCount++;
            App.http.rxBytes += new Blob([text]).size;
        }
    } catch (err) {
        console.warn('HTTP poll failed:', err);
    } finally {
        updateHttpKpiDisplay();
        if (App.http.active) {
            App.http.timer = setTimeout(executeHttpPoll, App.http.interval * 1000);
        }
    }
}

function updateHttpKpiDisplay() {
    document.getElementById('http-stat-tx-count').textContent = App.http.txCount;
    document.getElementById('http-stat-tx-bytes').textContent = `${formatBytes(App.http.txBytes)} uploaded`;
    document.getElementById('http-stat-rx-count').textContent = App.http.rxCount;
    document.getElementById('http-stat-rx-bytes').textContent = `${formatBytes(App.http.rxBytes)} downloaded`;
    document.getElementById('http-stat-rtt').textContent = `${App.http.lastRtt} ms`;
}

async function sendManualBurst(sizeKb) {
    const sizeBytes = sizeKb * 1024;
    const bodyStr = JSON.stringify({
        type: 'burst_test',
        padding: generatePadding(sizeBytes)
    });
    const bytes = new Blob([bodyStr]).size;

    App.http.txCount++;
    App.http.txBytes += bytes;
    updateHttpKpiDisplay();

    showToast(`Sending ${sizeKb} KB POST burst to /api/lab/traffic...`);

    const startTime = performance.now();
    try {
        const res = await fetch('/api/lab/traffic?size=small', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Connection': 'keep-alive'
            },
            body: bodyStr
        });
        const rtt = Math.round(performance.now() - startTime);
        if (res.ok) {
            const respText = await res.text();
            App.http.rxCount++;
            App.http.rxBytes += new Blob([respText]).size;
            App.http.lastRtt = rtt;
            updateHttpKpiDisplay();
            showToast(`Burst complete in ${rtt} ms (${sizeKb} KB)`);
        }
    } catch (err) {
        showToast(`Burst failed: ${err.message}`);
    }
}

async function sendParallelBurst() {
    showToast('Dispatching 5x parallel POST requests...');
    const promises = [1, 2, 3, 4, 5].map(i => {
        return fetch('/api/lab/traffic?size=medium', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parallel_req: i, time: Date.now() })
        });
    });

    try {
        await Promise.all(promises);
        showToast('All 5 parallel requests acknowledged!');
    } catch (err) {
        showToast('Some parallel requests failed');
    }
}

// ==========================================================================
// Modbus API Actions
// ==========================================================================

async function toggleCoil(index) {
    const currentVal = App.state.coils && App.state.coils[index] ? App.state.coils[index].value : false;
    try {
        await fetch('/api/modbus/write_coil', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: index, value: !currentVal })
        });
    } catch (err) {
        showToast(`Failed to toggle coil ${index}`);
    }
}

async function writeRegister(index, valueStr) {
    const val = parseInt(valueStr, 10);
    if (isNaN(val) || val < 0 || val > 65535) {
        showToast('Value must be an integer between 0 and 65535');
        return;
    }

    const inputElem = document.getElementById(`reg-input-${index}`);
    if (inputElem) {
        delete inputElem.dataset.userEdited;
    }

    try {
        await fetch('/api/modbus/write_register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: index, value: val })
        });
        showToast(`Written ${val} to register 40001+${index}`);
    } catch (err) {
        showToast(`Failed to write register: ${err.message}`);
    }
}

async function toggleMasterPoller() {
    const currentActive = !!App.state.master_config?.polling_active;
    try {
        await fetch('/api/modbus/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ polling_active: !currentActive })
        });
    } catch (err) {
        showToast('Failed to toggle poller');
    }
}

async function applyMasterConfig() {
    const txtTarget = document.getElementById('mb-txt-target');
    const numPort = document.getElementById('mb-num-port');
    const targetHost = txtTarget ? txtTarget.value.trim() : '127.0.0.1';
    const targetPort = numPort ? parseInt(numPort.value, 10) : 1502;
    const interval = parseFloat(document.getElementById('mb-rng-interval').value);
    const fc = parseInt(document.getElementById('mb-sel-fc').value, 10);
    const connMode = document.getElementById('mb-sel-conn-mode').value;

    if (txtTarget) delete txtTarget.dataset.userEdited;
    if (numPort) delete numPort.dataset.userEdited;

    try {
        await fetch('/api/modbus/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target_host: targetHost,
                target_port: targetPort,
                interval: interval,
                function_code: fc,
                connection_mode: connMode
            })
        });
        showToast(`Target updated: ${targetHost}:${targetPort}`);
    } catch (err) {
        showToast('Failed to apply configuration');
    }
}

async function sendManualModbusQuery() {
    const selFc = document.getElementById('mb-sel-fc');
    const fc = selFc ? parseInt(selFc.value, 10) : 3;
    try {
        showToast(`Sending single FC0${fc} wire query...`);
        const res = await fetch('/api/modbus/manual_query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fc: fc, start_addr: 0, quantity: 10 })
        });
        if (res.ok) {
            showToast(`FC0${fc} wire query dispatched!`);
        }
    } catch (err) {
        showToast('Query failed');
    }
}

// ==========================================================================
// Event Listeners & Initialization
// ==========================================================================

document.addEventListener('DOMContentLoaded', () => {
    // 1. Initialize WebSocket connection
    initWebSocket();

    // 2. Tab Navigation
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.getAttribute('data-tab');
            App.activeTab = targetTab;

            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

            btn.classList.add('active');
            document.getElementById(targetTab).classList.add('active');
        });
    });

    const txtTarget = document.getElementById('mb-txt-target');
    const numPort = document.getElementById('mb-num-port');
    if (txtTarget) {
        txtTarget.addEventListener('input', () => { txtTarget.dataset.userEdited = 'true'; });
    }
    if (numPort) {
        numPort.addEventListener('input', () => { numPort.dataset.userEdited = 'true'; });
    }

    const copyIpBtn = document.getElementById('btn-copy-ip');
    if (copyIpBtn) {
        copyIpBtn.addEventListener('click', () => {
            const ip = document.getElementById('hdr-lan-ip').textContent;
            copyToClipboard(ip, 'LAN IP');
        });
    }

    // 4. HTTP Controls
    document.getElementById('http-btn-toggle').addEventListener('click', toggleHttpPolling);

    document.getElementById('http-rng-interval').addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        App.http.interval = val;
        document.getElementById('http-lbl-interval').textContent = `${val.toFixed(1)} s`;
    });

    document.getElementById('http-sel-method').addEventListener('change', (e) => {
        App.http.method = e.target.value;
        document.getElementById('http-post-size-group').style.display = (e.target.value === 'POST') ? 'flex' : 'none';
    });

    document.getElementById('http-sel-server-size').addEventListener('change', (e) => {
        App.http.serverSize = e.target.value;
    });

    document.getElementById('http-sel-client-size').addEventListener('change', (e) => {
        App.http.clientSize = e.target.value;
    });

    document.getElementById('http-sel-keep-alive').addEventListener('change', (e) => {
        App.http.keepAlive = (e.target.value === 'true');
    });

    document.getElementById('http-btn-burst-10').addEventListener('click', () => sendManualBurst(10));
    document.getElementById('http-btn-burst-100').addEventListener('click', () => sendManualBurst(100));
    document.getElementById('http-btn-burst-parallel').addEventListener('click', sendParallelBurst);

    // 5. Modbus Master Poller Controls
    const btnTogglePoller = document.getElementById('mb-btn-toggle-poller');
    if (btnTogglePoller) {
        btnTogglePoller.addEventListener('click', toggleMasterPoller);
    }

    const btnApplyTarget = document.getElementById('mb-btn-apply-target');
    if (btnApplyTarget) {
        btnApplyTarget.addEventListener('click', applyMasterConfig);
    }

    const btnManualQuery = document.getElementById('mb-btn-manual-query');
    if (btnManualQuery) {
        btnManualQuery.addEventListener('click', sendManualModbusQuery);
    }

    const rngModbusInterval = document.getElementById('mb-rng-interval');
    if (rngModbusInterval) {
        rngModbusInterval.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            const lbl = document.getElementById('mb-lbl-interval');
            if (lbl) lbl.textContent = `${val.toFixed(1)} s`;
        });
        rngModbusInterval.addEventListener('change', applyMasterConfig);
    }

    const selModbusFc = document.getElementById('mb-sel-fc');
    if (selModbusFc) {
        selModbusFc.addEventListener('change', applyMasterConfig);
    }

    const selModbusConn = document.getElementById('mb-sel-conn-mode');
    if (selModbusConn) {
        selModbusConn.addEventListener('change', applyMasterConfig);
    }

    // 6. Analyzer Controls
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            App.activeLogFilter = btn.getAttribute('data-log-filter');
            renderAllLogs();
        });
    });

    document.getElementById('log-search-input').addEventListener('input', (e) => {
        App.searchQuery = e.target.value.trim();
        renderAllLogs();
    });

    document.getElementById('chk-autoscroll').addEventListener('change', (e) => {
        App.autoScroll = e.target.checked;
    });

    document.getElementById('btn-clear-logs').addEventListener('click', async () => {
        try {
            await fetch('/api/logs/clear', { method: 'POST' });
            App.logs = [];
            renderAllLogs();
            showToast('Protocol log cleared');
        } catch (err) {
            showToast('Failed to clear logs');
        }
    });

    // 7. Modal Close
    document.getElementById('btn-close-modal').addEventListener('click', () => {
        document.getElementById('packet-modal').classList.add('hidden');
    });
    document.getElementById('packet-modal').addEventListener('click', (e) => {
        if (e.target.id === 'packet-modal') {
            document.getElementById('packet-modal').classList.add('hidden');
        }
    });
});
