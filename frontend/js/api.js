/**
 * api.js - Cliente HTTP para comunicarse con el Backend FastAPI
 * Todas las llamadas a la API están centralizadas aquí
 */

// Bug fix: leer la URL en el momento de cada petición, no al cargar la página.
// Así funciona correctamente tanto en localhost como en EC2 sin F5 adicional.
//
// Normaliza el valor guardado para que SIEMPRE termine en /api/v1, sin importar
// si el usuario lo guardó como "http://host:8000" o "http://host:8000/api/v1".
// Antes, si el campo de Configuración se guardaba sin el sufijo /api/v1 (el valor
// por defecto del input así lo sugiere), TODAS las peticiones fallaban con 404
// de forma silenciosa y el dashboard quedaba en blanco/$0.00 sin ningún error visible.
function getApiBase() {
    const stored = localStorage.getItem('apiUrl');
    // Usa el puerto de la página actual (window.location.port), no uno fijo:
    // el frontend lo sirve el mismo FastAPI, así que siempre coincide con el
    // backend real sin importar en qué puerto esté corriendo (8000, 8001, etc).
    const port = window.location.port ? `:${window.location.port}` : '';
    const base = stored || `${window.location.protocol}//${window.location.hostname}${port}`;
    const trimmed = base.replace(/\/+$/, '');
    return trimmed.endsWith('/api/v1') ? trimmed : `${trimmed}/api/v1`;
}

// ============ HTTP CLIENT ============

async function apiRequest(endpoint, method = 'GET', body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body) options.body = JSON.stringify(body);

    const base = getApiBase();
    try {
        const response = await fetch(`${base}${endpoint}`, options);
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }
        return await response.json();
    } catch (err) {
        console.error(`API Error [${method} ${endpoint}]:`, err.message);
        throw err;
    }
}

// ============ HEALTH ============

const HealthAPI = {
    check: () => {
        const base = getApiBase().replace('/api/v1', '');
        return fetch(`${base}/api/v1/health`)
            .then(r => r.json())
            .catch(() => ({ status: 'offline', mt5_connected: false }));
    }
};

// ============ ACCOUNT ============

const AccountAPI = {
    getInfo: () => apiRequest('/account/info'),
    getStatus: () => apiRequest('/account/status'),
};

// ============ MARKET ============

const MarketAPI = {
    getTicker:   (symbol) => apiRequest(`/market/ticker/${symbol}`),
    getSymbols:  () => apiRequest('/market/symbols'),
    getAnalysis: (symbol, timeframe = 16385) =>
        apiRequest(`/market/analysis/${symbol}?timeframe=${timeframe}`),
    // Endpoint de velas OHLC para el gráfico — faltaba en la versión anterior
    getCandles:  (symbol, timeframe = 60, count = 200) =>
        apiRequest(`/market/candles/${symbol}?timeframe=${timeframe}&count=${count}`),
};

// ============ ORDERS ============

const OrdersAPI = {
    getPositions:  () => apiRequest('/orders/'),
    createOrder:   (orderData) => apiRequest('/orders/create', 'POST', orderData),
    closePosition: (ticket) => apiRequest(`/orders/close/${ticket}`, 'POST'),
    getHistory:    (days = 30, symbol = null) => {
        const params = `days=${days}${symbol ? '&symbol=' + symbol : ''}`;
        return apiRequest(`/orders/history?${params}`);
    },
};

// ============ STRATEGIES ============

const StrategiesAPI = {
    list: () => apiRequest('/strategies/'),
    getCatalog: () => apiRequest('/strategies/catalog'),
    start: (symbol, strategyType = 'MA_CROSS') =>
        apiRequest(`/strategies/start/${symbol}?strategy_type=${strategyType}`, 'POST'),
    stop: (strategyId) => apiRequest(`/strategies/stop/${strategyId}`, 'POST'),
    backtest: (symbol, strategyType, days = 90, initialBalance = 1000, riskPct = 0.01) => {
        const params = new URLSearchParams({
            symbol, strategy_type: strategyType,
            days, initial_balance: initialBalance, risk_pct: riskPct
        });
        return apiRequest(`/strategies/backtest?${params}`, 'POST');
    },
};

// ============ ANALYSIS ============

const AnalysisAPI = {
    getFull: (symbol) => apiRequest(`/analysis/full/${symbol}`),
};