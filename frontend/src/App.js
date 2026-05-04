import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, useNavigate, useLocation, useParams } from 'react-router-dom';
import axios from 'axios';
import './App.css';
import PremiumAgentWorkspace from './components/PremiumAgentWorkspace';

const API_BASE = '';

// Sidebar component
function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();

  const isActive = (path) => {
    if (path === '/projects') return location.pathname === '/' || location.pathname === '/projects';
    return location.pathname === path;
  };

  const menuItems = [
    { icon: '🏠', label: '主页', path: '/projects' },
    { icon: '🎬', label: '项目', path: '/projects' },
    { icon: '⚙️', label: '设置', path: '/settings' },
  ];

  return (
    <div className="sidebar">
      <div className="sidebar-logo">
        <span className="logo-z">Z</span>
      </div>
      <nav className="sidebar-nav">
        {menuItems.map((item, i) => (
          <div
            key={i}
            className={`sidebar-item ${isActive(item.path) && item.label !== '主页' ? 'active' : ''}`}
            onClick={() => navigate(item.path)}
          >
            <span className="sidebar-icon">{item.icon}</span>
            <span className="sidebar-label">{item.label}</span>
          </div>
        ))}
      </nav>
    </div>
  );
}

// Layout with sidebar
function MainLayout({ children }) {
  return (
    <div className="main-layout">
      <Sidebar />
      <div className="main-content">
        {children}
      </div>
    </div>
  );
}

// New Project Modal
const VISUAL_STYLES = [
  { id: 'realistic', label: '真实写实', sub: 'Photorealistic', img: '/styles/realistic_east.png', hot: true },
  { id: 'anime', label: '日韩动漫', sub: 'Anime Style', img: '/styles/anime_japanese_korean.png', hot: false },
  { id: 'cg3d', label: '3D 写实', sub: 'Realistic 3D CG', img: '/styles/realistic_3d_cg.png', hot: true },
  { id: 'pixar', label: '皮克斯卡通', sub: 'Pixar Cartoon', img: '/styles/pixar_3d_cartoon.png', hot: false },
  { id: 'chinese_cg', label: '国风 CG', sub: 'Chinese Style CG', img: '/styles/3d_cg_animation.png', hot: true },
  { id: 'chibi', label: '萌系 Q 版', sub: 'Chibi Cute', img: '/styles/anime_chibi.png', hot: false },
  { id: 'shinkai', label: '新海诚风', sub: 'Makoto Shinkai', img: '/styles/anime_shinkai.png', hot: false },
  { id: 'ghibli', label: '吉卜力', sub: 'Ghibli Style', img: '/styles/anime_ghibli.png', hot: false },
];

const GEN_MODES = [
  { id: 'parallel', icon: '⚡', label: '角色并行模式', sub: '基于角色参考图并行生成视频，速度更快' },
  { id: 'keyframe', icon: '🎞', label: '关键帧模式', sub: '逐帧精准控制，适合复杂镜头' },
];

function NewProjectModal({ onClose, onCreated }) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [genMode, setGenMode] = useState('parallel');
  const [visualStyle, setVisualStyle] = useState('realistic');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!title.trim()) return;
    setLoading(true);
    try {
      const res = await axios.post(`${API_BASE}/api/scripts`, {
        title: title.trim(),
        gen_mode: genMode,
        visual_style: visualStyle,
        style: VISUAL_STYLES.find(s => s.id === visualStyle)?.label || visualStyle,
        genre: '都市爱情',
        duration: '180',
        theme: title.trim(),
        description: description.trim() || title.trim(),
      });
      const newId = res.data.script_id || res.data.id;
      onCreated(newId);
    } catch (e) {
      console.error(e);
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box modal-box-wide" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">配置项目基本设置</h2>
          <span className="modal-hint">⚡ 预计生成速度：快</span>
        </div>
        <div className="modal-field">
          <label className="modal-label">项目名称</label>
          <input className="modal-input" type="text" placeholder="给你的短剧起个名字..." value={title} onChange={e => setTitle(e.target.value)} autoFocus />
        </div>
        <div className="modal-field">
          <label className="modal-label">剧情描述（一句话）</label>
          <input className="modal-input" type="text" placeholder="例如：帮我生成一部以猫咪为主角的007电影短视频..." value={description} onChange={e => setDescription(e.target.value)} />
        </div>
        <div className="modal-field">
          <label className="modal-label">生成模式</label>
          <div className="mode-cards">
            {GEN_MODES.map(m => (
              <div key={m.id} className={`mode-card ${genMode === m.id ? 'selected' : ''}`} onClick={() => setGenMode(m.id)}>
                <span className="mode-icon">{m.icon}</span>
                <div><div className="mode-label">{m.label}</div><div className="mode-sub">{m.sub}</div></div>
              </div>
            ))}
          </div>
        </div>
        <div className="modal-field">
          <label className="modal-label">视觉风格</label>
          <div className="style-grid">
            {VISUAL_STYLES.map(s => (
              <div key={s.id} className={`style-card ${visualStyle === s.id ? 'selected' : ''}`} onClick={() => setVisualStyle(s.id)}>
                <div className="style-img-wrap"><img src={s.img} alt={s.label} className="style-img" /></div>
                <div className="style-label">{s.label}</div><div className="style-sub">{s.sub}</div>{s.hot && <span className="style-hot">🔥</span>}
              </div>
            ))}
          </div>
        </div>
        <div className="modal-actions">
          <button className="btn-cancel" onClick={onClose}>取消</button>
          <button className="btn-create" onClick={handleSubmit} disabled={loading || !title.trim()}>{loading ? '创建中...' : '✓ 开始创作'}</button>
        </div>
      </div>
    </div>
  );
}

// Project List Page
function DramaList() {
  const navigate = useNavigate();
  const [dramas, setDramas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);

  const fetchDramas = async () => {
    try {
      const res = await axios.get(`${API_BASE}/api/scripts`);
      setDramas(res.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchDramas(); }, []);

  const handleCreated = (newId) => {
    setShowModal(false);
    navigate(`/project/${newId}`);
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  return (
    <div className="projects-page">
      <div className="projects-header">
        <h1 className="projects-title">我的项目</h1>
        <button className="btn-import">导入项目</button>
      </div>
      {loading ? <div className="loading-state">加载中...</div> : (
        <div className="projects-grid">
          <div className="card-new" onClick={() => setShowModal(true)}><div className="card-new-circle">+</div><span className="card-new-text">新建项目</span></div>
          {dramas.map(drama => (
            <div key={drama.script_id} className="project-card" onClick={() => navigate(`/project/${drama.script_id}`)}>
              <div className="card-cover">{drama.cover_url ? <img src={drama.cover_url} alt={drama.title} /> : <div className="card-cover-placeholder"><span>🎬</span></div>}</div>
              <div className="card-info"><div className="card-meta"><span className="card-date">{formatDate(drama.updated_at)}</span><button className="card-menu" onClick={e => e.stopPropagation()}>···</button></div><div className="card-title">{drama.title}</div></div>
            </div>
          ))}
        </div>
      )}
      {showModal && <NewProjectModal onClose={() => setShowModal(false)} onCreated={handleCreated} />}
    </div>
  );
}

function AgentWorkspaceWrapper() {
  const { id } = useParams();
  const navigate = useNavigate();
  const handleProjectCreated = (newId) => navigate(`/project/${newId}`, { replace: true });
  return <PremiumAgentWorkspace scriptId={id} onBack={() => navigate('/projects')} onProjectCreated={handleProjectCreated} />;
}

function SystemSettings() {
  const [systemInfo, setSystemInfo] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadSystemInfo = async () => {
      try {
        const response = await axios.get(`${API_BASE}/api/system/info`);
        setSystemInfo(response.data);
      } catch (err) {
        setSystemInfo({
          name: 'AI 短剧自动生成系统',
          version: '1.0.0',
          capabilities: [
            { id: 'script_generation', name: '编剧agent', model: 'deepseek-v4-pro', status: 'ready', icon: '📝' },
            { id: 'script_review', name: '剧本审阅与复核agent', model: 'deepseek-v4-pro', status: 'ready', icon: '✅' },
            { id: 'subject_analysis', name: '主体分析设计agent', model: 'deepseek-v4-pro', status: 'ready', icon: '🧩' },
            { id: 'subject_image', name: '主体生图agent', model: 'kling-v3-omni', status: 'ready', icon: '🎨' },
            { id: 'shot_script', name: '分镜脚本编写agent', model: 'deepseek-v4-pro', status: 'ready', icon: '🎬' },
            { id: 'shot_review', name: '分镜审阅agent', model: 'deepseek-v4-pro', status: 'ready', icon: '🔍' },
            { id: 'storyboard_image', name: '分镜生图agent', model: 'kling-v3-omni', status: 'ready', icon: '🖼️' },
            { id: 'video_generation', name: '分镜生视频agent', model: 'kling-v3-omni · sound=on', status: 'ready', icon: '🎞️' },
          ],
        });
      } finally { setLoading(false); }
    };
    loadSystemInfo();
  }, []);

  const getStatusBadge = (status) => status === 'ready' ? <span style={{ color: '#4caf50' }}>✅ 可用</span> : <span>{status}</span>;
  if (loading) return <div className="loading">加载中...</div>;
  return (
    <div className="settings-container">
      <h2>⚙️ 系统设置</h2>
      <section className="section"><h3>🤖 系统信息</h3><div className="info-card"><p><strong>系统名称：</strong>{systemInfo?.name}</p><p><strong>版本：</strong>{systemInfo?.version}</p></div></section>
      <section className="section"><h3>🎯 系统能力</h3><div className="capabilities-list">{systemInfo?.capabilities?.map(cap => <div key={cap.id} className="capability-card"><div className="capability-header"><span className="capability-icon">{cap.icon}</span><span className="capability-name">{cap.name}</span>{getStatusBadge(cap.status)}</div><div className="capability-model"><strong>模型：</strong><code>{cap.model}</code></div></div>)}</div></section>
    </div>
  );
}

function AppContent() {
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith('/project/');
  if (isWorkspace) return <Routes><Route path="/project/:id" element={<AgentWorkspaceWrapper />} /></Routes>;
  return <MainLayout><Routes><Route path="/" element={<DramaList />} /><Route path="/projects" element={<DramaList />} /><Route path="/settings" element={<SystemSettings />} /></Routes></MainLayout>;
}

export default function App() {
  return <Router><AppContent /></Router>;
}
