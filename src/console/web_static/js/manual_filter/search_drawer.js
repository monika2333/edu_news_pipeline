// Manual Filter JS - Search Drawer

// --- Search Drawer Logic ---

let searchState = {
    page: 1,
    limit: 20,
    loading: false,
    query: '',
    source: '',
    sentiment: '',
    status: '',
    startDate: '',
    endDate: ''
};

function setupSearchDrawer() {
    const toggleBtn = document.getElementById('search-drawer-toggle');
    const closeBtn = document.getElementById('search-drawer-close');
    const overlay = document.getElementById('search-overlay');
    const drawer = document.getElementById('search-drawer');
    const searchBtn = document.getElementById('btn-drawer-search');
    const inputs = document.querySelectorAll('.search-form-container input, .search-form-container select');

    if (!drawer) return;

    // Toggle logic
    function toggleDrawer(show) {
        drawer.classList.toggle('active', show);
        overlay.classList.toggle('active', show);
        toggleBtn.style.display = show ? 'none' : 'flex'; // Hide toggle when drawer is open
        localStorage.setItem('search_drawer_open', show);
    }

    if (toggleBtn) toggleBtn.addEventListener('click', () => toggleDrawer(true));
    if (closeBtn) closeBtn.addEventListener('click', () => toggleDrawer(false));
    if (overlay) overlay.addEventListener('click', () => toggleDrawer(false));
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && drawer.classList.contains('active')) {
            toggleDrawer(false);
        }
    });

    // Check persistence
    if (localStorage.getItem('search_drawer_open') === 'true') {
        toggleDrawer(true);
    }

    // Search logic
    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            searchState.page = 1;
            performDrawerSearch();
        });
    }

    inputs.forEach(input => {
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                searchState.page = 1;
                performDrawerSearch();
            }
        });
    });

    // Load persisted filters
    loadSearchFilters();
}

function loadSearchFilters() {
    try {
        const saved = JSON.parse(localStorage.getItem('search_filters') || '{}');
        if (saved) {
            if (saved.q) document.getElementById('search-q').value = saved.q;
            if (saved.source) document.getElementById('search-source').value = saved.source;
            if (saved.sentiment) document.getElementById('search-sentiment').value = saved.sentiment;
            if (saved.status) document.getElementById('search-status').value = saved.status;
            if (saved.startDate) document.getElementById('search-start-date').value = saved.startDate;
            if (saved.endDate) document.getElementById('search-end-date').value = saved.endDate;
        }
    } catch (e) { console.error('Failed to load search filters', e); }
}

function saveSearchFilters() {
    const filters = {
        q: document.getElementById('search-q').value,
        source: document.getElementById('search-source').value,
        sentiment: document.getElementById('search-sentiment').value,
        status: document.getElementById('search-status').value,
        startDate: document.getElementById('search-start-date').value,
        endDate: document.getElementById('search-end-date').value
    };
    localStorage.setItem('search_filters', JSON.stringify(filters));
    return filters;
}

async function performDrawerSearch() {
    const container = document.getElementById('search-results-list');
    const statsInfo = document.getElementById('search-results-stats');
    const pagination = document.getElementById('search-pagination');

    container.innerHTML = '<div class="loading">Searching...</div>';
    statsInfo.textContent = '';
    pagination.innerHTML = '';

    const filters = saveSearchFilters();

    const params = new URLSearchParams({
        page: searchState.page.toString(),
        limit: searchState.limit.toString()
    });

    if (filters.q) params.set('q', filters.q);
    if (filters.source) params.set('source', filters.source); // API expects list but single value works if handled or pass multiple
    // Checking API implementation: sources=source (List[str]). URL param source=...&source=... for list.
    // Dashboard inputs are single text fields, so single value is fine.

    if (filters.sentiment) params.set('sentiment', filters.sentiment);
    if (filters.status) params.set('status', filters.status);
    if (filters.startDate) params.set('start_date', filters.startDate);
    if (filters.endDate) params.set('end_date', filters.endDate);

    try {
        const res = await fetch(`/api/articles/search?${params.toString()}`);
        if (!res.ok) throw new Error('Search failed');
        const data = await res.json();

        renderDrawerSearchResults(data);
    } catch (e) {
        container.innerHTML = `<div class="error">Search failed: ${e.message}</div>`;
    }
}

function renderDrawerSearchResults(data) {
    const container = document.getElementById('search-results-list');
    const statsInfo = document.getElementById('search-results-stats');
    const pagination = document.getElementById('search-pagination');

    const items = data.items || [];
    const total = data.total || 0;
    const page = data.page || 1;
    const pages = data.pages || 1;

    statsInfo.textContent = `Found ${total} items`;

    if (!items.length) {
        container.innerHTML = '<div class="empty">No results found</div>';
        return;
    }

    container.innerHTML = items.map(item => `
        <div class="search-item">
            <h4>
                <a href="${item.url || '#'}" target="_blank" rel="noopener">${item.title || 'Untitled'}</a>
            </h4>
            <div class="search-meta">
                <span>${item.source || '-'}</span>
                <span>${item.publish_time_iso ? item.publish_time_iso.substring(0, 10) : (item.publish_time ? new Date(item.publish_time * 1000).toISOString().split('T')[0] : '-')}</span>
                <span class="badge ${getSentimentClass(item.sentiment_label)}">${item.sentiment_label || '-'}</span>
                <span>STATUS: ${item.status || 'unknown'}</span>
            </div>
            <div class="search-summary">${item.summary || item.llm_summary || '(No summary)'}</div>
        </div>
    `).join('');

    // Pagination
    let pagHtml = '';
    if (page > 1) {
        pagHtml += `<button class="btn btn-secondary btn-sm" onclick="changeSearchPage(${page - 1})">Prev</button>`;
    } else {
        pagHtml += `<button class="btn btn-secondary btn-sm" disabled>Prev</button>`;
    }
    pagHtml += `<span style="margin: 0 10px">Page ${page} of ${pages}</span>`;
    if (page < pages) {
        pagHtml += `<button class="btn btn-secondary btn-sm" onclick="changeSearchPage(${page + 1})">Next</button>`;
    } else {
        pagHtml += `<button class="btn btn-secondary btn-sm" disabled>Next</button>`;
    }
    pagination.innerHTML = pagHtml;
}

function changeSearchPage(newPage) {
    searchState.page = newPage;
    performDrawerSearch();
}