const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const resultsContainer = document.getElementById('results-container');
const statusText = document.getElementById('status-text');
const timeText = document.getElementById('time-text');
const resultsCount = document.getElementById('results-count');
const pagination = document.getElementById('pagination');
const prevBtn = document.getElementById('prev-btn');
const nextBtn = document.getElementById('next-btn');
const pageIndicator = document.getElementById('page-indicator');

let currentPage = 1;
let currentQuery = '';
const LIMIT = 10;

// Handle Search Execution
async function executeSearch(page = 1) {
    const query = searchInput.value.trim();
    if (!query) return;

    currentQuery = query;
    currentPage = page;

    // UI Update: Loading State
    statusText.textContent = "STATUS: SEARCHING...";
    statusText.style.color = "var(--neon-red)";
    resultsContainer.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--neon-blue);">[ FETCHING VECTORS... ]</div>';
    pagination.style.display = 'none';

    try {
        const response = await fetch(`/search?q=${encodeURIComponent(query)}&page=${page}&limit=${LIMIT}`);
        
        if (!response.ok) {
            throw new Error(`API Error: ${response.status}`);
        }

        const data = await response.json();
        renderResults(data);
    } catch (error) {
        statusText.textContent = "STATUS: ERROR";
        resultsContainer.innerHTML = `<div style="color: var(--neon-red); text-align: center;">[ SYSTEM FAILURE: ${error.message} ]</div>`;
    }
}

function renderResults(data) {
    // Update Stats
    statusText.textContent = "STATUS: SUCCESS";
    statusText.style.color = "var(--neon-blue)";
    timeText.textContent = `TIME: ${data.elapsed_ms}ms`;
    resultsCount.textContent = `MATCHES: ${data.total_results}`;

    // Render Cards
    resultsContainer.innerHTML = '';
    
    if (data.results.length === 0) {
        resultsContainer.innerHTML = '<div style="text-align:center; padding: 2rem; color: #555;">[ NO CORRELATION FOUND ]</div>';
        return;
    }

    data.results.forEach(result => {
        const score = (result.score * 100).toFixed(1);
        const card = document.createElement('div');
        card.className = 'result-card';
        card.innerHTML = `
            <a href="${result.url}" class="result-title" target="_blank">${result.title || 'UNTITLED_DOCUMENT'}</a>
            <div class="result-url">${result.url}</div>
            <div class="result-snippet">${result.about || result.body_snippet || ''}</div>
        `;
        resultsContainer.appendChild(card);
    });

    // Pagination Logic
    pagination.style.display = data.total_results > LIMIT ? 'flex' : 'none';
    pageIndicator.textContent = `PAGE: ${data.page}`;
    
    prevBtn.disabled = data.page <= 1;
    nextBtn.disabled = (data.page * data.limit) >= data.total_results;
}

// Event Listeners
searchBtn.addEventListener('click', () => executeSearch(1));

searchInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        executeSearch(1);
    }
});

prevBtn.addEventListener('click', () => {
    if (currentPage > 1) {
        executeSearch(currentPage - 1);
        window.scrollTo(0, 0);
    }
});

nextBtn.addEventListener('click', () => {
    executeSearch(currentPage + 1);
    window.scrollTo(0, 0);
});

// Initial Focus
searchInput.focus();
