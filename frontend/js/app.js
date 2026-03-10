/**
 * AI 语音任务助手 - 前端应用
 * 纯原生 JavaScript (ES6+)
 *
 * 支持两种 ASR 模式:
 * - 实时转写 (realtime): 录音时通过 WebSocket 流式转写，实时显示文字
 * - 文件转写 (file): 录音完成后上传文件，后端异步转写
 */

(function () {
    'use strict';

    // ==================== 常量 & 配置 ====================

    const API_BASE = '/api';
    const POLL_INTERVAL = 2000;
    const LIST_REFRESH_INTERVAL = 15000;
    const TOAST_DURATION = 3000;
    const REALTIME_SAMPLE_RATE = 16000;

    const STATUS_MAP = {
        CREATED: '已创建',
        UPLOADED: '已上传',
        TRANSCRIBING: '转写中...',
        SUMMARIZING: '总结中...',
        PACKAGING: '打包中...',
        DONE: '已完成',
        FAILED: '处理失败',
        EXPIRED: '已过期',
    };

    const STATUS_PROGRESS = {
        CREATED: 'progress-20',
        UPLOADED: 'progress-20',
        TRANSCRIBING: 'progress-40',
        SUMMARIZING: 'progress-60',
        PACKAGING: 'progress-80',
        DONE: 'progress-100 done',
        FAILED: 'progress-40 failed',
        EXPIRED: 'progress-100',
    };

    // ==================== DOM 引用 ====================

    const $id = (id) => document.getElementById(id);

    const recordBtn = $id('recordBtn');
    const recordIcon = $id('recordIcon');
    const recordStatus = $id('recordStatus');
    const recordTimer = $id('recordTimer');
    const waveform = $id('waveform');
    const pulseRing = $id('pulseRing');

    const asrModeSelector = $id('asrModeSelector');
    const realtimeTranscriptDiv = $id('realtimeTranscript');
    const realtimeTranscriptContent = $id('realtimeTranscriptContent');
    const realtimeIndicator = $id('realtimeIndicator');

    const currentTaskSection = $id('currentTaskSection');
    const currentTaskStatus = $id('currentTaskStatus');
    const currentTaskId = $id('currentTaskId');
    const currentTaskActions = $id('currentTaskActions');
    const taskProgressBar = $id('taskProgressBar');

    const taskList = $id('taskList');

    const settingsBtn = $id('settingsBtn');
    const settingsModal = $id('settingsModal');
    const modalCloseBtn = $id('modalCloseBtn');
    const modalCancelBtn = $id('modalCancelBtn');
    const modalSaveBtn = $id('modalSaveBtn');
    const summaryPrompt = $id('summaryPrompt');

    const toastContainer = $id('toastContainer');

    // ==================== 状态 ====================

    let isRecording = false;
    let isUploading = false;
    let mediaRecorder = null;
    let audioChunks = [];
    let recordingStream = null;
    let recordingStartTime = null;
    let timerInterval = null;
    let currentPollTaskId = null;
    let pollTimer = null;
    let listRefreshTimer = null;

    // ASR 模式相关
    let asrMode = 'realtime';
    let realtimeWs = null;
    let realtimeTranscript = '';
    let realtimeFinalReceived = false;
    let audioContext = null;
    let scriptProcessor = null;

    // ==================== 工具函数 ====================

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type === 'error' ? 'toast-error' : type === 'success' ? 'toast-success' : ''}`;
        toast.textContent = message;
        toastContainer.appendChild(toast);
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, TOAST_DURATION);
    }

    function formatDuration(totalSeconds) {
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = Math.floor(totalSeconds % 60);
        return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }

    function formatTime(isoString) {
        if (!isoString) return '--';
        try {
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return '--';
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            const hours = String(date.getHours()).padStart(2, '0');
            const minutes = String(date.getMinutes()).padStart(2, '0');
            return `${month}-${day} ${hours}:${minutes}`;
        } catch { return '--'; }
    }

    function getStatusText(status) {
        return STATUS_MAP[status] || status;
    }

    function shortTaskId(taskId) {
        if (!taskId) return '--';
        if (taskId.length <= 12) return taskId;
        return taskId.substring(0, 8) + '...';
    }

    async function apiRequest(url, options = {}) {
        try {
            const response = await fetch(url, options);
            if (!response.ok) {
                const errorText = await response.text().catch(() => '');
                throw new Error(`请求失败 (${response.status}): ${errorText || response.statusText}`);
            }
            return response;
        } catch (err) {
            if (err.name === 'TypeError' && err.message.includes('fetch')) {
                throw new Error('网络连接失败，请检查网络');
            }
            throw err;
        }
    }

    // ==================== ASR 模式选择 ====================

    document.querySelectorAll('input[name="asrMode"]').forEach((radio) => {
        radio.addEventListener('change', (e) => {
            asrMode = e.target.value;
            realtimeTranscriptContent.innerHTML = '<span class="realtime-placeholder">等待语音输入...</span>';
            realtimeTranscriptDiv.style.display = 'none';
        });
    });

    function disableModeSelector() {
        asrModeSelector.querySelectorAll('input').forEach((r) => (r.disabled = true));
    }

    function enableModeSelector() {
        asrModeSelector.querySelectorAll('input').forEach((r) => (r.disabled = false));
    }

    // ==================== 实时 ASR WebSocket ====================

    function connectRealtimeWs() {
        return new Promise((resolve, reject) => {
            const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${wsProtocol}//${location.host}/api/task/realtime-asr`;
            realtimeWs = new WebSocket(wsUrl);

            realtimeWs.onopen = () => {
                realtimeWs.send(JSON.stringify({
                    action: 'start',
                    sample_rate: REALTIME_SAMPLE_RATE,
                    format: 'pcm',
                }));
            };

            realtimeWs.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'started') {
                    resolve();
                } else if (data.type === 'partial') {
                    realtimeTranscriptContent.textContent = realtimeTranscript + data.text;
                    // 自动滚动到底部
                    realtimeTranscriptContent.scrollTop = realtimeTranscriptContent.scrollHeight;
                } else if (data.type === 'final') {
                    realtimeTranscript = data.text;
                    realtimeTranscriptContent.textContent = realtimeTranscript || '(未检测到语音内容)';
                    realtimeFinalReceived = true;
                    if (realtimeIndicator) realtimeIndicator.style.display = 'none';
                } else if (data.type === 'error') {
                    showToast('实时转写出错: ' + data.message, 'error');
                }
            };

            realtimeWs.onerror = () => {
                showToast('实时转写连接失败', 'error');
                reject(new Error('WebSocket 连接失败'));
            };

            realtimeWs.onclose = () => {
                // 非正常关闭时不做特殊处理
            };

            // 连接超时
            setTimeout(() => reject(new Error('实时转写连接超时')), 8000);
        });
    }

    function startPcmStreaming(stream) {
        try {
            audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: REALTIME_SAMPLE_RATE,
            });
        } catch {
            // 如果浏览器不支持指定采样率，使用默认采样率
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }

        const source = audioContext.createMediaStreamSource(stream);

        // ScriptProcessorNode: 广泛支持，用于采集 PCM 数据
        const bufferSize = 4096;
        scriptProcessor = audioContext.createScriptProcessor(bufferSize, 1, 1);

        scriptProcessor.onaudioprocess = (e) => {
            if (realtimeWs && realtimeWs.readyState === WebSocket.OPEN) {
                const float32Data = e.inputBuffer.getChannelData(0);

                // 如果实际采样率与目标不同，需要重采样
                let samples = float32Data;
                if (audioContext.sampleRate !== REALTIME_SAMPLE_RATE) {
                    samples = resample(float32Data, audioContext.sampleRate, REALTIME_SAMPLE_RATE);
                }

                // Float32 -> Int16 PCM
                const int16Data = new Int16Array(samples.length);
                for (let i = 0; i < samples.length; i++) {
                    const s = Math.max(-1, Math.min(1, samples[i]));
                    int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                realtimeWs.send(int16Data.buffer);
            }
        };

        source.connect(scriptProcessor);
        scriptProcessor.connect(audioContext.destination);
    }

    /**
     * 简单线性重采样
     */
    function resample(inputSamples, fromRate, toRate) {
        if (fromRate === toRate) return inputSamples;
        const ratio = fromRate / toRate;
        const outputLength = Math.round(inputSamples.length / ratio);
        const output = new Float32Array(outputLength);
        for (let i = 0; i < outputLength; i++) {
            const srcIndex = i * ratio;
            const idx = Math.floor(srcIndex);
            const frac = srcIndex - idx;
            if (idx + 1 < inputSamples.length) {
                output[i] = inputSamples[idx] * (1 - frac) + inputSamples[idx + 1] * frac;
            } else {
                output[i] = inputSamples[idx] || 0;
            }
        }
        return output;
    }

    function stopPcmStreaming() {
        if (scriptProcessor) {
            scriptProcessor.disconnect();
            scriptProcessor = null;
        }
        if (audioContext) {
            audioContext.close().catch(() => {});
            audioContext = null;
        }
    }

    function stopRealtimeWs() {
        if (realtimeWs && realtimeWs.readyState === WebSocket.OPEN) {
            realtimeWs.send(JSON.stringify({ action: 'stop' }));
        }
    }

    function closeRealtimeWs() {
        if (realtimeWs) {
            realtimeWs.close();
            realtimeWs = null;
        }
    }

    // ==================== 录音管理 ====================

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: REALTIME_SAMPLE_RATE,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                },
            });
            recordingStream = stream;

            // ---- 实时模式：先建立 WebSocket 连接 ----
            if (asrMode === 'realtime') {
                realtimeTranscript = '';
                realtimeFinalReceived = false;
                realtimeTranscriptDiv.style.display = 'block';
                realtimeTranscriptContent.innerHTML = '<span class="realtime-placeholder">正在连接转写服务...</span>';
                if (realtimeIndicator) realtimeIndicator.style.display = 'block';

                try {
                    await connectRealtimeWs();
                    realtimeTranscriptContent.innerHTML = '<span class="realtime-placeholder">等待语音输入...</span>';
                } catch (err) {
                    console.error('Failed to connect realtime ASR:', err);
                    showToast('实时转写服务连接失败，已切换为文件转写模式', 'error');
                    asrMode = 'file';
                    realtimeTranscriptDiv.style.display = 'none';
                    // 更新 radio 选中状态
                    const fileRadio = document.querySelector('input[name="asrMode"][value="file"]');
                    if (fileRadio) fileRadio.checked = true;
                }
            }

            // ---- MediaRecorder: 录制完整音频文件（两种模式都需要） ----
            let mimeType = 'audio/webm';
            if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                mimeType = 'audio/webm;codecs=opus';
            } else if (MediaRecorder.isTypeSupported('audio/webm')) {
                mimeType = 'audio/webm';
            } else if (MediaRecorder.isTypeSupported('audio/wav')) {
                mimeType = 'audio/wav';
            } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
                mimeType = 'audio/mp4';
            }

            audioChunks = [];
            mediaRecorder = new MediaRecorder(stream, { mimeType });

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) audioChunks.push(event.data);
            };

            mediaRecorder.onstop = () => {
                handleRecordingComplete();
            };

            mediaRecorder.onerror = (event) => {
                console.error('MediaRecorder error:', event.error);
                showToast('录音出错，请重试', 'error');
                cleanupRecording();
                resetRecordingUI();
            };

            mediaRecorder.start(100);

            // ---- 实时模式：启动 PCM 流式传输 ----
            if (asrMode === 'realtime' && realtimeWs) {
                startPcmStreaming(stream);
            }

            isRecording = true;
            recordingStartTime = Date.now();

            // 更新 UI
            disableModeSelector();
            recordBtn.classList.add('recording');
            recordIcon.textContent = '⏹';
            recordBtn.title = '点击停止录音';
            recordStatus.textContent = asrMode === 'realtime' ? '正在录音并实时转写...' : '正在录音...';
            recordTimer.style.display = 'block';
            recordTimer.textContent = '00:00';
            waveform.style.display = 'flex';
            pulseRing.classList.add('active');

            timerInterval = setInterval(() => {
                const elapsed = (Date.now() - recordingStartTime) / 1000;
                recordTimer.textContent = formatDuration(elapsed);
            }, 200);

        } catch (err) {
            console.error('Failed to start recording:', err);
            if (err.name === 'NotAllowedError') {
                showToast('请允许麦克风权限后重试', 'error');
            } else if (err.name === 'NotFoundError') {
                showToast('未检测到麦克风设备', 'error');
            } else {
                showToast('无法启动录音: ' + err.message, 'error');
            }
        }
    }

    function stopRecording() {
        isRecording = false;

        // 停止计时器
        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }

        // 实时模式：停止 PCM 流和 WebSocket
        if (asrMode === 'realtime') {
            stopPcmStreaming();
            stopRealtimeWs();
        }

        // 停止 MediaRecorder（触发 onstop -> handleRecordingComplete）
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
        }

        // 更新 UI
        pulseRing.classList.remove('active');
        waveform.style.display = 'none';
        recordStatus.textContent = asrMode === 'realtime' ? '正在等待转写结果...' : '正在上传录音...';
    }

    function cleanupRecording() {
        stopPcmStreaming();
        closeRealtimeWs();
        if (recordingStream) {
            recordingStream.getTracks().forEach((track) => track.stop());
            recordingStream = null;
        }
    }

    function resetRecordingUI() {
        isRecording = false;
        isUploading = false;

        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }

        enableModeSelector();
        recordBtn.classList.remove('recording', 'uploading');
        recordBtn.disabled = false;
        recordIcon.textContent = '🎤';
        recordBtn.title = '点击开始录音';
        recordStatus.textContent = '点击按钮开始录音';
        recordTimer.style.display = 'none';
        waveform.style.display = 'none';
        pulseRing.classList.remove('active');
    }

    async function handleRecordingComplete() {
        if (audioChunks.length === 0) {
            showToast('录音为空，请重试', 'error');
            cleanupRecording();
            resetRecordingUI();
            return;
        }

        // 实时模式：等待最终转写结果
        if (asrMode === 'realtime' && realtimeWs) {
            if (!realtimeFinalReceived) {
                recordStatus.textContent = '正在等待转写结果...';
                await new Promise((resolve) => {
                    const checkInterval = setInterval(() => {
                        if (realtimeFinalReceived) {
                            clearInterval(checkInterval);
                            resolve();
                        }
                    }, 200);
                    // 最长等待 15 秒
                    setTimeout(() => {
                        clearInterval(checkInterval);
                        resolve();
                    }, 15000);
                });
            }
            closeRealtimeWs();
        }

        // 停止录音流
        if (recordingStream) {
            recordingStream.getTracks().forEach((track) => track.stop());
            recordingStream = null;
        }

        isUploading = true;
        recordBtn.classList.remove('recording');
        recordBtn.classList.add('uploading');
        recordBtn.disabled = true;
        recordIcon.textContent = '⏳';
        recordStatus.textContent = '正在上传录音...';

        const mimeType = mediaRecorder.mimeType || 'audio/webm';
        const extension = mimeType.includes('wav') ? 'wav' : mimeType.includes('mp4') ? 'm4a' : 'webm';
        const blob = new Blob(audioChunks, { type: mimeType });
        const file = new File([blob], `recording.${extension}`, { type: mimeType });

        try {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('asr_mode', asrMode);

            // 实时模式：附带已获得的转写文本
            if (asrMode === 'realtime' && realtimeTranscript) {
                formData.append('transcript_text', realtimeTranscript);
            }

            const response = await apiRequest(`${API_BASE}/task/upload`, {
                method: 'POST',
                body: formData,
            });

            const data = await response.json();

            if (data.task_id) {
                showToast('录音上传成功', 'success');
                resetRecordingUI();
                showCurrentTask(data.task_id, data.status || 'CREATED');
                startPolling(data.task_id);
                loadTaskList();
            } else {
                throw new Error('未返回 task_id');
            }
        } catch (err) {
            console.error('Upload failed:', err);
            showToast('上传失败: ' + err.message, 'error');
            resetRecordingUI();
        }
    }

    // ==================== 录音按钮事件 ====================

    recordBtn.addEventListener('click', () => {
        if (isUploading) return;
        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    });

    // ==================== 当前任务状态 ====================

    function showCurrentTask(taskId, status) {
        currentTaskSection.style.display = 'block';
        currentTaskId.textContent = shortTaskId(taskId);
        currentTaskId.title = taskId;
        updateCurrentTaskStatus(status, taskId);
    }

    function updateCurrentTaskStatus(status, taskId) {
        currentTaskStatus.textContent = getStatusText(status);
        currentTaskStatus.className = `status-label status-${status}`;
        taskProgressBar.className = `task-progress-bar ${STATUS_PROGRESS[status] || ''}`;

        currentTaskActions.innerHTML = '';
        if (status === 'DONE') {
            const downloadBtn = document.createElement('button');
            downloadBtn.className = 'btn btn-download btn-sm';
            downloadBtn.innerHTML = '📥 下载结果';
            downloadBtn.addEventListener('click', () => downloadTask(taskId));
            currentTaskActions.appendChild(downloadBtn);
        } else if (status === 'FAILED') {
            const retryHint = document.createElement('span');
            retryHint.style.fontSize = '0.8rem';
            retryHint.style.color = 'var(--red)';
            retryHint.textContent = '任务处理失败，请重新录制';
            currentTaskActions.appendChild(retryHint);
        }
    }

    // ==================== 任务状态轮询 ====================

    function startPolling(taskId) {
        stopPolling();
        currentPollTaskId = taskId;

        async function poll() {
            try {
                const response = await apiRequest(`${API_BASE}/task/${taskId}/status`);
                const data = await response.json();
                updateCurrentTaskStatus(data.status, taskId);

                if (['DONE', 'FAILED', 'EXPIRED'].includes(data.status)) {
                    stopPolling();
                    loadTaskList();
                    if (data.status === 'DONE') showToast('任务处理完成！', 'success');
                    else if (data.status === 'FAILED') showToast('任务处理失败', 'error');
                    return;
                }
                pollTimer = setTimeout(poll, POLL_INTERVAL);
            } catch (err) {
                console.error('Polling error:', err);
                pollTimer = setTimeout(poll, POLL_INTERVAL * 2);
            }
        }
        poll();
    }

    function stopPolling() {
        if (pollTimer) {
            clearTimeout(pollTimer);
            pollTimer = null;
        }
        currentPollTaskId = null;
    }

    // ==================== 下载 ====================

    function downloadTask(taskId) {
        const url = `${API_BASE}/task/${taskId}/download`;
        const a = document.createElement('a');
        a.href = url;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }

    // ==================== 历史任务列表 ====================

    async function loadTaskList() {
        try {
            const response = await apiRequest(`${API_BASE}/tasks`);
            const data = await response.json();
            renderTaskList(data.tasks || []);
        } catch (err) {
            console.error('Failed to load task list:', err);
        }
    }

    function renderTaskList(tasks) {
        taskList.innerHTML = '';

        if (!tasks || tasks.length === 0) {
            const emptyEl = document.createElement('div');
            emptyEl.className = 'task-list-empty';
            emptyEl.innerHTML = '<span class="empty-icon">📭</span><p>暂无历史任务</p>';
            taskList.appendChild(emptyEl);
            return;
        }

        tasks.forEach((task) => {
            const card = document.createElement('div');
            card.className = 'task-card';

            const info = document.createElement('div');
            info.className = 'task-card-info';

            const top = document.createElement('div');
            top.className = 'task-card-top';

            const statusEl = document.createElement('span');
            statusEl.className = `status-label status-${task.status}`;
            statusEl.textContent = getStatusText(task.status);

            const idEl = document.createElement('span');
            idEl.className = 'task-card-id';
            idEl.textContent = shortTaskId(task.id);
            idEl.title = task.id;

            top.appendChild(statusEl);
            top.appendChild(idEl);

            const timeEl = document.createElement('div');
            timeEl.className = 'task-card-time';
            let timeText = `创建: ${formatTime(task.created_at)}`;
            if (task.expires_at) {
                timeText += `  |  过期: ${formatTime(task.expires_at)}`;
            }
            timeEl.textContent = timeText;

            info.appendChild(top);
            info.appendChild(timeEl);

            const actionsEl = document.createElement('div');
            actionsEl.className = 'task-card-actions';

            if (task.status === 'DONE' && task.zip_filename) {
                const downloadBtn = document.createElement('button');
                downloadBtn.className = 'btn btn-download btn-sm';
                downloadBtn.innerHTML = '📥 下载';
                downloadBtn.addEventListener('click', () => downloadTask(task.id));
                actionsEl.appendChild(downloadBtn);
            } else if (['TRANSCRIBING', 'SUMMARIZING', 'PACKAGING', 'CREATED', 'UPLOADED'].includes(task.status)) {
                const spinnerEl = document.createElement('span');
                spinnerEl.className = 'spinner';
                actionsEl.appendChild(spinnerEl);
            }

            card.appendChild(info);
            card.appendChild(actionsEl);
            taskList.appendChild(card);
        });
    }

    function startListRefresh() {
        loadTaskList();
        listRefreshTimer = setInterval(loadTaskList, LIST_REFRESH_INTERVAL);
    }

    // ==================== 设置管理 ====================

    async function openSettings() {
        settingsModal.classList.add('active');
        modalSaveBtn.disabled = true;
        summaryPrompt.value = '';

        try {
            const response = await apiRequest(`${API_BASE}/settings`);
            const data = await response.json();
            summaryPrompt.value = data.summary_prompt || '';
        } catch (err) {
            console.error('Failed to load settings:', err);
            showToast('加载设置失败', 'error');
        } finally {
            modalSaveBtn.disabled = false;
        }
    }

    function closeSettings() {
        settingsModal.classList.remove('active');
    }

    async function saveSettings() {
        const prompt = summaryPrompt.value.trim();
        modalSaveBtn.disabled = true;
        modalSaveBtn.textContent = '保存中...';

        try {
            await apiRequest(`${API_BASE}/settings`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ summary_prompt: prompt }),
            });
            showToast('设置已保存', 'success');
            closeSettings();
        } catch (err) {
            console.error('Failed to save settings:', err);
            showToast('保存失败: ' + err.message, 'error');
        } finally {
            modalSaveBtn.disabled = false;
            modalSaveBtn.textContent = '保存';
        }
    }

    settingsBtn.addEventListener('click', openSettings);
    modalCloseBtn.addEventListener('click', closeSettings);
    modalCancelBtn.addEventListener('click', closeSettings);
    modalSaveBtn.addEventListener('click', saveSettings);

    settingsModal.addEventListener('click', (e) => {
        if (e.target === settingsModal) closeSettings();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && settingsModal.classList.contains('active')) closeSettings();
    });

    // ==================== 初始化 ====================

    function init() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            recordBtn.disabled = true;
            recordBtn.classList.add('uploading');
            recordStatus.textContent = '当前浏览器不支持录音功能';
            showToast('浏览器不支持录音，请使用 Chrome/Firefox/Edge', 'error');
        }
        startListRefresh();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
