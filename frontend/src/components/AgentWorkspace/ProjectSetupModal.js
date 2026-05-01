import React, { useState } from 'react';
import './ProjectSetupModal.css';

const VIDEO_ENGINES = [
  { id: 'minimax', name: 'Minimax Hailuo', desc: '快速、动态丰富' },
  { id: 'kling', name: 'Kling 2.5', desc: '极致写实、人物一致' },
  { id: 'seedance', name: 'Seedance 2.0', desc: '电影级运镜、高逼真' },
  { id: 'sora', name: 'Sora 2', desc: '物理世界模拟' },
  { id: 'runway', name: 'Runway Gen-4', desc: '风格化、精细控制' },
  { id: 'luma', name: 'Luma Ray 2', desc: '快速生成、高质量' },
  { id: 'veo', name: 'Veo 3', desc: '长视频连贯性' },
  { id: 'pika', name: 'Pika 2.2', desc: '动漫与特效首选' },
  { id: 'wan', name: 'Wan 2.2', desc: '中国风、古装' },
  { id: 'hunyuan', name: 'HunyuanVideo', desc: '腾讯混元视频' },
  { id: 'cogvideo', name: 'CogVideoX', desc: '开源视频生态' }
];

const IMAGE_ENGINES = [
  { id: 'midjourney', name: 'Midjourney V7', desc: '艺术感、细节丰满' },
  { id: 'flux', name: 'Flux 1.1 Pro', desc: '极速、开源生态' },
  { id: 'dalle3', name: 'DALL·E 3', desc: '语义理解极强' },
  { id: 'ideogram', name: 'Ideogram 3', desc: '文字生成无敌' }
];

const ProjectSetupModal = ({ onConfirm }) => {
  const [aspectRatio, setAspectRatio] = useState('16:9');
  const [style, setStyle] = useState('写实电影风');
  const [videoEngine, setVideoEngine] = useState('kling');
  const [imageEngine, setImageEngine] = useState('midjourney');

  const handleSubmit = (e) => {
    e.preventDefault();
    onConfirm({ aspect_ratio: aspectRatio, style, video_engine: videoEngine, image_engine: imageEngine });
  };

  return (
    <div className="project-setup-overlay">
      <div className="project-setup-modal">
        <h2>Zopia Studio</h2>
        <p>配置您的全局工作流与生成模型</p>
        
        <form onSubmit={handleSubmit}>
          <div className="scroll-content">
            <div className="form-group">
              <label>视频生成引擎 (Video Engine)</label>
              <div className="engine-grid">
                {VIDEO_ENGINES.map(engine => (
                  <div 
                    key={engine.id}
                    className={`engine-card ${videoEngine === engine.id ? 'selected' : ''}`}
                    onClick={() => setVideoEngine(engine.id)}
                  >
                    <div className="engine-info">
                      <span className="engine-name">{engine.name}</span>
                      <span className="engine-desc">{engine.desc}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label>画幅比例 (Aspect Ratio)</label>
              <div className="options-grid">
                <div 
                  className={`option-card ${aspectRatio === '9:16' ? 'selected' : ''}`}
                  onClick={() => setAspectRatio('9:16')}
                >
                  <div className="ratio-box vertical"></div>
                  <span>竖屏 9:16</span>
                </div>
                <div 
                  className={`option-card ${aspectRatio === '16:9' ? 'selected' : ''}`}
                  onClick={() => setAspectRatio('16:9')}
                >
                  <div className="ratio-box horizontal"></div>
                  <span>横屏 16:9</span>
                </div>
                <div 
                  className={`option-card ${aspectRatio === '3:4' ? 'selected' : ''}`}
                  onClick={() => setAspectRatio('3:4')}
                >
                  <div className="ratio-box standard"></div>
                  <span>标准 3:4</span>
                </div>
                <div 
                  className={`option-card ${aspectRatio === '21:9' ? 'selected' : ''}`}
                  onClick={() => setAspectRatio('21:9')}
                >
                  <div className="ratio-box cinematic"></div>
                  <span>宽银幕 21:9</span>
                </div>
              </div>
            </div>

            <div className="form-group">
              <label>视觉风格 (Visual Style)</label>
              <select value={style} onChange={(e) => setStyle(e.target.value)}>
                <option value="写实电影风">写实电影风</option>
                <option value="二次元动漫">二次元动漫</option>
                <option value="3D盲盒质感">3D盲盒质感</option>
                <option value="赛博朋克">赛博朋克</option>
                <option value="唯美古风">唯美古风</option>
                <option value="水墨画">水墨画</option>
                <option value="废土风">废土风</option>
                <option value="极简主义">极简主义</option>
              </select>
            </div>
          </div>

          <div className="modal-footer">
            <button type="submit" className="start-btn">进入工作台</button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default ProjectSetupModal;
