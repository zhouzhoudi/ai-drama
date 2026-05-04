import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import './PremiumAgentWorkspace.css';

const API_BASE = '';

const NAV_ITEMS = [
  { id: 'dashboard', no: '01', icon: '▣', label: '工作台' },
  { id: 'scripts', no: '02', icon: '▤', label: '剧本管理' },
  { id: 'characters', no: '03', icon: '♙', label: '角色管理' },
  { id: 'locations', no: '04', icon: '⌂', label: '场景管理' },
  { id: 'storyboard', no: '05', icon: '▥', label: '分镜管理' },
  { id: 'assets', no: '06', icon: '◫', label: '素材库' },
  { id: 'videos', no: '07', icon: '▶', label: '视频管理' },
  { id: 'publish', no: '08', icon: '⇧', label: '发布与导出' },
  { id: 'settings', no: '09', icon: '⚙', label: '设置中心' },
];

const PIPELINE = [
  { no: '01', icon: '✎', title: '编剧', sub: '生成剧本', key: 'script' },
  { no: '02', icon: '◇', title: '剧本审阅', sub: '逻辑与内容复核', key: 'review' },
  { no: '03', icon: '▧', title: '主体分析', sub: '角色/场景/道具', key: 'subjects' },
  { no: '04', icon: '▧', title: '主体生图', sub: '生成角色与场景图', key: 'images' },
  { no: '05', icon: '▤', title: '分镜脚本', sub: '拆分镜头脚本', key: 'shot_script' },
  { no: '06', icon: '◇', title: '分镜审阅', sub: '镜头连贯性检查', key: 'shot_review' },
  { no: '07', icon: '▧', title: '分镜生图', sub: '生成分镜图', key: 'storyboard' },
  { no: '08', icon: '▷', title: '声音视频', sub: '生成视频 sound=on', key: 'video' },
];

const DEFAULT_PROJECT = {
  title: '猫咪007：皇家特工传奇',
  theme: '动作 / 冒险 / 喜剧',
  style: '写实电影风',
  synopsis: '一只聪明勇敢的猫咪特工，代号007，在伦敦、巴黎与东京等城市执行秘密任务，揭开一场威胁世界的阴谋。',
  script_id: '',
  updated_at: '2024-12-20 14:30',
  characters: [
    { name: '特工007', role: '主角', personality: '冷静、机智、优雅', image_url: '' },
    { name: '黑爪', role: '反派', personality: '神秘、狡猾', image_url: '' },
    { name: 'M夫人', role: '盟友', personality: '严厉、专业', image_url: '' },
  ],
  scenes: [],
};

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function getCharacterImage(character) {
  return character?.image_url || character?.appearance?.image_url || character?.generated_image_url || character?.image || '';
}

function getLocationImage(scene) {
  return scene?.location_image_url || scene?.scene_image_url || scene?.generated_image_url || scene?.image_url || '';
}

function getShotStoryboard(shot) {
  return shot?.storyboard_image_url || shot?.generated_image_url || shot?.image_url || '';
}

function getShotVideo(shot) {
  return shot?.generated_video_url || shot?.video_url || '';
}

function formatDate(value) {
  if (!value) return '未生成';
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 16);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  } catch (_) {
    return String(value).slice(0, 16);
  }
}

function flattenShots(data) {
  const scenes = safeArray(data?.scenes);
  return scenes.flatMap((scene, sceneIndex) => safeArray(scene.shots).map((shot, shotIndex) => ({
    ...shot,
    scene_title: scene.location || scene.title || scene.scene_number || `场景 ${sceneIndex + 1}`,
    scene_id: scene.scene_id || `scene_${sceneIndex + 1}`,
    display_no: shot.shot_number || shotIndex + 1,
  })));
}

function StatCard({ label, value, sub, tone = 'gold' }) {
  return (
    <div className={`pa-stat-card ${tone}`}>
      <div className="pa-stat-value">{value}</div>
      <div className="pa-stat-label">{label}</div>
      {sub && <div className="pa-stat-sub">{sub}</div>}
    </div>
  );
}

function EmptyThumb({ label = '未生成' }) {
  return <div className="pa-empty-thumb"><span>＋</span><small>{label}</small></div>;
}

export default function PremiumAgentWorkspace({ scriptId, onBack, onProjectCreated }) {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [workspaceData, setWorkspaceData] = useState(null);
  const [messages, setMessages] = useState([
    { role: 'assistant', text: '你好，我是你的导演 Agent。告诉我你想创作什么故事，我会按 8 步流程逐步推进。' }
  ]);
  const [input, setInput] = useState('');
  const [directorStatus, setDirectorStatus] = useState({
    phase: 'idle',
    label: '等待指令',
    detail: '编剧 · 审阅 · 主体 · 分镜 · 视频 sound=on',
    progress: 0,
    action: 'READY'
  });
  const chatEndRef = useRef(null);

  const data = workspaceData || DEFAULT_PROJECT;
  const characters = safeArray(data.characters);
  const scenes = safeArray(data.scenes);
  const shots = flattenShots(data);
  const scriptIdResolved = workspaceData?.script_id || scriptId;

  const progress = useMemo(() => {
    const scriptDone = workspaceData ? 1 : 0;
    const reviewed = workspaceData?.revision_note ? 1 : 0;
    const charImages = characters.filter(c => getCharacterImage(c)).length;
    const locationImages = scenes.filter(s => getLocationImage(s)).length;
    const storyboardImages = shots.filter(s => getShotStoryboard(s)).length;
    const videos = shots.filter(s => getShotVideo(s)).length;
    return {
      scriptDone,
      reviewed,
      charImages,
      locationImages,
      storyboardImages,
      videos,
      overall: Math.min(100, Math.round((scriptDone * 18) + (reviewed * 8) + (characters.length ? charImages / characters.length * 18 : 0) + (scenes.length ? locationImages / scenes.length * 12 : 0) + (shots.length ? storyboardImages / shots.length * 20 : 0) + (shots.length ? videos / shots.length * 24 : 0)))
    };
  }, [workspaceData, characters, scenes, shots]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (scriptId) fetchWorkspaceData(scriptId);
  }, [scriptId]);

  const fetchWorkspaceData = async (id) => {
    if (!id) return null;
    try {
      const res = await axios.get(`${API_BASE}/api/scripts/${id}`);
      setWorkspaceData(res.data);
      return res.data;
    } catch (error) {
      console.error('获取项目数据失败:', error);
      return null;
    }
  };

  const pushMessage = (role, text) => setMessages(prev => [...prev, { role, text }]);

  const runTask = async (label, request, nextTab = activeTab) => {
    const id = scriptIdResolved;
    if (!id) {
      pushMessage('assistant', '请先创建剧本项目，再执行这一步。');
      return;
    }
    setDirectorStatus({ phase: 'running', label, detail: '任务已提交，工作台会自动刷新。', progress: 58, action: label });
    try {
      await request(id);
      setActiveTab(nextTab);
      await fetchWorkspaceData(id);
      setDirectorStatus({ phase: 'done', label: `${label}完成`, detail: '工作台已刷新，请继续确认下一步。', progress: 100, action: label });
      pushMessage('assistant', `✅ ${label}已完成。你可以继续下一步，或告诉我哪里需要重做。`);
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || '任务失败';
      setDirectorStatus({ phase: 'error', label: `${label}失败`, detail, progress: 100, action: label });
      pushMessage('assistant', `❌ ${label}失败：${detail}`);
    }
  };

  const quickActions = {
    createScript: () => sendAgentMessage('帮我生成一部以猫咪为主角的007电影短视频'),
    reviewScript: () => sendAgentMessage('审阅当前剧本，检查逻辑、节奏和人物动机'),
    analyzeSubjects: () => sendAgentMessage('分析当前剧本里的角色、场景和道具'),
    generateCharacters: () => runTask('生成角色图', id => axios.post(`${API_BASE}/api/scripts/${id}/characters/batch-generate`, { force: false }), 'characters'),
    generateLocations: () => runTask('生成场景图', id => axios.post(`${API_BASE}/api/scripts/${id}/locations/batch-generate`, { force: false }), 'locations'),
    generateStoryboard: () => runTask('生成分镜图', id => axios.post(`${API_BASE}/api/scripts/${id}/storyboard/batch-generate`, {}), 'storyboard'),
    generateVideo: () => runTask('生成视频 sound=on', id => axios.post(`${API_BASE}/api/scripts/${id}/shots/batch-generate`), 'videos'),
    mergeFinal: () => runTask('合成成片', id => axios.post(`${API_BASE}/api/scripts/${id}/merge`), 'publish'),
  };

  const sendAgentMessage = async (overrideText) => {
    const text = (overrideText ?? input).trim();
    if (!text) return;
    setInput('');
    setMessages(prev => [...prev, { role: 'user', text }, { role: 'assistant', text: '' }]);
    setDirectorStatus({ phase: 'running', label: 'Director Agent 正在理解需求', detail: '正在选择 8 步流程中的下一步。', progress: 20, action: 'THINKING' });

    try {
      const payload = {
        script_id: scriptIdResolved,
        message: text,
        history: messages.slice(-6),
        project_config: workspaceData?.project_config || {},
        current_tab: activeTab,
        workspace_state: workspaceData || {}
      };
      const res = await fetch(`${API_BASE}/v1/agent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let latestScriptId = scriptIdResolved;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() || '';
        for (const chunk of chunks) {
          const lines = chunk.split('\n').filter(line => line.startsWith('data: ')).map(line => line.slice(6));
          if (!lines.length) continue;
          try {
            const event = JSON.parse(lines.join('\n'));
            if (event.type === 'chat') {
              setMessages(prev => {
                const next = [...prev];
                next[next.length - 1] = { ...next[next.length - 1], text: (next[next.length - 1].text || '') + event.content };
                return next;
              });
            } else if (event.type === 'clear_chat') {
              setMessages(prev => {
                const next = [...prev];
                next[next.length - 1] = { ...next[next.length - 1], text: '' };
                return next;
              });
            } else if (event.type === 'status') {
              setDirectorStatus({
                phase: event.phase || 'running',
                label: event.label || '处理中',
                detail: event.detail || '',
                progress: typeof event.progress === 'number' ? event.progress : 50,
                action: event.action
              });
            } else if (event.type === 'decision') {
              const actionMap = {
                CREATE_SCRIPT: 'scripts',
                REVIEW_SCRIPT: 'scripts',
                ANALYZE_SUBJECTS: 'characters',
                GENERATE_SUBJECT_IMAGES: 'characters',
                WRITE_SHOT_SCRIPT: 'storyboard',
                REVIEW_SHOTS: 'storyboard',
                GENERATE_STORYBOARD_IMAGES: 'storyboard',
                GENERATE_STORYBOARD_VIDEOS: 'videos'
              };
              if (actionMap[event.action]) setActiveTab(actionMap[event.action]);
            } else if (event.type === 'workspace_update' || event.type === 'action') {
              if (event.script_id) {
                latestScriptId = event.script_id;
                if (!scriptId && onProjectCreated) onProjectCreated(event.script_id);
                fetchWorkspaceData(event.script_id);
              }
            }
          } catch (err) {
            console.error('SSE parse error:', err, chunk);
          }
        }
      }
      if (latestScriptId) fetchWorkspaceData(latestScriptId);
    } catch (err) {
      setMessages(prev => {
        const next = [...prev];
        next[next.length - 1] = { role: 'assistant', text: `❌ 请求失败：${err.message}` };
        return next;
      });
      setDirectorStatus({ phase: 'error', label: 'Agent 请求失败', detail: err.message, progress: 100, action: 'ERROR' });
    }
  };

  const pipelineState = (index) => {
    const doneUntil = progress.scriptDone ? 1 : 0;
    if (index === 0 && progress.scriptDone) return 'done';
    if (index === 3 && progress.charImages > 0) return 'running';
    if (index === 6 && progress.storyboardImages > 0) return 'running';
    if (index === 7 && progress.videos > 0) return 'done';
    if (index <= doneUntil) return 'done';
    return index === doneUntil + 1 ? 'running' : 'waiting';
  };

  const renderDashboard = () => (
    <div className="pa-grid-page">
      <section className="pa-card pa-pipeline-card pa-span-12">
        <div className="pa-section-title"><span>创作流程总览 ✦</span><small>八步流程，AI 助力从剧本到视频一站式生成</small></div>
        <div className="pa-pipeline-row">
          {PIPELINE.map((step, i) => (
            <div key={step.key} className={`pa-pipe-step ${pipelineState(i)}`}>
              <div className="pa-pipe-no">{step.no}</div>
              <div className="pa-pipe-icon">{step.icon}</div>
              <div className="pa-pipe-title">{step.title}</div>
              <div className="pa-pipe-sub">{step.sub}</div>
              <div className="pa-pipe-state">{pipelineState(i) === 'done' ? '✓ 已完成' : pipelineState(i) === 'running' ? '◌ 进行中' : '○ 待开始'}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="pa-card pa-project-card pa-span-8">
        <div className="pa-section-title"><span>项目概览</span><small>当前项目的核心信息</small></div>
        <div className="pa-project-overview">
          <div className="pa-cover">
            {characters[0] && getCharacterImage(characters[0]) ? <img src={getCharacterImage(characters[0])} alt="cover" /> : <div className="pa-cover-fallback">007</div>}
          </div>
          <div className="pa-project-info">
            <h1>{data.title || '未命名短剧'}</h1>
            <div className="pa-tags"><span>动作</span><span>冒险</span><span>007 风格</span><span>猫咪主角</span></div>
            <p>{data.synopsis || data.summary || DEFAULT_PROJECT.synopsis}</p>
            <div className="pa-metrics-row">
              <StatCard label="剧本字数" value={data.word_count || '—'} sub="字" />
              <StatCard label="角色数量" value={characters.length || 0} sub="个" />
              <StatCard label="场景数量" value={scenes.length || 0} sub="个" />
              <StatCard label="镜头数量" value={shots.length || 0} sub="个" />
              <StatCard label="预计时长" value={data.total_duration || '03:12'} sub="分钟" />
            </div>
          </div>
        </div>
      </section>

      <section className="pa-card pa-progress-card pa-span-4">
        <div className="pa-section-title"><span>进度总览</span><small>整体完成度</small></div>
        <div className="pa-donut"><span>{progress.overall || 38}%</span><small>整体进度</small></div>
        <div className="pa-progress-list">
          <div><span>剧本创作</span><b style={{ width: `${progress.scriptDone ? 100 : 35}%` }} /></div>
          <div><span>主体资产</span><b style={{ width: `${Math.max(10, progress.charImages * 20)}%` }} /></div>
          <div><span>分镜制作</span><b style={{ width: `${Math.max(5, progress.storyboardImages * 12)}%` }} /></div>
          <div><span>视频生成</span><b style={{ width: `${Math.max(0, progress.videos * 15)}%` }} /></div>
        </div>
      </section>

      <section className="pa-card pa-span-12">
        <div className="pa-section-title"><span>最近任务</span><small>快速进入下一步</small></div>
        <div className="pa-quick-grid">
          <button onClick={quickActions.createScript}>新建剧本</button>
          <button onClick={quickActions.generateCharacters}>生成角色图</button>
          <button onClick={quickActions.generateLocations}>生成场景图</button>
          <button onClick={quickActions.generateStoryboard}>生成分镜图</button>
          <button onClick={quickActions.generateVideo}>生成视频</button>
        </div>
      </section>
    </div>
  );

  const renderScripts = () => (
    <div className="pa-grid-page">
      <section className="pa-card pa-span-8">
        <div className="pa-section-title"><span>我的剧本</span><small>剧本信息、风格、时长和状态</small></div>
        <table className="pa-table">
          <thead><tr><th>剧本信息</th><th>风格/类型</th><th>时长</th><th>创建时间</th><th>状态</th></tr></thead>
          <tbody>
            <tr><td>{data.title}</td><td>{data.style || data.theme}</td><td>{data.total_duration || '03:15'}</td><td>{formatDate(data.created_at)}</td><td><span className="pa-state done">已生成</span></td></tr>
            <tr><td>未来都市：记忆迷宫</td><td>科幻 / 悬疑</td><td>04:30</td><td>2024-12-18</td><td><span className="pa-state done">已完成</span></td></tr>
            <tr><td>时间猎人</td><td>动作 / 冒险</td><td>03:50</td><td>2024-12-15</td><td><span className="pa-state running">生成中</span></td></tr>
          </tbody>
        </table>
      </section>
      <section className="pa-card pa-span-4">
        <div className="pa-section-title"><span>剧本卡片预览</span><small>当前选中剧本</small></div>
        <div className="pa-script-preview">
          <div className="pa-mini-cover">007</div>
          <h2>{data.title}</h2>
          <div className="pa-tags"><span>动作</span><span>冒险</span><span>猫咪主角</span></div>
          <p>{data.synopsis || data.summary || DEFAULT_PROJECT.synopsis}</p>
          <button onClick={() => setActiveTab('scripts')}>查看详情</button>
        </div>
      </section>
      <section className="pa-card pa-span-12">
        <div className="pa-section-title"><span>剧本正文</span><small>可让 Agent 审阅或重写</small></div>
        <div className="pa-script-body">{data.full_script || data.script_text || data.synopsis || '当前剧本正文会显示在这里。你可以让 Agent 审阅、重写、增强冲突，或改成更强的电影感结构。'}</div>
      </section>
    </div>
  );

  const renderCharacters = () => (
    <div className="pa-grid-page">
      <section className="pa-card pa-span-9">
        <div className="pa-section-title"><span>角色列表</span><small>主体生图资产</small><button onClick={quickActions.generateCharacters}>＋ 新增/生成角色</button></div>
        <div className="pa-card-grid">
          {(characters.length ? characters : DEFAULT_PROJECT.characters).map((c, i) => (
            <div className="pa-asset-card" key={c.character_id || c.name || i}>
              <div className="pa-asset-thumb">{getCharacterImage(c) ? <img src={getCharacterImage(c)} alt={c.name} /> : <EmptyThumb label="角色图" />}</div>
              <h3>{c.name || `角色 ${i + 1}`}</h3>
              <p>{c.role || c.personality || c.description || '角色设定待完善'}</p>
              <div className="pa-asset-actions"><button onClick={() => runTask(`重做角色图：${c.name}`, id => axios.post(`${API_BASE}/api/scripts/${id}/characters/${encodeURIComponent(c.character_id || c.name)}/generate-image`), 'characters')}>重做</button><button>详情</button></div>
            </div>
          ))}
          <div className="pa-asset-card add"><EmptyThumb label="添加角色" /></div>
        </div>
      </section>
      <section className="pa-card pa-span-3">
        <div className="pa-section-title"><span>角色详情</span><small>当前主体</small></div>
        <div className="pa-detail-panel">
          <div className="pa-detail-avatar">{characters[0] && getCharacterImage(characters[0]) ? <img src={getCharacterImage(characters[0])} alt="char" /> : '🐱'}</div>
          <h2>{characters[0]?.name || '特工007'}</h2>
          <p>类型：主角</p><p>性格：冷静、勇敢、优雅</p><p>身份：秘密特工</p><p>模型：kling-v3-omni</p>
        </div>
      </section>
    </div>
  );

  const renderLocations = () => (
    <div className="pa-grid-page">
      <section className="pa-card pa-span-9">
        <div className="pa-section-title"><span>场景列表</span><small>场景视觉锚点</small><button onClick={quickActions.generateLocations}>＋ 生成场景图</button></div>
        <div className="pa-card-grid location">
          {(scenes.length ? scenes : [
            { location: '伦敦夜景', description: '外景 / 夜晚' },
            { location: '秘密基地', description: '内景 / 科技感' },
            { location: '豪华游轮', description: '外景 / 白天' },
            { location: '巴黎街头', description: '外景 / 日间' },
            { location: '东京塔下', description: '外景 / 夜晚' },
            { location: '安全屋', description: '内景 / 昏暗' },
          ]).map((s, i) => (
            <div className="pa-asset-card" key={s.scene_id || s.location || i}>
              <div className="pa-asset-thumb wide">{getLocationImage(s) ? <img src={getLocationImage(s)} alt={s.location} /> : <EmptyThumb label="场景图" />}</div>
              <h3>{String(i + 1).padStart(2, '0')} {s.location || s.title || `场景 ${i + 1}`}</h3>
              <p>{s.description || s.summary || '场景描述待完善'}</p>
            </div>
          ))}
          <div className="pa-asset-card add"><EmptyThumb label="添加场景" /></div>
        </div>
      </section>
      <section className="pa-card pa-span-3"><div className="pa-section-title"><span>场景详情</span><small>视觉基准</small></div><div className="pa-detail-panel"><div className="pa-detail-avatar scene">🏙</div><h2>{scenes[0]?.location || '伦敦夜景'}</h2><p>场景类型：外景</p><p>时间：夜晚</p><p>氛围：神秘、电影感、金色灯光</p></div></section>
    </div>
  );

  const renderStoryboard = () => (
    <div className="pa-grid-page">
      <section className="pa-card pa-span-9">
        <div className="pa-section-title"><span>分镜脚本列表</span><small>镜头、画面内容、时长与状态</small><button onClick={quickActions.generateStoryboard}>＋ 生成分镜图</button></div>
        <table className="pa-table">
          <thead><tr><th>镜头号</th><th>景别</th><th>画面内容</th><th>时长</th><th>状态</th></tr></thead>
          <tbody>
            {(shots.length ? shots : Array.from({ length: 8 }).map((_, i) => ({ display_no: i + 1, shot_type: ['全景','中景','近景','特写'][i % 4], content_description: '猫咪特工在城市夜景中执行秘密任务', duration: i % 3 + 4 }))).map((shot, i) => (
              <tr key={shot.shot_id || i}><td>{String(shot.display_no || i + 1).padStart(2, '0')}</td><td>{shot.shot_type || '中景'}</td><td>{shot.content_description || shot.description || '镜头内容待生成'}</td><td>{shot.duration || 5}s</td><td><span className={`pa-state ${getShotStoryboard(shot) ? 'done' : 'waiting'}`}>{getShotStoryboard(shot) ? '已生成' : '待开始'}</span></td></tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="pa-card pa-span-3"><div className="pa-section-title"><span>分镜详情</span><small>画面预览</small></div><div className="pa-story-preview">{shots[0] && getShotStoryboard(shots[0]) ? <img src={getShotStoryboard(shots[0])} alt="shot" /> : <EmptyThumb label="分镜图" />}</div><p className="pa-muted">镜头会使用角色图与场景图作为视觉锚点。</p></section>
    </div>
  );

  const renderAssets = () => {
    const assets = [
      ...characters.map(c => ({ type: '图片', name: `${c.name || '角色'}_角色图.jpg`, url: getCharacterImage(c) })),
      ...scenes.map(s => ({ type: '图片', name: `${s.location || '场景'}_场景图.jpg`, url: getLocationImage(s) })),
      ...shots.map((s, i) => ({ type: getShotVideo(s) ? '视频' : '分镜', name: `镜头_${i + 1}`, url: getShotVideo(s) || getShotStoryboard(s) }))
    ];
    return <div className="pa-grid-page"><section className="pa-card pa-span-9"><div className="pa-section-title"><span>素材库</span><small>图片、视频、音频、分镜资产</small></div><div className="pa-filter-row"><span>全部</span><span>图片</span><span>视频</span><span>音频</span><span>其他</span></div><div className="pa-card-grid assets">{(assets.length ? assets : [{ type:'图片', name:'特工007_角色图.jpg'},{type:'图片', name:'伦敦夜景_场景图.jpg'},{type:'视频', name:'镜头001.mp4'},{type:'音频', name:'旁白.wav'}]).map((a,i)=><div className="pa-asset-card" key={i}><div className="pa-asset-thumb">{a.url ? <img src={a.url} alt={a.name} /> : <EmptyThumb label={a.type} />}</div><h3>{a.name}</h3><p>{a.type} · 1024×1536</p></div>)}</div></section><section className="pa-card pa-span-3"><div className="pa-section-title"><span>素材详情</span><small>文件信息</small></div><div className="pa-detail-panel"><div className="pa-detail-avatar">🗂</div><p>类型：图片/视频</p><p>格式：JPG / MP4</p><p>模型：kling-v3-omni</p><p>创建时间：{formatDate(data.updated_at)}</p></div></section></div>;
  };

  const renderVideos = () => (
    <div className="pa-grid-page"><section className="pa-card pa-span-8"><div className="pa-section-title"><span>视频列表</span><small>分镜视频和最终成片</small><button onClick={quickActions.generateVideo}>＋ 生成视频</button></div><table className="pa-table"><thead><tr><th>视频信息</th><th>时长</th><th>分辨率</th><th>大小</th><th>状态</th></tr></thead><tbody>{(shots.length ? shots : Array.from({length:5}).map((_,i)=>({display_no:i+1,duration: i+4}))).map((s,i)=><tr key={i}><td>猫咪007_镜头_{String(i+1).padStart(2,'0')}</td><td>{s.duration || 5}s</td><td>1080P</td><td>{Math.round((s.duration || 5)*24)} MB</td><td><span className={`pa-state ${getShotVideo(s) ? 'done' : 'waiting'}`}>{getShotVideo(s)?'已完成':'待生成'}</span></td></tr>)}</tbody></table></section><section className="pa-card pa-span-4"><div className="pa-section-title"><span>视频预览</span><small>sound=on</small></div><div className="pa-video-preview">▶</div><button className="pa-main-btn" onClick={quickActions.mergeFinal}>合成最终成片</button></section></div>
  );

  const renderPublish = () => (
    <div className="pa-grid-page"><section className="pa-card pa-span-4"><div className="pa-section-title"><span>导出设置</span><small>最终成片参数</small></div><label>视频名称</label><input value={`${data.title || '猫咪007'}_最终版`} readOnly /><label>分辨率</label><select defaultValue="1080P"><option>1080P</option><option>4K</option></select><label>格式</label><select defaultValue="MP4"><option>MP4</option></select><button className="pa-main-btn" onClick={quickActions.mergeFinal}>导出视频</button></section><section className="pa-card pa-span-4"><div className="pa-section-title"><span>发布平台</span><small>一键分发</small></div>{['抖音','B站','YouTube','视频号','微博','快手'].map((p,i)=><div className="pa-platform" key={p}><span>{p}</span><b>{i%2===0?'已开启':'未开启'}</b></div>)}</section><section className="pa-card pa-span-4"><div className="pa-section-title"><span>导出历史</span><small>最近文件</small></div>{['猫咪007_最终版.mp4','猫咪007_预告片.mp4','猫咪007_竖版.mp4'].map(f=><div className="pa-file-row" key={f}><span>{f}</span><button>下载</button></div>)}</section></div>
  );

  const renderSettings = () => (
    <div className="pa-grid-page"><section className="pa-card pa-span-6"><div className="pa-section-title"><span>账户设置</span><small>基础信息</small></div><div className="pa-profile"><div className="pa-detail-avatar">🐱</div><div><h2>创作者</h2><p>creator@example.com</p><p>套餐：专业版</p></div></div><button className="pa-main-btn">增加信息</button></section><section className="pa-card pa-span-6"><div className="pa-section-title"><span>系统信息</span><small>模型与资源</small></div><div className="pa-setting-list"><p><span>语言模型</span><b>deepseek-v4-pro</b></p><p><span>图像模型</span><b>kling-v3-omni</b></p><p><span>视频模型</span><b>kling-v3-omni · sound=on</b></p><p><span>存储空间</span><b>23.7 GB / 100 GB</b></p><p><span>API 调用次数</span><b>12,345 / 100,000</b></p></div></section></div>
  );

  const renderMain = () => {
    if (activeTab === 'dashboard') return renderDashboard();
    if (activeTab === 'scripts') return renderScripts();
    if (activeTab === 'characters') return renderCharacters();
    if (activeTab === 'locations') return renderLocations();
    if (activeTab === 'storyboard') return renderStoryboard();
    if (activeTab === 'assets') return renderAssets();
    if (activeTab === 'videos') return renderVideos();
    if (activeTab === 'publish') return renderPublish();
    return renderSettings();
  };

  return (
    <div className="pa-shell">
      <header className="pa-topbar">
        <div className="pa-brand"><div className="pa-logo">✤</div><div><strong>AI 短剧创作工作台</strong><small>AI DRAMA STUDIO</small></div></div>
        <div className="pa-project-select">当前项目：{data.title || '未命名项目'} <span>⌄</span></div>
        <div className="pa-saved">● 已保存</div>
        <div className="pa-top-actions"><button>☾ 深色模式</button><button>♧</button><button>?</button><button onClick={onBack}>返回</button><div className="pa-user">创作者<br/><b>Pro</b></div></div>
      </header>

      <div className="pa-body">
        <aside className="pa-sidebar">
          <nav>{NAV_ITEMS.map(item => <button key={item.id} className={activeTab === item.id ? 'active' : ''} onClick={() => setActiveTab(item.id)}><span>{item.icon}</span>{item.label}</button>)}</nav>
          <div className="pa-storage"><b>存储空间</b><div><i style={{ width: '28%' }} /></div><span>23.7 GB / 100 GB</span></div>
          <div className="pa-plan">◇ 专业版<br/><span>剩余天数 30 天</span></div>
        </aside>

        <main className="pa-main">
          <div className="pa-page-title"><h1>{NAV_ITEMS.find(n => n.id === activeTab)?.no} {NAV_ITEMS.find(n => n.id === activeTab)?.label}</h1><small>猫咪007 · 皇家特工传奇</small></div>
          {renderMain()}
        </main>

        <aside className="pa-agent-panel">
          <section className="pa-director-card">
            <div className="pa-director-head"><div className="pa-bot">🤖</div><div><h3>Director Agent</h3><small>AI 导演 · 多智能体协作中</small></div><span>在线</span></div>
            <div className="pa-current-step"><b>当前步骤：{directorStatus.label}</b><p>{directorStatus.detail}</p></div>
            <div className="pa-mini-pipeline">{PIPELINE.map((s,i)=><span key={s.key} className={pipelineState(i)}>{s.no}</span>)}</div>
          </section>

          <section className="pa-chat-card">
            <div className="pa-chat-title"><b>对话助手</b><button onClick={() => setMessages([])}>清空对话</button></div>
            <div className="pa-chat-list">
              {messages.map((m,i)=><div key={i} className={`pa-chat-msg ${m.role}`}><p>{m.text}</p></div>)}
              <div ref={chatEndRef} />
            </div>
            <form className="pa-chat-input" onSubmit={(e)=>{e.preventDefault(); sendAgentMessage();}}>
              <input value={input} onChange={e=>setInput(e.target.value)} placeholder="输入你的创作需求..." />
              <button type="submit">➤</button>
            </form>
          </section>
        </aside>
      </div>
    </div>
  );
}
