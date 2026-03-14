/**
 * app.js - Lógica principal de la aplicación de trading
 * Gestiona la UI, navegación, datos en tiempo real y acciones del usuario
 */

// ============ ESTADO GLOBAL ============
const AppState = {
    currentPage: 'dashboard',
    currentOrderType: 'BUY',
    watchlist: ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD','AUDUSD','USDCAD','US30','BTCUSD'],
    refreshInterval: null,
    chart: null,
    chartSeries: null,
    currentChartSymbol: 'EURUSD',
    currentChartTimeframe: 60,  // H1 por defecto (en minutos)
    initialDataLoaded: false, // Control para la carga inicial de datos
};

// Mapa de timeframe (minutos) a nombre MT5
const TIMEFRAME_MAP = {
    1:    'M1',
    5:    'M5',
    15:   'M15',
    30:   'M30',
    60:   'H1',
    240:  'H4',
    1440: 'D1',
};

// ============ INICIALIZACIÓN ============

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initChart();
    // loadDashboard(); // Se elimina la carga directa para esperar la confirmación de conexión
    startAutoRefresh();
    checkServerHealth(); // Esta función se encargará de la carga inicial
    loadStrategyCatalog();  // Cargar catálogo de estrategias desde el backend
});

// ============ NAVEGACIÓN ============

function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            navigateTo(page);
        });
    });

    document.getElementById('btnRefresh').addEventListener('click', () => {
        loadCurrentPage();
        showToast('Datos actualizados', 'info');
    });
}

function navigateTo(page) {
    // Actualizar nav activo
    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
    document.querySelector(`[data-page="${page}"]`)?.classList.add('active');

    // Mostrar página correcta
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`)?.classList.add('active');

    // Actualizar título
    const titles = {
        dashboard: 'Dashboard',
        trading: 'Terminal de Trading',
        analysis: 'Análisis de Mercado',
        strategies: 'Estrategias Automáticas',
        positions: 'Posiciones',
        settings: 'Configuración'
    };
    document.getElementById('pageTitle').textContent = titles[page] || page;

    AppState.currentPage = page;
    loadCurrentPage();
}

function loadCurrentPage() {
    switch (AppState.currentPage) {
        case 'dashboard':   loadDashboard(); break;
        case 'trading':     loadTradingPage(); break;
        case 'analysis':    break; // Manual trigger
        case 'strategies':  loadStrategies(); break;
        case 'positions':   loadAllPositions(); break;
        case 'settings':    loadSettings(); break;
    }
}

// ============ AUTO REFRESH ============

function startAutoRefresh() {
    // Si ya existe un intervalo, lo limpiamos para evitar duplicados
    if (AppState.refreshInterval) {
        clearInterval(AppState.refreshInterval);
    }
    
    // Establecer el nuevo intervalo
    AppState.refreshInterval = setInterval(() => {
        // Solo refrescar si el servidor está conectado
        const statusDot = document.getElementById('statusDot');
        if (statusDot && statusDot.classList.contains('connected')) {
            loadCurrentPage();
            // Ya no es necesario llamar a updateWatchlistPrices() aquí,
            // porque loadDashboard() (parte de loadCurrentPage) ya lo hace.
        } else {
            console.log('Auto-refresh pausado: MT5 no conectado.');
        }
    }, 5000); // Cada 5 segundos
}

// ============ HEALTH CHECK ============

async function checkServerHealth() {
    try {
        const health = await HealthAPI.check();
        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        const serverDot = document.getElementById('serverDot');
        const serverText = document.getElementById('serverStatusText');

        if (health.mt5_connected) {
            if (dot) dot.className = 'status-dot connected';
            if (text) text.textContent = 'MT5 Conectado';
            if (serverDot) serverDot.className = 'status-dot connected';
            if (serverText) serverText.textContent = 'Servidor Online';

            // Si es la primera vez que se conecta, cargar los datos del dashboard
            if (!AppState.initialDataLoaded) {
                console.log("Conexión con MT5 establecida. Realizando carga inicial de datos...");
                loadDashboard();
                AppState.initialDataLoaded = true;
            }

        } else {
            if (dot) dot.className = 'status-dot disconnected';
            if (text) text.textContent = health.status === 'offline' ? 'Servidor Offline' : 'MT5 Desconectado';
            if (serverDot) serverDot.className = 'status-dot disconnected';
            if (serverText) serverText.textContent = health.status === 'offline' ? 'Servidor Offline' : 'Servidor Online, MT5 Desconectado';
        }
    } catch (err) {
        console.warn("Error en Health Check:", err.message);
    } finally {
        // Repetir cada 10 segundos
        setTimeout(checkServerHealth, 10000);
    }
}

// ============ DASHBOARD ============

async function loadDashboard() {
    await Promise.all([
        loadAccountInfo(),
        loadPositions(),
        loadStrategiesStatus(),
        updateWatchlistPrices(),
    ]);
}

async function loadAccountInfo() {
    try {
        const account = await AccountAPI.getInfo();
        document.getElementById('balance').textContent = formatCurrency(account.balance, account.currency);
        document.getElementById('equity').textContent = formatCurrency(account.equity, account.currency);
        document.getElementById('profit').textContent = formatCurrency(account.profit, account.currency);
        document.getElementById('marginFree').textContent = formatCurrency(account.margin_free, account.currency);
        document.getElementById('accountLogin').textContent = account.login;

        // Color del profit
        const profitEl = document.getElementById('profit');
        profitEl.style.color = account.profit >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
    } catch (err) {
        console.warn('No se pudo cargar info de cuenta:', err.message);
    }
}

async function loadPositions() {
    try {
        const positions = await OrdersAPI.getPositions();
        document.getElementById('positionsCount').textContent = positions.length;

        const tbody = document.getElementById('positionsBody');
        if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-row">No hay posiciones abiertas</td></tr>';
            return;
        }

        tbody.innerHTML = positions.map(pos => `
            <tr>
                <td><strong>${pos.symbol}</strong></td>
                <td class="type-${pos.type.toLowerCase()}">${pos.type}</td>
                <td>${pos.volume}</td>
                <td>${pos.price_open}</td>
                <td class="${pos.profit >= 0 ? 'profit-positive' : 'profit-negative'}">
                    ${formatCurrency(pos.profit)}
                </td>
                <td>
                    <button class="btn-icon" onclick="closePosition(${pos.ticket})" title="Cerrar posición">
                        <i class="fas fa-times"></i>
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.warn('No se pudo cargar posiciones:', err.message);
    }
}

async function loadStrategiesStatus() {
    try {
        const strategies = await StrategiesAPI.list();
        document.getElementById('botsCount').textContent = strategies.length;

        const container = document.getElementById('activeBotsList');
        if (strategies.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-robot"></i>
                    <p>No hay bots activos. Ve a <strong>Estrategias</strong> para iniciar uno.</p>
                </div>`;
            return;
        }

        container.innerHTML = strategies.map(s => `
            <div class="bot-card">
                <div class="bot-card-header">
                    <span class="bot-name">${s.name}</span>
                    <span class="bot-status running">● Activo</span>
                </div>
                <div class="bot-symbol">
                    <i class="fas fa-chart-line"></i> ${s.symbols.join(', ')}
                </div>
                <div class="bot-stats">
                    <div class="bot-stat">Trades: <strong>${s.stats?.daily_stats?.trades_count || 0}</strong></div>
                    <div class="bot-stat">Win Rate: <strong>${(s.stats?.daily_stats?.win_rate || 0).toFixed(1)}%</strong></div>
                </div>
            </div>
        `).join('');
    } catch (err) {
        console.warn('No se pudo cargar estrategias:', err.message);
    }
}

async function updateWatchlistPrices() {
    const watchlistEl = document.getElementById('watchlist');
    const items = [];

    for (const symbol of AppState.watchlist) {
        try {
            const data = await MarketAPI.getTicker(symbol);
            const change = ((data.bid - data.ask) / data.ask * 100).toFixed(2);
            items.push(`
                <div class="watchlist-item" onclick="navigateTo('trading')">
                    <span class="watchlist-symbol">${symbol}</span>
                    <span class="watchlist-price">${data.bid.toFixed(5)}</span>
                    <span class="watchlist-change ${change >= 0 ? 'up' : 'down'}">
                        ${change >= 0 ? '▲' : '▼'} ${Math.abs(change)}%
                    </span>
                </div>
            `);
        } catch {
            items.push(`
                <div class="watchlist-item">
                    <span class="watchlist-symbol">${symbol}</span>
                    <span class="watchlist-price" style="color:var(--text-muted)">--</span>
                </div>
            `);
        }
    }
    watchlistEl.innerHTML = items.join('');
}

// ============ TRADING PAGE ============

async function loadTradingPage() {
    const symbol = document.getElementById('chartSymbol').value;
    await updateChartPrices(symbol);
}

async function updateChartPrices(symbol) {
    try {
        const data = await MarketAPI.getTicker(symbol);
        document.getElementById('chartBid').textContent = data.bid.toFixed(5);
        document.getElementById('chartAsk').textContent = data.ask.toFixed(5);
    } catch (err) {
        console.warn('No se pudo actualizar precios del chart:', err.message);
    }
}

// ============ CHART (TradingView Lightweight Charts) ============

function initChart() {
    const container = document.getElementById('tradingChart');
    if (!container || typeof LightweightCharts === 'undefined') return;

    AppState.chart = LightweightCharts.createChart(container, {
        layout: {
            background: { color: '#1c2128' },
            textColor: '#8b949e',
        },
        grid: {
            vertLines: { color: '#21262d' },
            horzLines: { color: '#21262d' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#30363d' },
        timeScale: { borderColor: '#30363d', timeVisible: true },
        width: container.clientWidth,
        height: container.clientHeight || 400,
    });

    AppState.chartSeries = AppState.chart.addCandlestickSeries({
        upColor: '#3fb950',
        downColor: '#f85149',
        borderUpColor: '#3fb950',
        borderDownColor: '#f85149',
        wickUpColor: '#3fb950',
        wickDownColor: '#f85149',
    });

    // Cargar datos reales del símbolo por defecto
    loadChartData(AppState.currentChartSymbol, AppState.currentChartTimeframe);

    // Resize observer
    new ResizeObserver(() => {
        AppState.chart?.applyOptions({ width: container.clientWidth });
    }).observe(container);

    // ---- FIX: Cambio de símbolo recarga el gráfico ----
    document.getElementById('chartSymbol').addEventListener('change', (e) => {
        AppState.currentChartSymbol = e.target.value;
        // Sincronizar también el selector de orden
        const orderSymbol = document.getElementById('orderSymbol');
        if (orderSymbol) orderSymbol.value = e.target.value;
        updateChartPrices(e.target.value);
        loadChartData(AppState.currentChartSymbol, AppState.currentChartTimeframe);
    });

    // ---- FIX: Botones de timeframe recargan el gráfico ----
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            // Actualizar estado visual
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Actualizar timeframe en estado global y recargar datos
            AppState.currentChartTimeframe = parseInt(btn.dataset.tf, 10);
            loadChartData(AppState.currentChartSymbol, AppState.currentChartTimeframe);
        });
    });
}

/**
 * Carga datos históricos del backend y los muestra en el gráfico.
 * @param {string} symbol - Símbolo (ej: "EURUSD")
 * @param {number} timeframe - Timeframe en minutos (1, 5, 15, 60, 240, 1440)
 */
async function loadChartData(symbol, timeframe) {
    if (!AppState.chartSeries) return;

    const tfName = TIMEFRAME_MAP[timeframe] || 'H1';

    try {
        // Llamar al endpoint dedicado de velas OHLC con el timeframe correcto
        const data = await MarketAPI.getCandles(symbol, timeframe, 200);

        if (data && data.candles && data.candles.length > 0) {
            // Convertir datos al formato de LightweightCharts
            const candleData = data.candles.map(c => ({
                time: Math.floor(new Date(c.time).getTime() / 1000),
                open:  c.open,
                high:  c.high,
                low:   c.low,
                close: c.close,
            })).sort((a, b) => a.time - b.time);

            AppState.chartSeries.setData(candleData);
            AppState.chart.timeScale().fitContent();
            return;
        }
    } catch (err) {
        console.warn(`No se pudieron cargar datos reales para ${symbol} ${tfName}:`, err.message);
    }

    // Fallback: datos de ejemplo si no hay conexión
    const now = Math.floor(Date.now() / 1000);
    const intervalSecs = timeframe * 60;
    const sampleData = Array.from({ length: 100 }, (_, i) => {
        const base = 1.1000 + (Math.random() - 0.5) * 0.02;
        return {
            time: now - (100 - i) * intervalSecs,
            open:  base,
            high:  base + Math.random() * 0.003,
            low:   base - Math.random() * 0.003,
            close: base + (Math.random() - 0.5) * 0.002,
        };
    });
    AppState.chartSeries.setData(sampleData);
    AppState.chart.timeScale().fitContent();
}

// ============ ORDER MANAGEMENT ============

function setOrderType(type) {
    AppState.currentOrderType = type;
    document.getElementById('btnBuy').classList.toggle('active', type === 'BUY');
    document.getElementById('btnSell').classList.toggle('active', type === 'SELL');
}

function adjustVolume(delta) {
    const input = document.getElementById('orderVolume');
    const current = parseFloat(input.value) || 0.01;
    input.value = Math.max(0.01, (current + delta)).toFixed(2);
}

async function executeOrder() {
    const symbol = document.getElementById('orderSymbol').value;
    const volume = parseFloat(document.getElementById('orderVolume').value);
    const slVal = document.getElementById('orderSL').value;
    const tpVal = document.getElementById('orderTP').value;
    const sl = slVal && parseFloat(slVal) > 0 ? parseFloat(slVal) : null;
    const tp = tpVal && parseFloat(tpVal) > 0 ? parseFloat(tpVal) : null;
    const comment = document.getElementById('orderComment').value;

    if (!symbol || !volume || isNaN(volume) || volume <= 0) {
        showToast('Completa los campos requeridos (símbolo y volumen)', 'warning');
        return;
    }

    const orderData = {
        symbol,
        order_type: AppState.currentOrderType,
        volume,
        stop_loss: sl,
        take_profit: tp,
        comment: comment || `Manual ${AppState.currentOrderType}`,
        deviation: 20,
        magic_number: 234000,
    };

    showModal(
        'Confirmar Orden',
        `¿Deseas ejecutar una orden de <strong>${AppState.currentOrderType}</strong> en <strong>${symbol}</strong> con <strong>${volume} lotes</strong>?`,
        async () => {
            try {
                const result = await OrdersAPI.createOrder(orderData);
                const ticketNum = result.ticket || result.order_id || '—';
                showToast(`✅ Orden ejecutada! Ticket: #${ticketNum}`, 'success');
                loadPositions();
                loadAccountInfo();
            } catch (err) {
                // Mejorar mensajes de error conocidos de MT5
                const msg = translateMT5Error(err.message);
                showToast(`Error al ejecutar orden: ${msg}`, 'error');
            }
        }
    );
}

async function closePosition(ticket) {
    showModal(
        'Cerrar Posición',
        `¿Confirmas el cierre de la posición con ticket <strong>#${ticket}</strong>?`,
        async () => {
            try {
                await OrdersAPI.closePosition(ticket);
                showToast(`✅ Posición #${ticket} cerrada`, 'success');
                loadPositions();
                loadAccountInfo();
                loadAllPositions(); // Asegura la actualización de la tabla grande
            } catch (err) {
                const msg = translateMT5Error(err.message);
                showToast(`Error al cerrar posición: ${msg}`, 'error');
            }
        }
    );
}

async function closeAllPositions() {
    const positions = await OrdersAPI.getPositions().catch(() => []);
    if (positions.length === 0) {
        showToast('No hay posiciones abiertas', 'info');
        return;
    }

    showModal(
        'Cerrar Todas las Posiciones',
        `¿Confirmas el cierre de <strong>${positions.length} posición(es)</strong>?`,
        async () => {
            for (const pos of positions) {
                try {
                    await OrdersAPI.closePosition(pos.ticket);
                } catch (err) {
                    console.error(`Error cerrando ${pos.ticket}:`, err.message);
                }
            }
            showToast('Todas las posiciones cerradas', 'success');
            loadAllPositions();
            loadAccountInfo();
        }
    );
}

// ============ ANALYSIS ============

async function runAnalysis() {
    const symbol = document.getElementById('analysisSymbol').value;

    document.getElementById('analysisEmpty').style.display = 'none';
    document.getElementById('analysisResults').style.display = 'none';
    document.getElementById('analysisLoading').style.display = 'flex';

    try {
        const data = await AnalysisAPI.getFull(symbol);
        renderAnalysis(data);
        document.getElementById('analysisResults').style.display = 'grid';
    } catch (err) {
        showToast(`Error analizando ${symbol}: ${err.message}`, 'error');
        document.getElementById('analysisEmpty').style.display = 'flex';
    } finally {
        document.getElementById('analysisLoading').style.display = 'none';
    }
}

function renderAnalysis(data) {
    // Tendencia
    const trendEl = document.getElementById('trendIndicator');
    const trendIcons = { UPTREND: '↑', DOWNTREND: '↓', SIDEWAYS: '→' };
    const trendClasses = { UPTREND: 'up', DOWNTREND: 'down', SIDEWAYS: 'sideways' };
    const trendDir = data.trend_direction || 'SIDEWAYS';

    if (trendEl) {
        trendEl.className = `trend-indicator ${trendClasses[trendDir] || ''}`;
        trendEl.innerHTML = `<span>${trendIcons[trendDir] || '→'}</span><span>${trendDir}</span>`;
    }

    // trendText es un span DENTRO de trendIndicator; si existe como elemento separado lo actualizamos
    const trendText = document.getElementById('trendText');
    if (trendText) trendText.textContent = trendDir;

    // Señal general
    const signalEl = document.getElementById('overallSignal');
    const overall = data.signals?.overall || 'NEUTRAL';
    if (signalEl) {
        signalEl.textContent = overall;
        signalEl.className = `signal-badge ${overall}`;
    }

    // Indicadores
    const ind = data.indicators || {};
    const indicatorsHtml = [
        { name: 'RSI (14)', value: ind.rsi?.toFixed(2) },
        { name: 'MACD', value: ind.macd?.toFixed(5) },
        { name: 'ATR (14)', value: ind.atr?.toFixed(5) },
        { name: 'SMA (20)', value: ind.sma_20?.toFixed(5) },
        { name: 'SMA (50)', value: ind.sma_50?.toFixed(5) },
        { name: 'BB Upper', value: ind.bb_upper?.toFixed(5) },
        { name: 'BB Lower', value: ind.bb_lower?.toFixed(5) },
        { name: 'Stoch %K', value: ind.stoch_k?.toFixed(2) },
    ].filter(i => i.value).map(i => `
        <div class="indicator-row">
            <span class="indicator-name">${i.name}</span>
            <span class="indicator-value">${i.value}</span>
        </div>
    `).join('');
    const indicatorsListEl = document.getElementById('indicatorsList');
    if (indicatorsListEl) indicatorsListEl.innerHTML = indicatorsHtml;

    // Señales
    const signals = data.signals || {};
    const signalsHtml = Object.entries(signals)
        .filter(([k]) => k !== 'overall')
        .map(([key, value]) => `
            <div class="signal-row">
                <span class="signal-name">${key.toUpperCase()}</span>
                <span class="signal-value ${value}">${value}</span>
            </div>
        `).join('');
    const signalsListEl = document.getElementById('signalsList');
    if (signalsListEl) signalsListEl.innerHTML = signalsHtml;

    // Niveles
    const levels = data.support_resistance || {};
    const resistancesHtml = (levels.resistances || []).length > 0
        ? (levels.resistances || []).map(r => `
            <div class="level-item level-resistance">
                <span>Resistencia</span><span>${Number(r).toFixed(5)}</span>
            </div>`).join('')
        : '<p style="color:var(--text-muted);font-size:12px">No detectadas</p>';
    const supportsHtml = (levels.supports || []).length > 0
        ? (levels.supports || []).map(s => `
            <div class="level-item level-support">
                <span>Soporte</span><span>${Number(s).toFixed(5)}</span>
            </div>`).join('')
        : '<p style="color:var(--text-muted);font-size:12px">No detectados</p>';

    const levelsHtml = `
        <div class="levels-section"><h5>Resistencias</h5>${resistancesHtml}</div>
        <div class="levels-section"><h5>Soportes</h5>${supportsHtml}</div>
    `;
    const levelsDisplayEl = document.getElementById('levelsDisplay');
    if (levelsDisplayEl) levelsDisplayEl.innerHTML = levelsHtml;
}

// ============ MT5 ERROR TRANSLATOR ============

/**
 * Traduce mensajes de error de MT5 a texto legible en español.
 * @param {string} msg - Mensaje de error original
 * @returns {string} Mensaje traducido
 */
function translateMT5Error(msg) {
    if (!msg) return 'Error desconocido';
    const m = msg.toLowerCase();

    if (m.includes('autotrading disabled') || m.includes('10027'))
        return 'El AutoTrading está desactivado en MT5. Actívalo con el botón "AutoTrading" en la barra de herramientas de MT5.';
    if (m.includes('no money') || m.includes('10019'))
        return 'Fondos insuficientes para ejecutar la orden.';
    if (m.includes('market closed') || m.includes('10018'))
        return 'El mercado está cerrado en este momento.';
    if (m.includes('invalid volume') || m.includes('10014'))
        return 'Volumen inválido. Verifica el tamaño del lote.';
    if (m.includes('invalid stops') || m.includes('10016'))
        return 'Stop Loss o Take Profit inválidos para este símbolo.';
    if (m.includes('trade disabled') || m.includes('10017'))
        return 'El trading está deshabilitado para este símbolo.';
    if (m.includes('not connected') || m.includes('503'))
        return 'MT5 no está conectado. Verifica la conexión.';
    if (m.includes('requote') || m.includes('10004'))
        return 'Requote: el precio cambió. Intenta de nuevo.';
    if (m.includes('off quotes') || m.includes('10008'))
        return 'Sin cotizaciones disponibles. Intenta de nuevo.';

    return msg;
}

// ============ STRATEGIES ============

/**
 * Carga el catálogo de estrategias desde el backend y rellena el selector.
 */
async function loadStrategyCatalog() {
    try {
        const catalog = await StrategiesAPI.getCatalog();
        const select = document.getElementById('strategyType');
        const descEl = document.getElementById('strategyDescription');

        if (!select || !catalog || catalog.length === 0) return;

        // Rellenar opciones
        select.innerHTML = catalog.map(s =>
            `<option value="${s.id}">${s.name} (${s.timeframe})</option>`
        ).join('');

        // Mostrar descripción de la estrategia seleccionada
        function updateDescription() {
            const selected = catalog.find(s => s.id === select.value);
            if (selected && descEl) {
                descEl.innerHTML = `<strong>${selected.name}:</strong> ${selected.description}`;
            }
        }

        select.addEventListener('change', updateDescription);
        updateDescription(); // Mostrar descripción inicial
    } catch (err) {
        console.warn('No se pudo cargar catálogo de estrategias:', err.message);
    }
}

async function loadStrategies() {
    try {
        const strategies = await StrategiesAPI.list();
        const container = document.getElementById('strategiesList');

        if (strategies.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-robot"></i>
                    <p>No hay estrategias activas</p>
                </div>`;
            return;
        }

        container.innerHTML = strategies.map(s => `
            <div class="strategy-item">
                <div class="strategy-info">
                    <span class="strategy-name">${s.name}</span>
                    <span class="strategy-meta">
                        <i class="fas fa-chart-line"></i> ${s.symbols.join(', ')} &nbsp;|&nbsp;
                        Trades: ${s.stats?.daily_stats?.trades_count || 0} &nbsp;|&nbsp;
                        Win Rate: ${(s.stats?.daily_stats?.win_rate || 0).toFixed(1)}%
                    </span>
                </div>
                <button class="btn-sm btn-danger" onclick="stopStrategy('${s.id}')">
                    <i class="fas fa-stop"></i> Detener
                </button>
            </div>
        `).join('');
    } catch (err) {
        console.warn('No se pudo cargar estrategias:', err.message);
    }
}

async function startStrategy() {
    const symbol = document.getElementById('strategySymbol').value;
    const type = document.getElementById('strategyType').value;

    try {
        await StrategiesAPI.start(symbol, type);
        showToast(`Bot iniciado para ${symbol}`, 'success');
        loadStrategies();
        loadStrategiesStatus();
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

async function stopStrategy(strategyId) {
    try {
        await StrategiesAPI.stop(strategyId);
        showToast('Bot detenido', 'info');
        loadStrategies();
        loadStrategiesStatus();
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

async function stopAllStrategies() {
    const strategies = await StrategiesAPI.list().catch(() => []);
    for (const s of strategies) {
        await StrategiesAPI.stop(s.id).catch(() => {});
    }
    showToast('Todos los bots detenidos', 'info');
    loadStrategies();
}

// ============ ALL POSITIONS ============

async function loadAllPositions() {
    try {
        const positions = await OrdersAPI.getPositions();
        const tbody = document.getElementById('allPositionsBody');

        if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" class="empty-row">No hay posiciones abiertas</td></tr>';
            return;
        }

        tbody.innerHTML = positions.map(pos => `
            <tr>
                <td>#${pos.ticket}</td>
                <td><strong>${pos.symbol}</strong></td>
                <td class="type-${pos.type.toLowerCase()}">${pos.type}</td>
                <td>${pos.volume}</td>
                <td>${pos.price_open}</td>
                <td>${pos.price_current}</td>
                <td>${pos.stop_loss || '--'}</td>
                <td>${pos.take_profit || '--'}</td>
                <td class="${pos.profit >= 0 ? 'profit-positive' : 'profit-negative'}">
                    ${formatCurrency(pos.profit)}
                </td>
                <td>
                    <button class="btn-icon" onclick="closePosition(${pos.ticket})" title="Cerrar">
                        <i class="fas fa-times"></i>
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.warn('No se pudo cargar posiciones:', err.message);
    }
}

// ============ SETTINGS ============

function loadSettings() {
    const apiUrl = localStorage.getItem('apiUrl') || 'http://localhost:8000';
    document.getElementById('settingApiUrl').value = apiUrl;
    checkServerHealth();
}

function saveSettings() {
    const apiUrl = document.getElementById('settingApiUrl').value;
    localStorage.setItem('apiUrl', apiUrl);
    showToast('Configuración guardada. Recarga la página para aplicar.', 'success');
}

async function testConnection() {
    const health = await HealthAPI.check();
    if (health.mt5_connected) {
        showToast('Conexión exitosa con MT5', 'success');
    } else if (health.status === 'offline') {
        showToast('Servidor offline. Ejecuta: python run_server.py', 'error');
    } else {
        showToast('Servidor online pero MT5 desconectado', 'warning');
    }
}

// ============ MODAL ============

function showModal(title, body, onConfirm) {
    document.getElementById('modalTitle').textContent = title;
    document.getElementById('modalBody').innerHTML = body;
    document.getElementById('modal').style.display = 'flex';

    const confirmBtn = document.getElementById('modalConfirm');
    confirmBtn.onclick = async () => {
        closeModal();
        await onConfirm();
    };
}

function closeModal() {
    document.getElementById('modal').style.display = 'none';
}

// ============ TOAST NOTIFICATIONS ============

function showToast(message, type = 'info') {
    const icons = { success: 'check-circle', error: 'exclamation-circle', info: 'info-circle', warning: 'exclamation-triangle' };
    const container = document.getElementById('toastContainer');

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<i class="fas fa-${icons[type]}"></i><span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => toast.remove(), 4000);
}

// ============ UTILITIES ============

function formatCurrency(amount, currency = 'USD') {
    if (amount === null || amount === undefined) return '--';
    const formatted = Math.abs(amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const sign = amount < 0 ? '-' : '';
    return `${sign}$${formatted}`;
}
