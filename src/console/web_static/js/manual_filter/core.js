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

// 归入报别（采纳/备选归入综报/晚报）记入 localStorage，刷新后保持上次选择，直到用户主动切换
const ASSIGN_REPORT_TYPE_KEY = 'manual_filter_assign_report_type';

function readStoredAssignReportType() {
    try {
        return localStorage.getItem(ASSIGN_REPORT_TYPE_KEY) === 'wanbao' ? 'wanbao' : 'zongbao';
    } catch (error) {
        return 'zongbao'; // 读取失败时回退默认综报
    }
}

// 「只看值班编辑未处理」开关（仅管理员侧渲染）记入 localStorage，刷新后保持上次选择
const FILTER_DUTY_SCOPE_KEY = 'manual_filter_duty_scope';

function readStoredFilterDutyScope() {
    try {
        return localStorage.getItem(FILTER_DUTY_SCOPE_KEY) === 'unprocessed' ? 'unprocessed' : 'all';
    } catch (error) {
        return 'all'; // 读取失败时回退默认「全部」
    }
}

// State
let state = {
    filterPage: 1,
    reviewPage: 1,
    discardPage: 1,
    currentTab: INITIAL_TAB,
    filterCategory: 'internal_positive',
    filterQuery: '',
    filterViewMode: 'browse',
    filterSearchTotal: 0,
    latestIngestedAt: null,
    reviewView: 'selected',
    reviewReportType: 'zongbao',
    filterAssignReportType: readStoredAssignReportType(),
    // 值班工作区不渲染该开关，恒为 'all'，避免同浏览器管理员会话的存储值泄漏到值班请求
    filterDutyScope: IS_DUTY_WORKSPACE ? 'all' : readStoredFilterDutyScope(),
    discardQuery: '',
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
    filterSearchMeta: document.getElementById('filter-search-meta'),
    filterDutyScopeButtons: document.querySelectorAll('[data-duty-process-scope]'),
    filterBulkDiscardBtn: document.getElementById('btn-filter-bulk-discard'),
    cleanupModal: document.getElementById('cleanup-modal'),
    cleanupDateInput: document.getElementById('cleanup-date-input'),
    cleanupCategoryList: document.getElementById('cleanup-category-list'),
    cleanupStats: document.getElementById('cleanup-modal-stats'),
    cleanupFinalizedNote: document.getElementById('cleanup-finalized-note'),
    cleanupCancelBtn: document.getElementById('btn-cleanup-cancel'),
    cleanupConfirmBtn: document.getElementById('btn-cleanup-confirm'),
    reviewList: document.getElementById('review-list'),
    reviewSelectAll: document.getElementById('review-select-all'),
    reviewBulkStatus: document.getElementById('review-bulk-status'),
    clearReviewBucketsBtn: document.getElementById('btn-clear-review-buckets'),
    clearReviewBucketsModal: document.getElementById('clear-review-buckets-modal'),
    clearReviewBucketsTotal: document.getElementById('clear-review-buckets-total'),
    clearReviewBucketsCancelBtn: document.getElementById('btn-clear-review-cancel'),
    clearReviewBucketsConfirmBtn: document.getElementById('btn-clear-review-confirm'),
    discardList: document.getElementById('discard-list'),
    discardSearchInput: document.getElementById('discard-search-input'),
    discardSearchClear: document.getElementById('discard-search-clear'),
    discardSearchMeta: document.getElementById('discard-search-meta'),
    sortToggleBtn: document.getElementById('btn-toggle-sort'),
    reportTypeButtons: document.querySelectorAll('.report-type-btn'),
    reportTypeTab: document.getElementById('report-type-tab'),
    reportTypeTabText: document.getElementById('report-type-tab-text'),
    reportTypePopover: document.getElementById('report-type-popover'),
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
// 筛选页编辑保存串行化队列，决定操作前必须等待它排空（参照审阅页 pendingReviewEditPromise）
let pendingFilterEditPromise = Promise.resolve();
// 筛选页卡片「最近一次被服务端确认的摘要/来源」基准，articleId → { summary, llm_source }
const filterEditBaselines = new Map();
// loadFilterData 请求序号，只有最新一次请求允许渲染列表
let filterLoadSeq = 0;

function showToast(msg, type = 'success', action = null) {
    showToastAt(elements.toast, msg, type, action);
}
