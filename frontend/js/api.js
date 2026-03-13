/**
 * api.js - Cliente HTTP para comunicarse con el Backend FastAPI
 * Todas las llamadas a la API están centralizadas aquí
 */

// Auto-configuración desde URL
// Ejemplo: http://tuapp.com/?apiUrl=http://44.200.255.184:8000/api/v1
(function() {
    const params = new URLSearchParams(window.location.search);
    const apiUrlFromParams = params.get('apiUrl');
    if (apiUrlFromParams) {
        localStorage.setItem('apiUrl', apiUrlFromParams);
        // Limpiar la URL sin recargar la página
        const cleanUrl = window.location.pathname;
        window.history.replaceState({}, document.title, cleanUrl);
        console.log('✅ API URL configurada desde URL:', apiUrlFromParams);
    }
})();

const API_BASE = localStorage.getItem('apiUrl') || 'http://localhost:8000/api/v1';
console.log('🔌 Conectando a:', API_BASE);

// ============ HTTP CLIENT ============

async function apiRequest(endpoint, method = 'GET', body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body) options.body = JSON.stringify(body);

    try {
        const response = await fetch(`${API_BASE}${endpoint}`, options);
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
    check: () => fetch(`${API_BASE.replace('/api/v1', '')}/api/v1/health`)
        .then(r => r.json())
        .catch(() => ({ status: 'offline', mt5_connected: false }))
};

// ============ ACCOUNT ============

const AccountAPI = {
    getInfo: () => apiRequest('/account/info'),
    getStatus: () => apiRequest('/account/status'),
};

// ============ MARKET ============

const MarketAPI = {
    getTicker: (symbol) => apiRequest(`/market/ticker/${symbol}`),
    getSymbols: () => apiRequest('/market/symbols'),
    getAnalysis: (symbol, timeframe = 16385) =>
        apiRequest(`/market/analysis/${symbol}?timeframe=${timeframe}`),
};

// ============ ORDERS ============

const OrdersAPI = {
    getPositions: () => apiRequest('/orders/'),
    createOrder: (orderData) => apiRequest('/orders/create', 'POST', orderData),
    closePosition: (ticket) => apiRequest(`/orders/close/${ticket}`, 'POST'),
};

// ============ STRATEGIES ============

const StrategiesAPI = {
    list: () => apiRequest('/strategies/'),
    getCatalog: () => apiRequest('/strategies/catalog'),
    start: (symbol, strategyType = 'MA_CROSS') =>
        apiRequest(`/strategies/start/${symbol}?strategy_type=${strategyType}`, 'POST'),
    stop: (strategyId) => apiRequest(`/strategies/stop/${strategyId}`, 'POST'),
};

// ============ ANALYSIS ============

const AnalysisAPI = {
    getFull: (symbol) => apiRequest(`/analysis/full/${symbol}`),
};