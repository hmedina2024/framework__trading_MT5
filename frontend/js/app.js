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
    currentChartTimeframe: 60,
    initialDataLoaded: false,
    _refreshTick: 0,
};

// Frecuencia de actualización por tipo de dato (en ciclos de 5s)
const REFRESH_RATES = {
    prices:     1,   // cada 5s  — precios y watchlist
    positions:  3,   // cada 15s — posiciones abiertas
    account:    12,  // cada 60s — balance, bots
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
    // startAutoRefresh() se llama desde checkServerHealth() una vez confirmada la conexión
    checkServerHealth();
    loadStrategyCatalog();
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
        dashboard:   'Dashboard',
        trading:     'Terminal de Trading',
        analysis:    'Análisis de Mercado',
        strategies:  'Estrategias Automáticas',
        positions:   'Posiciones',
        seguimiento: 'Seguimiento de Bots',
        backtest: 'Backtest de Estrategias',
        rendimiento: 'Rendimiento',
        settings:    'Configuración'
    };
    document.getElementById('pageTitle').textContent = titles[page] || page;

    AppState.currentPage = page;
    loadCurrentPage();
}

function loadCurrentPage() {
    switch (AppState.currentPage) {
        case 'dashboard':    loadDashboard(); break;
        case 'trading':      loadTradingPage(); break;
        case 'analysis':     break;
        case 'strategies':   loadStrategies(); break;
        case 'positions':    loadAllPositions(); break;
        case 'backtest':    break; // carga bajo demanda
        case 'rendimiento':  rendLoadChart(_rendDays || 30); break;
        case 'seguimiento':  if (typeof segRefresh === 'function') segRefresh(); break;
        case 'settings':     loadSettings(); break;
    }
}

// ============ AUTO REFRESH INTELIGENTE ============

function startAutoRefresh() {
    if (AppState.refreshInterval) clearInterval(AppState.refreshInterval);

    AppState.refreshInterval = setInterval(async () => {
        const isConnected = document.getElementById('statusDot')
            ?.classList.contains('connected');
        if (!isConnected) return;

        AppState._refreshTick++;
        const tick = AppState._refreshTick;
        const page = AppState.currentPage;

        // Precios: cada 3s
        if (tick % REFRESH_RATES.prices === 0) {
            if (page === 'dashboard') updateWatchlistPrices();
            if (page === 'trading')   updateChartPrices(AppState.currentChartSymbol);
        }
        // Posiciones: cada 9s
        if (tick % REFRESH_RATES.positions === 0) {
            if (page === 'dashboard') loadPositions();
            if (page === 'positions') loadAllPositions();
        }
        // Cuenta y bots: cada 30s
        if (tick % REFRESH_RATES.account === 0) {
            if (page === 'dashboard' || page === 'trading')     loadAccountInfo();
            if (page === 'dashboard' || page === 'strategies')  loadStrategiesStatus();
            if (page === 'strategies') loadStrategies();
        }
    }, 5000);
}

// ============ HEALTH CHECK ============

async function checkServerHealth() {
    try {
        const health = await HealthAPI.check();
        const dot        = document.getElementById('statusDot');
        const text       = document.getElementById('statusText');
        const serverDot  = document.getElementById('serverDot');
        const serverText = document.getElementById('serverStatusText');

        if (health.mt5_connected) {
            if (dot)        dot.className        = 'status-dot connected';
            if (text)       text.textContent     = 'MT5 Conectado';
            if (serverDot)  serverDot.className  = 'status-dot connected';
            if (serverText) serverText.textContent = 'Servidor Online';

            // Carga inicial: si aún no se han cargado datos, cargarlos ahora.
            // Se evalúa en cada health check para recuperarse de fallos de red
            // en el primer intento (servidor tardó, EC2 frío, etc.)
            if (!AppState.initialDataLoaded) {
                console.log('MT5 conectado — cargando datos iniciales...');
                AppState.initialDataLoaded = true;
                await loadDashboard();
                // Arrancar el auto-refresh solo después de la carga inicial exitosa
                startAutoRefresh();
            }
        } else {
            if (dot)        dot.className        = 'status-dot disconnected';
            if (text)       text.textContent     = health.status === 'offline'
                ? 'Servidor Offline' : 'MT5 Desconectado';
            if (serverDot)  serverDot.className  = 'status-dot disconnected';
            if (serverText) serverText.textContent = health.status === 'offline'
                ? 'Servidor Offline' : 'Servidor Online, MT5 Desconectado';

            // Si se pierde la conexión después de haber cargado,
            // permitir reintentar la carga inicial cuando vuelva
            if (AppState.initialDataLoaded) {
                AppState.initialDataLoaded = false;
                if (AppState.refreshInterval) {
                    clearInterval(AppState.refreshInterval);
                    AppState.refreshInterval = null;
                }
            }
        }
    } catch (err) {
        console.warn('Health check falló:', err.message);
    } finally {
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

        // Color del profit — azul MT5 para positivo, rojo para negativo
        const profitEl = document.getElementById('profit');
        if (account.profit > 0) {
            profitEl.style.color = '#378ADD';
            profitEl.style.fontWeight = '700';
            profitEl.textContent = '+$' + account.profit.toFixed(2);
        } else if (account.profit < 0) {
            profitEl.style.color = '#E24B4A';
            profitEl.style.fontWeight = '700';
            profitEl.textContent = '-$' + Math.abs(account.profit).toFixed(2);
        } else {
            profitEl.style.color = 'var(--color-text-secondary)';
            profitEl.style.fontWeight = '400';
        }
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

        tbody.innerHTML = positions.map(pos => {
            const isPositive  = pos.profit > 0;
            const isNegative  = pos.profit < 0;
            const pnlColor    = isPositive ? '#378ADD' : isNegative ? '#E24B4A' : 'var(--color-text-secondary)';
            const pnlArrow    = isPositive ? '▲' : isNegative ? '▼' : '●';
            const pnlSign     = isPositive ? '+' : '';
            const typeColor   = pos.type === 'BUY' ? '#378ADD' : '#E24B4A';
            const priceDiff   = pos.type === 'BUY'
                ? (pos.price_current - pos.price_open)
                : (pos.price_open - pos.price_current);
            const pips        = (priceDiff / (pos.symbol.includes('JPY') ? 0.01 : 0.0001)).toFixed(1);
            const pipsColor   = priceDiff >= 0 ? '#378ADD' : '#E24B4A';

            return `
            <tr style="transition: background 0.3s">
                <td><strong>${pos.symbol}</strong></td>
                <td style="color:${typeColor};font-weight:600">${pos.type}</td>
                <td>${pos.volume}</td>
                <td style="font-size:12px">${pos.price_open}</td>
                <td style="color:${pnlColor};font-weight:600;font-size:14px">
                    ${pnlArrow} ${pnlSign}$${Math.abs(pos.profit).toFixed(2)}
                    <div style="font-size:10px;color:${pipsColor};font-weight:400">
                        ${priceDiff >= 0 ? '+' : ''}${pips} pips
                    </div>
                </td>
                <td>
                    <button class="btn-icon" onclick="closePosition(${pos.ticket})" title="Cerrar posición">
                        <i class="fas fa-times"></i>
                    </button>
                </td>
            </tr>`;
        }).join('');
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
            const change = data.daily_change !== undefined ? data.daily_change : 0;
            const digits = data.digits !== undefined ? data.digits : 5;
            items.push(`
                <div class="watchlist-item" onclick="navigateTo('trading')">
                    <span class="watchlist-symbol">${symbol}</span>
                    <span class="watchlist-price">${data.bid.toFixed(digits)}</span>
                    <span class="watchlist-change ${change >= 0 ? 'up' : 'down'}">
                        ${change >= 0 ? '▲' : '▼'} ${Math.abs(change).toFixed(2)}%
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

        tbody.innerHTML = positions.map(pos => {
            const isPositive = pos.profit > 0;
            const isNegative = pos.profit < 0;
            const pnlColor   = isPositive ? '#378ADD' : isNegative ? '#E24B4A' : 'var(--color-text-secondary)';
            const pnlBg      = isPositive ? 'rgba(55,138,221,0.08)' : isNegative ? 'rgba(226,75,74,0.08)' : 'transparent';
            const pnlArrow   = isPositive ? '▲' : isNegative ? '▼' : '●';
            const pnlSign    = isPositive ? '+' : '';
            const typeColor  = pos.type === 'BUY' ? '#378ADD' : '#E24B4A';
            const typeBg     = pos.type === 'BUY' ? 'rgba(55,138,221,0.12)' : 'rgba(226,75,74,0.12)';
            const priceDiff  = pos.type === 'BUY'
                ? (pos.price_current - pos.price_open)
                : (pos.price_open - pos.price_current);
            const isJPY      = pos.symbol.includes('JPY');
            const pipSize    = isJPY ? 0.01 : pos.symbol.includes('XAU') ? 0.1 : 0.0001;
            const pips       = (priceDiff / pipSize).toFixed(1);
            const pipsColor  = priceDiff >= 0 ? '#378ADD' : '#E24B4A';
            const slDist     = pos.stop_loss
                ? Math.abs(pos.price_current - pos.stop_loss).toFixed(pos.symbol.includes('JPY') ? 2 : 5)
                : '--';
            const tpDist     = pos.take_profit
                ? Math.abs(pos.take_profit - pos.price_current).toFixed(pos.symbol.includes('JPY') ? 2 : 5)
                : '--';

            return `
            <tr>
                <td style="font-size:11px;color:var(--color-text-secondary)">#${pos.ticket}</td>
                <td><strong>${pos.symbol}</strong></td>
                <td>
                    <span style="color:${typeColor};background:${typeBg};padding:2px 8px;border-radius:4px;font-weight:600;font-size:12px">
                        ${pos.type}
                    </span>
                </td>
                <td>${pos.volume}</td>
                <td style="font-size:12px">${pos.price_open}</td>
                <td style="font-weight:500">${pos.price_current}
                    <div style="font-size:10px;color:${pipsColor}">${priceDiff >= 0 ? '+' : ''}${pips} pips</div>
                </td>
                <td style="font-size:12px;color:#E24B4A">${pos.stop_loss || '--'}</td>
                <td style="font-size:12px;color:#378ADD">${pos.take_profit || '--'}</td>
                <td style="background:${pnlBg};border-radius:6px;padding:4px 8px">
                    <div style="color:${pnlColor};font-weight:700;font-size:15px">
                        ${pnlArrow} ${pnlSign}$${Math.abs(pos.profit).toFixed(2)}
                    </div>
                </td>
                <td>
                    <button class="btn-icon" onclick="closePosition(${pos.ticket})" title="Cerrar"
                        style="color:#E24B4A">
                        <i class="fas fa-times"></i>
                    </button>
                </td>
            </tr>`;
        }).join('');
    } catch (err) {
        console.warn('No se pudo cargar posiciones:', err.message);
    }
}

// ============ SETTINGS ============

function loadSettings() {
    // Default dinámico: mismo puerto de la página actual, no uno fijo
    // (evita quedar apuntando a un puerto/proceso viejo si el backend cambia).
    const port = window.location.port ? `:${window.location.port}` : '';
    const dynamicDefault = `${window.location.protocol}//${window.location.hostname}${port}/api/v1`;
    const apiUrl = localStorage.getItem('apiUrl') || dynamicDefault;
    document.getElementById('settingApiUrl').value = apiUrl;
    checkServerHealth();
}

function saveSettings() {
    // Normaliza para que siempre termine en /api/v1 sin importar lo que escriba
    // el usuario (con o sin el sufijo) — evita que el dashboard quede en blanco
    // por peticiones 404 silenciosas.
    let apiUrl = document.getElementById('settingApiUrl').value.trim().replace(/\/+$/, '');
    if (!apiUrl.endsWith('/api/v1')) apiUrl += '/api/v1';
    localStorage.setItem('apiUrl', apiUrl);
    document.getElementById('settingApiUrl').value = apiUrl;
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

// ============ SEGUIMIENTO — CARGA AUTOMÁTICA DESDE HISTORIAL MT5 ============

/**
 * Carga el historial de deals cerrados desde MT5 y calcula las métricas
 * de seguimiento por estrategia + símbolo automáticamente.
 * Fusiona con los registros manuales existentes sin duplicar.
 */
async function segLoadFromHistory(days = 30) {
    const btn = document.getElementById('seg-btn-sync');
    if (btn) { btn.disabled = true; btn.textContent = 'Sincronizando...'; }

    try {
        const data = await OrdersAPI.getHistory(days);
        if (!data || !data.deals || data.deals.length === 0) {
            showToast('No hay historial de trades en los últimos ' + days + ' días', 'info');
            return;
        }

        // Agrupar deals por strategy + symbol
        // El campo "strategy" viene resuelto desde el backend via magic number
        // — nunca será "tp" ni "sl"
        const groups = {};
        for (const deal of data.deals) {
            const strat = deal.strategy || 'DESCONOCIDA';
            const key   = `${strat}_${deal.symbol}`;
            if (!groups[key]) {
                groups[key] = { symbol: deal.symbol, strat,
                                wins: 0, losses: 0,
                                totalWin: 0, totalLoss: 0, trades: 0,
                                lastDate: deal.time };
            }
            const g = groups[key];
            g.trades++;
            const net = deal.profit + (deal.commission || 0) + (deal.swap || 0);
            if (net >= 0) { g.wins++;   g.totalWin  += net; }
            else          { g.losses++; g.totalLoss += Math.abs(net); }
            if (deal.time > g.lastDate) g.lastDate = deal.time;
        }

        if (Object.keys(groups).length === 0) {
            showToast('Historial encontrado pero sin trades cerrados aún', 'info');
            return;
        }

        // Cargar registros existentes y agregar/actualizar
        const existing = segLoad();
        let added = 0, updated = 0;

        for (const [key, g] of Object.entries(groups)) {
            if (g.trades === 0) continue;
            const avgWin  = g.wins   > 0 ? g.totalWin  / g.wins   : 0;
            const avgLoss = g.losses > 0 ? g.totalLoss / g.losses : 0;
            const fecha   = new Date(g.lastDate).toLocaleDateString('es-CO',
                            { day: '2-digit', month: '2-digit', year: '2-digit' });

            const idx = existing.findIndex(r => r.sym === g.symbol && r.strat === g.strat);
            const entry = { strat: g.strat, sym: g.symbol, trades: g.trades,
                            wins: g.wins, avgWin: parseFloat(avgWin.toFixed(2)),
                            avgLoss: parseFloat(avgLoss.toFixed(2)),
                            fase: 'Fase 1', fecha };

            if (idx >= 0) { existing[idx] = entry; updated++; }
            else          { existing.push(entry); added++; }
        }

        segSave(existing);
        segRefresh();
        showToast(
            `Sincronizado: ${added} nuevos, ${updated} actualizados (${data.count} deals, ${days} días)`,
            'success'
        );
    } catch (err) {
        showToast('Error al sincronizar historial: ' + err.message, 'error');
        console.error('segLoadFromHistory error:', err);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '⟳ Sincronizar MT5'; }
    }
}

// ============================================================================
// MÓDULO DE RENDIMIENTO — Equity Curve, Drawdown, P&L por estrategia
// ============================================================================

let _rendChartEquity   = null;
let _rendChartStrategy = null;
let _rendChartHourly   = null;
let _rendDays          = 30;

async function rendLoadChart(days = 30) {
    _rendDays = days;

    // Actualizar botones activos
    ['7d','30d','90d'].forEach(d => {
        const btn = document.getElementById(`rend-btn-${d}`);
        if (btn) btn.classList.toggle('btn-active', `${days}d` === d ||
            (days === 7 && d === '7d') || (days === 30 && d === '30d') || (days === 90 && d === '90d'));
    });

    try {
        const data = await OrdersAPI.getHistory(days);
        if (!data || !data.deals || data.deals.length === 0) {
            rendShowEmpty();
            return;
        }

        const deals = data.deals;

        // --- Equity Curve ---
        // Construir serie temporal: balance acumulado a lo largo del tiempo
        const accountData = await AccountAPI.getInfo();
        const currentBalance = accountData ? accountData.balance : 1000;

        // Ordenar deals por tiempo
        const sorted = [...deals].sort((a, b) => new Date(a.time) - new Date(b.time));

        // Calcular P&L acumulado desde el pasado
        let totalPnl = sorted.reduce((sum, d) => sum + (d.profit || 0) + (d.commission || 0) + (d.swap || 0), 0);
        let runningBalance = currentBalance - totalPnl;

        const equityLabels = [];
        const equityValues = [];
        let peak = runningBalance;
        let maxDrawdown = 0;
        const drawdownValues = [];

        for (const deal of sorted) {
            const net = (deal.profit || 0) + (deal.commission || 0) + (deal.swap || 0);
            runningBalance += net;
            const date = new Date(deal.time);
            equityLabels.push(date.toLocaleDateString('es-CO', {month:'short', day:'numeric'}));
            equityValues.push(parseFloat(runningBalance.toFixed(2)));

            if (runningBalance > peak) peak = runningBalance;
            const dd = peak > 0 ? ((peak - runningBalance) / peak * 100) : 0;
            if (dd > maxDrawdown) maxDrawdown = dd;
            drawdownValues.push(parseFloat(dd.toFixed(2)));
        }

        // Agregar punto actual
        equityLabels.push('Ahora');
        equityValues.push(parseFloat(currentBalance.toFixed(2)));

        // Actualizar métricas
        const startBalance = equityValues[0] || currentBalance;
        const pnl = currentBalance - startBalance;
        document.getElementById('rend-balance').textContent   = '$' + currentBalance.toFixed(2);
        document.getElementById('rend-pnl').textContent       = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
        document.getElementById('rend-pnl').style.color       = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
        document.getElementById('rend-drawdown').textContent  = maxDrawdown.toFixed(2) + '%';
        document.getElementById('rend-trades').textContent    = deals.length;

        // Renderizar Equity Chart
        const ctxEq = document.getElementById('equityChart').getContext('2d');
        if (_rendChartEquity) _rendChartEquity.destroy();
        _rendChartEquity = new Chart(ctxEq, {
            type: 'line',
            data: {
                labels: equityLabels,
                datasets: [{
                    label: 'Balance',
                    data: equityValues,
                    borderColor: '#3B6D11',
                    backgroundColor: 'rgba(59,109,17,0.08)',
                    borderWidth: 2,
                    pointRadius: 2,
                    fill: true,
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { maxTicksLimit: 10, color: '#888' }, grid: { color: 'rgba(128,128,128,0.1)' } },
                    y: { ticks: { color: '#888', callback: v => '$' + v.toFixed(0) }, grid: { color: 'rgba(128,128,128,0.1)' } }
                }
            }
        });

        // --- P&L por estrategia ---
        const stratMap = {};
        for (const deal of deals) {
            const strat = deal.strategy || 'DESCONOCIDA';
            if (!stratMap[strat]) stratMap[strat] = 0;
            stratMap[strat] += (deal.profit || 0) + (deal.commission || 0) + (deal.swap || 0);
        }
        const stratLabels = Object.keys(stratMap).sort((a, b) => stratMap[b] - stratMap[a]);
        const stratValues = stratLabels.map(s => parseFloat(stratMap[s].toFixed(2)));
        const stratColors = stratValues.map(v => v >= 0 ? '#3B6D11' : '#A32D2D');

        const ctxSt = document.getElementById('strategyPnlChart').getContext('2d');
        if (_rendChartStrategy) _rendChartStrategy.destroy();
        _rendChartStrategy = new Chart(ctxSt, {
            type: 'bar',
            data: {
                labels: stratLabels,
                datasets: [{ data: stratValues, backgroundColor: stratColors, borderRadius: 4 }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#888', font: { size: 11 } }, grid: { display: false } },
                    y: { ticks: { color: '#888', callback: v => '$' + v.toFixed(0) }, grid: { color: 'rgba(128,128,128,0.1)' } }
                }
            }
        });

        // --- Trades por hora ---
        const hourMap = new Array(24).fill(0);
        const hourWins = new Array(24).fill(0);
        for (const deal of deals) {
            const h = new Date(deal.time).getUTCHours();
            hourMap[h]++;
            const net = (deal.profit || 0) + (deal.commission || 0) + (deal.swap || 0);
            if (net >= 0) hourWins[h]++;
        }

        const ctxHr = document.getElementById('hourlyChart').getContext('2d');
        if (_rendChartHourly) _rendChartHourly.destroy();
        _rendChartHourly = new Chart(ctxHr, {
            type: 'bar',
            data: {
                labels: Array.from({length:24}, (_,i) => `${i}h`),
                datasets: [
                    { label: 'Trades', data: hourMap, backgroundColor: 'rgba(55,138,221,0.5)', borderRadius: 3 },
                    { label: 'Wins',   data: hourWins, backgroundColor: 'rgba(59,109,17,0.7)',  borderRadius: 3 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: true, labels: { color: '#888', font: { size: 11 } } } },
                scales: {
                    x: { ticks: { color: '#888', font: { size: 10 }, maxRotation: 0 }, grid: { display: false } },
                    y: { ticks: { color: '#888' }, grid: { color: 'rgba(128,128,128,0.1)' } }
                }
            }
        });

    } catch (err) {
        console.error('rendLoadChart error:', err);
        rendShowEmpty();
    }
}

function rendShowEmpty() {
    document.getElementById('rend-balance').textContent  = '--';
    document.getElementById('rend-pnl').textContent      = '--';
    document.getElementById('rend-drawdown').textContent = '--';
    document.getElementById('rend-trades').textContent   = '0';
}

// ============================================================================
// MÓDULO DE BACKTESTING
// ============================================================================

let _btChart = null;

async function runBacktest() {
    const symbol      = document.getElementById('bt-symbol').value;
    const strategy    = document.getElementById('bt-strategy').value;
    const days        = parseInt(document.getElementById('bt-days').value);
    const balance     = parseFloat(document.getElementById('bt-balance').value) || 1000;
    const riskPct     = parseFloat(document.getElementById('bt-risk').value) / 100 || 0.01;

    const btnEl       = document.getElementById('bt-run-btn');
    const resultsEl   = document.getElementById('bt-results');
    const loadingEl   = document.getElementById('bt-loading');

    btnEl.disabled    = true;
    btnEl.textContent = '⏳ Ejecutando...';
    resultsEl.style.display  = 'none';
    loadingEl.style.display  = 'flex';

    try {
        const r = await StrategiesAPI.backtest(symbol, strategy, days, balance, riskPct);

        loadingEl.style.display  = 'none';
        resultsEl.style.display  = 'block';

        const pnlColor = r.total_pnl >= 0 ? '#3B6D11' : '#A32D2D';
        const metrics = [
            { label: 'P&L total',      value: `${r.total_pnl >= 0 ? '+' : ''}$${r.total_pnl.toFixed(2)}`, color: pnlColor },
            { label: 'Retorno',        value: `${r.total_pnl_pct >= 0 ? '+' : ''}${r.total_pnl_pct.toFixed(2)}%`, color: pnlColor },
            { label: 'Win Rate',       value: `${r.win_rate.toFixed(1)}%`, color: r.win_rate >= 50 ? '#3B6D11' : '#A32D2D' },
            { label: 'Trades',         value: r.trades },
            { label: 'Profit Factor',  value: r.profit_factor.toFixed(2) },
            { label: 'Max Drawdown',   value: `-${r.max_drawdown.toFixed(2)}%`, color: '#A32D2D' },
            { label: 'Gan. promedio',  value: `+$${r.avg_win.toFixed(2)}`, color: '#3B6D11' },
            { label: 'Pérd. promedio', value: `-$${r.avg_loss.toFixed(2)}`, color: '#A32D2D' },
        ];

        document.getElementById('bt-metrics').innerHTML = metrics.map(m => `
            <div style="background:var(--color-background-secondary);padding:.7rem;border-radius:var(--border-radius-md)">
                <div style="font-size:11px;color:var(--color-text-secondary)">${m.label}</div>
                <div style="font-size:17px;font-weight:500;color:${m.color || 'var(--color-text-primary)'}">
                    ${m.value}
                </div>
            </div>`).join('');

        // Equity curve
        if (r.equity_curve && r.equity_curve.length > 1) {
            const ctx = document.getElementById('btEquityChart').getContext('2d');
            if (_btChart) _btChart.destroy();
            const balances = r.equity_curve.map(p => p.balance);
            const colors   = balances.map(b => b >= balance ? '#3B6D11' : '#A32D2D');
            _btChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: r.equity_curve.map((_, i) => i === 0 ? 'Inicio' : `T${i}`),
                    datasets: [{
                        data: balances,
                        borderColor: r.total_pnl >= 0 ? '#3B6D11' : '#A32D2D',
                        backgroundColor: r.total_pnl >= 0 ? 'rgba(59,109,17,0.08)' : 'rgba(163,45,45,0.08)',
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: false },
                        y: { ticks: { color: '#888', callback: v => '$' + v.toFixed(0) },
                             grid: { color: 'rgba(128,128,128,0.1)' } }
                    }
                }
            });
        }

    } catch (err) {
        loadingEl.style.display = 'none';
        resultsEl.style.display = 'block';
        document.getElementById('bt-metrics').innerHTML =
            `<div style="color:var(--color-text-danger);grid-column:span 2">Error: ${err.message}</div>`;
    } finally {
        btnEl.disabled    = false;
        btnEl.textContent = '▶ Ejecutar Backtest';
    }
}