// Manual Filter JS - Core

let API_BASE = '/api/manual_filter';
const IS_DUTY_WORKSPACE = document.body.dataset.workspaceMode === 'duty';
const INITIAL_TAB = document.body.dataset.initialTab === 'review' ? 'review' : 'filter';

const GROUP_ORDER = [
    { key: 'internal_negative', label: '京内负面' },
    { key: 'internal_positive', label: '京内正面' },
    { key: 'external_positive', label: '京外正面' },
    { key: 'external_negative', label: '京外负面' }
];

const FILTER_CATEGORIES = ['internal_positive', 'internal_negative', 'external_positive', 'external_negative'];

// State
let state = {
    filterPage: 1,
    reviewPage: 1,
    discardPage: 1,
    currentTab: INITIAL_TAB,
    filterCategory: 'internal_positive',
    filterQuery: '',
    filterPublishedBefore: '',
    filterViewMode: 'browse',
    hideSubmitted: false,
    filterSearchTotal: 0,
    reviewView: 'selected',
    reviewReportType: 'zongbao',
    showGroups: true,
    reviewCollapsedGroups: {},
    reviewData: {
        selected: [],
        backup: []
    },
    filterCounts: {
        internal_positive: 0,
        internal_negative: 0,
        external_positive: 0,
        external_negative: 0
    },
    reviewCounts: {
        zongbao: { selected: 0, backup: 0 },
        wanbao: { selected: 0, backup: 0 }
    }
};

let shouldForceClusterRefresh = false;
let emptyFilterPageReloadTimer = null;
let reviewSortableInstances = [];

// UI mode
let isSortMode = false;
const MOBILE_REVIEW_BREAKPOINT = 768;

// DOM Elements
const elements = {
    tabs: document.querySelectorAll('.tab-btn'),
    contents: document.querySelectorAll('.tab-content'),
    filterList: document.getElementById('filter-list'),
    filterTabButtons: document.querySelectorAll('.filter-tab-btn[data-category]'),
    filterSearchInput: document.getElementById('filter-search-input'),
    filterSearchClear: document.getElementById('filter-search-clear'),
    filterDateBefore: document.getElementById('filter-date-before'),
    filterSearchMeta: document.getElementById('filter-search-meta'),
    hideSubmitted: document.getElementById('filter-hide-submitted'),
    reviewList: document.getElementById('review-list'),
    reviewSelectAll: document.getElementById('review-select-all'),
    reviewBulkStatus: document.getElementById('review-bulk-status'),
    discardList: document.getElementById('discard-list'),
    sortToggleBtn: document.getElementById('btn-toggle-sort'),
    reportTypeButtons: document.querySelectorAll('.report-type-btn'),
    stats: {
        pending: document.getElementById('stat-pending'),
        selected: document.getElementById('stat-selected'),
        backup: document.getElementById('stat-backup'),
        exported: document.getElementById('stat-exported')
    },
    reviewRailButtons: document.querySelectorAll('.review-category-btn'),
    reviewSearchInput: document.getElementById('review-search-input'),
    reviewSearchClear: document.getElementById('review-search-clear'),
    toast: document.getElementById('toast')
};

let isBulkUpdatingReview = false;
let pendingReviewEditPromise = Promise.resolve();
