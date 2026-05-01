import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import './AgentWorkspace.css';
import ProjectSetupModal from './ProjectSetupModal';

const API_BASE = '';

export default function AgentWorkspace({ scriptId, onBack, onProjectCreated }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: '你好，我是你的 AI 短剧导演 ✨\n\n你可以告诉我你的创意想法，比如：\n"帮我写一个重返18岁的校园剧"\n"设计一个冷酷的总裁男主角色"\n"把当前的场景生成视频"\n\n准备好开始了吗？' }
  ]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [workspaceData, setWorkspaceData] = useState(null);
  const [activeTab, setActiveTab] = useState('canvas');
  const [projectConfig, setProjectConfig] = useState(null);
  const [directorStatus, setDirectorStatus] = useState(null);
  const [directorTimeline, setDirectorTimeline] = useState([]);
  // lightbox：点击图片放大查看。{ url, title }，null 表示不显示。
  const [lightbox, setLightbox] = useState(null);

  const openLightbox = (url, title = '') => {
    if (url) setLightbox({ url, title });
  };
  const closeLightbox = () => setLightbox(null);

  // ESC 关闭 lightbox
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e) => { if (e.key === 'Escape') closeLightbox(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightbox]);

  const chatEndRef = useRef(null);
  const projectPollingRef = useRef(null);
  const timelineRef = useRef(null);

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => { scrollToBottom(); }, [messages]);

  // 执行日志新条目时自动滚到容器底部，避免老条目挡住最新进度
  useEffect(() => {
    const el = timelineRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [directorTimeline]);

  useEffect(() => {
    if (scriptId) {
      fetchWorkspaceData(scriptId).then(data => {
        if (data?.status === 'processing') startProjectPolling(scriptId);
      });
      setMessages([{ role: 'assistant', text: '已加载现有项目！我们可以继续修改剧本、设计角色或生成分镜。' }]);
    }
    return () => {
      if (projectPollingRef.current) {
        clearInterval(projectPollingRef.current);
        projectPollingRef.current = null;
      }
    };
  }, [scriptId]);

  const fetchWorkspaceData = async (id) => {
    try {
      const res = await axios.get(`${API_BASE}/api/scripts/${id}`);
      setWorkspaceData(res.data);
      if (res.data?.project_config) setProjectConfig(res.data.project_config);
      return res.data;
    } catch (error) {
      console.error('获取项目数据失败:', error);
      return null;
    }
  };

  const getErrorMessage = (err) => {
    const detail = err?.response?.data?.detail || err?.response?.data?.error || err?.message;
    if (typeof detail === 'string') return detail;
    if (detail) return JSON.stringify(detail);
    return '未知错误';
  };

  const pushAssistantMessage = (text) => {
    setMessages(prev => [...prev, { role: 'assistant', text }]);
  };

  // 一个后台异步任务完成后，告诉用户接下来怎么走。
  // 我们刻意不自动接力——一切下一步都由用户触发，避免烧错额度或方向走偏。
  const NEXT_STEP_HINTS = {
    GENERATE_CHARACTERS: (
      '🎨 **角色图已全部生成完毕**，请到「角色」Tab 检查：\n' +
      '  • 不满意可以告诉我「**重做 [角色名] 的图**」或点角色卡片上的「重做」按钮\n' +
      '  • 角色形象会作为后续分镜图的参考，请仔细把关\n\n' +
      '👉 **全部满意后，回复「生成场景图」我再进入下一步**。'
    ),
    GENERATE_LOCATIONS: (
      '🏞 **场景图已全部生成完毕**，请到「角色」Tab 下方的「场景」区检查：\n' +
      '  • 不满意可以告诉我「**重做 [场景名] 的图**」或点场景卡上的「重做」按钮\n' +
      '  • 场景图会作为分镜图里 [@场景] 的视觉锚点\n\n' +
      '👉 **全部满意后，回复「生成分镜图」我再进入下一步**。'
    ),
    GENERATE_STORYBOARD: (
      '🎬 **分镜图已全部生成完毕**，请到「分镜」Tab 逐镜确认：\n' +
      '  • 不满意单镜可以点该镜头的「重做」按钮，或告诉我「**重做第 N 镜**」\n' +
      '  • 分镜图会作为视频生成的首帧参考\n\n' +
      '👉 **全部满意后，回复「生成视频」我再进入下一步**（视频生成耗时和额度都比较多）。'
    ),
    GENERATE_SHOT_VIDEOS: (
      '🎥 **分镜视频已全部生成完毕**：\n' +
      '  • 可以让我「**自动审片**」逐镜检查\n' +
      '  • 也可以人工逐镜审核，不合格可单镜重做\n\n' +
      '👉 全部确认后，回复「**合成成片**」我再合成最终视频。'
    ),
    GENERATE_AUDIO: '🎙️ 配音已生成，可以试听并调整。确认后告诉我"合成成片"。',
    MERGE_FINAL: '✅ 最终成片已合成完毕，可以下载查看。',
  };

  const pollTaskStatus = (id, label = '后台任务', options = {}) => {
    if (!id) return null;
    const intervalMs = options.intervalMs || 3000;
    const maxAttempts = options.maxAttempts || 80;
    let attempts = 0;
    const iv = setInterval(async () => {
      attempts += 1;
      try {
        const [statusRes] = await Promise.all([
          axios.get(`${API_BASE}/api/scripts/${id}/status`),
          fetchWorkspaceData(id)
        ]);
        const task = statusRes.data || {};
        if (task.status === 'failed') {
          clearInterval(iv);
          const detail = task.error || task.current_step || '后台任务失败，但没有返回具体错误。';
          setDirectorStatus({
            phase: 'error',
            label: `${label}失败`,
            detail,
            progress: 100,
            action: task.action
          });
          setDirectorTimeline(prev => [...prev, {
            phase: 'error',
            label: `${label}失败`,
            detail,
            progress: 100,
            time: new Date().toLocaleTimeString()
          }].slice(-8));
          pushAssistantMessage(`❌ ${label}失败：${detail}`);
        } else if (task.status === 'completed') {
          clearInterval(iv);
          await fetchWorkspaceData(id);
          setDirectorStatus(prev => ({
            ...(prev || {}),
            phase: 'done',
            label: `${label}完成`,
            detail: task.current_step || '任务已完成，工作台已刷新。',
            progress: 100
          }));
          // 把"下一步该做什么"作为助手消息推到对话里，让用户掌控节奏。
          // 用 task.action 优先匹配，匹配不到就按 label 关键字兜底。
          let hint = NEXT_STEP_HINTS[task.action];
          if (!hint) {
            if (/场景/.test(label)) hint = NEXT_STEP_HINTS.GENERATE_LOCATIONS;
            else if (/角色/.test(label)) hint = NEXT_STEP_HINTS.GENERATE_CHARACTERS;
            else if (/分镜/.test(label) && !/视频/.test(label)) hint = NEXT_STEP_HINTS.GENERATE_STORYBOARD;
            else if (/视频/.test(label)) hint = NEXT_STEP_HINTS.GENERATE_SHOT_VIDEOS;
            else if (/配音|音频/.test(label)) hint = NEXT_STEP_HINTS.GENERATE_AUDIO;
            else if (/合成|成片/.test(label)) hint = NEXT_STEP_HINTS.MERGE_FINAL;
          }
          if (hint) {
            // 如果伴有局部失败，错误信息也带出来。
            const errSuffix = task.error ? `\n\n⚠️ 但有部分失败：${task.error}` : '';
            pushAssistantMessage(hint + errSuffix);
          }
        } else if (attempts >= maxAttempts) {
          clearInterval(iv);
          const detail = `${label}轮询超时：已经等待 ${Math.round(attempts * intervalMs / 1000)} 秒，最后状态：${task.current_step || task.status || '未知'}。`;
          setDirectorStatus({ phase: 'error', label: `${label}卡住了`, detail, progress: 100 });
          pushAssistantMessage(`⚠️ ${detail}`);
        } else if (task.status && task.status !== 'unknown') {
          setDirectorStatus(prev => ({
            ...(prev || {}),
            phase: 'running',
            label: task.current_step || label,
            detail: task.error || task.current_step || '后台任务运行中...',
            progress: typeof task.progress === 'number' ? task.progress : (prev?.progress || 60)
          }));
        }
      } catch (err) {
        if (attempts >= maxAttempts) {
          clearInterval(iv);
          const detail = getErrorMessage(err);
          setDirectorStatus({ phase: 'error', label: `${label}状态查询失败`, detail, progress: 100 });
          pushAssistantMessage(`❌ ${label}状态查询失败：${detail}`);
        }
      }
    }, intervalMs);
    return iv;
  };

  const startProjectPolling = (id) => {
    if (!id || projectPollingRef.current) return;
    setDirectorStatus({
      phase: 'running',
      label: '剧本正在后台生成',
      detail: '项目已创建，可以先进入工作台；完整剧本生成后会自动刷新。',
      progress: 20
    });
    let attempts = 0;
    projectPollingRef.current = setInterval(async () => {
      attempts += 1;
      const data = await fetchWorkspaceData(id);
      if (!data || data.status !== 'processing' || attempts >= 120) {
        clearInterval(projectPollingRef.current);
        projectPollingRef.current = null;
        setDirectorStatus(prev => data?.status === 'draft' ? {
          ...(prev || {}),
          phase: 'done',
          label: '剧本已生成',
          detail: '工作台已刷新，可以继续生成分镜图、角色图或视频。',
          progress: 100
        } : prev);
      }
    }, 3000);
  };

  const handleVoiceSelect = async (characterId, voiceId) => {
    if (!workspaceData?.script_id) return;
    try {
      const res = await axios.put(`${API_BASE}/api/scripts/${workspaceData.script_id}/characters/${characterId}/voice`, {
        voice_id: voiceId
      });
      if (res.data.status === 'success') fetchWorkspaceData(workspaceData.script_id);
    } catch (err) {
      console.error('声音设置失败:', err);
    }
  };

  /**
   * 触发一次后端任务并维护 directorStatus。
   *
   * @param label  顶部状态卡显示的中文标签
   * @param request  (scriptId) => Promise，调用具体后端 API
   * @param tab  请求成功后切到哪个 tab
   * @param options.poll  是否在请求成功后启动 /status 轮询；
   *   - 后台异步任务（batch-generate、merge、regenerate-video 等）传 true（默认）
   *   - 同步端点（单镜头分镜重做、单角色图、单确认/审片）必须传 false，
   *     否则会去轮询全局 task_store，吃到上次批量任务残留的 processing 状态，
   *     导致 "图片其实已经出来了，但日志卡在执行中"。
   * @param options.intervalMs / options.maxAttempts  轮询参数
   */
  const runProjectTask = async (label, request, tab = 'storyboard', options = {}) => {
    const { poll = true, intervalMs = 3000, maxAttempts = 80 } = options;
    const id = workspaceData?.script_id || scriptId;
    if (!id) return;
    setDirectorStatus({
      phase: 'queued',
      label,
      detail: '任务已提交，工作台会自动刷新。',
      progress: 55
    });
    try {
      await request(id);
      setActiveTab(tab);
      if (poll) {
        pollTaskStatus(id, label, { intervalMs, maxAttempts });
      } else {
        // 同步任务：请求 await 完成 = 任务完成，直接刷工作台并落 done。
        await fetchWorkspaceData(id);
        setDirectorStatus(prev => ({
          ...(prev || {}),
          phase: 'done',
          label: `${label} 已完成`,
          detail: '工作台已刷新。',
          progress: 100
        }));
      }
    } catch (err) {
      console.error(label, err);
      const detail = getErrorMessage(err);
      setDirectorStatus({
        phase: 'error',
        label: '任务提交失败',
        detail,
        progress: 100
      });
      pushAssistantMessage(`❌ ${label}提交失败：${detail}`);
    }
  };

  // ---------- 后台异步任务（需要 polling） ----------
  const generateStoryboards = () => runProjectTask(
    '正在生成分镜图',
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/storyboard/batch-generate`, {})
  );

  const generateConfirmedVideos = () => runProjectTask(
    '正在生成已确认镜头视频',
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/shots/batch-generate`),
    'storyboard',
    { intervalMs: 5000 }
  );

  const mergeFinalVideo = () => runProjectTask(
    '正在合成最终成片',
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/merge`)
  );

  const regenerateVideo = (shotId) => runProjectTask(
    `正在重生成视频：${shotId}`,
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/shots/${shotId}/regenerate-video`),
    'storyboard',
    { intervalMs: 5000 }
  );

  // ---------- 同步端点：请求返回即完成，不要去轮询全局 status ----------
  const autoReviewAll = () => runProjectTask(
    '正在自动审片',
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/shots/auto-review`, { use_model: true }),
    'storyboard',
    { poll: false }
  );

  const regenerateStoryboard = (shotId) => runProjectTask(
    `正在重生成分镜图：${shotId}`,
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/shots/${shotId}/storyboard`, { force: true }),
    'storyboard',
    { poll: false }
  );

  const confirmStoryboard = (shotId, confirmed = true) => runProjectTask(
    confirmed ? `已确认分镜：${shotId}` : `已取消确认：${shotId}`,
    (id) => axios.put(`${API_BASE}/api/scripts/${id}/shots/${shotId}/confirm`, { confirmed }),
    'storyboard',
    { poll: false }
  );

  const reviewShot = (shotId, status, notes = '') => runProjectTask(
    `审片状态已更新：${shotId}`,
    (id) => axios.put(`${API_BASE}/api/scripts/${id}/shots/${shotId}/review`, { status, notes }),
    'storyboard',
    { poll: false }
  );

  const autoReviewShot = (shotId) => runProjectTask(
    `正在自动审片：${shotId}`,
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/shots/${shotId}/auto-review`, { use_model: true }),
    'storyboard',
    { poll: false }
  );

  // 重做单个角色参考图。后端 character_generator.generate_character() 不检查旧 image_url，
  // 直接覆盖写回 script.json，所以这里调单角色端点即等价于 force redo（同步）。
  const regenerateCharacterImage = (characterRef) => runProjectTask(
    `正在重生成角色图：${characterRef}`,
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/characters/${encodeURIComponent(characterRef)}/generate-image`),
    'characters',
    { poll: false }
  );

  // 上传角色参考图：上传成功后会自动刷新 workspaceData，下次"重做"会走图生图。
  const uploadCharacterReference = async (characterRef, file) => {
    const id = workspaceData?.script_id || scriptId;
    if (!id || !file) return;
    setDirectorStatus({
      phase: 'queued',
      label: `正在上传 ${characterRef} 的参考图`,
      detail: file.name,
      progress: 30
    });
    try {
      const formData = new FormData();
      formData.append('file', file);
      await axios.post(
        `${API_BASE}/api/scripts/${id}/characters/${encodeURIComponent(characterRef)}/reference-image`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      );
      await fetchWorkspaceData(id);
      setDirectorStatus({
        phase: 'done',
        label: `${characterRef} 参考图已上传`,
        detail: '下次"重做"会以这张参考图为蓝本生成。',
        progress: 100
      });
      pushAssistantMessage(
        `📷 已上传「${characterRef}」的参考图，下次生成会以它为蓝本（图生图）。点角色卡上的「重做」立刻生效。`
      );
    } catch (err) {
      const detail = getErrorMessage(err);
      setDirectorStatus({ phase: 'error', label: '参考图上传失败', detail, progress: 100 });
      pushAssistantMessage(`❌ 参考图上传失败：${detail}`);
    }
  };

  // 清掉角色所有参考图，回到纯文生图。
  const clearCharacterReferences = async (characterRef) => {
    const id = workspaceData?.script_id || scriptId;
    if (!id) return;
    try {
      await axios.delete(
        `${API_BASE}/api/scripts/${id}/characters/${encodeURIComponent(characterRef)}/reference-image`
      );
      await fetchWorkspaceData(id);
      pushAssistantMessage(`🗑️ 已清除「${characterRef}」的参考图，下次生成会回到纯文生图。`);
    } catch (err) {
      pushAssistantMessage(`❌ 清除参考图失败：${getErrorMessage(err)}`);
    }
  };

  // ---------- 场景图：与角色图同样的同步/异步双入口 ----------
  // 批量生成所有场景图（后台任务，前端轮询 status；用于"全部生成"按钮）。
  const batchGenerateLocations = () => runProjectTask(
    '正在生成场景图',
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/locations/batch-generate`, { force: false }),
    'characters',
    { intervalMs: 4000 }
  );

  // 单场景重做：直接调单场景端点，同步等待完成（与重做角色同模式）。
  const regenerateLocationImage = (sceneRef) => runProjectTask(
    `正在重生成场景图：${sceneRef}`,
    (id) => axios.post(`${API_BASE}/api/scripts/${id}/locations/${encodeURIComponent(sceneRef)}/generate-image`),
    'characters',
    { poll: false }
  );

  const uploadLocationReference = async (sceneRef, file) => {
    const id = workspaceData?.script_id || scriptId;
    if (!id || !file) return;
    setDirectorStatus({
      phase: 'queued',
      label: `正在上传场景参考图`,
      detail: file.name,
      progress: 30
    });
    try {
      const formData = new FormData();
      formData.append('file', file);
      await axios.post(
        `${API_BASE}/api/scripts/${id}/locations/${encodeURIComponent(sceneRef)}/reference-image`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      );
      await fetchWorkspaceData(id);
      setDirectorStatus({
        phase: 'done',
        label: `场景参考图已上传`,
        detail: '下次"重做"会以这张参考图为蓝本生成。',
        progress: 100
      });
      pushAssistantMessage(
        `📷 已上传场景参考图，下次生成会以它为蓝本（图生图）。点该场景卡上的「重做」立刻生效。`
      );
    } catch (err) {
      const detail = getErrorMessage(err);
      setDirectorStatus({ phase: 'error', label: '场景参考图上传失败', detail, progress: 100 });
      pushAssistantMessage(`❌ 场景参考图上传失败：${detail}`);
    }
  };

  const clearLocationReferences = async (sceneRef) => {
    const id = workspaceData?.script_id || scriptId;
    if (!id) return;
    try {
      await axios.delete(
        `${API_BASE}/api/scripts/${id}/locations/${encodeURIComponent(sceneRef)}/reference-image`
      );
      await fetchWorkspaceData(id);
      pushAssistantMessage(`🗑️ 已清除场景参考图，下次生成会回到纯文生图。`);
    } catch (err) {
      pushAssistantMessage(`❌ 清除场景参考图失败：${getErrorMessage(err)}`);
    }
  };

  const MINIMAX_VOICES = [
    { id: 'male-qn-qingse', name: '清涩男声', icon: '👦🏻' },
    { id: 'female-tianmei', name: '甜美女声', icon: '👧🏻' },
    { id: 'male-qn-badao', name: '霸道总裁', icon: '👨🏻‍💼' },
    { id: 'female-yujie', name: '御姐女声', icon: '👩🏻‍💼' },
    { id: 'presenter_male', name: '男解说', icon: '🎙️' },
    { id: 'presenter_female', name: '女解说', icon: '🎤' },
  ];

  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!input.trim()) return;

    const userMessage = { role: 'user', text: input };
    setMessages(prev => [...prev, userMessage, { role: 'assistant', text: '' }]);
    setDirectorStatus({
      phase: 'starting',
      label: '准备接收指令',
      detail: '正在把你的需求、当前项目状态和工作区上下文发给 Director Agent。',
      progress: 5
    });
    setDirectorTimeline([
      {
        phase: 'starting',
        label: '准备接收指令',
        detail: '正在发送需求和工作区上下文',
        progress: 5,
        time: new Date().toLocaleTimeString()
      }
    ]);
    setInput('');
    setIsTyping(true);

    try {
      const currentScriptId = workspaceData?.script_id || scriptId;
      const payload = {
        script_id: currentScriptId,
        message: userMessage.text,
        history: messages.slice(-5),
        project_config: projectConfig || workspaceData?.project_config || {},
        current_tab: activeTab,
        workspace_state: workspaceData || {}
      };

      const res = await fetch(`${API_BASE}/v1/agent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = body.detail || body.error || JSON.stringify(body);
        } catch (_) {
          try { detail = await res.text(); } catch (__) {}
        }
        throw new Error(detail);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let done = false;
      let sseBuffer = '';
      let latestScriptId = currentScriptId;

      const startPolling = (id, intervalMs = 3000, maxAttempts = 40, label = '后台任务') => {
        pollTaskStatus(id, label, { intervalMs, maxAttempts });
      };

      const handleAgentEvent = async (data) => {
        if (data.type === 'chat') {
          setMessages(prev => {
            const msgs = [...prev];
            msgs[msgs.length - 1].text += data.content;
            return msgs;
          });
        } else if (data.type === 'status') {
          const nextStatus = {
            phase: data.phase || 'running',
            label: data.label || '处理中',
            detail: data.detail || '',
            progress: typeof data.progress === 'number' ? data.progress : 50,
            action: data.action
          };
          setDirectorStatus(nextStatus);
          setDirectorTimeline(prev => {
            const last = prev[prev.length - 1];
            const sameStep = last && last.phase === nextStatus.phase && last.label === nextStatus.label;
            const item = {
              ...nextStatus,
              time: new Date().toLocaleTimeString()
            };
            if (sameStep) {
              return [...prev.slice(0, -1), item];
            }
            return [...prev, item].slice(-8);
          });
        } else if (data.type === 'clear_chat') {
          setMessages(prev => {
            const msgs = [...prev];
            msgs[msgs.length - 1].text = '';
            return msgs;
          });
        } else if (data.type === 'decision') {
          if (data.ui_patch?.active_tab) setActiveTab(data.ui_patch.active_tab);
        } else if (data.type === 'workspace_update') {
          if (data.script_id) {
            latestScriptId = data.script_id;
            if (!scriptId && onProjectCreated) onProjectCreated(data.script_id);
            // 关键：不要 await fetchWorkspaceData，否则后端慢的时候会把后续 SSE 事件
            // （比如 progress=100 的 status done）堵在事件队列里，用户会看到进度卡在 78~82%。
            // 改为 fire-and-forget；fetchWorkspaceData 内部会自己 setWorkspaceData。
            fetchWorkspaceData(data.script_id).catch(err => console.error('workspace_update fetch failed:', err));
          }
          if (data.tab) setActiveTab(data.tab);
        } else if (data.type === 'action') {
          if (data.script_id) latestScriptId = data.script_id;
          if (data.status === 'pending_background') {
            setDirectorStatus({
              phase: 'queued',
              label: `${data.action} 已进入后台队列`,
              detail: '这个任务会持续一段时间，我会自动刷新左侧工作台。',
              progress: 60,
              action: data.action
            });
          } else if (data.status === 'completed') {
            setDirectorStatus(prev => ({
              ...(prev || {}),
              phase: 'done',
              label: `${data.action} 已完成`,
              detail: '左侧工作区已经刷新。',
              progress: 100,
              action: data.action
            }));
          }
          const actionTabs = {
            CREATE_SCRIPT: 'script',
            REWRITE_SCRIPT: 'script',
            GENERATE_CHARACTERS: 'characters',
            GENERATE_STORYBOARD: 'storyboard',
            GENERATE_SHOT_VIDEOS: 'storyboard',
            GENERATE_AUDIO: 'storyboard',
            MERGE_FINAL: 'storyboard'
          };
          if (actionTabs[data.action]) setActiveTab(actionTabs[data.action]);
          if (data.poll) startPolling(data.script_id || latestScriptId, data.action === 'GENERATE_SHOT_VIDEOS' ? 5000 : 3000, 80, data.action || '后台任务');
          if (data.status === 'completed' && (data.script_id || latestScriptId)) fetchWorkspaceData(data.script_id || latestScriptId);
        }
      };

      while (!done) {
        const { value, done: readerDone } = await reader.read();
        done = readerDone;
        if (!value) continue;
        sseBuffer += decoder.decode(value, { stream: true });
        const events = sseBuffer.split('\n\n');
        sseBuffer = events.pop() || '';

        for (const rawEvent of events) {
          const dataLines = rawEvent
            .split('\n')
            .filter(line => line.startsWith('data: '))
            .map(line => line.slice(6));
          if (!dataLines.length) continue;
          try {
            const data = JSON.parse(dataLines.join('\n'));
            await handleAgentEvent(data);
          } catch(err) {
            console.error('Parse SSE event error:', err, rawEvent);
          }
        }
      }

      if (sseBuffer.trim()) {
        const line = sseBuffer.split('\n').find(l => l.startsWith('data: '));
        if (line) {
          try { await handleAgentEvent(JSON.parse(line.slice(6))); }
          catch(err) { console.error('Parse final SSE event error:', err, sseBuffer); }
        }
      }
    } catch (error) {
      setMessages(prev => {
        const msgs = [...prev];
        msgs[msgs.length - 1].text += `\n网络错误: ${error.message}`;
        return msgs;
      });
    } finally {
      setIsTyping(false);
    }
  };

  // Tab definitions
  const TABS = [
    { id: 'canvas',     label: '画布',    icon: '⬡' },
    { id: 'script',     label: '剧本',    icon: '📄' },
    { id: 'characters', label: '角色',    icon: '👤' },
    { id: 'storyboard', label: '分镜',    icon: '🎬' },
  ];

  const renderDialogueText = (dialogue) => {
    if (!dialogue) return '';
    if (typeof dialogue === 'string') return dialogue;
    const speaker = dialogue.character_id || dialogue.character || dialogue.name || '';
    const text = dialogue.text || dialogue.tts_text || '';
    return speaker && text ? `${speaker}：${text}` : text;
  };

  const findCharacter = (characterId) =>
    workspaceData?.characters?.find(c => c.character_id === characterId || c.name === characterId);

  const shotStatusLabel = (shot) => {
    if (shot.status === 'review_approved') return '已通过';
    if (shot.status === 'needs_regen') return '需重做';
    if (shot.status === 'video_ready') return '视频完成';
    if (shot.status === 'storyboard_approved') return '分镜已确认';
    if (shot.status === 'storyboard_ready') return '待确认';
    if (shot.status === 'video_failed' || shot.status === 'storyboard_failed') return '失败';
    return '待分镜';
  };

  return (
    <div className="agent-container">
      {/* Project setup modal intercept */}
      {!scriptId && !projectConfig && (
        <ProjectSetupModal
          onConfirm={(config) => {
            setProjectConfig(config);
            setMessages(prev => [
              ...prev,
              { role: 'assistant', text: `配置已确认 (引擎 ${config.video_engine.toUpperCase()}，画幅 ${config.aspect_ratio}，风格 ${config.style})。现在告诉我你想写个什么故事吧？` }
            ]);
          }}
        />
      )}

      {/* ===== Top Navbar ===== */}
      <header className="zopia-navbar">
        <div className="navbar-left">
          <div className="navbar-logo">Z</div>
          <div className="navbar-breadcrumb">
            <span>我的项目</span>
            <span className="sep">/</span>
            <span className="active-crumb">
              {workspaceData?.title || 'AI 短剧导演'}
            </span>
            <span className="dropdown-arrow">▾</span>
          </div>
        </div>

        <nav className="navbar-center">
          {TABS.map(tab => (
            <button
              key={tab.id}
              className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <span className="tab-icon">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="navbar-right">
          <button className="navbar-back-btn" onClick={onBack}>← 返回</button>
          <button className="navbar-icon-btn" title="设置">⚙</button>
          <div className="user-avatar">Z</div>
        </div>
      </header>

      {/* ===== Body ===== */}
      <div className="zopia-body">
        {/* Main workspace */}
        <main className="workspace-main">
          {directorStatus && directorStatus.phase !== 'done' && (
            <div className="workspace-status-strip">
              <span className="workspace-status-dot" />
              <span>{directorStatus.label}</span>
              <small>{directorStatus.detail}</small>
            </div>
          )}
          <div className="panel">
              {/* Canvas */}
              {activeTab === 'canvas' && (
                !workspaceData ? (
                  <div className="empty-workspace">
                    <span style={{ fontSize: 40 }}>🎬</span>
                    <h3>工作台</h3>
                    <p>告诉右侧的 Director Agent 你的构思，创作马上开始...</p>
                  </div>
                ) : (
                  <div className="canvas-panel">
                    <div className="canvas-panel-content">
                      <span style={{ fontSize: 48, display: 'block', marginBottom: 20 }}>🔀</span>
                      <h3>工作流画布</h3>
                      <p>在这里通过节点视图管理你的短剧生成流水线...</p>
                    </div>
                  </div>
                )
              )}

              {/* Script */}
              {activeTab === 'script' && (
                !workspaceData ? (
                  <div className="empty-workspace">
                    <span style={{ fontSize: 40 }}>📄</span>
                    <h3>剧本</h3>
                    <p>还没有剧本，告诉 Director Agent 你的故事创意吧...</p>
                  </div>
                ) :
                <div className="script-panel">
                  <h2>{workspaceData.title}</h2>
                  <div className="meta-info">
                    <span className="badge">{workspaceData.genre || workspaceData.theme || '未定题材'}</span>
                    <span className="badge">{workspaceData.style || '默认风格'}</span>
                    {workspaceData.total_duration && <span className="badge">约 {workspaceData.total_duration}s</span>}
                  </div>
                  {workspaceData.generation_warning && (
                    <div className="script-warning-banner">
                      ⚠️ {workspaceData.generation_warning}
                    </div>
                  )}
                  <div className="synopsis">
                    <h3>故事梗概</h3>
                    <p>{workspaceData.synopsis}</p>
                  </div>
                  {(workspaceData.screenplay_text || workspaceData.full_script) && (
                    <div className="screenplay-block">
                      <h3>完整剧本</h3>
                      <pre>{workspaceData.screenplay_text || workspaceData.full_script}</pre>
                    </div>
                  )}
                  <div className="scenes-list">
                    <h3>分场剧本</h3>
                    {workspaceData.scenes?.map((scene, idx) => (
                      <div key={idx} className="scene-card screenplay-scene-card">
                        <h4>{scene.scene_number || `第${idx + 1}场`}：{scene.location || '未指定地点'} / {scene.time_of_day || '时间未定'}</h4>
                        {scene.scene_summary && <p className="scene-summary">{scene.scene_summary}</p>}
                        {scene.script_text && <pre className="scene-script-text">{scene.script_text}</pre>}
                        <div className="scene-shot-script-list">
                          {scene.shots?.map((shot, shotIdx) => {
                            const dialogueText = renderDialogueText(shot.dialogue);
                            return (
                              <div key={shot.shot_id || shotIdx} className="scene-shot-script-item">
                                <div className="scene-shot-header">
                                  镜头 {shot.shot_number || shotIdx + 1}
                                  {shot.time_code && <> · <span className="shot-time-code">{shot.time_code}</span></>}
                                  {' '}· {shot.shot_type || '镜头'} · {shot.duration || 0}s
                                </div>
                                {shot.shot_text ? (
                                  <p className="scene-shot-text">{shot.shot_text}</p>
                                ) : (
                                  <>
                                    <p className="scene-shot-desc">动作：{shot.content_description || '暂无画面描述'}</p>
                                    {dialogueText && <p className="scene-shot-dialogue">对白：{dialogueText}</p>}
                                  </>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Characters */}
              {activeTab === 'characters' && (
                !workspaceData ? (
                  <div className="empty-workspace">
                    <span style={{ fontSize: 40 }}>👤</span>
                    <h3>角色</h3>
                    <p>还没有角色，先生成剧本后再来这里设计角色...</p>
                  </div>
                ) :
                <div className="characters-panel">
                  <div className="characters-section-head">
                    <h3>出场角色</h3>
                    <span className="characters-section-hint">{workspaceData.characters?.length || 0} 个角色</span>
                  </div>
                  <div className="character-grid">
                    {workspaceData.characters?.map((char, idx) => (
                      <div key={idx} className="character-card">
                        <div className="char-image">
                          <div className="char-status-dot" />
                          {char.appearance?.image_url ? (
                            <img
                              src={char.appearance.image_url}
                              alt={char.name}
                              className="clickable-img"
                              title="点击查看大图"
                              onClick={() => openLightbox(char.appearance.image_url, char.name)}
                            />
                          ) : (
                            <div className="placeholder-img">暂无图像</div>
                          )}
                        </div>
                        <div className="char-info">
                          <h4>
                            {char.name}
                            {(char.gender || char.age_range) && (
                              <span className="char-meta-tag">
                                {[char.gender === 'female' ? '女' : char.gender === 'male' ? '男' : char.gender, char.age_range].filter(Boolean).join(' · ')}
                              </span>
                            )}
                          </h4>
                          {char.personality && <p className="desc">{char.personality}</p>}
                          {char.background && (
                            <p className="char-bg" title={char.background}>{char.background}</p>
                          )}

                          {/* 外貌 / 造型细节，给用户确认是否要重做用 */}
                          {(char.appearance?.face || char.appearance?.hair || char.appearance?.body || char.appearance?.dress_style || char.appearance?.distinguishing_features) && (
                            <ul className="char-appearance">
                              {char.appearance?.face && <li><span>面部</span>{char.appearance.face}</li>}
                              {char.appearance?.hair && <li><span>发型</span>{char.appearance.hair}</li>}
                              {char.appearance?.body && <li><span>身形</span>{char.appearance.body}</li>}
                              {char.appearance?.dress_style && <li><span>穿着</span>{char.appearance.dress_style}</li>}
                              {char.appearance?.distinguishing_features && <li><span>特征</span>{char.appearance.distinguishing_features}</li>}
                            </ul>
                          )}

                          {/* Voice row */}
                          <div className="voice-row">
                            <button className="voice-play-btn" title="试听">▶</button>
                            <span className="voice-name">
                              {MINIMAX_VOICES.find(v => v.id === char.voice_id)?.name || '未选音色'}
                            </span>
                            <button className="voice-switch-btn">切换</button>
                          </div>

                          {/* Voice options */}
                          <div className="voice-options" style={{ marginTop: 6 }}>
                            {MINIMAX_VOICES.map(voice => (
                              <button
                                key={voice.id}
                                className={`voice-btn ${char.voice_id === voice.id ? 'active' : ''}`}
                                onClick={() => handleVoiceSelect(char.character_id || char.name, voice.id)}
                                title={voice.name}
                              >
                                {voice.icon}
                              </button>
                            ))}
                          </div>

                          {/* 用户上传的参考图：上传后下次重做会走图生图 */}
                          {(char.reference_image_urls?.length > 0) && (
                            <div className="char-refs">
                              <div className="char-refs-header">
                                <span>📷 参考图（{char.reference_image_urls.length}）</span>
                                <button
                                  className="char-refs-clear"
                                  onClick={() => clearCharacterReferences(char.character_id || char.name)}
                                  title="清空所有参考图，回到纯文生图"
                                >清空</button>
                              </div>
                              <div className="char-refs-list">
                                {char.reference_image_urls.map((refUrl, ri) => (
                                  <img
                                    key={refUrl + ri}
                                    src={refUrl}
                                    alt="参考图"
                                    className={`char-ref-thumb clickable-img ${ri === 0 ? 'is-active' : ''}`}
                                    title={ri === 0 ? '当前生效（最近上传）· 点击查看大图' : '点击查看大图'}
                                    onClick={() => openLightbox(refUrl, `${char.name} 的参考图`)}
                                  />
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Action row */}
                          <div className="char-actions">
                            <label
                              className="char-action-btn"
                              title="上传一张参考图，下次生成会走图生图"
                            >
                              <span className="action-icon">⬆</span>上传参考图
                              <input
                                type="file"
                                accept="image/png,image/jpeg,image/jpg,image/webp"
                                style={{ display: 'none' }}
                                onChange={(e) => {
                                  const f = e.target.files?.[0];
                                  if (f) uploadCharacterReference(char.character_id || char.name, f);
                                  e.target.value = '';
                                }}
                              />
                            </label>
                            <button className="char-action-btn">
                              <span className="action-icon">✏</span>编辑
                            </button>
                            <button
                              className="char-action-btn"
                              onClick={() => regenerateCharacterImage(char.character_id || char.name)}
                              title={char.reference_image_urls?.length ? '以最新一张参考图为蓝本重做（图生图）' : '按文字描述重新生成（文生图）'}
                            >
                              <span className="action-icon">↺</span>重做
                              {char.reference_image_urls?.length > 0 && <span className="char-action-flag">(图生图)</span>}
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* ==== 场景区：跟角色同 Tab，提供 @场景 的视觉锚点 ==== */}
                  <div className="locations-section">
                    <div className="characters-section-head">
                      <h3>剧本场景</h3>
                      <span className="characters-section-hint">
                        {workspaceData.scenes?.length || 0} 个场景
                      </span>
                      <button
                        className="locations-batch-btn"
                        onClick={batchGenerateLocations}
                        title="一次性为所有场景生成场景图"
                      >
                        全部生成场景图
                      </button>
                    </div>
                    <p className="locations-section-desc">
                      场景图是分镜里 <code>[@场景名]</code> 的实际视觉参考。
                      每个场景对应剧本里的一个地点（白天/夜晚），不会出现人物。
                    </p>

                    <div className="location-grid">
                      {workspaceData.scenes?.map((scene, sIdx) => {
                        const sceneRef = scene.scene_id || scene.location_id || `scene_${sIdx + 1}`;
                        const refs = scene.reference_image_urls || [];
                        return (
                          <div key={sceneRef} className="location-card">
                            <div className="loc-image">
                              {scene.location_image_url ? (
                                <img
                                  src={scene.location_image_url}
                                  alt={scene.location}
                                  className="clickable-img"
                                  title="点击查看大图"
                                  onClick={() => openLightbox(
                                    scene.location_image_url,
                                    `${scene.scene_number || ''} · ${scene.location || ''}`
                                  )}
                                />
                              ) : (
                                <div className="placeholder-img">暂无场景图</div>
                              )}
                            </div>
                            <div className="loc-info">
                              <h4>
                                {scene.location || sceneRef}
                                <span className="char-meta-tag">
                                  {[scene.scene_number, scene.time_of_day].filter(Boolean).join(' · ')}
                                </span>
                              </h4>
                              {scene.scene_summary && (
                                <p className="desc" title={scene.scene_summary}>{scene.scene_summary}</p>
                              )}

                              {refs.length > 0 && (
                                <div className="char-refs">
                                  <div className="char-refs-header">
                                    <span>📷 参考图（{refs.length}）</span>
                                    <button
                                      className="char-refs-clear"
                                      onClick={() => clearLocationReferences(sceneRef)}
                                      title="清空所有参考图，回到纯文生图"
                                    >清空</button>
                                  </div>
                                  <div className="char-refs-list">
                                    {refs.map((url, ri) => (
                                      <img
                                        key={url + ri}
                                        src={url}
                                        alt="场景参考图"
                                        className={`char-ref-thumb clickable-img ${ri === 0 ? 'is-active' : ''}`}
                                        title={ri === 0 ? '当前生效（最近上传）· 点击查看大图' : '点击查看大图'}
                                        onClick={() => openLightbox(url, `${scene.location} 的参考图`)}
                                      />
                                    ))}
                                  </div>
                                </div>
                              )}

                              <div className="char-actions">
                                <label
                                  className="char-action-btn"
                                  title="上传场景参考图，下次生成会走图生图"
                                >
                                  <span className="action-icon">⬆</span>上传参考图
                                  <input
                                    type="file"
                                    accept="image/png,image/jpeg,image/jpg,image/webp"
                                    style={{ display: 'none' }}
                                    onChange={(e) => {
                                      const f = e.target.files?.[0];
                                      if (f) uploadLocationReference(sceneRef, f);
                                      e.target.value = '';
                                    }}
                                  />
                                </label>
                                <button
                                  className="char-action-btn"
                                  onClick={() => regenerateLocationImage(sceneRef)}
                                  title={refs.length ? '以最新一张参考图为蓝本重做（图生图）' : '按场景描述重新生成（文生图）'}
                                >
                                  <span className="action-icon">↺</span>重做
                                  {refs.length > 0 && <span className="char-action-flag">(图生图)</span>}
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}

              {/* Storyboard */}
              {activeTab === 'storyboard' && (
                !workspaceData ? (
                  <div className="empty-workspace">
                    <span style={{ fontSize: 40 }}>🎬</span>
                    <h3>分镜</h3>
                    <p>还没有分镜，先完成剧本和角色设计...</p>
                  </div>
                ) :
                <div className="storyboard-panel">
                  {/* 工具栏 */}
                  <div className="sb-toolbar">
                    <span className="sb-tool-pill">画幅 {workspaceData.project_config?.aspect_ratio || '9:16'}</span>
                    <span className="sb-tool-pill">引擎 {workspaceData.project_config?.video_engine || 'Kling'}</span>
                    <button className="sb-tool-pill" onClick={generateStoryboards}>生成分镜图</button>
                    <button className="sb-tool-pill" onClick={generateConfirmedVideos}>生成已确认视频</button>
                    <button className="sb-tool-pill" onClick={autoReviewAll}>自动审片</button>
                    <button className="sb-tool-pill" onClick={mergeFinalVideo}>合成成片</button>
                    <span className="sb-count">共 {workspaceData.scenes?.reduce((acc, s) => acc + (s.shots?.length || 0), 0)} 个镜头</span>
                  </div>
                  {(workspaceData.final_video_url || workspaceData.timeline?.final_video_url) && (
                    <div className="final-video-card">
                      <div>
                        <strong>最终成片已生成</strong>
                        <p>{workspaceData.final_video_url || workspaceData.timeline?.final_video_url}</p>
                      </div>
                      <video src={workspaceData.final_video_url || workspaceData.timeline?.final_video_url} controls />
                    </div>
                  )}
                  {/* 表头 */}
                  <div className="sb-table-head">
                    <div className="sb-col-num">#</div>
                    <div className="sb-col-desc">描述</div>
                    <div className="sb-col-entity">角色</div>
                    <div className="sb-col-images">图片</div>
                    <div className="sb-col-video">视频</div>
                  </div>
                  {/* 分镜行 */}
                  {workspaceData.scenes?.map((scene, sIdx) =>
                    scene.shots?.map((shot, shotIdx) => {
                      const globalIdx = workspaceData.scenes.slice(0, sIdx).reduce((a, s) => a + (s.shots?.length || 0), 0) + shotIdx + 1;
                      return (
                        <div key={shot.shot_id || `${sIdx}-${shotIdx}`} className="sb-row">
                          <div className="sb-col-num">
                            <span className="sb-num">{globalIdx}</span>
                          </div>
                          <div className="sb-col-desc">
                            <span className="sb-dur-tag">{shot.time_code || `${shot.duration}s`}</span>
                            {shot.shot_text ? (
                              <p className="sb-desc-text">{shot.shot_text}</p>
                            ) : (
                              <>
                                <p className="sb-desc-text">{shot.content_description}</p>
                                {shot.dialogue?.text && (
                                  <p className="sb-dialogue">
                                    {shot.dialogue.character_id && <span className="sb-char-tag">{shot.dialogue.character_id}</span>}
                                    说："{shot.dialogue.text}"
                                  </p>
                                )}
                              </>
                            )}
                            <div className="sb-row-actions">
                              <span className={`shot-status-pill status-${shot.status || 'pending'}`}>{shotStatusLabel(shot)}</span>
                              {shot.auto_review?.score !== undefined && (
                                <span className="shot-score">审片 {shot.auto_review.score} 分</span>
                              )}
                              {shot.asset_refs?.location && (
                                <span className="shot-asset-ref">场景 {shot.asset_refs.location}</span>
                              )}
                              {shot.review_notes && <span className="shot-review-note">{shot.review_notes}</span>}
                            </div>
                          </div>
                          <div className="sb-col-entity">
                            {(shot.character_ids?.length ? shot.character_ids : workspaceData.characters?.slice(0, 2).map(c => c.character_id || c.name) || []).map((charId, ci) => {
                              const char = findCharacter(charId);
                              return (
                                <div key={charId || ci} className="sb-entity-avatar" title={char?.name || charId}>
                                  {char?.appearance?.image_url
                                    ? <img src={char.appearance.image_url} alt={char.name} />
                                    : <span>{(char?.name || charId || '?')?.[0]}</span>}
                                </div>
                              );
                            })}
                            <div className="sb-entity-add">+</div>
                          </div>
                          <div className="sb-col-images">
                            <div className="sb-img-grid">
                              {shot.storyboard_image_url || shot.reference_image_url
                                ? <img
                                    src={shot.storyboard_image_url || shot.reference_image_url}
                                    alt="storyboard"
                                    className="sb-img-thumb clickable-img"
                                    title="点击查看大图"
                                    onClick={() => openLightbox(
                                      shot.storyboard_image_url || shot.reference_image_url,
                                      `分镜 ${shot.shot_id || ''} · ${shot.shot_type || ''}`
                                    )}
                                  />
                                : <div className="sb-img-empty">暂无</div>}
                            </div>
                            <div className="sb-row-actions">
                              <button className="sb-action-btn" onClick={() => regenerateStoryboard(shot.shot_id)}>生成/重做分镜</button>
                              {(shot.storyboard_image_url || shot.reference_image_url) && (
                                <button className="sb-action-btn" onClick={() => confirmStoryboard(shot.shot_id, !shot.storyboard_confirmed)}>
                                  {shot.storyboard_confirmed ? '取消确认' : '确认分镜'}
                                </button>
                              )}
                            </div>
                          </div>
                          <div className="sb-col-video">
                            <div className="sb-video-box">
                              {shot.generated_video_url || shot.video_url ? (
                                <video src={shot.generated_video_url || shot.video_url} controls />
                              ) : (
                                <div className="sb-video-empty">
                                  <span>▶</span>
                                  <p>待生成</p>
                                </div>
                              )}
                              <div className="sb-status-dot" />
                            </div>
                            <div className="sb-row-actions">
                              <button className="sb-action-btn" onClick={() => regenerateVideo(shot.shot_id)}>重生成视频</button>
                              {(shot.generated_video_url || shot.video_url) && (
                                <>
                                  <button className="sb-action-btn" onClick={() => autoReviewShot(shot.shot_id)}>自动审片</button>
                                  <button className="sb-action-btn" onClick={() => reviewShot(shot.shot_id, 'approved')}>通过</button>
                                  <button className="sb-action-btn" onClick={() => reviewShot(shot.shot_id, 'needs_regen', '需要重生成')}>需重做</button>
                                </>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>
        </main>

        {/* ===== Right Chat Panel ===== */}
        <aside className="chat-panel">
          <div className="chat-panel-header">
            <span className="layout-icon">⊟</span>
            <div className="chat-agent-dropdown">
              <span>Director Agent</span>
              <span className="arrow">▾</span>
            </div>
            <button className="new-chat-btn">+ New Chat</button>
          </div>

          {directorStatus && (
            <div className={`director-status-card ${directorStatus.phase || ''}`}>
              <div className="director-status-top">
                <span className="director-pulse" />
                <span className="director-status-label">{directorStatus.label}</span>
                <span className="director-status-percent">{Math.round(directorStatus.progress || 0)}%</span>
              </div>
              <div className="director-progress-track">
                <div className="director-progress-fill" style={{ width: `${Math.min(100, Math.max(0, directorStatus.progress || 0))}%` }} />
              </div>
              {directorStatus.detail && <div className="director-status-detail">{directorStatus.detail}</div>}
              {directorStatus.action && <div className="director-action-pill">Action: {directorStatus.action}</div>}
            </div>
          )}

          {directorTimeline.length > 0 && (
            <div className="director-timeline" ref={timelineRef}>
              <div className="director-timeline-title">Director 执行日志</div>
              {directorTimeline.map((step, idx) => {
                const isLatest = idx === directorTimeline.length - 1;
                const isDone = step.phase === 'done' || (!isLatest && step.phase !== 'error');
                const isError = step.phase === 'error';
                return (
                  <div key={`${step.phase}-${step.label}-${idx}`} className={`director-timeline-item ${isLatest ? 'latest' : ''} ${isDone ? 'done' : ''} ${isError ? 'error' : ''}`}>
                    <span className="timeline-dot">{isDone ? '✓' : isError ? '!' : ''}</span>
                    <div className="timeline-copy">
                      <div className="timeline-row">
                        <span>{step.label}</span>
                        <small>{step.time}</small>
                      </div>
                      {step.detail && <p>{step.detail}</p>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="chat-area">
            {messages.map((msg, index) => (
              <div key={index} className={`message ${msg.role}`}>
                <div className="avatar">{msg.role === 'assistant' ? 'Director' : 'You'}</div>
                <div className="bubble">{msg.text}</div>
              </div>
            ))}
            {isTyping && (
              <div className="message assistant typing">
                <div className="avatar">Director</div>
                <div className="bubble">
                  <span className="dot" /><span className="dot" /><span className="dot" />
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <form className="chat-input" onSubmit={handleSendMessage}>
            <input
              type="text"
              placeholder="用自然语言描述你的要求..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isTyping}
            />
            <button type="submit" className="send-btn" disabled={!input.trim() || isTyping}>
              ↑
            </button>
          </form>
        </aside>
      </div>

      {/* Lightbox：点击图片放大查看 */}
      {lightbox && (
        <div className="lightbox-backdrop" onClick={closeLightbox}>
          <button
            className="lightbox-close"
            onClick={(e) => { e.stopPropagation(); closeLightbox(); }}
            title="关闭 (ESC)"
          >×</button>
          <div className="lightbox-content" onClick={(e) => e.stopPropagation()}>
            <img src={lightbox.url} alt={lightbox.title || '预览'} />
            {lightbox.title && <div className="lightbox-caption">{lightbox.title}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
