import re

with open('/Users/zhoumi/ai-drama-system/frontend/src/components/AgentWorkspace/index.js', 'r') as f:
    content = f.read()

# Change the initial modal layout to match Zopia
content = re.sub(
    r'<div className="project-setup-modal-content">',
    r'<div className="project-setup-modal-content zopia-modal">',
    content
)

with open('/Users/zhoumi/ai-drama-system/frontend/src/components/AgentWorkspace/index.js', 'w') as f:
    f.write(content)

with open('/Users/zhoumi/ai-drama-system/frontend/src/components/AgentWorkspace/AgentWorkspace.css', 'r') as f:
    css_content = f.read()

# Make the modal look exactly like Zopia
css_content += """

/* Zopia exact modal override */
.project-setup-overlay {
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(8px);
}

.zopia-modal {
    background: #111111;
    border: 1px solid #222222;
    border-radius: 16px;
    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    padding: 40px !important;
    width: 600px !important;
    max-width: 90vw !important;
}

.zopia-modal h2 {
    font-size: 24px;
    font-weight: 600;
    text-align: center;
    margin-bottom: 8px;
    letter-spacing: -0.02em;
}

.zopia-modal > p {
    text-align: center;
    color: #888888;
    margin-bottom: 32px;
}

.setup-form-group {
    margin-bottom: 24px;
}

.setup-form-group label {
    font-size: 12px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #666666;
    margin-bottom: 12px;
    display: block;
}

.engine-cards, .ratio-cards {
    display: flex;
    gap: 12px;
}

.engine-card, .ratio-card {
    background: #1A1A1A;
    border: 1px solid #2A2A2A;
    border-radius: 12px;
    transition: all 0.2s ease;
}

.engine-card:hover, .ratio-card:hover {
    border-color: #444444;
    background: #222222;
}

.engine-card.active, .ratio-card.active {
    border-color: #FFFFFF;
    background: #1A1A1A;
    box-shadow: 0 0 0 1px #FFFFFF;
}

.engine-card .engine-icon {
    font-size: 20px;
    margin-right: 12px;
}

.engine-info h4 {
    font-size: 14px;
    color: #FFFFFF;
    margin-bottom: 4px;
}

.engine-info p {
    font-size: 12px;
    color: #888888;
}

.ratio-card {
    padding: 16px 0;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}

.ratio-box {
    border: 2px solid #555555;
    border-radius: 4px;
    margin-bottom: 12px;
    transition: all 0.2s ease;
}

.ratio-card.active .ratio-box {
    border-color: #FFFFFF;
    background: rgba(255, 255, 255, 0.1);
}

.ratio-card span {
    font-size: 13px;
    color: #AAAAAA;
    font-weight: 500;
}

.ratio-card.active span {
    color: #FFFFFF;
}

.style-select {
    width: 100%;
    background: #1A1A1A;
    border: 1px solid #2A2A2A;
    color: #FFFFFF;
    padding: 14px 16px;
    border-radius: 8px;
    font-size: 14px;
    appearance: none;
    background-image: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23FFFFFF%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E");
    background-repeat: no-repeat, repeat;
    background-position: right .7em top 50%, 0 0;
    background-size: .65em auto, 100%;
}

.style-select:focus {
    outline: none;
    border-color: #555555;
}

.start-btn {
    width: 100%;
    background: #FFFFFF;
    color: #000000;
    border: none;
    border-radius: 8px;
    padding: 16px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    margin-top: 16px;
}

.start-btn:hover {
    background: #EEEEEE;
    transform: translateY(-1px);
}

.start-btn:disabled {
    background: #333333;
    color: #666666;
    cursor: not-allowed;
    transform: none;
}
"""

with open('/Users/zhoumi/ai-drama-system/frontend/src/components/AgentWorkspace/AgentWorkspace.css', 'w') as f:
    f.write(css_content)
