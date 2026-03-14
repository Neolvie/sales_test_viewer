(function () {
    var ADMIN_STORAGE_KEY = 'salestester_admin_token';
    var currentPage = 1;
    var pageSize = 20;
    var totalResults = 0;
    var allThemes = [];
    var selectedSessions = new Map();
    var sessionsCache = [];

    function getAuthHeader() {
        var t = sessionStorage.getItem(ADMIN_STORAGE_KEY);
        return t ? { 'Authorization': 'Bearer ' + t } : {};
    }

    function api(url, options) {
        options = options || {};
        options.headers = Object.assign({}, options.headers || {}, getAuthHeader());
        return fetch(url, options).then(function (r) {
            if (r.status === 401) {
                sessionStorage.removeItem(ADMIN_STORAGE_KEY);
                document.body.classList.remove('admin-logged-in');
                throw new Error('Не авторизован');
            }
            return r;
        });
    }

    // --- Login ---
    document.getElementById('btnLogin').onclick = function () {
        var pwd = document.getElementById('adminPassword').value;
        var errEl = document.getElementById('loginError');
        errEl.style.display = 'none';
        if (!pwd) {
            errEl.textContent = 'Введите пароль';
            errEl.style.display = 'block';
            return;
        }
        api('/api/admin/sessions?page=1&limit=1', { headers: { 'Authorization': 'Bearer ' + pwd } })
            .then(function (r) {
                if (!r.ok) throw new Error('Неверный пароль');
                sessionStorage.setItem(ADMIN_STORAGE_KEY, pwd);
                document.body.classList.add('admin-logged-in');
                document.getElementById('adminPassword').value = '';
                loadResults(1);
                loadThemesForFilter();
                loadThemesTab();
                loadPromptsTab();
            })
            .catch(function (e) {
                errEl.textContent = e.message || 'Ошибка входа';
                errEl.style.display = 'block';
            });
    };

    // --- Date pickers (flatpickr, Russian locale) ---
    var fpFrom = flatpickr('#filterDateFrom', {
        locale: 'ru',
        dateFormat: 'd.m.Y',
        allowInput: false
    });
    var fpTo = flatpickr('#filterDateTo', {
        locale: 'ru',
        dateFormat: 'd.m.Y',
        allowInput: false
    });

    function setDefaultDateRange() {
        var today = new Date();
        var from = new Date();
        from.setDate(today.getDate() - 29);
        fpFrom.setDate(from, false);
        fpTo.setDate(today, false);
    }
    setDefaultDateRange();

    // Helper: convert flatpickr display value "dd.mm.yyyy" → "yyyy-mm-dd" for API
    function fpValueToApi(val) {
        if (!val) return '';
        var parts = val.split('.');
        if (parts.length !== 3) return '';
        return parts[2] + '-' + parts[1] + '-' + parts[0];
    }

    if (sessionStorage.getItem(ADMIN_STORAGE_KEY)) {
        document.body.classList.add('admin-logged-in');
        loadResults(1);
        loadThemesForFilter();
        loadThemesTab();
        loadPromptsTab();
    }

    // Enter in FIO filter applies filter
    document.getElementById('filterName').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); loadResults(1); }
    });

    // --- Tabs ---
    document.querySelectorAll('#adminTabs .nav-link').forEach(function (a) {
        a.onclick = function (e) {
            e.preventDefault();
            document.querySelectorAll('#adminTabs .nav-link').forEach(function (x) { x.classList.remove('active'); });
            this.classList.add('active');
            var tab = this.getAttribute('data-tab');
            document.querySelectorAll('.tab-pane').forEach(function (p) {
                p.style.display = p.id === 'tab' + tab.charAt(0).toUpperCase() + tab.slice(1) ? 'block' : 'none';
            });
            if (tab === 'results') loadResults(currentPage);
            if (tab === 'themes') loadThemesTab();
            if (tab === 'prompts') loadPromptsTab();
        };
    });
    document.querySelector('#adminTabs .nav-link[data-tab="results"]').classList.add('active');

    // --- Results (server-side pagination + filters) ---
    function buildResultsQuery(page) {
        var q = ['page=' + page, 'limit=' + pageSize];
        var name = document.getElementById('filterName').value;
        if (name) q.push('name=' + encodeURIComponent(name));
        var themeId = document.getElementById('filterTheme').value;
        if (themeId) q.push('theme_id=' + themeId);
        var score = document.getElementById('filterScore').value;
        if (score) q.push('score=' + score);
        var dateFrom = fpValueToApi(document.getElementById('filterDateFrom').value);
        if (dateFrom) q.push('date_from=' + dateFrom);
        var dateTo = fpValueToApi(document.getElementById('filterDateTo').value);
        if (dateTo) q.push('date_to=' + dateTo);
        return '/api/admin/sessions?' + q.join('&');
    }

    function loadResults(page) {
        currentPage = page;
        var tbody = document.getElementById('resultsBody');
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-3">Загрузка...</td></tr>';
        api(buildResultsQuery(page))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                sessionsCache = data.data || [];
                totalResults = data.total || 0;
                renderResultsTable(sessionsCache);
                renderResultsPagination(data.total, data.page, data.limit);
                document.getElementById('resultsSummary').textContent = 'Всего: ' + totalResults;
                updateAnalyzeButton();
            })
            .catch(function (e) {
                tbody.innerHTML = '<tr><td colspan="8" class="text-danger">' + (e.message || 'Ошибка') + '</td></tr>';
            });
    }

    var scoreClasses = { 1: 'score-1', 2: 'score-2', 3: 'score-3', 4: 'score-4', 5: 'score-5' };

    function scoreBadgeHtml(score) {
        if (score == null) return '—';
        var cls = scoreClasses[score] || 'bg-secondary';
        return '<span class="badge badge-score ' + cls + '">' + score + '</span>';
    }

    function showTextModal(title, text) {
        document.getElementById('textViewTitle').textContent = title;
        document.getElementById('textViewContent').textContent = text || '—';
        new bootstrap.Modal(document.getElementById('textViewModal')).show();
    }

    function renderResultsTable(rows) {
        var tbody = document.getElementById('resultsBody');
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center py-3 text-muted">Нет данных</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map(function (r) {
            var dateStr = r.created_at ? new Date(r.created_at).toLocaleString('ru-RU') : '—';
            var resPreview = (r.result || '').slice(0, 55) + ((r.result || '').length > 55 ? '…' : '');
            var ansPreview = (r.user_answer || '').slice(0, 40) + ((r.user_answer || '').length > 40 ? '…' : '');
            var checked = selectedSessions.has(r.id) ? 'checked' : '';
            var resCell = '<span class="text-muted" style="font-size:0.8rem; white-space:normal; text-align:left; display:block;" title="' + escapeAttr(r.result || '') + '">' + escapeHtml(resPreview) + '</span>' +
                (r.result ? '<button type="button" class="btn btn-outline-secondary btn-view-text mt-1 view-result" data-id="' + r.id + '">Открыть</button>' : '');
            var ansCell = '<span class="text-muted" style="font-size:0.8rem; white-space:normal; text-align:left; display:block;" title="' + escapeAttr(r.user_answer || '') + '">' + escapeHtml(ansPreview) + '</span>' +
                (r.user_answer ? '<button type="button" class="btn btn-outline-secondary btn-view-text mt-1 view-answer" data-id="' + r.id + '">Открыть</button>' : '');
            return '<tr data-id="' + r.id + '"><td><input type="checkbox" class="result-cb" data-id="' + r.id + '" ' + checked + '></td><td class="small text-muted">' + r.id + '</td><td class="small" style="white-space:nowrap">' + dateStr + '</td><td>' + escapeHtml(r.full_name) + '</td><td class="small">' + escapeHtml(r.theme_name) + '</td><td class="text-center">' + scoreBadgeHtml(r.score) + '</td><td style="min-width:140px">' + resCell + '</td><td style="min-width:130px">' + ansCell + '</td></tr>';
        }).join('');
        tbody.querySelectorAll('.result-cb').forEach(function (cb) {
            cb.onchange = function () {
                var id = parseInt(cb.getAttribute('data-id'), 10);
                var row = sessionsCache.find(function (s) { return s.id === id; });
                if (cb.checked && row) selectedSessions.set(id, row);
                else selectedSessions.delete(id);
                updateAnalyzeButton();
            };
        });
        tbody.querySelectorAll('.view-result').forEach(function (btn) {
            btn.onclick = function () {
                var id = parseInt(btn.getAttribute('data-id'), 10);
                var row = sessionsCache.find(function (s) { return s.id === id; });
                if (row) showTextModal('Результат оценки ИИ — ' + row.full_name, row.result);
            };
        });
        tbody.querySelectorAll('.view-answer').forEach(function (btn) {
            btn.onclick = function () {
                var id = parseInt(btn.getAttribute('data-id'), 10);
                var row = sessionsCache.find(function (s) { return s.id === id; });
                if (row) showTextModal('Ответ сотрудника — ' + row.full_name, row.user_answer);
            };
        });
    }

    function escapeHtml(s) {
        if (!s) return '';
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }
    function escapeAttr(s) {
        if (!s) return '';
        return String(s).replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function renderResultsPagination(total, page, limit) {
        var ul = document.getElementById('resultsPagination');
        ul.innerHTML = '';
        var pages = Math.max(1, Math.ceil(total / limit));
        if (pages <= 1) return;
        for (var i = 1; i <= Math.min(pages, 10); i++) {
            var li = document.createElement('li');
            li.className = 'page-item' + (i === page ? ' active' : '');
            var a = document.createElement('a');
            a.className = 'page-link';
            a.href = '#';
            a.textContent = i;
            a.onclick = function (p) { return function (e) { e.preventDefault(); loadResults(p); }; }(i);
            li.appendChild(a);
            ul.appendChild(li);
        }
    }

    document.getElementById('btnFilter').onclick = function () { loadResults(1); };
    document.getElementById('selectAllResults').onchange = function () {
        var checked = this.checked;
        sessionsCache.forEach(function (r) {
            if (checked) selectedSessions.set(r.id, r);
            else selectedSessions.delete(r.id);
        });
        document.querySelectorAll('.result-cb').forEach(function (cb) { cb.checked = checked; });
        updateAnalyzeButton();
    };

    function loadThemesForFilter() {
        api('/api/admin/themes').then(function (r) { return r.json(); }).then(function (list) {
            allThemes = list;
            var sel = document.getElementById('filterTheme');
            sel.innerHTML = '<option value="">—</option>' + list.map(function (t) { return '<option value="' + t.id + '">' + escapeHtml(t.name) + '</option>'; }).join('');
        });
    }

    var lastAnalysisText = '';

    function updateAnalyzePanel() {
        var n = selectedSessions.size;
        var btn = document.getElementById('btnShowAnalyze');
        var cnt = document.getElementById('selectedCount');
        btn.disabled = n === 0;
        if (n > 0) {
            cnt.textContent = n;
            cnt.style.display = 'inline';
        } else {
            cnt.style.display = 'none';
            // hide panel if nothing selected
            document.getElementById('analyzePanel').style.display = 'none';
        }
    }

    function updateAnalyzeButton() { updateAnalyzePanel(); }

    document.getElementById('btnShowAnalyze').onclick = function () {
        var panel = document.getElementById('analyzePanel');
        panel.style.display = 'block';
        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };

    // --- Themes ---
    var themesListAdmin = [];
    function loadThemesTab() {
        api('/api/admin/themes').then(function (r) { return r.json(); }).then(function (list) {
            themesListAdmin = list;
            var tbody = document.getElementById('themesBody');
            tbody.innerHTML = list.map(function (t) {
                return '<tr><td>' + t.id + '</td><td>' + escapeHtml(t.name) + '</td><td>' + (t.is_active ? 'Да' : 'Нет') + '</td><td>' + themeRowActions(t) + '</td></tr>';
            }).join('');
            tbody.querySelectorAll('.edit-theme').forEach(function (btn) {
                btn.onclick = function () { openThemeForm(parseInt(btn.getAttribute('data-id'), 10)); };
            });
        });
    }

    document.getElementById('btnAddTheme').onclick = function () { openThemeForm(null); };

    function openThemeForm(id) {
        document.getElementById('themeId').value = id || '';
        if (id) {
            var t = themesListAdmin.find(function (x) { return x.id === id; }) || allThemes.find(function (x) { return x.id === id; }) || {};
            document.getElementById('themeName').value = t.name || '';
            document.getElementById('themeRefText').value = t.reference_text || '';
            document.getElementById('themeActive').checked = t.is_active !== false;
        } else {
            document.getElementById('themeName').value = '';
            document.getElementById('themeRefText').value = '';
            document.getElementById('themeActive').checked = true;
        }
        new bootstrap.Modal(document.getElementById('themeFormModal')).show();
    }

    document.getElementById('btnSaveTheme').onclick = function () {
        var id = document.getElementById('themeId').value;
        var name = document.getElementById('themeName').value.trim();
        var refText = document.getElementById('themeRefText').value.trim();
        var isActive = document.getElementById('themeActive').checked;
        if (!name || !refText) { alert('Заполните название и эталонный текст'); return; }
        var method = id ? 'PUT' : 'POST';
        var url = id ? '/api/admin/themes/' + id : '/api/admin/themes';
        var body = id ? JSON.stringify({ name: name, reference_text: refText, is_active: isActive }) : JSON.stringify({ name: name, reference_text: refText, is_active: isActive });
        api(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: body })
            .then(function (r) { return r.json(); })
            .then(function () {
                bootstrap.Modal.getInstance(document.getElementById('themeFormModal')).hide();
                loadThemesTab();
                loadThemesForFilter();
            })
            .catch(function (e) { alert(e.message || 'Ошибка'); });
    };

    document.addEventListener('click', function (e) {
        if (e.target && e.target.classList && e.target.classList.contains('delete-theme')) {
            var id = parseInt(e.target.getAttribute('data-id'), 10);
            if (!confirm('Удалить тему?')) return;
            api('/api/admin/themes/' + id, { method: 'DELETE' })
                .then(function () { loadThemesTab(); loadThemesForFilter(); })
                .catch(function (err) { alert(err.message || 'Ошибка'); });
        }
    });

    function themeRowActions(t) {
        return '<button type="button" class="btn btn-sm btn-outline-secondary edit-theme" data-id="' + t.id + '">Изменить</button> <button type="button" class="btn btn-sm btn-outline-danger delete-theme" data-id="' + t.id + '">Удалить</button>';
    }

    // --- Prompts ---
    var promptsList = [];
    function loadPromptsTab() {
        api('/api/admin/prompts').then(function (r) { return r.json(); }).then(function (list) {
            promptsList = list;
            var tbody = document.getElementById('promptsBody');
            tbody.innerHTML = list.map(function (p) {
                var status = p.is_active
                    ? '<span class="badge bg-success">Активный</span>'
                    : (p.is_draft ? '<span class="badge bg-warning text-dark">Черновик</span>' : '<span class="text-muted small">—</span>');
                var notes = escapeHtml(p.notes || '') || '<span class="text-muted small">—</span>';
                return '<tr' + (p.is_active ? ' class="table-success"' : '') + '><td>v' + p.version + '</td><td>' + notes + '</td><td>' + status + '</td><td class="small text-muted">' + (p.created_at || '').slice(0, 16).replace('T', ' ') + '</td><td><button type="button" class="btn btn-sm btn-outline-secondary edit-prompt" data-id="' + p.id + '">Открыть</button></td></tr>';
            }).join('');
            tbody.querySelectorAll('.edit-prompt').forEach(function (btn) {
                btn.onclick = function () { openPromptEdit(parseInt(btn.getAttribute('data-id'), 10)); };
            });
        });
    }

    document.getElementById('btnAddPrompt').onclick = function () {
        api('/api/admin/prompts/default-template').then(function (r) { return r.json(); }).then(function (tpl) {
            return api('/api/admin/prompts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: tpl.content || '' })
            }).then(function (r) { return r.json(); });
        }).then(function (newPrompt) {
            loadPromptsTab();
            openPromptEdit(newPrompt.id);
        }).catch(function (e) { alert(e.message || 'Ошибка'); });
    };

    function openPromptEdit(id) {
        var p = promptsList.find(function (x) { return x.id === id; });
        if (!p) return;
        document.getElementById('promptId').value = p.id;
        document.getElementById('promptContent').value = p.content || '';
        document.getElementById('promptNotes').value = p.notes || '';
        document.getElementById('btnActivatePrompt').style.display = p.is_draft ? 'inline-block' : 'none';
        document.getElementById('btnTestOnSession').style.display = 'inline-block';
        new bootstrap.Modal(document.getElementById('promptEditModal')).show();
    }

    document.getElementById('btnSavePrompt').onclick = function () {
        var id = document.getElementById('promptId').value;
        if (!id) return;
        api('/api/admin/prompts/' + id, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: document.getElementById('promptContent').value, notes: document.getElementById('promptNotes').value })
        }).then(function (r) { return r.json(); }).then(function () { loadPromptsTab(); bootstrap.Modal.getInstance(document.getElementById('promptEditModal')).hide(); }).catch(function (e) { alert(e.message || 'Ошибка'); });
    };

    document.getElementById('btnActivatePrompt').onclick = function () {
        var id = document.getElementById('promptId').value;
        if (!id) return;
        api('/api/admin/prompts/' + id + '/activate', { method: 'POST' })
            .then(function () { loadPromptsTab(); bootstrap.Modal.getInstance(document.getElementById('promptEditModal')).hide(); })
            .catch(function (e) { alert(e.message || 'Ошибка'); });
    };

    document.getElementById('btnTestOnSession').onclick = function () {
        var promptId = document.getElementById('promptId').value;
        if (!promptId) return;
        api('/api/admin/sessions?page=1&limit=500').then(function (r) { return r.json(); }).then(function (d) {
            var sel = document.getElementById('testSessionSelect');
            sel.innerHTML = '<option value="">— Выберите сессию —</option>';
            (d.data || []).forEach(function (s) {
                var opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = s.id + ' — ' + (s.full_name || '') + ' — ' + (s.theme_name || '');
                sel.appendChild(opt);
            });
        });
        document.getElementById('promptTestResult').textContent = '';
        new bootstrap.Modal(document.getElementById('promptTestModal')).show();
    };

    document.getElementById('btnRunPromptTest').onclick = function () {
        var promptId = document.getElementById('promptId').value;
        var sessionId = document.getElementById('testSessionSelect').value;
        if (!promptId || !sessionId) { alert('Выберите сессию'); return; }
        var resEl = document.getElementById('promptTestResult');
        var ptSpinner = document.getElementById('promptTestSpinner');
        var btnRun = document.getElementById('btnRunPromptTest');
        resEl.textContent = '';
        ptSpinner.classList.remove('d-none');
        btnRun.disabled = true;
        api('/api/admin/prompts/' + promptId + '/test-on-session/' + sessionId, { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) { resEl.textContent = data.result || ''; })
            .catch(function (e) { resEl.textContent = 'Ошибка: ' + (e.message || ''); })
            .finally(function () { ptSpinner.classList.add('d-none'); btnRun.disabled = false; });
    };


    // --- Analyze (inline on Results tab) ---
    document.getElementById('btnCollapseAnalyze').onclick = function () {
        document.getElementById('analyzePanel').style.display = 'none';
    };

    document.getElementById('btnClearSelection').onclick = function () {
        selectedSessions.clear();
        document.querySelectorAll('.result-cb').forEach(function (cb) { cb.checked = false; });
        var sa = document.getElementById('selectAllResults');
        if (sa) sa.checked = false;
        updateAnalyzePanel();
    };

    document.getElementById('btnAnalyze').onclick = function () {
        var answers = Array.from(selectedSessions.values());
        if (!answers.length) return;
        var prompt = document.getElementById('analysisPrompt').value.trim()
            || 'проанализируй и обобщи наиболее явные ошибки и дай рекомендации';
        var resEl = document.getElementById('analysisResult');
        var spinner = document.getElementById('analyzeSpinner');
        var btnAnalyze = document.getElementById('btnAnalyze');
        var btnDl = document.getElementById('btnDownloadAnalysis');
        lastAnalysisText = '';
        resEl.textContent = '';
        resEl.style.display = 'none';
        spinner.style.display = 'inline-flex';
        btnDl.style.display = 'none';
        btnAnalyze.disabled = true;

        api('/api/admin/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ selected_answers: answers, prompt: prompt })
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.body.getReader();
        }).then(function (reader) {
            var decoder = new TextDecoder();
            var firstChunk = true;
            function read() {
                reader.read().then(function (v) {
                    if (v.done) {
                        spinner.style.display = 'none';
                        btnAnalyze.disabled = false;
                        btnDl.style.display = lastAnalysisText ? 'inline-block' : 'none';
                        return;
                    }
                    if (firstChunk) {
                        firstChunk = false;
                        spinner.style.display = 'none';
                        resEl.style.display = 'block';
                    }
                    var chunk = decoder.decode(v.value);
                    lastAnalysisText += chunk;
                    resEl.textContent += chunk;
                    resEl.scrollTop = resEl.scrollHeight;
                    read();
                });
            }
            read();
        }).catch(function (e) {
            spinner.style.display = 'none';
            btnAnalyze.disabled = false;
            resEl.textContent = 'Ошибка: ' + (e.message || '');
            resEl.style.display = 'block';
        });
    };

    document.getElementById('btnDownloadAnalysis').onclick = function () {
        if (!lastAnalysisText) return;
        var blob = new Blob([lastAnalysisText], { type: 'text/markdown' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'analysis_' + new Date().toISOString().slice(0, 10) + '.md';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };
})();
