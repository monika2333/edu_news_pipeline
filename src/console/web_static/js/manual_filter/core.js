// Manual Filter JS - Core

const API_BASE = '/api/manual_filter';

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
    actor: localStorage.getItem('actor') || '',
    currentTab: 'filter',
    filterCategory: 'internal_positive',
    reviewView: 'selected',
    reviewReportType: 'zongbao',
    showGroups: true,
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
    reviewList: document.getElementById('review-list'),
    reviewSelectAll: document.getElementById('review-select-all'),
    reviewBulkStatus: document.getElementById('review-bulk-status'),
    discardList: document.getElementById('discard-list'),
    actorInput: document.getElementById('actor-input'),
    sortToggleBtn: document.getElementById('btn-toggle-sort'),
    exportTemplate: document.getElementById('export-template'),
    exportPeriod: document.getElementById('export-period'),
    exportTotal: document.getElementById('export-total'),
    exportPreviewBtn: document.getElementById('btn-export-preview'),
    exportConfirmBtn: document.getElementById('btn-export-confirm'),
    reportTypeButtons: document.querySelectorAll('.report-type-btn'),
    stats: {
        pending: document.getElementById('stat-pending'),
        selected: document.getElementById('stat-selected'),
        backup: document.getElementById('stat-backup'),
        exported: document.getElementById('stat-exported')
    },
    reviewRailButtons: document.querySelectorAll('.review-category-btn'),
    reviewSearchInput: document.getElementById('review-search-input'),
    modal: document.getElementById('export-modal'),
    modalText: document.getElementById('export-text'),
    toast: document.getElementById('toast')
};

let isBulkUpdatingReview = false;
