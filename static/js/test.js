(function () {
    var LS_KEY_NAME = 'salestester_full_name';
    var BP = (window.BASE_PATH || '');
    var themes = [];
    var selectedThemeId = null;
    var selectedThemeName = '';
    var fullName = '';
    var mediaRecorder = null;
    var audioChunks = [];
    var timeoutMinutes = 20;
    var timeoutTimer = null;
    var timeoutTickInterval = null;

    // Load config (timeout) from backend
    fetch(BP + '/api/config').then(function (r) { return r.json(); }).then(function (cfg) {
        if (cfg.test_timeout_minutes > 0) timeoutMinutes = cfg.test_timeout_minutes;
    }).catch(function () {});

    // ── IndexedDB helpers for pending recording recovery ──────────────────────
    var DB_NAME = 'salestester_db';
    var DB_STORE = 'pending_recording';
    var DB_KEY = 'recording';

    function openDB(cb) {
        try {
            var req = indexedDB.open(DB_NAME, 1);
            req.onupgradeneeded = function (e) {
                e.target.result.createObjectStore(DB_STORE);
            };
            req.onsuccess = function (e) { cb(null, e.target.result); };
            req.onerror = function (e) { cb(e.target.error); };
        } catch (e) { cb(e); }
    }

    function savePendingRecording(data) {
        openDB(function (err, db) {
            if (err) return;
            try {
                db.transaction(DB_STORE, 'readwrite').objectStore(DB_STORE).put(data, DB_KEY);
            } catch (e) {}
        });
    }

    function loadPendingRecording(cb) {
        openDB(function (err, db) {
            if (err) { cb(null); return; }
            try {
                var req = db.transaction(DB_STORE, 'readonly').objectStore(DB_STORE).get(DB_KEY);
                req.onsuccess = function (e) { cb(e.target.result || null); };
                req.onerror = function () { cb(null); };
            } catch (e) { cb(null); }
        });
    }

    function clearPendingRecording() {
        openDB(function (err, db) {
            if (err) return;
            try {
                db.transaction(DB_STORE, 'readwrite').objectStore(DB_STORE).delete(DB_KEY);
            } catch (e) {}
        });
    }
    // ─────────────────────────────────────────────────────────────────────────

    function startSessionTimeout() {
        clearSessionTimeout();
        if (!timeoutMinutes) return;
        var endsAt = Date.now() + timeoutMinutes * 60 * 1000;
        var timerEl = document.getElementById('sessionTimer');

        timeoutTickInterval = setInterval(function () {
            var left = endsAt - Date.now();
            if (left <= 0) {
                clearSessionTimeout();
                alert('Время сессии истекло. Пожалуйста, начните заново.');
                resetToStep1();
                return;
            }
            var m = Math.floor(left / 60000);
            var s = Math.floor((left % 60000) / 1000);
            timerEl.textContent = 'Осталось: ' + m + ':' + (s < 10 ? '0' : '') + s;
        }, 1000);
    }

    function clearSessionTimeout() {
        if (timeoutTickInterval) { clearInterval(timeoutTickInterval); timeoutTickInterval = null; }
        var timerEl = document.getElementById('sessionTimer');
        if (timerEl) timerEl.textContent = '';
    }

    function resetToStep1() {
        clearSessionTimeout();
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            try { mediaRecorder.stop(); } catch (e) {}
        }
        audioChunks = [];
        selectedThemeId = null;
        selectedThemeName = '';
        document.getElementById('loadingArea').style.display = 'none';
        document.getElementById('submitArea').style.display = 'none';
        document.getElementById('recordArea').style.display = 'block';
        document.getElementById('fullName').value = fullName;
        showStep(1);
    }

    var step1 = document.getElementById('step1');
    var step2 = document.getElementById('step2');
    var step3 = document.getElementById('step3');
    var step4 = document.getElementById('step4');
    var fullNameInput = document.getElementById('fullName');
    var themeList = document.getElementById('themeList');
    var selectedThemeNameEl = document.getElementById('selectedThemeName');
    var recordArea = document.getElementById('recordArea');
    var submitArea = document.getElementById('submitArea');
    var loadingArea = document.getElementById('loadingArea');
    var recordStatus = document.getElementById('recordStatus');
    var resultContent = document.getElementById('resultContent');
    var resultScore = document.getElementById('resultScore');

    // Восстановить ФИО из localStorage
    var savedName = localStorage.getItem(LS_KEY_NAME);
    if (savedName) fullNameInput.value = savedName;

    // Проверить незавершённую запись в IndexedDB
    loadPendingRecording(function (pending) {
        if (!pending) return;
        var banner = document.getElementById('recoverBanner');
        document.getElementById('recoverThemeName').textContent = pending.themeName;
        document.getElementById('recoverFullName').textContent = pending.fullName;
        banner.style.display = 'block';

        document.getElementById('btnRecover').onclick = function () {
            banner.style.display = 'none';
            fullName = pending.fullName;
            fullNameInput.value = fullName;
            localStorage.setItem(LS_KEY_NAME, fullName);
            selectedThemeId = pending.themeId;
            selectedThemeName = pending.themeName;
            selectedThemeNameEl.textContent = 'Тема: ' + pending.themeName;
            audioChunks = [pending.blob];
            recordArea.style.display = 'none';
            submitArea.style.display = 'block';
            showStep(3);
            startSessionTimeout();
        };

        document.getElementById('btnRecoverDismiss').onclick = function () {
            banner.style.display = 'none';
            clearPendingRecording();
        };
    });

    function showStep(step) {
        step1.style.display = step === 1 ? 'block' : 'none';
        step2.style.display = step === 2 ? 'block' : 'none';
        step3.style.display = step === 3 ? 'block' : 'none';
        step4.style.display = step === 4 ? 'block' : 'none';
    }

    document.getElementById('btnStep1').onclick = function () {
        fullName = (fullNameInput.value || '').trim();
        if (!fullName) {
            alert('Введите ФИО');
            return;
        }
        localStorage.setItem(LS_KEY_NAME, fullName);
        loadThemes();
    };

    function loadThemes() {
        themeList.innerHTML = '<p>Загрузка тем...</p>';
        fetch(BP + '/api/themes')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                themes = data;
                if (!themes.length) {
                    themeList.innerHTML = '<p>Нет доступных тем. Обратитесь к администратору.</p>';
                    return;
                }
                themeList.innerHTML = '';
                themes.forEach(function (t) {
                    var btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'btn-directum';
                    btn.style.cssText = 'display: block; width: 100%; margin-bottom: 8px; padding: 12px 16px; text-align: left; border-radius: 4px; cursor: pointer; box-sizing: border-box;';
                    btn.textContent = t.name;
                    btn.onclick = function () {
                        selectedThemeId = t.id;
                        selectedThemeName = t.name;
                        selectedThemeNameEl.textContent = 'Тема: ' + t.name;
                        showStep(3);
                        resetRecording();
                        startSessionTimeout();
                    };
                    themeList.appendChild(btn);
                });
                showStep(2);
            })
            .catch(function () {
                themeList.innerHTML = '<p>Ошибка загрузки тем.</p>';
            });
    }

    document.getElementById('btnBack2').onclick = function () { showStep(1); };
    document.getElementById('btnBack3').onclick = function () { showStep(2); };

    function resetRecording() {
        audioChunks = [];
        clearPendingRecording();
        recordArea.style.display = 'block';
        submitArea.style.display = 'none';
        recordStatus.textContent = '';
        document.getElementById('btnRecord').textContent = '🎤 Нажать и говорить';
        document.getElementById('btnRecord').disabled = false;
    }

    document.getElementById('btnRecord').onclick = function () {
        var btn = this;
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            // Disable immediately to prevent double-click race condition:
            // after stop(), state becomes 'inactive' synchronously but onstop fires
            // asynchronously — a second click would fall through to getUserMedia()
            // and clear audioChunks right before onstop shows the submit button.
            btn.disabled = true;
            mediaRecorder.stop();
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            alert('Запись с микрофона не поддерживается в этом браузере.');
            return;
        }
        navigator.mediaDevices.getUserMedia({ audio: true })
            .then(function (stream) {
                var options = { mimeType: 'audio/webm;codecs=opus' };
                if (!MediaRecorder.isTypeSupported(options.mimeType)) {
                    options = {};
                }
                mediaRecorder = new MediaRecorder(stream, options);
                audioChunks = [];
                mediaRecorder.ondataavailable = function (e) {
                    if (e.data.size > 0) audioChunks.push(e.data);
                };
                mediaRecorder.onstop = function () {
                    stream.getTracks().forEach(function (t) { t.stop(); });
                    if (audioChunks.length) {
                        recordArea.style.display = 'none';
                        submitArea.style.display = 'block';
                        savePendingRecording({
                            themeId: selectedThemeId,
                            themeName: selectedThemeName,
                            fullName: fullName,
                            blob: new Blob(audioChunks, { type: 'audio/webm' }),
                            savedAt: Date.now()
                        });
                    }
                    recordStatus.textContent = '';
                    btn.textContent = '🎤 Нажать и говорить';
                    btn.disabled = false;
                };
                mediaRecorder.start();
                btn.textContent = '⏹ Остановить запись';
                recordStatus.textContent = '● Идёт запись...';
            })
            .catch(function (err) {
                alert('Нет доступа к микрофону: ' + (err.message || 'Ошибка'));
                btn.disabled = false;
            });
    };

    document.getElementById('btnRerecord').onclick = function () {
        clearPendingRecording();
        resetRecording();
    };

    document.getElementById('btnSubmit').onclick = function () {
        if (!audioChunks.length) return;
        var btn = document.getElementById('btnSubmit');
        btn.disabled = true;
        clearSessionTimeout();
        submitArea.style.display = 'none';
        loadingArea.style.display = 'block';

        var blob = new Blob(audioChunks, { type: 'audio/webm' });
        var form = new FormData();
        form.append('full_name', fullName);
        form.append('theme_id', selectedThemeId);
        form.append('audio', blob, 'recording.webm');

        fetch(BP + '/api/test', { method: 'POST', body: form })
            .then(function (r) {
                var ct = r.headers.get('content-type') || '';
                if (!r.ok) {
                    if (ct.indexOf('application/json') !== -1) {
                        return r.json().then(function (j) { throw new Error(j.detail || ('Ошибка ' + r.status)); });
                    }
                    throw new Error('Ошибка сервера ' + r.status + '. Попробуйте ещё раз.');
                }
                return r.json();
            })
            .then(function (data) {
                clearPendingRecording();
                resultContent.textContent = data.result || '';
                if (data.score != null) {
                    resultScore.textContent = 'Итоговая оценка: ' + data.score;
                    var colors = { 1: '#dc3545', 2: '#fd7e14', 3: '#e6a817', 4: '#5baa5b', 5: '#198754' };
                    resultScore.style.color = colors[data.score] || '#333';
                } else {
                    resultScore.textContent = '';
                }
                loadingArea.style.display = 'none';
                showStep(4);
            })
            .catch(function (err) {
                loadingArea.style.display = 'none';
                submitArea.style.display = 'block';
                btn.disabled = false;
                alert('Ошибка: ' + (err.message || 'Не удалось отправить ответ'));
            });
    };

    document.getElementById('btnNewTest').onclick = function () {
        fullNameInput.value = fullName;
        selectedThemeId = null;
        selectedThemeName = '';
        audioChunks = [];
        showStep(1);
    };
})();
