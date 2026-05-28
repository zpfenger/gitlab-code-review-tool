/* 人员能效页面 - 数据加载 + ECharts 渲染 + 详情抽屉 */
(function () {
    'use strict';

    var STATE = {
        date: '',
        sort_by: 'score',
        order: 'desc',
        items: [],
        teamStats: null,
        isAdmin: false,
    };

    var GRADE_CLASS = {
        '优秀': 'grade-excellent',
        '良好': 'grade-good',
        '一般': 'grade-average',
        '待改进': 'grade-poor',
    };

    var chartCodeTop, chartGradePie, chartTrend;

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

    function gradeBadge(grade) {
        var cls = GRADE_CLASS[grade] || 'grade-none';
        return '<span class="grade-badge ' + cls + '">' + (grade || '-') + '</span>';
    }

    function escapeHtml(s) {
        if (s == null) return '';
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ── 数据加载 ─────────────────────────────────
    function loadList() {
        var params = new URLSearchParams({
            date: STATE.date,
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
                console.error('loadList failed:', err);
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
        document.getElementById('efficiencyTbody').innerHTML =
            '<tr><td colspan="9" class="text-center text-muted py-4">' + msg + '</td></tr>';
    }

    function renderTable() {
        if (!STATE.items.length) {
            var hint = STATE.isAdmin ? '请点击右上方"立即补算"。' : '请联系管理员。';
            document.getElementById('efficiencyTbody').innerHTML =
                '<tr><td colspan="9" class="text-center text-muted py-4">' +
                '该日数据未生成。' + hint + '</td></tr>';
            return;
        }
        var rows = STATE.items.map(function (it) {
            return '<tr data-email="' + escapeHtml(it.author_email) + '">' +
                '<td>' + escapeHtml(it.author_name) + '</td>' +
                '<td><span class="text-muted small">' + escapeHtml(it.author_email) + '</span></td>' +
                '<td>' + escapeHtml(it.commits_count) + '</td>' +
                '<td class="text-success">+' + escapeHtml(it.additions) + '</td>' +
                '<td class="text-danger">-' + escapeHtml(it.deletions) + '</td>' +
                '<td>' + escapeHtml(it.files_changed) + '</td>' +
                '<td>' + (it.review_score != null ? escapeHtml(it.review_score) : '-') + '</td>' +
                '<td>' + gradeBadge(it.review_grade) + '</td>' +
                '<td><span class="text-muted small">' +
                escapeHtml((it.projects_involved || []).join('，')) + '</span></td>' +
                '</tr>';
        }).join('');
        document.getElementById('efficiencyTbody').innerHTML = rows;
        // 行点击 → 抽屉
        document.querySelectorAll('#efficiencyTbody tr[data-email]').forEach(function (tr) {
            tr.addEventListener('click', function () {
                openDrawer(tr.dataset.email);
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
                {
                    name: '新增', type: 'bar', stack: 'total', color: '#28a745',
                    data: top.map(function (t) { return t.additions; }),
                },
                {
                    name: '删除', type: 'bar', stack: 'total', color: '#dc3545',
                    data: top.map(function (t) { return t.deletions; }),
                },
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

    // ── 详情抽屉 ─────────────────────────────────
    function openDrawer(email) {
        document.getElementById('drawerOverlay').classList.add('active');
        document.getElementById('detailDrawer').classList.add('active');
        document.getElementById('drawerTitle').textContent = email + ' (' + STATE.date + ')';
        document.getElementById('drawerBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            date: STATE.date,
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

    function renderDrawer(data) {
        var s = data.summary || {};
        var work = s.work_summary || [];
        var commits = data.commits || [];
        var trend = data.trend || [];

        var workHtml = work.length
            ? work.map(function (w) { return '<li>' + escapeHtml(w) + '</li>'; }).join('')
            : '<li class="text-muted">无</li>';

        var commitHtml = commits.length
            ? commits.map(function (c) {
                return '<li class="mb-2">' +
                    '<code>' + c.commit_sha.substring(0, 8) + '</code> ' +
                    '<span class="badge bg-secondary">' + escapeHtml(c.branch) + '</span> ' +
                    '<span class="text-muted">' + c.commit_date + '</span>' +
                    '</li>';
            }).join('')
            : '<li class="text-muted">无</li>';

        var html =
            '<div class="mb-3">' +
            '  <strong>综合评分:</strong> ' + (s.review_score != null ? escapeHtml(s.review_score) : '-') +
            '  ' + gradeBadge(s.review_grade) +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">评分简述</div>' +
            '  <div>' + escapeHtml(s.review_summary || '-') + '</div>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">今日主要工作</div>' +
            '  <ol class="mb-0">' + workHtml + '</ol>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">近 7 天趋势</div>' +
            '  <div id="chartTrend"></div>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">今日提交 (' + commits.length + ')</div>' +
            '  <ul class="list-unstyled small">' + commitHtml + '</ul>' +
            '</div>';
        document.getElementById('drawerBody').innerHTML = html;
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
                {
                    name: '新增', type: 'line', color: '#28a745',
                    data: trend.map(function (t) { return t.additions; }),
                },
                {
                    name: '删除', type: 'line', color: '#dc3545',
                    data: trend.map(function (t) { return t.deletions; }),
                },
                {
                    name: '评分', type: 'line', yAxisIndex: 1, color: '#0d6efd',
                    data: trend.map(function (t) { return t.review_score; }),
                    markLine: {
                        data: [
                            { yAxis: 90, lineStyle: { color: '#28a745', type: 'dashed' } },
                            { yAxis: 60, lineStyle: { color: '#dc3545', type: 'dashed' } },
                        ],
                    },
                },
            ],
        });
    }

    function closeDrawer() {
        document.getElementById('drawerOverlay').classList.remove('active');
        document.getElementById('detailDrawer').classList.remove('active');
        // 销毁抽屉内的图表，避免内存泄漏
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
                loadList();
            });
        });
    }

    // ── 补算按钮（仅管理员） ───────────────────
    function checkAdminAndBindRecompute() {
        apiRequest('/api/auth/me')
            .then(function (resp) {
                if (!resp) return;
                return resp.json();
            })
            .then(function (data) {
                if (!data) return;
                STATE.isAdmin = data.roles && data.roles.indexOf('system_admin') !== -1;
                if (STATE.isAdmin) {
                    document.getElementById('btnRecompute').style.display = '';
                }
            })
            .catch(function (err) {
                console.error('checkAdmin failed:', err);
            });
        document.getElementById('btnRecompute').addEventListener('click', function () {
            if (!confirm('确认补算 ' + STATE.date + ' 的人员能效？')) return;
            var btn = document.getElementById('btnRecompute');
            btn.disabled = true;
            apiRequest('/api/efficiency/recompute', {
                method: 'POST',
                body: JSON.stringify({ date: STATE.date, force: false }),
            })
                .then(function (r) {
                    if (r && r.ok) {
                        showNotification('补算完成', 'success');
                        loadList();
                    } else {
                        showNotification('补算失败', 'danger');
                    }
                })
                .catch(function (err) {
                    console.error('recompute failed:', err);
                    showNotification('补算请求失败', 'danger');
                })
                .finally(function () {
                    btn.disabled = false;
                });
        });
    }

    // ── 窗口 resize → 图表自适应 ────────────────
    function handleResize() {
        if (chartCodeTop) chartCodeTop.resize();
        if (chartGradePie) chartGradePie.resize();
        if (chartTrend) chartTrend.resize();
    }

    // ── 入口 ─────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function () {
        STATE.date = yesterday();
        document.getElementById('filterDate').value = STATE.date;
        document.getElementById('filterDate').addEventListener('change', function (e) {
            STATE.date = e.target.value || yesterday();
            loadList();
        });
        document.getElementById('btnRefresh').addEventListener('click', loadList);
        document.getElementById('drawerClose').addEventListener('click', closeDrawer);
        document.getElementById('drawerOverlay').addEventListener('click', closeDrawer);
        bindSort();
        checkAdminAndBindRecompute();
        loadList();
        window.addEventListener('resize', throttle(handleResize, 150));
    });
})();
