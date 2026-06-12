/* 人员能效页面 - Tab 切换 + 区间查询 + 月度查询 + 详情弹窗 */
(function () {
    'use strict';

    var STATE = {
        mode: 'daily',        // 'daily' | 'monthly'
        startDate: '',
        endDate: '',
        yearMonth: '',
        sort_by: 'score',
        order: 'desc',
        items: [],
        teamStats: null,
        isAdmin: false,
        isProjectAdmin: false,
        currentUserEmail: '',
    };

    var GRADE_CLASS = {
        '优秀': 'grade-excellent',
        '良好': 'grade-good',
        '一般': 'grade-average',
        '待改进': 'grade-poor',
    };

    var chartCodeTop, chartGradePie, chartTrend, chartMonthlyTrend;

    // ── 工具 ──────────────────────────────────────
    function fmtDate(d) {
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    function yesterday() {
        var d = new Date();
        d.setDate(d.getDate() - 1);
        return fmtDate(d);
    }

    function weekStart() {
        var d = new Date();
        var day = d.getDay();
        var diff = day === 0 ? 6 : day - 1;
        d.setDate(d.getDate() - diff);
        return fmtDate(d);
    }

    function currentMonth() {
        var d = new Date();
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        return y + '-' + m;
    }

    function gradeBadge(grade) {
        var cls = GRADE_CLASS[grade] || 'grade-none';
        return '<span class="grade-badge ' + cls + '">' + (grade || '-') + '</span>';
    }

    function formatTokenUsage(usage) {
        if (!usage || !usage.total_tokens) return '-';
        return String(usage.total_tokens).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    function escapeHtml(s) {
        if (s == null) return '';
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function renderMarkdown(md) {
        if (typeof marked === 'undefined') return escapeHtml(md);
        try {
            return marked.parse(md, { breaks: true, gfm: true });
        } catch (e) {
            return escapeHtml(md);
        }
    }

    // ── Tab 切换 ──────────────────────────────────
    window.switchEfficiencyTab = function(el, mode) {
        // 更新tab状态
        document.querySelectorAll('.desktop-tab-item').forEach(function(tab) {
            tab.classList.remove('active');
        });
        el.classList.add('active');

        // 更新模式状态
        STATE.mode = mode;
        document.getElementById('filterDaily').style.display = mode === 'daily' ? '' : 'none';
        document.getElementById('filterMonthly').style.display = mode === 'monthly' ? '' : 'none';
        updateTableHeader();
        loadData();
    };

    function switchTab(mode) {
        STATE.mode = mode;
        document.querySelectorAll('.desktop-tab-item').forEach(function (t) {
            t.classList.toggle('active', t.id === 'tab' + mode.charAt(0).toUpperCase() + mode.slice(1));
        });
        document.getElementById('filterDaily').style.display = mode === 'daily' ? '' : 'none';
        document.getElementById('filterMonthly').style.display = mode === 'monthly' ? '' : 'none';
        loadData();
    }

    // ── 数据加载 ─────────────────────────────────
    function loadData() {
        if (STATE.mode === 'daily') {
            loadDailyList();
        } else {
            loadMonthlyList();
        }
    }

    function loadDailyList() {
        var params = new URLSearchParams({
            start_date: STATE.startDate,
            end_date: STATE.endDate,
            sort_by: STATE.sort_by,
            order: STATE.order,
            limit: '500',
        });
        apiRequest('/api/efficiency/list?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    renderEmpty('数据加载失败');
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (!json) return;
                if (!json.success) {
                    renderEmpty(json.message || '加载失败');
                    return;
                }
                STATE.items = json.data.items || [];
                STATE.teamStats = json.data.team_stats || {};
                renderStats();
                renderTable();
                renderCharts();
            })
            .catch(function (err) {
                console.error('loadDailyList failed:', err);
                renderEmpty('网络异常，请稍后重试');
            });
    }

    function loadMonthlyList() {
        var params = new URLSearchParams({
            year_month: STATE.yearMonth,
            sort_by: STATE.sort_by,
            order: STATE.order,
            limit: '500',
        });
        apiRequest('/api/efficiency/monthly/list?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    renderEmpty('数据加载失败');
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (!json) return;
                if (!json.success) {
                    renderEmpty(json.message || '加载失败');
                    return;
                }
                STATE.items = json.data.items || [];
                STATE.teamStats = json.data.team_stats || {};
                renderStats();
                renderMonthlyTable();
                renderCharts();
            })
            .catch(function (err) {
                console.error('loadMonthlyList failed:', err);
                renderEmpty('网络异常，请稍后重试');
            });
    }

    // ── 渲染：团队概览 ──────────────────────────
    function renderStats() {
        var s = STATE.teamStats || {};
        document.getElementById('stat-commits').textContent = s.total_commits != null ? s.total_commits : '-';
        document.getElementById('stat-add').textContent = '+' + (s.total_additions || 0);
        document.getElementById('stat-del').textContent = '-' + (s.total_deletions || 0);
        document.getElementById('stat-avg').textContent = s.avg_score != null ? s.avg_score : '-';
        document.getElementById('stat-count').textContent = s.person_count || 0;
    }

    // ── 渲染：表格 ───────────────────────────────
    function renderEmpty(msg) {
        var cols = STATE.mode === 'daily' ? 10 : 11;
        document.getElementById('efficiencyTbody').innerHTML =
            '<tr><td colspan="' + cols + '" class="text-center text-muted py-4">' + msg + '</td></tr>';
    }

    function renderTable() {
        // 按天模式表格（区间汇总）
        if (!STATE.items.length) {
            var hint = STATE.isAdmin ? '请点击右上方"立即补算"。' : '请联系管理员。';
            renderEmpty('该日期范围无数据。' + hint);
            return;
        }
        var rows = STATE.items.map(function (it) {
            var clickable = canViewDetail(it);
            var rowStyle = clickable ? '' : ' style="cursor:default;opacity:0.7;"';
            var rowClass = clickable ? ' class="clickable-row"' : '';
            return '<tr data-email="' + escapeHtml(it.author_email) + '"' + rowStyle + rowClass + '>' +
                '<td>' + escapeHtml(it.author_name) + '</td>' +
                '<td><span class="text-muted small">' + escapeHtml(it.author_email) + '</span></td>' +
                '<td>' + escapeHtml(it.commits_count) + '</td>' +
                '<td class="text-success">+' + escapeHtml(it.additions) + '</td>' +
                '<td class="text-danger">-' + escapeHtml(it.deletions) + '</td>' +
                '<td>' + escapeHtml(it.files_changed) + '</td>' +
                '<td>' + (it.review_score != null ? escapeHtml(it.review_score) : '-') + '</td>' +
                '<td>' + gradeBadge(it.review_grade) + '</td>' +
                '<td>' + escapeHtml(formatTokenUsage(it.token_usage)) + '</td>' +
                '<td><span class="text-muted small">' +
                escapeHtml((it.projects_involved || []).join('，')) + '</span></td>' +
                '</tr>';
        }).join('');
        document.getElementById('efficiencyTbody').innerHTML = rows;

        // 区间模式：点击行 → 弹窗显示每日明细（仅可查看的行）
        var isRange = STATE.startDate !== STATE.endDate;
        document.querySelectorAll('#efficiencyTbody tr[data-email].clickable-row').forEach(function (tr) {
            tr.addEventListener('click', function () {
                if (isRange) {
                    openRangeDetailModal(tr.dataset.email);
                } else {
                    openDrawer(tr.dataset.email);
                }
            });
        });
    }

    function renderMonthlyTable() {
        // 按月模式表格
        if (!STATE.items.length) {
            renderEmpty('该月无数据。请点击"立即补算"生成月度汇总。');
            return;
        }
        var rows = STATE.items.map(function (it) {
            var clickable = canViewDetail(it);
            var rowStyle = clickable ? '' : ' style="cursor:default;opacity:0.7;"';
            var rowClass = clickable ? ' class="clickable-row"' : '';
            return '<tr data-email="' + escapeHtml(it.author_email) + '"' + rowStyle + rowClass + '>' +
                '<td>' + escapeHtml(it.author_name) + '</td>' +
                '<td><span class="text-muted small">' + escapeHtml(it.author_email) + '</span></td>' +
                '<td>' + escapeHtml(it.active_days) + '</td>' +
                '<td>' + escapeHtml(it.commits_count) + '</td>' +
                '<td class="text-success">+' + escapeHtml(it.additions) + '</td>' +
                '<td class="text-danger">-' + escapeHtml(it.deletions) + '</td>' +
                '<td>' + escapeHtml(it.files_changed) + '</td>' +
                '<td>' + (it.review_score != null ? escapeHtml(it.review_score) : '-') + '</td>' +
                '<td>' + gradeBadge(it.review_grade) + '</td>' +
                '<td>' + escapeHtml(formatTokenUsage(it.token_usage)) + '</td>' +
                '<td><span class="text-muted small">' +
                escapeHtml((it.projects_involved || []).join('，')) + '</span></td>' +
                '</tr>';
        }).join('');
        document.getElementById('efficiencyTbody').innerHTML = rows;

        // 点击行 → 月度详情弹窗（仅可查看的行）
        document.querySelectorAll('#efficiencyTbody tr[data-email].clickable-row').forEach(function (tr) {
            tr.addEventListener('click', function () {
                openMonthlyDetailModal(tr.dataset.email);
            });
        });
    }

    // ── 渲染：ECharts ────────────────────────────
    function renderCharts() {
        renderCodeTopChart();
        renderGradePieChart();
    }

    function renderCodeTopChart() {
        if (!chartCodeTop) {
            chartCodeTop = echarts.init(document.getElementById('chartCodeTop'));
        }
        var top = STATE.items.slice()
            .sort(function (a, b) { return (b.additions + b.deletions) - (a.additions + a.deletions); })
            .slice(0, 10).reverse();
        chartCodeTop.setOption({
            title: { text: '代码量 TOP 10', left: 'left', textStyle: { fontSize: 14 } },
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { right: 10, data: ['新增', '删除'] },
            grid: { left: 100, right: 30, top: 50, bottom: 20 },
            xAxis: { type: 'value' },
            yAxis: { type: 'category', data: top.map(function (t) { return t.author_name; }) },
            series: [
                { name: '新增', type: 'bar', stack: 'total', color: '#28a745',
                  data: top.map(function (t) { return t.additions; }) },
                { name: '删除', type: 'bar', stack: 'total', color: '#dc3545',
                  data: top.map(function (t) { return t.deletions; }) },
            ],
        });
    }

    function renderGradePieChart() {
        if (!chartGradePie) {
            chartGradePie = echarts.init(document.getElementById('chartGradePie'));
        }
        var buckets = { '优秀': 0, '良好': 0, '一般': 0, '待改进': 0, '未评': 0 };
        STATE.items.forEach(function (it) {
            var key = it.review_grade || '未评';
            buckets[key] = (buckets[key] || 0) + 1;
        });
        var pieData = Object.keys(buckets)
            .filter(function (k) { return buckets[k] > 0; })
            .map(function (k) { return { name: k, value: buckets[k] }; });
        chartGradePie.setOption({
            title: { text: '评分分布', left: 'left', textStyle: { fontSize: 14 } },
            tooltip: { trigger: 'item' },
            color: ['#28a745', '#0d6efd', '#ffc107', '#dc3545', '#999'],
            series: [{
                type: 'pie', radius: ['40%', '70%'], center: ['50%', '55%'],
                data: pieData,
                label: { formatter: '{b}: {c}' },
            }],
        });
    }

    // ── 区间明细弹窗 ─────────────────────────────
    function openRangeDetailModal(email) {
        var modal = document.getElementById('rangeDetailModal');
        modal.classList.add('active');
        document.getElementById('rangeModalTitle').textContent =
            email + ' (' + STATE.startDate + ' ~ ' + STATE.endDate + ')';
        document.getElementById('rangeModalBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            start_date: STATE.startDate,
            end_date: STATE.endDate,
        });
        apiRequest('/api/efficiency/detail?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    document.getElementById('rangeModalBody').innerHTML =
                        '<div class="text-danger">加载失败</div>';
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (json) renderRangeDetailModal(json.data || {}, email);
            })
            .catch(function () {
                document.getElementById('rangeModalBody').innerHTML =
                    '<div class="text-danger">网络异常</div>';
            });
    }

    function renderRangeDetailModal(data, email) {
        var items = data.daily_items || [];
        if (!items.length) {
            document.getElementById('rangeModalBody').innerHTML =
                '<div class="text-muted">该区间无数据</div>';
            return;
        }

        var tableRows = items.map(function (d) {
            return '<tr data-date="' + escapeHtml(d.stat_date) + '" style="cursor:pointer;">' +
                '<td>' + escapeHtml(d.stat_date) + '</td>' +
                '<td>' + escapeHtml(d.commits_count) + '</td>' +
                '<td class="text-success">+' + escapeHtml(d.additions) + '</td>' +
                '<td class="text-danger">-' + escapeHtml(d.deletions) + '</td>' +
                '<td>' + escapeHtml(d.files_changed) + '</td>' +
                '<td>' + (d.review_score != null ? escapeHtml(d.review_score) : '-') + '</td>' +
                '<td>' + gradeBadge(d.review_grade) + '</td>' +
                '<td>' + escapeHtml(formatTokenUsage(d.token_usage)) + '</td>' +
                '</tr>';
        }).join('');

        var html =
            '<div class="text-muted small mb-2">区间每日明细（点击行查看详情）</div>' +
            '<table class="daily-detail-table">' +
            '<thead><tr>' +
            '<th>日期</th><th>提交</th><th>新增</th><th>删除</th><th>文件</th><th>评分</th><th>等级</th><th>Token</th>' +
            '</tr></thead>' +
            '<tbody>' + tableRows + '</tbody></table>';

        document.getElementById('rangeModalBody').innerHTML = html;

        // 点击行 → 关闭弹窗，打开详情抽屉
        document.querySelectorAll('#rangeModalBody tr[data-date]').forEach(function (tr) {
            tr.addEventListener('click', function () {
                var selectedDate = tr.dataset.date;
                closeRangeDetailModal();
                openDrawerForDate(email, selectedDate);
            });
        });
    }

    function closeRangeDetailModal() {
        document.getElementById('rangeDetailModal').classList.remove('active');
    }

    // ── 月度详情弹窗 ─────────────────────────────
    function openMonthlyDetailModal(email) {
        var modal = document.getElementById('monthlyDetailModal');
        modal.classList.add('active');
        document.getElementById('monthlyModalTitle').textContent =
            email + ' ' + STATE.yearMonth + ' 月度详情';
        document.getElementById('monthlyModalBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            year_month: STATE.yearMonth,
        });
        apiRequest('/api/efficiency/monthly/detail?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    document.getElementById('monthlyModalBody').innerHTML =
                        '<div class="text-danger">加载失败</div>';
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (json) renderMonthlyDetailModal(json.data || {});
            })
            .catch(function () {
                document.getElementById('monthlyModalBody').innerHTML =
                    '<div class="text-danger">网络异常</div>';
            });
    }

    function renderMonthlyDetailModal(data) {
        var s = data.summary || {};
        var trend = data.daily_trend || [];
        var work = s.work_summary || [];

        var workHtml = '';
        if (work.length) {
            var markdownContent = work.map(function (w) { return '- ' + w; }).join('\n');
            workHtml = '<div class="markdown-body">' + renderMarkdown(markdownContent) + '</div>';
        } else {
            workHtml = '<div class="text-muted">无</div>';
        }

        var html =
            '<div class="mb-3" style="display:flex; gap:var(--space-4); align-items:center;">' +
            '  <div style="font-size:2rem; font-weight:700; color:var(--color-primary);">' +
            (s.review_score != null ? escapeHtml(s.review_score) : '-') + '</div>' +
            '  ' + gradeBadge(s.review_grade) +
            '  <span class="text-muted">活跃 ' + (s.active_days || 0) + ' 天</span>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">月度评分简述</div>' +
            '  <div class="markdown-body">' + renderMarkdown(s.review_summary || '-') + '</div>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">月度主要工作</div>' +
            '  ' + workHtml +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">每日趋势</div>' +
            '  <div id="chartMonthlyTrend" style="width:100%;height:280px;"></div>' +
            '</div>';
        document.getElementById('monthlyModalBody').innerHTML = html;
        renderMonthlyTrendChart(trend);
    }

    function renderMonthlyTrendChart(trend) {
        if (chartMonthlyTrend) { chartMonthlyTrend.dispose(); chartMonthlyTrend = null; }
        var el = document.getElementById('chartMonthlyTrend');
        if (!el) return;
        chartMonthlyTrend = echarts.init(el);
        chartMonthlyTrend.setOption({
            tooltip: { trigger: 'axis' },
            legend: { data: ['新增', '删除', '评分'] },
            grid: { left: 40, right: 40, top: 30, bottom: 30 },
            xAxis: { type: 'category', data: trend.map(function (t) { return t.stat_date; }) },
            yAxis: [
                { type: 'value', name: '代码量' },
                { type: 'value', name: '评分', min: 0, max: 100 },
            ],
            series: [
                { name: '新增', type: 'line', color: '#28a745',
                  data: trend.map(function (t) { return t.additions; }) },
                { name: '删除', type: 'line', color: '#dc3545',
                  data: trend.map(function (t) { return t.deletions; }) },
                { name: '评分', type: 'line', yAxisIndex: 1, color: '#0d6efd',
                  data: trend.map(function (t) { return t.review_score; }),
                  markLine: { data: [
                      { yAxis: 90, lineStyle: { color: '#28a745', type: 'dashed' } },
                      { yAxis: 60, lineStyle: { color: '#dc3545', type: 'dashed' } },
                  ] } },
            ],
        });
    }

    function closeMonthlyDetailModal() {
        document.getElementById('monthlyDetailModal').classList.remove('active');
        if (chartMonthlyTrend) { chartMonthlyTrend.dispose(); chartMonthlyTrend = null; }
    }

    // ── 详情抽屉（复用现有） ─────────────────────
    function openDrawerForDate(email, dateStr) {
        document.getElementById('drawerOverlay').classList.add('active');
        document.getElementById('detailDrawer').classList.add('active');
        document.getElementById('drawerTitle').textContent = email + ' (' + dateStr + ')';
        document.getElementById('drawerBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            date: dateStr,
            trend_days: '7',
        });
        apiRequest('/api/efficiency/detail?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    document.getElementById('drawerBody').innerHTML =
                        '<div class="text-danger">加载详情失败</div>';
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (json) renderDrawer(json.data || {});
            })
            .catch(function (err) {
                console.error('openDrawerForDate failed:', err);
                document.getElementById('drawerBody').innerHTML =
                    '<div class="text-danger">网络异常，请稍后重试</div>';
            });
    }

    function openDrawer(email) {
        document.getElementById('drawerOverlay').classList.add('active');
        document.getElementById('detailDrawer').classList.add('active');
        document.getElementById('drawerTitle').textContent = email + ' (' + STATE.startDate + ')';
        document.getElementById('drawerBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            date: STATE.startDate,
            trend_days: '7',
        });
        apiRequest('/api/efficiency/detail?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    document.getElementById('drawerBody').innerHTML =
                        '<div class="text-danger">加载详情失败</div>';
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (json) renderDrawer(json.data || {});
            })
            .catch(function (err) {
                console.error('openDrawer failed:', err);
                document.getElementById('drawerBody').innerHTML =
                    '<div class="text-danger">网络异常，请稍后重试</div>';
            });
    }

    function renderDailyReports(reports) {
        if (!reports || !reports.length) {
            return '<div class="text-muted small">无日报</div>';
        }

        var buttons = reports.map(function (r, idx) {
            return '<button type="button" class="btn btn-sm btn-secondary daily-report-item" data-report-index="' + idx + '">' +
                '<span><i class="bi bi-file-earmark-text"></i> ' + escapeHtml(r.project || '-') + '</span>' +
                '<span class="text-muted small">' + escapeHtml(r.date || '') + '</span>' +
                '</button>';
        }).join('');

        return '<div class="daily-report-list">' + buttons + '</div>';
    }

    function bindDailyReportLinks(reports) {
        document.querySelectorAll('.daily-report-item[data-report-index]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var idx = Number(btn.dataset.reportIndex);
                if (!Number.isNaN(idx) && reports[idx]) {
                    openDailyReportModal(reports[idx]);
                }
            });
        });
    }

    function openDailyReportModal(report) {
        var modal = document.getElementById('dailyReportModal');
        var title = document.getElementById('dailyReportModalTitle');
        var body = document.getElementById('dailyReportModalBody');
        modal.classList.add('active');
        title.textContent = (report.project || '-') + ' / ' + (report.date || '-') + ' / ' + (report.author || '-');
        body.innerHTML = '<div class="text-muted">加载中...</div>';

        apiRequest('/api/reports/content?path=' + encodeURIComponent(report.filename || ''))
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    body.innerHTML = '<div class="text-danger">日报加载失败</div>';
                    return null;
                }
                return resp.json();
            })
            .then(function (json) {
                if (!json) return;
                var content = json.data && json.data.content ? json.data.content : '';
                body.innerHTML = '<div class="markdown-body">' + renderMarkdown(content || '无内容') + '</div>';
            })
            .catch(function () {
                body.innerHTML = '<div class="text-danger">日报加载失败</div>';
            });
    }

    function closeDailyReportModal() {
        document.getElementById('dailyReportModal').classList.remove('active');
    }

    function renderDrawer(data) {
        var s = data.summary || {};
        var work = s.work_summary || [];
        var trend = data.trend || [];
        var dailyReports = data.daily_reports || [];

        var workHtml = '';
        if (work.length) {
            var markdownContent = work.map(function (w) { return '- ' + w; }).join('\n');
            workHtml = '<div class="markdown-body">' + renderMarkdown(markdownContent) + '</div>';
        } else {
            workHtml = '<div class="text-muted">无</div>';
        }

        var html =
            '<div class="mb-3">' +
            '  <strong>综合评分:</strong> ' + (s.review_score != null ? escapeHtml(s.review_score) : '-') +
            '  ' + gradeBadge(s.review_grade) +
            '  <span class="text-muted" style="margin-left:var(--space-2);">Token: ' + escapeHtml(formatTokenUsage(s.token_usage)) + '</span>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">评分简述</div>' +
            '  <div class="markdown-body">' + renderMarkdown(s.review_summary || '-') + '</div>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">今日主要工作</div>' +
            '  ' + workHtml +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">当前人员审查报告-日报</div>' +
            '  ' + renderDailyReports(dailyReports) +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">近 7 天趋势</div>' +
            '  <div id="chartTrend"></div>' +
            '</div>';
        document.getElementById('drawerBody').innerHTML = html;
        bindDailyReportLinks(dailyReports);
        renderTrendChart(trend);
    }

    function renderTrendChart(trend) {
        if (chartTrend) { chartTrend.dispose(); chartTrend = null; }
        var el = document.getElementById('chartTrend');
        if (!el) return;
        chartTrend = echarts.init(el);
        chartTrend.setOption({
            tooltip: { trigger: 'axis' },
            legend: { data: ['新增', '删除', '评分'] },
            grid: { left: 40, right: 40, top: 30, bottom: 30 },
            xAxis: { type: 'category', data: trend.map(function (t) { return t.stat_date; }) },
            yAxis: [
                { type: 'value', name: '代码量' },
                { type: 'value', name: '评分', min: 0, max: 100 },
            ],
            series: [
                { name: '新增', type: 'line', color: '#28a745',
                  data: trend.map(function (t) { return t.additions; }) },
                { name: '删除', type: 'line', color: '#dc3545',
                  data: trend.map(function (t) { return t.deletions; }) },
                { name: '评分', type: 'line', yAxisIndex: 1, color: '#0d6efd',
                  data: trend.map(function (t) { return t.review_score; }),
                  markLine: { data: [
                      { yAxis: 90, lineStyle: { color: '#28a745', type: 'dashed' } },
                      { yAxis: 60, lineStyle: { color: '#dc3545', type: 'dashed' } },
                  ] } },
            ],
        });
    }

    function closeDrawer() {
        document.getElementById('drawerOverlay').classList.remove('active');
        document.getElementById('detailDrawer').classList.remove('active');
        if (chartTrend) { chartTrend.dispose(); chartTrend = null; }
    }

    // ── 排序交互 ─────────────────────────────────
    function bindSort() {
        document.querySelectorAll('#efficiencyTable th[data-sort]').forEach(function (th) {
            th.addEventListener('click', function () {
                var field = th.dataset.sort;
                if (STATE.sort_by === field) {
                    STATE.order = STATE.order === 'desc' ? 'asc' : 'desc';
                } else {
                    STATE.sort_by = field;
                    STATE.order = 'desc';
                }
                document.querySelectorAll('#efficiencyTable th').forEach(function (t) {
                    t.classList.remove('sorted');
                });
                th.classList.add('sorted');
                loadData();
            });
        });
    }

    // ── 补算按钮 + 异步轮询 ──────────────────────
    var _pollTimer = null;

    function openRecomputeModal() {
        // 如果正在补算，点击按钮切换为显示进度
        if (_pollTimer) {
            toggleRecomputeProgress(true);
            return;
        }
        var desc = STATE.mode === 'monthly'
            ? '补算 ' + STATE.yearMonth + ' 的月度能效数据'
            : '补算 ' + STATE.startDate + ' ~ ' + STATE.endDate + ' 的人员能效数据';
        document.getElementById('recomputeModalDesc').textContent = desc;
        document.getElementById('recomputeForce').checked = false;
        document.getElementById('recomputeModal').classList.add('active');
    }

    function closeRecomputeModal() {
        document.getElementById('recomputeModal').classList.remove('active');
    }

    function confirmRecompute() {
        var force = document.getElementById('recomputeForce').checked;
        closeRecomputeModal();

        var btn = document.getElementById('btnRecompute');
        btn.disabled = true;

        var url, body;
        if (STATE.mode === 'monthly') {
            url = '/api/efficiency/monthly/recompute';
            body = { year_month: STATE.yearMonth, force: force };
        } else {
            url = '/api/efficiency/recompute';
            body = { start_date: STATE.startDate, end_date: STATE.endDate, force: force };
        }

        apiRequest(url, {
            method: 'POST',
            body: JSON.stringify(body),
        })
            .then(function (r) {
                if (!r || !r.ok) {
                    return r.json().then(function (j) {
                        showNotification(j.message || '补算请求失败', 'danger');
                        btn.disabled = false;
                    }).catch(function () {
                        showNotification('补算请求失败', 'danger');
                        btn.disabled = false;
                    });
                }
                return r.json();
            })
            .then(function (json) {
                if (!json) return;
                if (json.success) {
                    showNotification(json.message || '补算任务已启动', 'info');
                    startRecomputePolling();
                } else {
                    showNotification(json.message || '补算失败', 'danger');
                    btn.disabled = false;
                }
            })
            .catch(function () {
                showNotification('补算请求失败', 'danger');
                btn.disabled = false;
            });
    }

    function startRecomputePolling() {
        console.log('[Recompute] Starting polling...');
        toggleRecomputeProgress(true);
        _pollTimer = setInterval(pollRecomputeStatus, 3000);
        console.log('[Recompute] Polling started, timer:', _pollTimer);
    }

    function stopRecomputePolling() {
        if (_pollTimer) {
            clearInterval(_pollTimer);
            _pollTimer = null;
        }
        var btn = document.getElementById('btnRecompute');
        btn.disabled = false;
    }

    function pollRecomputeStatus() {
        apiRequest('/api/efficiency/recompute/status')
            .then(function (r) {
                if (!r || !r.ok) {
                    console.warn('[Recompute] Status request failed:', r ? r.status : 'null');
                    return null;
                }
                return r.json();
            })
            .then(function (json) {
                if (!json || !json.success) {
                    console.warn('[Recompute] Invalid response:', json);
                    return;
                }
                var d = json.data || {};
                console.log('[Recompute] Status update:', d);
                renderRecomputeProgress(d);

                if (!d.is_running) {
                    stopRecomputePolling();
                    // 显示完成通知
                    if (d.error) {
                        showNotification('补算异常：' + d.error, 'danger');
                    } else if (d.task_type === 'monthly') {
                        showNotification('月度补算完成', 'success');
                    } else {
                        var msg = '补算完成：处理 ' + (d.processed || []).length + ' 天，'
                            + '跳过 ' + (d.skipped || []).length + ' 天，'
                            + '失败 ' + (d.failed || []).length + ' 天';
                        showNotification(msg, 'success');
                    }
                    // 3 秒后隐藏进度条
                    setTimeout(function () { toggleRecomputeProgress(false); }, 3000);
                    loadData();
                }
            })
            .catch(function () {
                // 轮询出错不停止，可能只是网络波动
            });
    }

    function toggleRecomputeProgress(show) {
        var el = document.getElementById('recomputeProgress');
        console.log('[Recompute] toggleRecomputeProgress:', show, 'element:', el);
        if (el) {
            el.style.display = show ? '' : 'none';
            console.log('[Recompute] Element display set to:', el.style.display);
        }
    }

    function renderRecomputeProgress(d) {
        var el = document.getElementById('recomputeProgress');
        if (!el) return;

        var total = d.total_days || 1;
        var done = d.processed_days || 0;
        var pct = Math.min(Math.round(done / total * 100), 100);

        var statusText = '';
        if (d.task_type === 'monthly') {
            statusText = '正在补算月度数据...';
        } else {
            statusText = '正在补算 ' + (d.current_date || '...') +
                '（' + done + '/' + total + ' 天）';
        }

        var detailParts = [];
        if (d.processed && d.processed.length) {
            detailParts.push('已完成 ' + d.processed.length + ' 天');
        }
        if (d.skipped && d.skipped.length) {
            detailParts.push('跳过 ' + d.skipped.length + ' 天');
        }
        if (d.failed && d.failed.length) {
            detailParts.push('<span class="text-danger">失败 ' + d.failed.length + ' 天</span>');
        }

        el.innerHTML =
            '<div style="display:flex; align-items:center; gap:var(--space-3); margin-top:var(--space-2);">' +
            '  <div style="flex:1;">' +
            '    <div style="display:flex; justify-content:space-between; margin-bottom:2px;">' +
            '      <span class="small text-muted">' + statusText + '</span>' +
            '      <span class="small text-muted">' + pct + '%</span>' +
            '    </div>' +
            '    <div style="height:6px; background:var(--color-border); border-radius:3px; overflow:hidden;">' +
            '      <div style="height:100%; width:' + pct + '%; background:var(--color-primary); border-radius:3px; transition:width 0.3s;"></div>' +
            '    </div>' +
            (detailParts.length ? '    <div class="small text-muted" style="margin-top:2px;">' + detailParts.join('，') + '</div>' : '') +
            '  </div>' +
            '  <button class="btn btn-sm btn-secondary" id="btnCancelRecompute" onclick="cancelRecompute()">取消</button>' +
            '</div>';
    }

    window.cancelRecompute = function() {
        apiRequest('/api/efficiency/recompute/cancel', { method: 'POST' })
            .then(function (r) {
                if (r && r.ok) {
                    showNotification('正在取消补算...', 'info');
                }
            })
            .catch(function () {});
    };

    function canViewDetail(item) {
        // 使用后端返回的 can_view_detail 字段
        if (item && item.can_view_detail !== undefined) {
            return item.can_view_detail;
        }
        // 降级逻辑：系统管理员可看任何人
        if (STATE.isAdmin) return true;
        // 普通用户：只能看自己
        if (!item || !item.author_email) return false;
        return item.author_email.toLowerCase() === STATE.currentUserEmail.toLowerCase();
    }

    function checkAdminAndBindRecompute() {
        // 返回 promise，调用方可 await 确保角色信息就绪后再渲染
        var authPromise = apiRequest('/api/auth/me')
            .then(function (resp) {
                if (!resp) return null;
                return resp.json();
            })
            .then(function (data) {
                if (!data) return;
                STATE.isAdmin = data.roles && data.roles.indexOf('system_admin') !== -1;
                STATE.isProjectAdmin = data.roles && data.roles.indexOf('project_admin') !== -1;
                STATE.currentUserEmail = data.email || '';
                if (STATE.isAdmin) {
                    document.getElementById('btnRecompute').style.display = '';
                    checkRunningRecompute();
                }
            })
            .catch(function (err) {
                console.error('checkAdmin failed:', err);
            });

        document.getElementById('btnRecompute').addEventListener('click', openRecomputeModal);
        document.getElementById('recomputeConfirm').addEventListener('click', confirmRecompute);
        document.getElementById('recomputeCancel').addEventListener('click', closeRecomputeModal);
        document.getElementById('recomputeModalClose').addEventListener('click', closeRecomputeModal);
        document.getElementById('recomputeModal').addEventListener('click', function (e) {
            if (e.target === this) closeRecomputeModal();
        });

        return authPromise;
    }

    function checkRunningRecompute() {
        apiRequest('/api/efficiency/recompute/status')
            .then(function (r) {
                if (!r || !r.ok) return;
                return r.json();
            })
            .then(function (json) {
                if (!json || !json.success) return;
                if (json.data && json.data.is_running) {
                    // 有正在运行的补算任务，启动轮询
                    startRecomputePolling();
                }
            })
            .catch(function () {});
    }

    // ── 窗口 resize ─────────────────────────────
    function handleResize() {
        if (chartCodeTop) chartCodeTop.resize();
        if (chartGradePie) chartGradePie.resize();
        if (chartTrend) chartTrend.resize();
        if (chartMonthlyTrend) chartMonthlyTrend.resize();
    }

    // ── 表头适配月度模式 ────────────────────────
    function updateTableHeader() {
        var thead = document.querySelector('#efficiencyTable thead tr');
        if (STATE.mode === 'monthly') {
            thead.innerHTML =
                '<th style="min-width:100px;">姓名</th>' +
                '<th style="min-width:160px;">邮箱</th>' +
                '<th style="min-width:70px;" data-sort="active_days">活跃天数 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="commits">提交 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="additions">新增 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="deletions">删除 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="files_changed">文件 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="score" class="sorted">评分 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;">等级</th>' +
                '<th style="min-width:90px;">Token 消耗</th>' +
                '<th style="min-width:140px;">涉及项目</th>';
        } else {
            thead.innerHTML =
                '<th style="min-width:100px;">姓名</th>' +
                '<th style="min-width:160px;">邮箱</th>' +
                '<th style="min-width:80px;" data-sort="commits">提交 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="additions">新增 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="deletions">删除 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="files_changed">文件 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="score" class="sorted">评分 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;">等级</th>' +
                '<th style="min-width:90px;">Token 消耗</th>' +
                '<th style="min-width:140px;">涉及项目</th>';
        }
        bindSort();
    }

    // ── 入口 ─────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function () {
        // 初始化日期
        STATE.startDate = weekStart();
        STATE.endDate = yesterday();
        STATE.yearMonth = currentMonth();

        if(STATE.startDate > STATE.endDate){
            //当开始日期大于结束日期时，将开始日期设置为结束日期
            STATE.startDate = STATE.endDate;
        }

        document.getElementById('startDate').value = STATE.startDate;
        document.getElementById('endDate').value = STATE.endDate;
        document.getElementById('filterMonth').value = STATE.yearMonth;

        // 日期变化
        document.getElementById('startDate').addEventListener('change', function (e) {
            STATE.startDate = e.target.value || yesterday();
            loadData();
        });
        document.getElementById('endDate').addEventListener('change', function (e) {
            STATE.endDate = e.target.value || yesterday();
            loadData();
        });
        document.getElementById('filterMonth').addEventListener('change', function (e) {
            STATE.yearMonth = e.target.value || currentMonth();
            loadData();
        });

        document.getElementById('btnRefresh').addEventListener('click', loadData);

        // 弹窗关闭
        document.getElementById('rangeModalClose').addEventListener('click', closeRangeDetailModal);
        document.getElementById('rangeDetailModal').addEventListener('click', function (e) {
            if (e.target === this) closeRangeDetailModal();
        });
        document.getElementById('monthlyModalClose').addEventListener('click', closeMonthlyDetailModal);
        document.getElementById('monthlyDetailModal').addEventListener('click', function (e) {
            if (e.target === this) closeMonthlyDetailModal();
        });
        document.getElementById('dailyReportModalClose').addEventListener('click', closeDailyReportModal);
        document.getElementById('dailyReportModal').addEventListener('click', function (e) {
            if (e.target === this) closeDailyReportModal();
        });

        // 抽屉关闭
        document.getElementById('drawerClose').addEventListener('click', closeDrawer);
        document.getElementById('drawerOverlay').addEventListener('click', closeDrawer);

        bindSort();
        // 先获取角色信息，再加载数据，避免竞态导致行全部灰色不可点击
        checkAdminAndBindRecompute().then(function () {
            loadData();
        });
        window.addEventListener('resize', throttle(handleResize, 150));
    });
})();
