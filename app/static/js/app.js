/**
 * GitLab Code Review Tool - Main JavaScript
 */

// CSRF Token handling (if needed)
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

// API Helper
async function apiRequest(url, options = {}) {
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
        },
        credentials: 'same-origin',
    };

    const mergedOptions = {
        ...defaultOptions,
        ...options,
        headers: {
            ...defaultOptions.headers,
            ...options.headers,
        },
    };

    const response = await fetch(url, mergedOptions);

    // Handle session expiration
    if (response.status === 401) {
        window.location.href = '/login';
        return null;
    }

    return response;
}

// Show notification
function showNotification(message, type = 'info') {
    // Map custom types to Bootstrap alert types
    const typeMap = {
        'success': 'success',
        'error': 'danger',
        'warning': 'warning',
        'info': 'info'
    };
    const alertType = typeMap[type] || 'info';

    const container = document.querySelector('.flash-messages') || createFlashContainer();

    const alert = document.createElement('div');
    alert.className = `alert alert-${alertType} alert-dismissible fade show`;
    alert.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;

    container.appendChild(alert);

    // Auto dismiss after 5 seconds
    setTimeout(() => {
        alert.classList.remove('show');
        setTimeout(() => alert.remove(), 300);
    }, 5000);
}

function createFlashContainer() {
    const container = document.createElement('div');
    container.className = 'flash-messages';
    document.body.appendChild(container);
    return container;
}

// Show loading overlay
function showLoading() {
    const overlay = document.createElement('div');
    overlay.className = 'spinner-overlay';
    overlay.id = 'loadingOverlay';
    overlay.innerHTML = `
        <div class="spinner-border text-primary" role="status">
            <span class="visually-hidden">加载中...</span>
        </div>
    `;
    document.body.appendChild(overlay);
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.remove();
    }
}

// Format date
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    });
}

// Format duration
function formatDuration(startTime, endTime) {
    if (!endTime) return '-';

    const start = new Date(startTime);
    const end = new Date(endTime);
    const diff = Math.floor((end - start) / 1000);

    if (diff < 60) return `${diff}秒`;
    if (diff < 3600) return `${Math.floor(diff / 60)}分${diff % 60}秒`;
    return `${Math.floor(diff / 3600)}小时${Math.floor((diff % 3600) / 60)}分`;
}

// Confirm dialog wrapper
function confirmAction(message) {
    return new Promise((resolve) => {
        const result = confirm(message);
        resolve(result);
    });
}

// Toast notification (alternative to alert)
function showToast(message, type = 'info') {
    // Use Bootstrap toast if available
    const toastContainer = document.getElementById('toastContainer');
    if (toastContainer) {
        const toast = document.createElement('div');
        toast.className = `toast show`;
        toast.innerHTML = `
            <div class="toast-header bg-${type} text-white">
                <strong class="me-auto">提示</strong>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
            </div>
            <div class="toast-body">${message}</div>
        `;
        toastContainer.appendChild(toast);

        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    } else {
        // Fallback to showNotification
        showNotification(message, type);
    }
}

// Debounce function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Throttle function
function throttle(func, limit) {
    let inThrottle;
    return function executedFunction(...args) {
        if (!inThrottle) {
            func(...args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// Local storage helper
const storage = {
    get(key) {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : null;
        } catch (e) {
            console.error('Storage get error:', e);
            return null;
        }
    },
    set(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
            return true;
        } catch (e) {
            console.error('Storage set error:', e);
            return false;
        }
    },
    remove(key) {
        try {
            localStorage.removeItem(key);
            return true;
        } catch (e) {
            console.error('Storage remove error:', e);
            return false;
        }
    }
};

// Form validation helper
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return false;

    const requiredFields = form.querySelectorAll('[required]');
    let isValid = true;

    requiredFields.forEach(field => {
        if (!field.value.trim()) {
            field.classList.add('is-invalid');
            isValid = false;
        } else {
            field.classList.remove('is-invalid');
        }
    });

    return isValid;
}

// Modal functions for right drawer style
function openModal(modalId) {
    const overlay = document.querySelector('.modal-overlay');
    const modal = document.getElementById(modalId);
    
    if (overlay) {
        overlay.classList.add('active');
    }
    if (modal) {
        modal.classList.add('active');
        modal.removeAttribute('tabindex');
    }
    
    // Prevent body scroll
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    const overlays = document.querySelectorAll('.modal-overlay');
    const modals = document.querySelectorAll('.modal');
    
    overlays.forEach(overlay => overlay.classList.remove('active'));
    modals.forEach(modal => modal.classList.remove('active'));
    
    // Restore body scroll
    document.body.style.overflow = '';
}

// Close modal on ESC key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeModal();
    }
});

// Close modal when clicking overlay
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        closeModal();
    }
});

// Export functions for global use
window.apiRequest = apiRequest;
window.showNotification = showNotification;
window.showLoading = showLoading;
window.hideLoading = hideLoading;
window.formatDate = formatDate;
window.formatDuration = formatDuration;
window.confirmAction = confirmAction;
window.showToast = showToast;
window.storage = storage;
window.validateForm = validateForm;
window.openModal = openModal;
window.closeModal = closeModal;

// ── 通用工具函数（多页面共享）──────────────────────

// 日期格式化为 YYYY-MM-DD
function fmtDate(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}

// HTML 转义
function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

// Markdown 渲染（需 marked.js，fallback 到纯文本转义）
function renderMarkdown(md) {
    if (typeof marked !== 'undefined') {
        try { return marked.parse(md, { breaks: true, gfm: true }); } catch (e) { /* fallback */ }
    }
    return escapeHtml(md).replace(/\n/g, '<br>');
}

// 快速查询按钮初始化
// options: { defaultRange: 'today', startDateId: 'filterStartDate', endDateId: 'filterEndDate', onChange: function }
function initQuickFilter(options) {
    var opts = Object.assign({ defaultRange: 'today', startDateId: 'filterStartDate', endDateId: 'filterEndDate', onChange: null }, options);
    var startInput = document.getElementById(opts.startDateId);
    var endInput = document.getElementById(opts.endDateId);

    // 计算日期范围
    function calcRange(range) {
        var today = new Date();
        var start, end;
        if (range === 'today') {
            start = end = fmtDate(today);
        } else if (range === 'yesterday') {
            var d = new Date(); d.setDate(d.getDate() - 1);
            start = end = fmtDate(d);
        } else if (range === 'thisWeek') {
            var d = new Date(); var day = d.getDay();
            d.setDate(d.getDate() - (day === 0 ? 6 : day - 1));
            start = fmtDate(d); end = fmtDate(today);
        } else if (range === 'thisMonth') {
            var y = today.getFullYear();
            var m = String(today.getMonth() + 1).padStart(2, '0');
            start = y + '-' + m + '-01'; end = fmtDate(today);
        }
        return { start: start, end: end };
    }

    // 按钮点击
    document.querySelectorAll('.quick-filter-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var range = calcRange(btn.dataset.range);
            if (startInput) startInput.value = range.start;
            if (endInput) endInput.value = range.end;
            document.querySelectorAll('.quick-filter-btn').forEach(function (b) {
                b.classList.toggle('active', b === btn);
            });
            if (opts.onChange) opts.onChange(range);
        });
    });

    // 日期手动变更时清除按钮激活
    function clearActive() {
        document.querySelectorAll('.quick-filter-btn').forEach(function (btn) {
            btn.classList.remove('active');
        });
    }
    if (startInput) startInput.addEventListener('change', clearActive);
    if (endInput) endInput.addEventListener('change', clearActive);

    // 设置默认
    var defaultBtn = document.querySelector('.quick-filter-btn[data-range="' + opts.defaultRange + '"]');
    if (defaultBtn) {
        defaultBtn.classList.add('active');
        var range = calcRange(opts.defaultRange);
        if (startInput && !startInput.value) startInput.value = range.start;
        if (endInput && !endInput.value) endInput.value = range.end;
    }
}

// 通用分页渲染
// containerId: 分页容器DOM ID, currentPage: 当前页, totalPages: 总页数, onPageChange: 回调
function renderPagination(containerId, currentPage, totalPages, onPageChange) {
    var links = document.getElementById(containerId);
    if (!links) return;
    if (totalPages <= 1) { links.innerHTML = ''; return; }

    var html = '';
    // 上一页
    if (currentPage > 1) {
        html += '<span class="page-link" data-page="' + (currentPage - 1) + '">上一页</span>';
    } else {
        html += '<span class="page-link disabled">上一页</span>';
    }
    // 页码
    var start = Math.max(1, currentPage - 2);
    var end = Math.min(totalPages, currentPage + 2);
    if (start > 1) {
        html += '<span class="page-link" data-page="1">1</span>';
        if (start > 2) html += '<span class="page-link disabled">...</span>';
    }
    for (var i = start; i <= end; i++) {
        html += '<span class="page-link ' + (i === currentPage ? 'active' : '') + '" data-page="' + i + '">' + i + '</span>';
    }
    if (end < totalPages) {
        if (end < totalPages - 1) html += '<span class="page-link disabled">...</span>';
        html += '<span class="page-link" data-page="' + totalPages + '">' + totalPages + '</span>';
    }
    // 下一页
    if (currentPage < totalPages) {
        html += '<span class="page-link" data-page="' + (currentPage + 1) + '">下一页</span>';
    } else {
        html += '<span class="page-link disabled">下一页</span>';
    }
    links.innerHTML = html;

    // 绑定点击事件
    links.querySelectorAll('.page-link[data-page]').forEach(function (el) {
        el.addEventListener('click', function () {
            var page = parseInt(el.dataset.page);
            if (page >= 1 && page <= totalPages && onPageChange) onPageChange(page);
        });
    });
}

// 按钮加载状态辅助（异步操作期间禁用按钮并显示spinner）
async function withButtonLoading(btn, asyncFn, loadingText) {
    loadingText = loadingText || '处理中...';
    var originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' + loadingText;
    try {
        return await asyncFn();
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

window.fmtDate = fmtDate;
window.escapeHtml = escapeHtml;
window.renderMarkdown = renderMarkdown;
window.initQuickFilter = initQuickFilter;
window.renderPagination = renderPagination;
window.withButtonLoading = withButtonLoading;

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', function() {
    // Enable Bootstrap tooltips
    const tooltips = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltips.forEach(el => new bootstrap.Tooltip(el));

    // Enable Bootstrap popovers
    const popovers = document.querySelectorAll('[data-bs-toggle="popover"]');
    popovers.forEach(el => new bootstrap.Popover(el));

    // Auto-hide alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.classList.remove('show');
            setTimeout(() => alert.remove(), 300);
        }, 5000);
    });

    // Handle form submissions with loading state
    const forms = document.querySelectorAll('form[data-loading]');
    forms.forEach(form => {
        form.addEventListener('submit', function() {
            const submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerHTML = `
                    <span class="spinner-border spinner-border-sm me-2"></span>
                    处理中...
                `;
            }
        });
    });
});
